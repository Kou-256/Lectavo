from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import warnings
import wave
from array import array
from collections.abc import Callable
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from threading import Event

from .paths import get_resource_base_dirs, user_cache_dir

ProgressCallback = Callable[[str, int, int | None, str], None]

_CUDA_DLL_DIRECTORY_HANDLES: list[object] = []
_CUDA_DLL_DIRECTORIES_CONFIGURED = False


@dataclass(frozen=True)
class TranscriptionSegment:
    start: float
    end: float
    text: str


@dataclass
class TranscriptionConfig:
    model_size: str = "small"
    language: str | None = "auto"
    device: str = "cpu"
    compute_type: str = "int8"
    keep_audio: bool = False
    ffmpeg: str = "ffmpeg"
    hf_token: str | None = None


@dataclass(frozen=True)
class TranscriptionResult:
    segments: list[TranscriptionSegment]
    text_path: Path
    srt_path: Path
    audio_path: Path | None
    warning: str | None = None
    duration_seconds: float | None = None
    status: str = "transcribed"


@dataclass(frozen=True)
class CudaRuntimeStatus:
    available: bool
    device_count: int
    missing_dlls: tuple[str, ...] = ()
    message: str = ""


class TranscriberUnavailableError(RuntimeError):
    pass


class TranscriptionCancelled(RuntimeError):
    pass


def transcribe_video(
    video_path: Path,
    output_dir: Path,
    video_stem: str,
    config: TranscriptionConfig,
    progress_callback: ProgressCallback | None = None,
    cancel_event: Event | None = None,
    duration_seconds: float | None = None,
) -> TranscriptionResult:
    configure_huggingface_environment()
    check_cancelled(cancel_event)
    emit_progress(progress_callback, "audio_extract", 0, None, "音声を抽出しています")
    temp_audio_dir: tempfile.TemporaryDirectory[str] | None = None
    if config.keep_audio:
        audio_dir = output_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        audio_path = audio_dir / f"{video_stem}.wav"
    else:
        temp_audio_dir = tempfile.TemporaryDirectory(prefix="lectavo_audio_")
        audio_path = Path(temp_audio_dir.name) / f"{video_stem}.wav"

    try:
        extract_audio(video_path, audio_path, config.ffmpeg, cancel_event)
        if duration_seconds is None:
            with wave.open(str(audio_path), "rb") as audio:
                duration_seconds = audio.getnframes() / audio.getframerate()
        check_cancelled(cancel_event)

        if is_silent_audio(audio_path, cancel_event):
            message = "無音です（音量がほぼゼロのため、文字起こしを行いませんでした）。"
            emit_progress(progress_callback, "silence", 1, 1, message)
            text_path = output_dir / f"{video_stem}_transcript.txt"
            srt_path = output_dir / f"{video_stem}_transcript.srt"
            text_path.write_text(message + "\n", encoding="utf-8")
            write_srt([], srt_path)
            return TranscriptionResult(
                [],
                text_path,
                srt_path,
                audio_path if config.keep_audio else None,
                message,
                duration_seconds,
                "silence",
            )

        emit_progress(
            progress_callback,
            "model_prepare",
            0,
            None,
            f"文字起こしモデルを準備しています: faster-whisper-{config.model_size}",
        )
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise TranscriberUnavailableError(
                "faster-whisper が未導入のため文字起こしをスキップします。"
            ) from exc

        active_device = config.device
        active_compute_type = config.compute_type
        warning: str | None = None
        if active_device.lower() == "cuda":
            cuda_status = inspect_cuda_runtime()
            if not cuda_status.available:
                warning = f"CUDAが使えないためCPUで続行しました（{cuda_status.message}）"
                emit_progress(progress_callback, "model_prepare", 0, None, warning)
                active_device = "cpu"
                active_compute_type = "int8"
        try:
            model = create_whisper_model(
                WhisperModel,
                config,
                active_device,
                active_compute_type,
                progress_callback,
                cancel_event,
            )
        except Exception as exc:
            if active_device.lower() != "cuda" or not should_fallback_to_cpu(config, exc):
                raise
            warning = "CUDAが使えないためCPUで続行しました"
            emit_progress(progress_callback, "model_prepare", 0, None, warning)
            active_device = "cpu"
            active_compute_type = "int8"
            model = create_whisper_model(
                WhisperModel,
                config,
                active_device,
                active_compute_type,
                progress_callback,
                cancel_event,
            )
        check_cancelled(cancel_event)
        language = None if config.language in (None, "", "auto") else config.language
        emit_progress(
            progress_callback, "transcribe", 0, seconds_to_int(duration_seconds), "文字起こし中"
        )
        try:
            segment_iter, _ = model.transcribe(str(audio_path), language=language, vad_filter=True)
            segments = collect_segments(
                segment_iter, progress_callback, cancel_event, duration_seconds
            )
        except Exception as exc:
            if not should_fallback_to_cpu(config, exc) or active_device == "cpu":
                raise
            warning = "CUDAが使えないためCPUで続行しました"
            emit_progress(progress_callback, "model_prepare", 0, None, warning)
            model = create_whisper_model(
                WhisperModel, config, "cpu", "int8", progress_callback, cancel_event
            )
            segment_iter, _ = model.transcribe(str(audio_path), language=language, vad_filter=True)
            segments = collect_segments(
                segment_iter, progress_callback, cancel_event, duration_seconds
            )
    finally:
        if temp_audio_dir is not None:
            temp_audio_dir.cleanup()

    text_path = output_dir / f"{video_stem}_transcript.txt"
    srt_path = output_dir / f"{video_stem}_transcript.srt"
    write_text_transcript(segments, text_path)
    write_srt(segments, srt_path)
    status = "transcribed" if segments else "no_speech"
    if not segments:
        message = "発話を検出できませんでした（無音とは限りません。音量・言語・録音内容を確認してください）。"
        text_path.write_text(message + "\n", encoding="utf-8")
        warning = f"{warning}\n{message}" if warning else message

    return TranscriptionResult(
        segments=segments,
        text_path=text_path,
        srt_path=srt_path,
        audio_path=audio_path if config.keep_audio else None,
        warning=warning,
        duration_seconds=duration_seconds,
        status=status,
    )


