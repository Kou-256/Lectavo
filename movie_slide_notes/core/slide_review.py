"""Review and correct extracted slides, then rebuild the PDF and notes."""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from ..aligner import (
    CombinedSlideNote,
    CombinedSlideOccurrence,
    transcripts_for_starts,
    write_slide_markdown,
)
from ..lazy_imports import LazyModule
from ..pdf_writer import write_pdf
from ..slide_detector import (
    SlideRecord,
    crop_region,
    format_timestamp_for_filename,
    write_frame_png,
)
from .processor import BatchResult, ProcessResult, sanitize_stem, write_combined_notes

cv2 = LazyModule("cv2")

_NAME_PATTERN = re.compile(r"^(?P<prefix>.*?)slide_\d{3,}_(?P<rest>.+)$")


@dataclass(frozen=True)
class ReviewSlide:
    path: Path
    timestamp: float
    source_index: int
    occurrences: tuple[tuple[int, float], ...] = ()
    added: bool = False

    @property
    def sort_key(self) -> tuple[int, float]:
        return self.source_index, self.timestamp


@dataclass
class ReviewTarget:
    label: str
    slides: list[ReviewSlide]
    slides_dir: Path
    result_indices: list[int]
    combined: bool
    pdf_path: Path | None = None
    markdown_path: Path | None = None
    title: str = ""
    file_prefix: str = ""
    result_index: int | None = None
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ReviewOutcome:
    slides: list[SlideRecord]
    removed: int
    added: int
    pdf_path: Path | None
    markdown_path: Path | None


def build_review_targets(batch: BatchResult) -> list[ReviewTarget]:
    targets: list[ReviewTarget] = []
    if batch.output_mode == "combined":
        if not batch.combined_slides:
            return targets
        occurrences = batch.combined_occurrences or []
        slides: list[ReviewSlide] = []
        for position, record in enumerate(batch.combined_slides):
            occ = tuple(occurrences[position]) if position < len(occurrences) else ()
            source_index = occ[0][0] if occ else _result_index_for_name(batch, record.video_name)
            slides.append(
                ReviewSlide(
                    path=record.path,
                    timestamp=record.timestamp,
                    source_index=source_index,
                    occurrences=occ or ((source_index, record.timestamp),),
                )
            )
        targets.append(
            ReviewTarget(
                label="すべての動画（まとめて出力）",
                slides=slides,
                slides_dir=batch.combined_slides[0].path.parent,
                result_indices=list(range(len(batch.results))),
                combined=True,
                pdf_path=batch.combined_pdf_path,
                markdown_path=batch.combined_markdown_path,
            )
        )
        return targets

    for index, result in enumerate(batch.results):
        if not result.slides:
            continue
        first = result.slides[0].path
        match = _NAME_PATTERN.match(first.name)
        targets.append(
            ReviewTarget(
                label=result.video_path.name,
                slides=[
                    ReviewSlide(
                        path=record.path,
                        timestamp=record.timestamp,
                        source_index=index,
                        occurrences=((index, record.timestamp),),
                    )
                    for record in result.slides
                ],
                slides_dir=first.parent,
                result_indices=[index],
                combined=False,
                pdf_path=result.pdf_path,
                markdown_path=result.markdown_path,
                title=result.output_stem or result.video_path.stem,
                file_prefix=match.group("prefix") if match else "",
                result_index=index,
            )
        )
    return targets


def added_slide_name(target: ReviewTarget, batch: BatchResult, source_index: int, ts: float) -> str:
    """Return a file name in the same style as the target's existing slides."""
    time_part = format_timestamp_for_filename(ts)
    if target.combined:
        video = batch.results[source_index].video_path.stem
        return f"slide_000_{sanitize_stem(video)}_{time_part}_added.png"
    return f"{target.file_prefix}slide_000_{time_part}_added.png"


def capture_frame(
    video_path: Path,
    timestamp: float,
    slide_region: tuple[float, float, float, float] | None = None,
):
    """Read one frame (BGR ndarray) at ``timestamp`` seconds."""
    cap = cv2.VideoCapture(str(video_path))
    try:
        if not cap.isOpened():
            raise RuntimeError("動画を読み込めませんでした。")
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, timestamp) * 1000.0)
        ok, frame = cap.read()
        if not ok:
            raise RuntimeError("動画フレームを読み込めませんでした。")
    finally:
        cap.release()
    if slide_region is not None:
        frame = crop_region(frame, slide_region)
    return frame


def video_duration(video_path: Path) -> float:
    cap = cv2.VideoCapture(str(video_path))
    try:
        if not cap.isOpened():
            return 0.0
        fps = cap.get(cv2.CAP_PROP_FPS)
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        return float(frames / fps) if fps > 0 and frames > 0 else 0.0
    finally:
        cap.release()


def save_capture(frame, folder: Path, name: str) -> Path:
    path = folder / name
    write_frame_png(frame, path)
    return path


def insert_chronologically(slides: list[ReviewSlide], new: ReviewSlide) -> int:
    """Insert ``new`` after the last slide that comes earlier. Returns its position."""
    position = len(slides)
    for index, slide in enumerate(slides):
        if slide.sort_key > new.sort_key:
            position = index
            break
    slides.insert(position, new)
    return position


