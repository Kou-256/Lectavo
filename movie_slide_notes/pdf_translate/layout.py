"""Reading page structure: normalized pages, text blocks, and rendered images."""

from __future__ import annotations

import io
import re
from collections import Counter
from pathlib import Path
from statistics import median

from movie_slide_notes.pdf_translate.models import PageInfo, TextBlock

SERIF_HINTS = (
    "times",
    "serif",
    "roman",
    "minion",
    "garamond",
    "georgia",
    "cambria",
    "palatino",
    "cmr",
    "nimbusrom",
    "stix",
    "mincho",
    "book antiqua",
)
BOLD_HINTS = ("bold", "black", "heavy", "semibold", "demi", ",b")

_WORD = re.compile(r"[^\W\d_]{2,}")
_CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
_URL_ONLY = re.compile(r"^\s*(https?://|www\.|doi:|10\.\d{4,}/)\S*\s*$", re.IGNORECASE)


def normalize_pdf(source: Path) -> bytes:
    """Return the PDF with every page upright and its visible area starting at (0, 0).

    Rotated pages and pages whose crop box does not start at the origin are
    rewritten so the text layer, the renderer and the writer all share one
    coordinate system. Other pages are copied unchanged.
    """
    from pypdf import PdfReader, PdfWriter, Transformation
    from pypdf.generic import RectangleObject

    reader = PdfReader(str(source))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:  # pragma: no cover - depends on the file
            raise ValueError("パスワード付きのPDFは翻訳できません。") from exc
    writer = PdfWriter(clone_from=reader)
    for page in writer.pages:
        if int(page.get("/Rotate", 0) or 0) % 360:
            page.transfer_rotation_to_content()
        box = page.cropbox
        left, bottom = float(box.left), float(box.bottom)
        width, height = float(box.width), float(box.height)
        if abs(left) > 0.01 or abs(bottom) > 0.01 or page.mediabox != box:
            page.add_transformation(Transformation().translate(-left, -bottom))
            rect = RectangleObject([0, 0, width, height])
            page.mediabox = rect
            page.cropbox = rect
            for name in ("/TrimBox", "/BleedBox", "/ArtBox"):
                if name in page:
                    del page[name]
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def page_sizes(pdf_bytes: bytes) -> list[tuple[float, float]]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    return [(float(p.mediabox.width), float(p.mediabox.height)) for p in reader.pages]


def join_lines(lines: list[str]) -> str:
    """Join wrapped lines into one paragraph, undoing end-of-line hyphenation."""
    text = ""
    for raw in lines:
        line = " ".join(raw.split())
        if not line:
            continue
        if not text:
            text = line
        elif text.endswith("-") and line[:1].islower() and len(text) > 1 and text[-2].isalpha():
            text = text[:-1] + line
        else:
            text = f"{text} {line}"
    return text


def should_translate(text: str) -> bool:
    """True for prose and headings in any language; False for formulas, numbers and URLs.

    Text that is already in the target language is filtered later, by the pipeline.
    """
    stripped = text.strip()
    if not stripped or _URL_ONLY.match(stripped):
        return False
    letters = sum(1 for char in stripped if char.isalpha())
    visible = len(re.sub(r"\s", "", stripped))
    if letters < 2 or letters / max(1, visible) < 0.5:
        return False  # mostly symbols and digits: equations, tables of numbers
    ideographic = len(_CJK.findall(stripped))
    if ideographic >= 2:
        return True
    words = _WORD.findall(stripped)
    if not words:
        return False
    if len(words) == 1:
        # A single word only counts as a heading when it is a real word.
        return len(words[0]) >= 4 and letters / max(1, visible) >= 0.6
    return True


def _font_flags(fontnames: list[str]) -> tuple[bool, bool]:
    if not fontnames:
        return False, False
    name = Counter(fontnames).most_common(1)[0][0].lower()
    name = name.split("+", 1)[-1]
    bold = any(hint in name for hint in BOLD_HINTS)
    serif = any(hint in name for hint in SERIF_HINTS) and "sans" not in name
    return bold, serif


def _rgb(color) -> tuple[float, float, float] | None:
    try:
        if color is None:
            return None
        if isinstance(color, (int, float)):
            value = float(color)
            return (value, value, value)
        values = [float(v) for v in color]
    except (TypeError, ValueError):
        return None
    if len(values) == 1:
        return (values[0],) * 3
    if len(values) == 3:
        return tuple(max(0.0, min(1.0, v)) for v in values)  # type: ignore[return-value]
    if len(values) == 4:
        c, m, y, k = values
        return ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    return None


def _is_centered(lines, page_width: float) -> bool:
    if len(lines) >= 2:
        centers = [(line.x0 + line.x1) / 2 for line in lines]
        lefts = [line.x0 for line in lines]
        spread = max(centers) - min(centers)
        return spread < 3 and (max(lefts) - min(lefts)) > 6
    line = lines[0]
    center = (line.x0 + line.x1) / 2
    return abs(center - page_width / 2) < page_width * 0.03 and line.width < page_width * 0.8


def _line_size(line) -> float:
    sizes = [c.size for c in line if hasattr(c, "size") and getattr(c, "fontname", None)]
    return median(sizes) if sizes else 0.0


def split_by_size(lines: list) -> list[list]:
    """Split a text box where the font size jumps, e.g. a title above its subtitle."""
    groups: list[list] = []
    previous = None
    for line in lines:
        size = _line_size(line)
        if groups and previous and size and not (0.8 <= size / previous <= 1.25):
            groups.append([line])
        elif groups:
            groups[-1].append(line)
        else:
            groups.append([line])
        previous = size or previous
    return groups