def create_whisper_model(
    whisper_model_class,
    config: TranscriptionConfig,
    device: str,
    compute_type: str,
    progress_callback: ProgressCallback | None = None,
    cancel_event: Event | None = None,
):
    if device.lower() == "cuda":
        configure_cuda_dll_directories()
    from faster_whisper.utils import download_model

    check_cancelled(cancel_event)
    try:
        if Path(config.model_size).is_dir():
            model_path = Path(config.model_size)
        else:
            try:
                model_path = Path(
                    download_model(
                        config.model_size,
                        local_files_only=True,
                        use_auth_token=config.hf_token or False,
                    )
                )
            except Exception:
                check_cancelled(cancel_event)
                model_path = Path(
                    download_model(config.model_size, use_auth_token=config.hf_token or False)
                )
        if not model_files_readable(model_path):
            emit_progress(
                progress_callback,
                "model_prepare",
                0,
                None,
                "モデルの保存データが不完全です。モデルを再取得しています（無音エラーではありません）。",
            )
            model_path = repair_model(config, model_path)
        check_cancelled(cancel_event)
        try:
            return whisper_model_class(
                str(model_path), device=device, compute_type=compute_type, local_files_only=True
            )
        except RuntimeError as exc:
            if "unable to open file" not in str(exc).lower():
                raise
            emit_progress(
                progress_callback,
                "model_prepare",
                0,
                None,
                "モデルを開けません。キャッシュを修復しています（無音エラーではありません）。",
            )
            repaired = repair_model(config, model_path)
            check_cancelled(cancel_event)
            return whisper_model_class(
                str(repaired), device=device, compute_type=compute_type, local_files_only=True
            )
    except TranscriptionCancelled:
        raise
    except Exception as exc:
        if should_fallback_to_cpu(config, exc):
            raise
        raise RuntimeError(
            "文字起こしモデルを読み込めません。無音が原因ではありません。"
            "モデルの取得失敗・キャッシュ破損の可能性があります。"
            "ネットワークと空き容量を確認し、再試行するか別のモデルを選択してください。"
        ) from exc


def model_files_readable(folder: Path) -> bool:
    try:
        model = folder / "model.bin"
        if model.stat().st_size < 1024:
            return False
        with model.open("rb") as handle:
            if not handle.read(16):
                return False
        for name in ("config.json", "tokenizer.json"):
            with (folder / name).open(encoding="utf-8") as handle:
                json.load(handle)
        return True
    except (OSError, ValueError):
        return False