def apply_review(
    batch: BatchResult,
    target: ReviewTarget,
    slides: Sequence[ReviewSlide],
    remove_file: Callable[[Path], None] | None = None,
) -> ReviewOutcome:
    """Write the reviewed slide order to disk and rebuild the PDF and notes."""
    remove_file = remove_file or _unlink
    slides = list(slides)
    kept_paths = {slide.path.resolve() for slide in slides}
    removed = [slide for slide in target.slides if slide.path.resolve() not in kept_paths]
    for slide in removed:
        if slide.path.exists():
            remove_file(slide.path)

    target.slides_dir.mkdir(parents=True, exist_ok=True)
    staged: list[tuple[ReviewSlide, Path]] = []
    for position, slide in enumerate(slides):
        temporary = target.slides_dir / f"__review_{position:04d}{slide.path.suffix or '.png'}"
        if slide.path.resolve().parent == target.slides_dir.resolve():
            slide.path.replace(temporary)
        else:
            shutil.copy2(slide.path, temporary)
        staged.append((slide, temporary))

    records: list[SlideRecord] = []
    reviewed: list[ReviewSlide] = []
    for position, (slide, temporary) in enumerate(staged, start=1):
        final = target.slides_dir / renumbered_name(slide.path.name, position, target.file_prefix)
        temporary.replace(final)
        video_name = batch.results[slide.source_index].output_stem or (
            batch.results[slide.source_index].video_path.stem
        )
        records.append(
            SlideRecord(
                index=position, timestamp=slide.timestamp, path=final, video_name=video_name
            )
        )
        reviewed.append(replace(slide, path=final, added=False))

    pdf_path = target.pdf_path
    markdown_path = target.markdown_path
    if target.combined:
        batch.combined_slides = records
        batch.combined_slide_count = len(records)
        batch.combined_occurrences = [list(slide.occurrences) for slide in reviewed]
        if pdf_path is not None:
            pdf_path = _rewrite_pdf(records, pdf_path, batch.pdf_margin)
            batch.combined_pdf_path = pdf_path
        if markdown_path is not None:
            notes = combined_notes_for(batch.results, records, reviewed)
            write_combined_notes(batch.results, notes, markdown_path)
    else:
        result: ProcessResult = batch.results[target.result_index]
        result.slides = records
        if pdf_path is not None:
            pdf_path = _rewrite_pdf(records, pdf_path, batch.pdf_margin)
            result.pdf_path = pdf_path
        if markdown_path is not None:
            segments = result.transcription.segments if result.transcription else []
            write_slide_markdown(
                target.title,
                records,
                segments,
                markdown_path,
                transcripts=transcripts_for_starts([r.timestamp for r in records], segments),
            )
        if batch.combined_pdf_path is not None:
            all_slides = [slide.path for item in batch.results for slide in item.slides]
            batch.combined_pdf_path = _rewrite_pdf_paths(
                all_slides, batch.combined_pdf_path, batch.pdf_margin
            )

    target.slides = reviewed
    target.pdf_path = pdf_path
    return ReviewOutcome(
        slides=records,
        removed=len(removed),
        added=sum(1 for slide in slides if slide.added),
        pdf_path=pdf_path,
        markdown_path=markdown_path,
    )


def combined_notes_for(
    results: Sequence[ProcessResult],
    records: Sequence[SlideRecord],
    reviewed: Sequence[ReviewSlide],
) -> list[CombinedSlideNote]:
    transcripts: dict[tuple[int, int], str] = {}
    for result_index, result in enumerate(results):
        segments = result.transcription.segments if result.transcription else []
        keys = [
            (slide_position, occ_position)
            for slide_position, slide in enumerate(reviewed)
            for occ_position, (source, _start) in enumerate(slide.occurrences)
            if source == result_index
        ]
        if not keys:
            continue
        starts = [reviewed[s].occurrences[o][1] for s, o in keys]
        for key, text in zip(keys, transcripts_for_starts(starts, segments)):
            transcripts[key] = text
    notes: list[CombinedSlideNote] = []
    for slide_position, (record, slide) in enumerate(zip(records, reviewed)):
        notes.append(
            CombinedSlideNote(
                slide=record,
                occurrences=tuple(
                    CombinedSlideOccurrence(
                        video_title=results[source].video_path.stem,
                        start=start,
                        transcript=transcripts.get((slide_position, occ_position), ""),
                    )
                    for occ_position, (source, start) in enumerate(slide.occurrences)
                ),
            )
        )
    return notes


def renumbered_name(name: str, position: int, fallback_prefix: str = "") -> str:
    match = _NAME_PATTERN.match(name)
    if match:
        return f"{match.group('prefix')}slide_{position:03d}_{match.group('rest')}"
    return f"{fallback_prefix}slide_{position:03d}_{name}"


def _rewrite_pdf(records: Sequence[SlideRecord], pdf_path: Path, margin: float) -> Path | None:
    return _rewrite_pdf_paths([record.path for record in records], pdf_path, margin)


def _rewrite_pdf_paths(paths: Sequence[Path], pdf_path: Path, margin: float) -> Path | None:
    if not paths:
        if pdf_path.exists():
            pdf_path.unlink()
        return None
    write_pdf(paths, pdf_path, margin)
    return pdf_path


def _result_index_for_name(batch: BatchResult, video_name: str) -> int:
    for index, result in enumerate(batch.results):
        if video_name in {result.output_stem, result.video_path.stem}:
            return index
    return 0


def _unlink(path: Path) -> None:
    path.unlink(missing_ok=True)
