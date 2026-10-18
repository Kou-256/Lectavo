from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SlideTranscript:
    slide: object
    start: float
    end: float | None
    transcript: str


@dataclass(frozen=True)
class CombinedSlideOccurrence:
    video_title: str
    start: float
    transcript: str


@dataclass(frozen=True)
class CombinedSlideNote:
    slide: object
    occurrences: tuple[CombinedSlideOccurrence, ...]


def align_segments_to_slides(
    slides: Sequence[object], segments: Sequence[object]
) -> list[SlideTranscript]:
    aligned: list[SlideTranscript] = []
    for index, slide in enumerate(slides):
        start = float(getattr(slide, "timestamp"))
        end = float(getattr(slides[index + 1], "timestamp")) if index + 1 < len(slides) else None
        texts: list[str] = []
        for segment in segments:
            segment_start = float(getattr(segment, "start"))
            segment_end = float(getattr(segment, "end"))
            overlaps_slide = segment_end > start and (end is None or segment_start < end)
            if overlaps_slide:
                text = str(getattr(segment, "text")).strip()
                if text:
                    texts.append(text)
        aligned.append(
            SlideTranscript(
                slide=slide,
                start=start,
                end=end,
                transcript="\n".join(texts),
            )
        )
    return aligned


def write_slide_markdown(
    video_title: str,
    slides: Sequence[object],
    segments: Sequence[object],
    output_path: Path,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    aligned = align_segments_to_slides(slides, segments)

    lines: list[str] = [f"# {video_title}", ""]
    if not aligned:
        lines.extend(["スライドは検出されませんでした。", ""])

    for item in aligned:
        slide = item.slide
        slide_index = int(getattr(slide, "index"))
        timestamp = format_timestamp(item.start)
        slide_path = Path(getattr(slide, "path"))
        relative_path = os.path.relpath(slide_path, output_path.parent).replace(os.sep, "/")

        lines.extend(
            [
                f"## Slide {slide_index}",
                "",
                f"![Slide {slide_index}](<{relative_path}>)",
                "",
                f"**表示時刻:** {timestamp}",
                "",
                "### Transcript",
                "",
            ]
        )
        lines.append(
            item.transcript if item.transcript else "_このスライド区間の文字起こしはありません。_"
        )
        summary = summarize_slide_transcript(item.transcript)
        if summary:
            lines.extend(["", "### Summary", "", summary])
        lines.extend(["", "---", ""])

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def write_combined_slide_markdown(
    title: str,
    notes: Sequence[CombinedSlideNote],
    output_path: Path,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [f"# {title}", ""]
    if not notes:
        lines.extend(["スライドは検出されませんでした。", ""])

    for index, note in enumerate(notes, start=1):
        slide_path = Path(getattr(note.slide, "path"))
        relative_path = os.path.relpath(slide_path, output_path.parent).replace(os.sep, "/")
        lines.extend(
            [
                f"## Slide {index}",
                "",
                f"![Slide {index}](<{relative_path}>)",
                "",
                "### Transcript",
                "",
            ]
        )
        if not note.occurrences:
            lines.append("_このスライド区間の文字起こしはありません。_")
        for occurrence in note.occurrences:
            lines.extend(
                [
                    f"#### {occurrence.video_title} - {format_timestamp(occurrence.start)}",
                    "",
                    occurrence.transcript or "_この表示区間の文字起こしはありません。_",
                    "",
                ]
            )
        lines.extend(["---", ""])

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def summarize_slide_transcript(transcript: str) -> str:
    """Hook for a future LLM summary step. It intentionally works without an API key."""
    del transcript
    return ""


def format_timestamp(seconds: float) -> str:
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"