def repair_model(config: TranscriptionConfig, original: Path) -> Path:
    from huggingface_hub import snapshot_download

    # Materialize a separate repair copy without deleting or modifying old caches.
    repo_folder = original.parent.parent.name
    if repo_folder.startswith("models--"):
        repo_id = repo_folder.removeprefix("models--").replace("--", "/")
    elif "/" in config.model_size and not Path(config.model_size).exists():
        repo_id = config.model_size
    else:
        raise RuntimeError("ローカルモデルが不完全です。モデルを再配置してください。")
    destination = (
        user_cache_dir() / "repaired_models" / hashlib.sha256(repo_id.encode()).hexdigest()[:16]
    )
    if not model_files_readable(destination) and model_files_readable(original):
        destination.mkdir(parents=True, exist_ok=True)
        for source in original.iterdir():
            if source.name not in (
                "model.bin",
                "config.json",
                "tokenizer.json",
                "preprocessor_config.json",
            ) and not source.name.startswith("vocabulary."):
                continue
            if not source.is_file():
                continue
            with tempfile.NamedTemporaryFile(dir=destination, delete=False) as temporary:
                temporary_path = Path(temporary.name)
            try:
                shutil.copyfile(source, temporary_path)
                temporary_path.replace(destination / source.name)
            finally:
                temporary_path.unlink(missing_ok=True)
    if not model_files_readable(destination):
        snapshot_download(
            repo_id,
            local_dir=str(destination),
            force_download=True,
            token=config.hf_token or False,
            allow_patterns=[
                "config.json",
                "preprocessor_config.json",
                "model.bin",
                "tokenizer.json",
                "vocabulary.*",
            ],
        )
    if not model_files_readable(destination):
        raise RuntimeError("モデルの再取得後の検証に失敗しました。")
    return destination


def is_silent_audio(path: Path, cancel_event: Event | None = None) -> bool:
    with wave.open(str(path), "rb") as audio:
        if audio.getnframes() == 0:
            raise RuntimeError("音声データが空です。ファイルの内容を確認してください。")
        while chunk := audio.readframes(16000):
            check_cancelled(cancel_event)
            samples = array("h")
            samples.frombytes(chunk)
            if any(abs(sample) > 2 for sample in samples):
                return False
    return True


def should_fallback_to_cpu(config: TranscriptionConfig, exc: Exception) -> bool:
    if config.device.lower() != "cuda":
        return False
    message = str(exc).lower()
    cuda_markers = [
        "cuda",
        "cublas",
        "cudnn",
        "cudart",
        "cufft",
        "curand",
        "cusolver",
        "cusparse",
        "nvcuda",
        "library",
        "dll",
    ]
    return any(marker in message for marker in cuda_markers)


def collect_segments(
    segment_iter,
    progress_callback: ProgressCallback | None,
    cancel_event: Event | None,
    duration_seconds: float | None,
) -> list[TranscriptionSegment]:
    segments: list[TranscriptionSegment] = []
    for segment in segment_iter:
        check_cancelled(cancel_event)
        typed_segment = TranscriptionSegment(
            start=float(segment.start),
            end=float(segment.end),
            text=segment.text.strip(),
        )
        segments.append(typed_segment)
        emit_progress(
            progress_callback,
            "transcribe",
            seconds_to_int(typed_segment.end),
            seconds_to_int(duration_seconds),
            "文字起こし中",
        )
    return segments


