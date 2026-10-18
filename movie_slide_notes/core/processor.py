from __future__ import annotations

import re
import shutil
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from threading import Event

import cv2
import numpy as np

from ..aligner import (
    CombinedSlideNote,
    CombinedSlideOccurrence,
    align_segments_to_slides,
    write_combined_slide_markdown,
    write_slide_markdown,
)
from ..duplicate_filter import DuplicateConfig, DuplicateFilter
from ..pdf_writer import write_pdf
from ..slide_detector import (
    VIDEO_EXTENSIONS,
    DetectionStats,
    SlideDetectionCancelled,
    SlideDetectionConfig,
    SlideDetector,
    SlideRecord,
    VideoReadError,
)
from ..transcriber import (
    TranscriberUnavailableError,
    TranscriptionCancelled,
    TranscriptionConfig,
    TranscriptionResult,
    TranscriptionSegment,
    inspect_cuda_runtime,
    transcribe_video,
    write_srt,
)
from ..transcriber import (
    format_timestamp as format_transcript_timestamp,
)
from .output_naming import OutputNamingConfig, output_stem


@dataclass(frozen=True)
class OperationOptions:
    extract_slides: bool = True
    generate_pdf: bool = True
    transcribe_audio: bool = True
    generate_markdown: bool = True
    generate_combined_pdf: bool = True

    def normalized(self) -> OperationOptions:
        extract_slides = (
            self.extract_slides
            or self.generate_pdf
            or self.generate_markdown
            or self.generate_combined_pdf
        )
        return replace(
            self,
            extract_slides=extract_slides,
        )


@dataclass(frozen=True)
class ProgressEvent:
    video_index: int
    video_count: int
    video_path: Path | None
    stage: str
    current: int
    total: int | None
    message: str
    overall_current: int = 0
    overall_total: int = 0

    @property
    def percent(self) -> int:
        if self.total:
            return max(0, min(100, int(self.current / self.total * 100)))
        return 0

    @property
    def overall_percent(self) -> int:
        if self.overall_total:
            return max(0, min(100, int(self.overall_current / self.overall_total * 100)))
        return 0


@dataclass
class ProcessingOptions:
    operations: OperationOptions = field(default_factory=OperationOptions)
    slide_config: SlideDetectionConfig = field(default_factory=SlideDetectionConfig)
    duplicate_config: DuplicateConfig = field(default_factory=DuplicateConfig)
    transcription_config: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    pdf_margin: float = 0.0
    per_video_dirs: bool = True
    output_mode: str = "per_video"
    show_progress: bool = False
    output_naming: OutputNamingConfig = field(default_factory=OutputNamingConfig)


@dataclass
class ProcessResult:
    video_path: Path
    output_dir: Path
    slides: list[SlideRecord] = field(default_factory=list)
    stats: DetectionStats | None = None
    pdf_path: Path | None = None
    markdown_path: Path | None = None
    transcription: TranscriptionResult | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    cancelled: bool = False

    @property
    def succeeded(self) -> bool:
        return self.error is None and not self.cancelled


@dataclass
class BatchResult:
    results: list[ProcessResult]
    output_mode: str = "per_video"
    combined_pdf_path: Path | None = None
    combined_transcript_path: Path | None = None
    combined_srt_path: Path | None = None
    combined_markdown_path: Path | None = None
    combined_slide_count: int = 0
    combined_duplicate_count: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def success_count(self) -> int:
        return sum(1 for result in self.results if result.succeeded)

    @property
    def failed_count(self) -> int:
        return sum(1 for result in self.results if result.error)

    @property
    def cancelled_count(self) -> int:
        return sum(1 for result in self.results if result.cancelled)


@dataclass
class _CombinedSlide:
    source: SlideRecord
    occurrences: list[CombinedSlideOccurrence] = field(default_factory=list)
    final: SlideRecord | None = None


class ProcessingCancelled(RuntimeError):
    pass


class ProcessingConfigurationError(RuntimeError):
    pass