def _block_from_lines(index: int, lines: list, page_width: float) -> TextBlock | None:
    from pdfminer.layout import LTChar

    chars = [c for line in lines for c in line if isinstance(c, LTChar)]
    if not chars:
        return None
    text = join_lines([line.get_text() for line in lines])
    if not should_translate(text):
        return None
    sizes = [c.size for c in chars if c.size > 0]
    bold, serif = _font_flags([c.fontname for c in chars])
    colors = [_rgb(getattr(c.graphicstate, "ncolor", None)) for c in chars]
    colors = [c for c in colors if c is not None]
    color = Counter(colors).most_common(1)[0][0] if colors else (0.0, 0.0, 0.0)
    return TextBlock(
        page_index=index,
        x0=min(line.x0 for line in lines),
        y0=min(line.y0 for line in lines),
        x1=max(line.x1 for line in lines),
        y1=max(line.y1 for line in lines),
        text=text,
        font_size=median(sizes) if sizes else 10.0,
        bold=bold,
        serif=serif,
        color=color,
        centered=_is_centered(lines, page_width),
    )


def extract_text_pages(
    pdf_bytes: bytes, page_indexes: list[int]
) -> dict[int, tuple[list[TextBlock], int, float]]:
    """Text blocks per page, with the page's character count and image coverage.

    Text inside figures is not grouped into blocks, so figure labels stay as they are.
    """
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LAParams, LTChar, LTFigure, LTImage, LTTextBox

    params = LAParams(line_margin=0.5, char_margin=2.0, word_margin=0.1, boxes_flow=0.5)
    result: dict[int, tuple[list[TextBlock], int, float]] = {}
    for index, layout in zip(
        page_indexes,
        extract_pages(io.BytesIO(pdf_bytes), page_numbers=page_indexes, laparams=params),
        strict=False,
    ):
        page_width = layout.width
        page_area = max(1.0, layout.width * layout.height)
        blocks: list[TextBlock] = []
        char_count = 0
        image_area = 0.0

        def walk(item, inside_figure: bool = False):
            nonlocal char_count, image_area
            if isinstance(item, LTImage):
                image_area += item.width * item.height
                return
            if isinstance(item, LTFigure):
                if item.width * item.height > 0:
                    has_image = any(isinstance(child, LTImage) for child in item)
                    if has_image:
                        image_area += item.width * item.height
                for child in item:
                    walk(child, True)
                return
            if isinstance(item, LTChar):
                char_count += 1
                return
            if isinstance(item, LTTextBox) and not inside_figure:
                lines = [line for line in item if any(isinstance(c, LTChar) for c in line)]
                char_count += sum(1 for line in lines for c in line if isinstance(c, LTChar))
                for group in split_by_size(lines):
                    block = _block_from_lines(index, group, page_width)
                    if block is not None:
                        blocks.append(block)
                return
            if hasattr(item, "__iter__"):
                for child in item:
                    walk(child, inside_figure)

        for element in layout:
            walk(element)
        result[index] = (blocks, char_count, min(1.0, image_area / page_area))
    return result


def is_scanned(char_count: int, image_coverage: float) -> bool:
    return char_count < 30 and image_coverage > 0.4


class PageRenderer:
    """Renders pages of one PDF to PIL images (``scale`` = pixels per point)."""

    def __init__(self, pdf_bytes: bytes):
        import pypdfium2 as pdfium

        self._document = pdfium.PdfDocument(pdf_bytes)

    def render(self, index: int, scale: float):
        page = self._document[index]
        try:
            return page.render(scale=scale).to_pil().convert("RGB")
        finally:
            page.close()

    def close(self) -> None:
        self._document.close()

    def __enter__(self) -> PageRenderer:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def render_page(pdf_bytes: bytes, index: int, scale: float):
    with PageRenderer(pdf_bytes) as renderer:
        return renderer.render(index, scale)


def sample_colors(image, scale: float, page_height: float, block: TextBlock):
    """Background and text color under ``block`` from a rendered page image."""
    import numpy as np

    x0 = max(0, int(block.x0 * scale))
    x1 = min(image.width, int(block.x1 * scale) + 1)
    top = max(0, int((page_height - block.y1) * scale))
    bottom = min(image.height, int((page_height - block.y0) * scale) + 1)
    if x1 - x0 < 2 or bottom - top < 2:
        return (1.0, 1.0, 1.0), block.color
    region = np.asarray(image.crop((x0, top, x1, bottom))).reshape(-1, 3).astype(np.int32)
    quantized = region // 16
    keys, inverse, counts = np.unique(quantized, axis=0, return_inverse=True, return_counts=True)
    dominant = int(counts.argmax())
    # Average the real pixels of the most common color, so white stays pure white.
    background = np.median(region[inverse.reshape(-1) == dominant], axis=0)
    if block.source != "ocr":
        return tuple(float(v) / 255 for v in background), block.color
    distance = np.abs(region - background).sum(axis=1)
    ink = region[distance > 120]
    if len(ink) >= 4:
        # The darkest third avoids the anti-aliased edge pixels of the glyphs.
        ink = ink[np.argsort(np.abs(ink - background).sum(axis=1))[-max(1, len(ink) // 3) :]]
        text = tuple(float(v) / 255 for v in np.median(ink, axis=0))
    else:
        text = (0.0, 0.0, 0.0)
    return tuple(float(v) / 255 for v in background), text


def page_list(total: int, first: int, last: int) -> list[int]:
    first = max(1, first)
    last = total if last <= 0 else min(total, last)
    if first > last:
        raise ValueError("ページ範囲が正しくありません。")
    return list(range(first - 1, last))


def build_pages(sizes: list[tuple[float, float]], indexes: list[int]) -> list[PageInfo]:
    return [PageInfo(index=i, width=sizes[i][0], height=sizes[i][1]) for i in indexes]