def extract_audio(
    video_path: Path,
    audio_path: Path,
    ffmpeg: str = "ffmpeg",
    cancel_event: Event | None = None,
) -> None:
    """Decode the first audio stream to 16 kHz mono PCM with bundled PyAV.

    ``ffmpeg`` remains in the signature for CLI and saved-settings compatibility.
    PyAV ships the required FFmpeg libraries, so no external executable is used.
    """
    del ffmpeg
    check_cancelled(cancel_event)
    try:
        import av
        from av.audio.resampler import AudioResampler
    except ImportError as exc:
        raise TranscriberUnavailableError("PyAV が未導入のため動画音声を読み込めません。") from exc

    audio_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        container = av.open(str(video_path))
    except Exception as exc:
        raise RuntimeError(
            "動画の音声を読み込めません。ファイルが破損しているか、未対応形式の可能性があります。"
        ) from exc

    try:
        audio_streams = [stream for stream in container.streams if stream.type == "audio"]
        if not audio_streams:
            raise RuntimeError("動画に音声トラックがありません。")
        stream = audio_streams[0]
        resampler = AudioResampler(format="s16", layout="mono", rate=16000)
        with wave.open(str(audio_path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            for source_frame in container.decode(stream):
                check_cancelled(cancel_event)
                for frame in _resampled_frames(resampler.resample(source_frame)):
                    output.writeframes(frame.to_ndarray().tobytes())
            for frame in _resampled_frames(resampler.resample(None)):
                check_cancelled(cancel_event)
                output.writeframes(frame.to_ndarray().tobytes())
    finally:
        container.close()


def _resampled_frames(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def inspect_cuda_runtime() -> CudaRuntimeStatus:
    configure_cuda_dll_directories()
    try:
        import ctranslate2
    except Exception as exc:
        return CudaRuntimeStatus(False, 0, message=f"CTranslate2を読み込めません: {exc}")

    try:
        device_count = int(ctranslate2.get_cuda_device_count())
    except Exception as exc:
        return CudaRuntimeStatus(False, 0, message=f"CUDAデバイスを確認できません: {exc}")
    if device_count < 1:
        return CudaRuntimeStatus(
            False, 0, message="対応するNVIDIA GPUまたはドライバーが見つかりません"
        )

    if os.name == "nt":
        required_dlls = ("cublas64_12.dll", "cudnn64_9.dll")
        missing = tuple(name for name in required_dlls if not can_load_windows_dll(name))
        if missing:
            return CudaRuntimeStatus(
                False,
                device_count,
                missing_dlls=missing,
                message="必要なCUDAランタイムがありません: " + ", ".join(missing),
            )
    return CudaRuntimeStatus(True, device_count, message="CUDAを利用できます")


def configure_cuda_dll_directories() -> tuple[Path, ...]:
    """Register NVIDIA wheel DLL folders for source and PyInstaller builds."""
    global _CUDA_DLL_DIRECTORIES_CONFIGURED
    if os.name != "nt" or _CUDA_DLL_DIRECTORIES_CONFIGURED:
        return ()

    candidates: list[Path] = []
    package_parts = {
        "ctranslate2": ("ctranslate2",),
        "nvidia.cublas": ("nvidia", "cublas", "bin"),
        "nvidia.cuda_nvrtc": ("nvidia", "cuda_nvrtc", "bin"),
    }
    for module_name, relative_parts in package_parts.items():
        try:
            spec = find_spec(module_name)
        except (ImportError, ModuleNotFoundError, ValueError):
            spec = None
        if spec and spec.submodule_search_locations:
            for location in spec.submodule_search_locations:
                package_dir = Path(location)
                candidates.append(
                    package_dir / "bin" if module_name.startswith("nvidia.") else package_dir
                )
        candidates.extend(base.joinpath(*relative_parts) for base in get_resource_base_dirs())

    registered: list[Path] = []
    for directory in dict.fromkeys(path.resolve() for path in candidates if path.is_dir()):
        try:
            handle = os.add_dll_directory(str(directory))
        except (AttributeError, OSError):
            continue
        _CUDA_DLL_DIRECTORY_HANDLES.append(handle)
        registered.append(directory)

    if registered:
        existing_path = os.environ.get("PATH", "")
        registered_text = [str(path) for path in registered]
        os.environ["PATH"] = os.pathsep.join(
            registered_text + ([existing_path] if existing_path else [])
        )
    _CUDA_DLL_DIRECTORIES_CONFIGURED = True
    return tuple(registered)


def can_load_windows_dll(name: str) -> bool:
    try:
        import ctypes

        ctypes.WinDLL(name)
    except (AttributeError, OSError):
        return False
    return True


def configure_huggingface_environment() -> None:
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
    os.environ.setdefault("HF_HUB_VERBOSITY", "error")
    os.environ.setdefault("HF_HOME", str(user_cache_dir() / "huggingface"))
    warnings.filterwarnings("ignore", message=".*unauthenticated requests.*")
    warnings.filterwarnings("ignore", message=".*cache-system uses symlinks.*")
    logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
    logging.getLogger("faster_whisper").setLevel(logging.ERROR)
    try:
        from huggingface_hub.utils import logging as hf_logging

        hf_logging.set_verbosity_error()
    except Exception:
        pass


def check_cancelled(cancel_event: Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise TranscriptionCancelled("文字起こしをキャンセルしました。")


def emit_progress(
    progress_callback: ProgressCallback | None,
    stage: str,
    current: int,
    total: int | None,
    message: str,
) -> None:
    if progress_callback is not None:
        progress_callback(stage, current, total, message)


def seconds_to_int(seconds: float | None) -> int | None:
    if seconds is None:
        return None
    return max(0, int(round(seconds)))


def write_text_transcript(segments: list[TranscriptionSegment], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for segment in segments:
        lines.append(
            f"[{format_timestamp(segment.start)} --> {format_timestamp(segment.end)}] "
            f"{segment.text}"
        )
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def write_srt(segments: list[TranscriptionSegment], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    blocks: list[str] = []
    for index, segment in enumerate(segments, start=1):
        blocks.append(
            "\n".join(
                [
                    str(index),
                    f"{format_timestamp(segment.start, srt=True)} --> "
                    f"{format_timestamp(segment.end, srt=True)}",
                    segment.text,
                ]
            )
        )
    path.write_text("\n\n".join(blocks) + ("\n" if blocks else ""), encoding="utf-8")


def format_timestamp(seconds: float, srt: bool = False) -> str:
    total_ms = int(round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    if srt:
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"