ProgressHandler = Callable[[ProgressEvent], None]
LogHandler = Callable[[str], None]


PRESETS: dict[str, dict[str, float | int]] = {
    "standard": {},
    "sensitive": {
        "interval": 0.5,
        "min_component_area": 400,
        "diff_ratio_threshold": 0.015,
        "ssim_threshold": 0.95,
        "page_diff_ratio_threshold": 0.006,
    },
    "conservative": {
        "min_component_area": 1500,
        "diff_ratio_threshold": 0.04,
        "page_diff_ratio_threshold": 0.025,
        "min_gap": 7,
    },
}


def process_batch(
    video_paths: Sequence[Path],
    output_dir: Path,
    options: ProcessingOptions,
    progress_callback: ProgressHandler | None = None,
    log_callback: LogHandler | None = None,
    cancel_event: Event | None = None,
) -> BatchResult:
    options = replace(options, operations=options.operations.normalized())
    options = replace(
        options,
        output_naming=replace(
            options.output_naming,
            timestamp=options.output_naming.timestamp or datetime.now(),
        ),
    )
    validate_options(options)
    output_dir.mkdir(parents=True, exist_ok=True)

    videos = sorted((Path(path) for path in video_paths), key=natural_video_sort_key)
    results: list[ProcessResult] = []
    all_slides: list[SlideRecord] = []
    total_units = max(1, len(videos) * 100)
    combined_mode = options.output_mode == "combined"
    temporary_root: tempfile.TemporaryDirectory[str] | None = None
    work_output_dir = output_dir
    video_options = options
    if combined_mode:
        temporary_root = tempfile.TemporaryDirectory(prefix="lectavo_batch_")
        work_output_dir = Path(temporary_root.name)
        video_options = replace(
            options,
            operations=replace(
                options.operations,
                generate_pdf=False,
                generate_markdown=False,
                generate_combined_pdf=False,
            ),
            per_video_dirs=True,
        )

    try:
        for index, video_path in enumerate(videos, start=1):
            if is_cancelled(cancel_event):
                results.append(cancelled_result(video_path, output_dir))
                break

            try:
                video_work_output = (
                    work_output_dir / f"{index:04d}" if combined_mode else work_output_dir
                )
                result = process_video(
                    video_path=video_path,
                    output_dir=video_work_output,
                    options=video_options,
                    video_index=index,
                    video_count=len(videos),
                    progress_callback=progress_callback,
                    log_callback=log_callback,
                    cancel_event=cancel_event,
                    overall_base=(index - 1) * 100,
                    overall_total=total_units,
                )
                results.append(result)
                all_slides.extend(result.slides)
            except ProcessingCancelled:
                results.append(cancelled_result(video_path, output_dir))
                emit_log(log_callback, f"{video_path.name}: キャンセルしました")
                break
            except Exception as exc:
                message = describe_exception(exc)
                emit_log(log_callback, f"{video_path.name}: エラー - {message}")
                results.append(
                    ProcessResult(video_path=video_path, output_dir=output_dir, error=message)
                )
                continue

        batch = BatchResult(results=results, output_mode=options.output_mode)
        if combined_mode and not is_cancelled(cancel_event):
            finalize_combined_outputs(
                batch,
                output_dir,
                options,
                progress_callback,
                log_callback,
                total_units,
                len(videos),
            )
            for result in results:
                result.output_dir = output_dir
        elif (
            options.operations.generate_combined_pdf
            and all_slides
            and not is_cancelled(cancel_event)
        ):
            stem = output_stem(videos, options.output_naming, combined=True)
            batch.combined_pdf_path = output_dir / f"{stem}_slides.pdf"
            write_pdf(
                [slide.path for slide in all_slides], batch.combined_pdf_path, options.pdf_margin
            )
        return batch
    finally:
        if temporary_root is not None:
            temporary_root.cleanup()


