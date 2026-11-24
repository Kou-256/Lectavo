"""OCR for scanned pages, using the RapidOCR models bundled with the app."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import Lock

from movie_slide_notes.pdf_translate.layout import join_lines, should_translate
from movie_slide_notes.pdf_translate.models import TextBlock

# 300 dpi keeps small print readable; very large pages are capped below.
OCR_SCALE = 300 / 72
MAX_OCR_SIDE = 4200
MIN_SCORE = 0.5

_engine = None
_engine_lock = Lock()


@dataclass
class OcrLine:
    left: float
    top: float
    right: float
    bottom: float
    text: str

    @property
    def height(self) -> float:
        return self.bottom - self.top


def _get_engine():
    global _engine
    with _engine_lock:
        if _engine is None:
            from rapidocr import RapidOCR

            # RapidOCR configures its own logger on import; keep it to warnings.
            for name in ("RapidOCR", "rapidocr"):
                logging.getLogger(name).setLevel(logging.WARNING)
            _engine = RapidOCR()
        return _engine


def ocr_scale(page_width: float, page_height: float) -> float:
    return min(OCR_SCALE, MAX_OCR_SIDE / max(page_width, page_height, 1.0))


def recognize_lines(image) -> list[OcrLine]:
    """Text lines in a page image, in pixel coordinates (origin top-left)."""
    import numpy as np

    result = _get_engine()(np.asarray(image))
    if result is None or result.boxes is None or result.txts is None:
        return []
    lines = []
    for box, text, score in zip(result.boxes, result.txts, result.scores, strict=False):
        if score < MIN_SCORE or not text.strip():
            continue
        xs = [float(point[0]) for point in box]
        ys = [float(point[1]) for point in box]
        lines.append(OcrLine(min(xs), min(ys), max(xs), max(ys), text.strip()))
    return lines


def group_paragraphs(lines: list[OcrLine]) -> list[list[OcrLine]]:
    """Group lines into paragraphs: same column, similar size, small vertical gap."""
    paragraphs: list[list[OcrLine]] = []
    for line in sorted(lines, key=lambda item: (item.top, item.left)):
        target = None
        for paragraph in reversed(paragraphs):
            last = paragraph[-1]
            gap = line.top - last.bottom
            overlap = min(line.right, last.right) - max(line.left, last.left)
            narrower = max(1.0, min(line.right - line.left, last.right - last.left))
            similar = 0.8 <= line.height / max(1.0, last.height) <= 1.25
            heading_above = (
                len(paragraph) == 1
                and (last.right - last.left) < 0.6 * (line.right - line.left)
                and not last.text.rstrip().endswith(("-", ","))
            )
            if (
                -0.3 * last.height <= gap <= 0.9 * last.height
                and overlap > 0.5 * narrower
                and similar
                and not heading_above
            ):
                target = paragraph
                break
        if target is None:
            paragraphs.append([line])
        else:
            target.append(line)
    return paragraphs


def ocr_blocks(
    image, scale: float, page_index: int, page_width: float, page_height: float
) -> list[TextBlock]:
    """Translatable paragraphs found by OCR, converted to PDF points."""
    blocks = []
    for paragraph in group_paragraphs(recognize_lines(image)):
        text = join_lines([line.text for line in paragraph])
        if not should_translate(text):
            continue
        left = min(line.left for line in paragraph) / scale
        right = max(line.right for line in paragraph) / scale
        top = min(line.top for line in paragraph) / scale
        bottom = max(line.bottom for line in paragraph) / scale
        heights = sorted(line.height for line in paragraph)
        line_height = heights[len(heights) // 2] / scale
        center = (left + right) / 2
        blocks.append(
            TextBlock(
                page_index=page_index,
                x0=left,
                y0=page_height - bottom,
                x1=right,
                y1=page_height - top,
                text=text,
                font_size=max(5.0, line_height * 0.78),
                centered=len(paragraph) == 1
                and abs(center - page_width / 2) < page_width * 0.03
                and right - left < page_width * 0.8,
                source="ocr",
            )
        )
    return blocks
