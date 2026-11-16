"""Checks that run before a job starts, so long jobs do not fail late."""

from __future__ import annotations

import shutil
import tempfile
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from importlib.util import find_spec
from pathlib import Path

from ..media import AUDIO_EXTENSIONS

LEVEL_OK = "ok"
LEVEL_INFO = "info"
LEVEL_WARNING = "warning"
LEVEL_ERROR = "error"

# Approximate download sizes of faster-whisper models (MB).
MODEL_DOWNLOAD_MB = {
    "tiny": 75,
    "base": 145,
    "small": 484,
    "medium": 1530,
    "large-v3": 3090,
}
MIN_FREE_MB = 500
OUTPUT_MB_PER_HOUR = 120
AUDIO_TEMP_MB_PER_HOUR = 120


@dataclass(frozen=True)
class PreflightItem:
    level: str
    category: str
    title: str
    detail: str = ""


@dataclass
class PreflightReport:
    items: list[PreflightItem] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(item.level == LEVEL_ERROR for item in self.items)

    @property
    def has_warnings(self) -> bool:
        return any(item.level == LEVEL_WARNING for item in self.items)

    @property
    def needs_attention(self) -> bool:
        return self.has_errors or self.has_warnings

    def add(self, level: str, category: str, title: str, detail: str = "") -> None:
        self.items.append(PreflightItem(level, category, title, detail))


@dataclass(frozen=True)
class MediaInfo:
    readable: bool
    has_video: bool = False
    has_audio: bool = False
    duration_seconds: float | None = None
    error: str = ""


@dataclass(frozen=True)
class GpuStatus:
    available: bool
    message: str = ""


MediaProber = Callable[[Path], MediaInfo]
ModelChecker = Callable[[str], bool]
GpuInspector = Callable[[], GpuStatus]
DiskUsage = Callable[[Path], int]


def run_preflight(
    videos: Sequence[Path],
    output_dir: Path,
    options,
    *,
    media_prober: MediaProber | None = None,
    model_checker: ModelChecker | None = None,
    gpu_inspector: GpuInspector | None = None,
    free_space: DiskUsage | None = None,
    transcriber_installed: bool | None = None,
) -> PreflightReport:
    """Inspect inputs, output folder, transcription model and GPU before a run.

    ``options`` is a :class:`ProcessingOptions`. Probing functions can be
    replaced for tests.
    """
    media_prober = media_prober or probe_media
    model_checker = model_checker or model_is_cached
    gpu_inspector = gpu_inspector or inspect_gpu
    free_space = free_space or free_bytes
    report = PreflightReport()
    operations = options.operations.normalized()
    transcribe = operations.transcribe_audio

    from .processor import ProcessingConfigurationError, validate_options

    try:
        validate_options(options)
    except ProcessingConfigurationError as exc:
        report.add(LEVEL_ERROR, "settings", "設定に問題があります", str(exc))

    # Inputs -------------------------------------------------------------------
    total_duration = 0.0
    longest = 0.0
    readable_count = 0
    audio_only_inputs = bool(videos) and all(
        Path(path).suffix.lower() in AUDIO_EXTENSIONS for path in videos
    )
    for path in videos:
        path = Path(path)
        if not path.is_file():
            report.add(LEVEL_ERROR, "input", f"{path.name}: ファイルが見つかりません", str(path))
            continue
        info = media_prober(path)
        if not info.readable:
            report.add(
                LEVEL_ERROR,
                "input",
                f"{path.name}: ファイルを読み込めません",
                info.error or "破損しているか、未対応の形式の可能性があります。",
            )
            continue
        readable_count += 1
        if info.duration_seconds:
            total_duration += info.duration_seconds
            longest = max(longest, info.duration_seconds)
        is_audio_file = path.suffix.lower() in AUDIO_EXTENSIONS
        if transcribe and not info.has_audio:
            if is_audio_file:
                report.add(
                    LEVEL_ERROR,
                    "audio",
                    f"{path.name}: 音声トラックがありません",
                    "処理できません。",
                )
            else:
                report.add(
                    LEVEL_WARNING,
                    "audio",
                    f"{path.name}: 音声トラックがありません",
                    "この動画は文字起こしをスキップし、スライドだけを出力します。",
                )
        if not is_audio_file and operations.extract_slides and not info.has_video:
            report.add(
                LEVEL_WARNING,
                "input",
                f"{path.name}: 映像トラックがありません",
                "スライドは抽出されません。",
            )
    if readable_count:
        summary = f"{readable_count} 件のファイルを読み込めます"
        detail = f"合計 {format_hours(total_duration)}" if total_duration else ""
        report.add(LEVEL_OK, "input", summary, detail)
    elif not videos:
        report.add(LEVEL_ERROR, "input", "入力ファイルがありません")

    # Output folder --------------------------------------------------------------
    output_dir = Path(output_dir)
    writable, write_error = check_writable(output_dir)
    if not writable:
        report.add(LEVEL_ERROR, "output", "出力先に書き込めません", write_error)
    else:
        hours = total_duration / 3600
        needed_mb = MIN_FREE_MB + int(hours * OUTPUT_MB_PER_HOUR)
        free_mb = free_space(nearest_existing(output_dir)) // (1024 * 1024)
        if free_mb < needed_mb:
            report.add(
                LEVEL_ERROR,
                "disk",
                "出力先の空き容量が不足しています",
                f"空き {format_mb(free_mb)} / 必要な目安 {format_mb(needed_mb)}",
            )
        elif free_mb < needed_mb * 3:
            report.add(
                LEVEL_WARNING,
                "disk",
                "出力先の空き容量が少なめです",
                f"空き {format_mb(free_mb)} / 必要な目安 {format_mb(needed_mb)}",
            )
        else:
            report.add(LEVEL_OK, "disk", "出力先の空き容量は十分です", f"空き {format_mb(free_mb)}")

        if transcribe and longest:
            temp_dir = Path(tempfile.gettempdir())
            temp_needed = int(longest / 3600 * AUDIO_TEMP_MB_PER_HOUR) + 100
            temp_free = free_space(nearest_existing(temp_dir)) // (1024 * 1024)
            if temp_free < temp_needed:
                report.add(
                    LEVEL_ERROR,
                    "disk",
                    "一時フォルダの空き容量が不足しています",
                    f"音声の一時保存に約 {format_mb(temp_needed)} 必要です（空き {format_mb(temp_free)}）。",
                )

    # Transcription model --------------------------------------------------------
    if transcribe:
        installed = (
            transcriber_installed
            if transcriber_installed is not None
            else find_spec("faster_whisper") is not None
        )
        model_size = options.transcription_config.model_size
        if not installed:
            report.add(
                LEVEL_ERROR if audio_only_inputs else LEVEL_WARNING,
                "model",
                "文字起こしエンジンが見つかりません",
                "faster-whisper が未導入のため、文字起こしはスキップされます。",
            )
        elif model_checker(model_size):
            report.add(LEVEL_OK, "model", f"文字起こしモデル（{model_size}）は準備済みです")
        else:
            size = MODEL_DOWNLOAD_MB.get(model_size)
            size_text = f"約 {format_mb(size)}の" if size else ""
            report.add(
                LEVEL_WARNING,
                "model",
                f"文字起こしモデル（{model_size}）を初回ダウンロードします",
                f"{size_text}ダウンロードにはネットワーク接続が必要です。",
            )

        device = str(options.transcription_config.device).lower()
        gpu = gpu_inspector()
        if device == "cuda":
            if gpu.available:
                report.add(LEVEL_OK, "gpu", "GPU（CUDA）で文字起こしします")
            else:
                report.add(
                    LEVEL_WARNING,
                    "gpu",
                    "GPUを使えないため、CPUで処理します",
                    (gpu.message + " " if gpu.message else "")
                    + "CPUでは文字起こしに時間がかかります。",
                )
        elif gpu.available:
            report.add(
                LEVEL_INFO,
                "gpu",
                "GPUが利用できます",
                "文字起こし処理を「GPU（CUDA）」にすると速くなります。",
            )
        else:
            report.add(LEVEL_OK, "gpu", "CPUで文字起こしします")

    return report