def finalize_combined_outputs(
    batch: BatchResult,
    output_dir: Path,
    options: ProcessingOptions,
    progress_callback: ProgressHandler | None,
    log_callback: LogHandler | None,
    total_units: int,
    video_count: int,
) -> None:
    emit_progress(
        progress_callback,
        None,
        video_count,
        video_count,
        "combined_pdf",
        0,
        1,
        "全動画の出力をまとめています",
        total_units,
        total_units,
    )
    stem = output_stem(
        [result.video_path for result in batch.results], options.output_naming, combined=True
    )
    combined_slides, combined_notes, duplicate_count = collect_combined_slides(
        batch.results,
        output_dir,
        options.duplicate_config,
        slides_subdir="slides" if stem == "all" else f"{stem}_images",
    )
    batch.combined_slide_count = len(combined_slides)
    batch.combined_duplicate_count = duplicate_count
    emit_log(
        log_callback,
        f"全動画のスライドを統合しました: 保存 {len(combined_slides)} 件、重複除去 {duplicate_count} 件",
    )

    if options.operations.generate_pdf and combined_slides:
        batch.combined_pdf_path = output_dir / f"{stem}_slides.pdf"
        write_pdf(
            [slide.path for slide in combined_slides], batch.combined_pdf_path, options.pdf_margin
        )
        emit_log(log_callback, f"統合PDFを生成しました: {batch.combined_pdf_path}")

    if options.operations.transcribe_audio:
        transcript_paths = write_combined_transcripts(batch.results, output_dir, stem)
        if transcript_paths is not None:
            batch.combined_transcript_path, batch.combined_srt_path = transcript_paths
            emit_log(
                log_callback, f"統合文字起こしを生成しました: {batch.combined_transcript_path}"
            )

    if options.operations.generate_markdown:
        batch.combined_markdown_path = output_dir / f"{stem}_lecture_notes.md"
        write_combined_slide_markdown(
            "Lectavo 講義ノート", combined_notes, batch.combined_markdown_path
        )
        emit_log(log_callback, f"統合講義ノートを生成しました: {batch.combined_markdown_path}")


def collect_combined_slides(
    results: Sequence[ProcessResult],
    output_dir: Path,
    duplicate_config: DuplicateConfig,
    slides_subdir: str = "slides",
) -> tuple[list[SlideRecord], list[CombinedSlideNote], int]:
    duplicate_filter = DuplicateFilter(duplicate_config)
    unique: list[_CombinedSlide] = []
    saved_path_to_index: dict[Path, int] = {}
    duplicate_count = 0

    for result in results:
        segments = result.transcription.segments if result.transcription else []
        aligned = align_segments_to_slides(result.slides, segments)
        for slide, aligned_item in zip(result.slides, aligned):
            frame = read_slide_image(slide.path)
            match = duplicate_filter.find_duplicate(frame) if frame is not None else None
            if match is not None and match.path.resolve() in saved_path_to_index:
                unique_index = saved_path_to_index[match.path.resolve()]
                duplicate_count += 1
            else:
                unique_index = len(unique)
                unique.append(_CombinedSlide(source=slide))
                saved_path_to_index[slide.path.resolve()] = unique_index
                if frame is not None:
                    duplicate_filter.add(frame, slide.path, slide.timestamp)
            unique[unique_index].occurrences.append(
                CombinedSlideOccurrence(
                    video_title=result.video_path.stem,
                    start=slide.timestamp,
                    transcript=aligned_item.transcript,
                )
            )

    slides_dir = output_dir / slides_subdir
    slides_dir.mkdir(parents=True, exist_ok=True)
    final_slides: list[SlideRecord] = []
    notes: list[CombinedSlideNote] = []
    for index, item in enumerate(unique, start=1):
        source = item.source
        suffix = source.path.suffix.lower() or ".png"
        filename = (
            f"slide_{index:03d}_{sanitize_stem(Path(source.video_name).stem)}_"
            f"{format_slide_filename_time(source.timestamp)}{suffix}"
        )
        destination = slides_dir / filename
        shutil.copy2(source.path, destination)
        final = SlideRecord(
            index=index,
            timestamp=source.timestamp,
            path=destination,
            video_name=source.video_name,
        )
        item.final = final
        final_slides.append(final)
        notes.append(CombinedSlideNote(slide=final, occurrences=tuple(item.occurrences)))
    return final_slides, notes, duplicate_count


def write_combined_transcripts(
    results: Sequence[ProcessResult],
    output_dir: Path,
    stem: str = "all",
) -> tuple[Path, Path] | None:
    transcript_results = [result for result in results if result.transcription is not None]
    if not transcript_results:
        return None

    text_path = output_dir / f"{stem}_transcript.txt"
    srt_path = output_dir / f"{stem}_transcript.srt"
    lines: list[str] = []
    global_segments: list[TranscriptionSegment] = []
    offset = 0.0
    for result in results:
        if result.transcription is not None:
            lines.extend([f"# {result.video_path.stem}", ""])
            for segment in result.transcription.segments:
                lines.append(
                    f"[{format_transcript_timestamp(segment.start)} --> "
                    f"{format_transcript_timestamp(segment.end)}] {segment.text}"
                )
                global_segments.append(
                    TranscriptionSegment(
                        start=segment.start + offset,
                        end=segment.end + offset,
                        text=segment.text,
                    )
                )
            lines.append("")
        offset += result_duration(result)

    text_path.write_text("\n".join(lines), encoding="utf-8")
    write_srt(global_segments, srt_path)
    return text_path, srt_path


def read_slide_image(path: Path) -> np.ndarray | None:
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except (OSError, ValueError):
        return None


def result_duration(result: ProcessResult) -> float:
    if result.stats and result.stats.duration_seconds:
        return float(result.stats.duration_seconds)
    if result.transcription and result.transcription.segments:
        return max(segment.end for segment in result.transcription.segments)
    if result.slides:
        return max(slide.timestamp for slide in result.slides)
    return 0.0