def probe_media(path: Path) -> MediaInfo:
    """Return stream information using PyAV, falling back to OpenCV."""
    try:
        import av
    except ImportError:
        av = None
    if av is not None:
        try:
            container = av.open(str(path))
        except Exception as exc:
            return MediaInfo(readable=False, error=str(exc))
        try:
            has_video = any(stream.type == "video" for stream in container.streams)
            has_audio = any(stream.type == "audio" for stream in container.streams)
            duration = container.duration / 1_000_000 if container.duration else None
            return MediaInfo(True, has_video, has_audio, duration)
        finally:
            container.close()
    try:
        import cv2
    except ImportError:
        return MediaInfo(readable=True, has_video=True, has_audio=True)
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            return MediaInfo(readable=False)
        fps = cap.get(cv2.CAP_PROP_FPS)
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        duration = frames / fps if fps > 0 and frames > 0 else None
        # Without PyAV the audio track cannot be inspected; assume present.
        return MediaInfo(True, True, True, duration)
    finally:
        cap.release()


def model_is_cached(model_size: str) -> bool:
    if Path(model_size).is_dir():
        return True
    try:
        from faster_whisper.utils import download_model

        from ..transcriber import configure_huggingface_environment, model_files_readable

        configure_huggingface_environment()
        path = Path(download_model(model_size, local_files_only=True))
        return model_files_readable(path)
    except Exception:
        return False


def inspect_gpu() -> GpuStatus:
    try:
        from ..transcriber import inspect_cuda_runtime

        status = inspect_cuda_runtime()
        return GpuStatus(status.available, status.message)
    except Exception as exc:
        return GpuStatus(False, str(exc))


def check_writable(folder: Path) -> tuple[bool, str]:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / f".lectavo_write_test_{uuid.uuid4().hex}"
        probe.write_bytes(b"ok")
        probe.unlink()
        return True, ""
    except OSError as exc:
        return False, exc.strerror or str(exc)


def nearest_existing(path: Path) -> Path:
    path = Path(path)
    for candidate in [path, *path.parents]:
        if candidate.exists():
            return candidate
    return path


def free_bytes(path: Path) -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0


def format_mb(value: int | float) -> str:
    if value >= 1024:
        return f"{value / 1024:.1f} GB"
    return f"{int(value)} MB"


def format_hours(seconds: float) -> str:
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"