def format_slide_filename_time(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    return f"{minutes:02d}m{secs:02d}s"


def process_video(
    video_path: Path,
    output_dir: Path,
    options: ProcessingOptions,
    video_index: int = 1,
    video_count: int = 1,
    progress_callback: ProgressHandler | None = None,
    log_callback: LogHandler | None = None,
    cancel_event: Event | None = None,
    overall_base: int = 0,
    overall_total: int = 100,
) -> ProcessResult:
    if is_cancelled(cancel_event):
        raise ProcessingCancelled("処理をキャンセルしました。")

    video_path = Path(video_path)
    video_stem = output_stem([video_path], options.output_naming, combined=False)
    video_output_dir = output_dir / video_stem if options.per_video_dirs else output_dir
    video_output_dir.mkdir(parents=True, exist_ok=True)
    result = ProcessResult(video_path=video_path, output_dir=video_output_dir)

    emit_log(log_callback, f"{video_path.name}: 処理を開始しました")
    emit_progress(
        progress_callback,
        video_path,
        video_index,
        video_count,
        "start",
        0,
        1,
        "処理を開始しました",
        overall_base,
        overall_total,
    )

    if options.operations.extract_slides:
        emit_log(log_callback, f"{video_path.name}: スライド解析を開始しました")
        detector = SlideDetector(options.slide_config)
        duplicate_filter = DuplicateFilter(options.duplicate_config)

        def slide_progress(stage: str, current: int, total: int | None, message: str) -> None:
            emit_progress(
                progress_callback,
                video_path,
                video_index,
                video_count,
                stage,
                current,
                total,
                f"{message}: {current} / {total or '?'} フレーム",
                overall_base + scale_progress(current, total, 60),
                overall_total,
            )

        try:
            slides, stats = detector.extract_slides(
                video_path=video_path,
                output_dir=video_output_dir / "slides",
                video_stem=video_stem,
                duplicate_filter=duplicate_filter,
                show_progress=options.show_progress,
                progress_callback=slide_progress,
                cancel_event=cancel_event,
                filename_prefix=None if not options.per_video_dirs else "",
            )
        except SlideDetectionCancelled as exc:
            raise ProcessingCancelled(str(exc)) from exc
        result.slides = slides
        result.stats = stats
        emit_log(
            log_callback,
            f"{video_path.name}: 候補 {stats.candidates} 件、重複 {stats.duplicates} 件、保存 {len(slides)} 件",
        )

    if options.operations.generate_pdf:
        if is_cancelled(cancel_event):
            raise ProcessingCancelled("PDF生成をキャンセルしました。")
        emit_stage(
            log_callback,
            progress_callback,
            video_path,
            video_index,
            video_count,
            "pdf",
            "PDF生成中",
            overall_base + 65,
            overall_total,
        )
        if result.slides:
            result.pdf_path = video_output_dir / f"{video_stem}_slides.pdf"
            try:
                write_pdf(
                    [slide.path for slide in result.slides], result.pdf_path, options.pdf_margin
                )
                emit_log(log_callback, f"{video_path.name}: PDFを生成しました: {result.pdf_path}")
            except Exception as exc:
                result.error = describe_exception(exc)
                emit_log(log_callback, f"{video_path.name}: PDF生成に失敗しました - {result.error}")
        else:
            result.warnings.append("PDFにするスライドがありません。")
            emit_log(log_callback, f"{video_path.name}: PDFにするスライドがありません")

    if options.operations.transcribe_audio:
        if is_cancelled(cancel_event):
            raise ProcessingCancelled("文字起こしをキャンセルしました。")

        def transcribe_progress(stage: str, current: int, total: int | None, message: str) -> None:
            emit_progress(
                progress_callback,
                video_path,
                video_index,
                video_count,
                stage,
                current,
                total,
                format_transcription_message(stage, current, total, message),
                overall_base + 70 + scale_progress(current, total, 25),
                overall_total,
            )

        try:
            result.transcription = transcribe_video(
                video_path=video_path,
                output_dir=video_output_dir,
                video_stem=video_stem,
                config=options.transcription_config,
                progress_callback=transcribe_progress,
                cancel_event=cancel_event,
                duration_seconds=result.stats.duration_seconds if result.stats else None,
            )
            if result.transcription.warning:
                result.warnings.append(result.transcription.warning)
                emit_log(log_callback, f"{video_path.name}: {result.transcription.warning}")
            emit_log(
                log_callback,
                f"{video_path.name}: 文字起こしを保存しました: {result.transcription.text_path}",
            )
        except TranscriberUnavailableError as exc:
            warning = describe_exception(exc)
            result.warnings.append(warning)
            emit_log(log_callback, f"{video_path.name}: 文字起こしをスキップしました - {warning}")
        except TranscriptionCancelled as exc:
            raise ProcessingCancelled(str(exc)) from exc
        except Exception as exc:
            result.error = describe_exception(exc)
            emit_log(log_callback, f"{video_path.name}: 文字起こしに失敗しました - {result.error}")

    if options.operations.generate_markdown:
        if is_cancelled(cancel_event):
            raise ProcessingCancelled("Markdown生成をキャンセルしました。")
        emit_stage(
            log_callback,
            progress_callback,
            video_path,
            video_index,
            video_count,
            "markdown",
            "Markdown生成中",
            overall_base + 97,
            overall_total,
        )
        segments = result.transcription.segments if result.transcription else []
        result.markdown_path = video_output_dir / f"{video_stem}_slides.md"
        write_slide_markdown(video_stem, result.slides, segments, result.markdown_path)
        emit_log(log_callback, f"{video_path.name}: Markdownを生成しました: {result.markdown_path}")

    final_stage = "error" if result.error else "done"
    final_message = "エラー" if result.error else "完了"
    emit_stage(
        log_callback,
        progress_callback,
        video_path,
        video_index,
        video_count,
        final_stage,
        final_message,
        overall_base + 100,
        overall_total,
    )
    return result


def discover_videos(input_path: Path, recursive: bool = False) -> list[Path]:
    input_path = Path(input_path)
    if input_path.is_file():
        return [input_path] if input_path.suffix.lower() in VIDEO_EXTENSIONS else []
    if not input_path.is_dir():
        return []

    iterator = input_path.rglob("*") if recursive else input_path.iterdir()
    return sorted(
        (path for path in iterator if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS),
        key=natural_video_sort_key,
    )


def natural_video_sort_key(path: Path) -> tuple[tuple[tuple[int, object], ...], str]:
    """Sort lecture names naturally so 13-9 comes before 13-10."""
    name_parts: list[tuple[int, object]] = []
    for part in re.split(r"(\d+)", Path(path).name.casefold()):
        if part.isdigit():
            name_parts.append((1, int(part)))
        else:
            name_parts.append((0, part))
    return tuple(name_parts), str(Path(path).parent).casefold()


def apply_preset(config: SlideDetectionConfig, preset_name: str) -> SlideDetectionConfig:
    values = PRESETS.get(preset_name, {})
    return replace(config, **values)


def validate_options(options: ProcessingOptions) -> None:
    try:
        output_stem([], options.output_naming, combined=True)
    except ValueError as exc:
        raise ProcessingConfigurationError(str(exc)) from exc
    if options.output_mode not in {"combined", "per_video"}:
        raise ProcessingConfigurationError(f"未対応の出力方式です: {options.output_mode}")


def is_cuda_available() -> bool:
    return inspect_cuda_runtime().available


def sanitize_stem(stem: str) -> str:
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", stem).strip(" ._")
    return sanitized or "video"


def describe_exception(exc: Exception) -> str:
    if isinstance(exc, ProcessingConfigurationError):
        return str(exc)
    if isinstance(exc, TranscriberUnavailableError):
        return "faster-whisperが未導入のため文字起こしを実行できません。"
    if isinstance(exc, VideoReadError):
        return (
            "動画を読み込めませんでした。ファイルが破損しているか、未対応形式の可能性があります。"
        )
    if isinstance(exc, OSError) and getattr(exc, "winerror", None) == 112:
        return "出力先の空き容量が不足している可能性があります。"
    return str(exc) or exc.__class__.__name__


def cancelled_result(video_path: Path, output_dir: Path) -> ProcessResult:
    return ProcessResult(
        video_path=Path(video_path),
        output_dir=Path(output_dir),
        cancelled=True,
    )


def is_cancelled(cancel_event: Event | None) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def scale_progress(current: int, total: int | None, span: int) -> int:
    if not total:
        return 0
    return max(0, min(span, int(current / total * span)))


def format_transcription_message(stage: str, current: int, total: int | None, message: str) -> str:
    if stage == "model_prepare":
        if message.startswith("CUDAが使えないため"):
            return message
        return f"{message}。初回のみダウンロードが必要です。"
    if total:
        return f"{message}: {format_seconds(current)} / {format_seconds(total)}"
    return message


def format_seconds(seconds: int) -> str:
    minutes, secs = divmod(max(0, seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def emit_stage(
    log_callback: LogHandler | None,
    progress_callback: ProgressHandler | None,
    video_path: Path | None,
    video_index: int,
    video_count: int,
    stage: str,
    message: str,
    overall_current: int,
    overall_total: int,
) -> None:
    emit_log(log_callback, f"{video_path.name if video_path else '全体'}: {message}")
    emit_progress(
        progress_callback,
        video_path,
        video_index,
        video_count,
        stage,
        1,
        1,
        message,
        overall_current,
        overall_total,
    )


def emit_progress(
    progress_callback: ProgressHandler | None,
    video_path: Path | None,
    video_index: int,
    video_count: int,
    stage: str,
    current: int,
    total: int | None,
    message: str,
    overall_current: int,
    overall_total: int,
) -> None:
    if progress_callback is not None:
        progress_callback(
            ProgressEvent(
                video_index=video_index,
                video_count=video_count,
                video_path=video_path,
                stage=stage,
                current=current,
                total=total,
                message=message,
                overall_current=overall_current,
                overall_total=overall_total,
            )
        )


def emit_log(log_callback: LogHandler | None, message: str) -> None:
    if log_callback is not None:
        log_callback(message)
