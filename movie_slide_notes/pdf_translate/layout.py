"""Reading page structure: normalized pages, text blocks, and rendered images."""

from __future__ import annotations

import io
import re
from collections import Counter
from pathlib import Path
from statistics import median
from threading import RLock

from movie_slide_notes.pdf_translate.markup import strip_tags
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
_URL = re.compile(r"https?://|www\.|doi\.org|\bdoi:", re.IGNORECASE)
_REF_NUMBER = re.compile(r"^\s*(\[\d{1,3}\]|\(\d{1,3}\)|\d{1,3}\))")
_INITIAL = re.compile(r"(?<![A-Za-z])[A-Z]\.(?:-[A-Z]\.)?(?=[\s,])")
_YEAR = re.compile(r"\((?:19|20)\d{2}[a-z]?\)|\b(?:19|20)\d{2}[a-z]?[.;,)]")
# "Nat. Mater. 2, 180 (2003)": volume, page and year.
_CITATION = re.compile(r"\b\d{1,4},\s*[A-Z]?\d{1,8}\s*\((?:19|20)\d{2}\)")
_CAMEL = re.compile(r"[a-z][A-Z]")
# PDFium must not be called from two threads at once; several PDFs can be
# translated side by side, so every call goes through this lock.
PDFIUM_LOCK = RLock()
# A figure covering most of the page is a whole page wrapped in a form, not an illustration.
PAGE_WRAPPER_COVERAGE = 0.6
# Fonts used for formulas (TeX, STIX, Euclid, MathType, Cambria Math...).
MATH_FONT_HINTS = (
    "symbol",
    "stix",
    "math",
    "cmmi",
    "cmsy",
    "cmex",
    "cmbsy",
    "euclid",
    "mtextra",
    "mt-extra",
    "mtsy",
    "mtmi",
    "esstix",
    "msam",
    "msbm",
    "rsfs",
    "eufm",
    "eusm",
    "txsy",
    "txmi",
    "pxsy",
    "pxmi",
    "xits",
)
ITALIC_HINTS = ("italic", "oblique", "-it", ",it", "cmti", "cmmi")
_RELATION = re.compile(r"[=<>≤≥≈≠∝∑∏∫√±∓×÷]")
_EQUATION_NUMBER = re.compile(r"^\(\s*\d{1,3}\s*[a-z]?\s*\)$")


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
        # A single word only counts as a heading when it is a real word, not an identifier.
        return (
            len(words[0]) >= 4 and letters / max(1, visible) >= 0.6 and not _CAMEL.search(stripped)
        )
    return True


def looks_like_reference(text: str) -> bool:
    """True for bibliography entries, which are left in the original language.

    Entries such as ``12) J. Wang, Y. Yoo, and C. Gao, Science 279, 1712 (1998).``
    are lists of author initials, journals, volumes and years.
    """
    initials = len(_INITIAL.findall(text))
    years = len(_YEAR.findall(text))
    numbered = bool(_REF_NUMBER.match(text))
    if numbered and _URL.search(text):
        return True
    words = max(1, len(text.split()))
    if initials and _CITATION.search(text):
        return True
    if years and initials >= 2 and initials / words >= 0.08:
        return True
    return numbered and initials >= 3


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


def _font_base(fontname: str) -> str:
    return (fontname or "").split("+", 1)[-1].lower()


def is_math_line(line) -> bool:
    """True for a line of a displayed formula, which is left as it is.

    A line is a formula when most of its characters use math fonts or are
    single italic letters (variables), or when it has a relation sign such as
    "=" but hardly any ordinary words.
    """
    from pdfminer.layout import LTChar

    tokens: list[list] = [[]]
    for item in line:
        if isinstance(item, LTChar) and item.get_text().strip():
            tokens[-1].append(item)
        elif tokens[-1]:
            tokens.append([])
    tokens = [token for token in tokens if token]
    total = sum(len(token) for token in tokens)
    if not total:
        return False
    text = "".join(c.get_text() for token in tokens for c in token)
    if _EQUATION_NUMBER.match(text):
        return True
    math = 0
    words = 0
    for token in tokens:
        fonts = [_font_base(c.fontname) for c in token]
        math_font = [any(hint in font for hint in MATH_FONT_HINTS) for font in fonts]
        letters = sum(1 for c in token if c.get_text().isalpha())
        italic = all(any(hint in font for hint in ITALIC_HINTS) for font in fonts)
        if italic and letters <= 2:
            math += len(token)
            continue
        math += sum(math_font)
        plain = "".join(c.get_text() for c, m in zip(token, math_font, strict=True) if not m)
        words += len(re.findall(r"[^\W\d_]{3,}", plain))
    if math / total >= 0.5:
        return True
    return bool(_RELATION.search(text)) and words < 2


def split_math(lines: list) -> list[list]:
    """Split a text box around formula lines, dropping the formulas themselves."""
    groups: list[list] = [[]]
    for line in lines:
        if is_math_line(line):
            if groups[-1]:
                groups.append([])
        else:
            groups[-1].append(line)
    return [group for group in groups if group]


def _main_font(lines: list) -> str:
    from pdfminer.layout import LTChar

    fonts = Counter(_font_base(c.fontname) for line in lines for c in line if isinstance(c, LTChar))
    return fonts.most_common(1)[0][0] if fonts else ""


def merge_orphans(boxes: list[list]) -> list[list]:
    """Join a one-line box to the paragraph right above or below it.

    pdfminer sometimes cuts the first or last line of a paragraph into its own
    box (for example next to a formula). Translated alone, that line has to be
    squeezed into a single line of the page.
    """
    merged = [list(lines) for lines in boxes]
    changed = True
    while changed:
        changed = False
        for i, upper in enumerate(merged):
            for j, lower in enumerate(merged):
                if i == j or (len(upper) > 1 and len(lower) > 1):
                    continue
                ux0, uy0, ux1, _uy1 = _bbox(upper)
                lx0, _ly0, lx1, ly1 = _bbox(lower)
                height = median(line.y1 - line.y0 for line in upper + lower)
                gap = uy0 - ly1
                overlap = min(ux1, lx1) - max(ux0, lx0)
                narrower = max(1e-6, min(ux1 - ux0, lx1 - lx0))
                if not (-0.5 * height <= gap <= 0.6 * height):
                    continue
                if overlap < 0.5 * narrower or abs(ux0 - lx0) > 2.5 * height:
                    continue
                sizes = (
                    median(_line_size(line) for line in upper),
                    median(_line_size(line) for line in lower),
                )
                if min(sizes) <= 0 or not (0.85 <= sizes[0] / sizes[1] <= 1.18):
                    continue
                if _main_font(upper) != _main_font(lower):
                    continue
                upper.extend(lower)
                del merged[j]
                changed = True
                break
            if changed:
                break
    return merged


# Adobe Symbol encoding: fonts such as Symbol or Euclid Symbol without a Unicode
# map are read as Latin letters ("m" for μ, "s" for σ).
_SYMBOL_GREEK = dict(
    zip(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
        "αβχδεφγηιϕκλμνοπθρστυϖωξψζΑΒΧΔΕΦΓΗΙϑΚΛΜΝΟΠΘΡΣΤΥςΩΞΨΖ",
        strict=True,
    )
)
# A character this much smaller than the line's text, and off its baseline, is a script.
SCRIPT_SIZE = 0.85


def _char_text(char) -> str:
    text = char.get_text()
    font = _font_base(char.fontname)
    if len(text) == 1 and text in _SYMBOL_GREEK and "symbol" in font and "seg" not in font:
        return _SYMBOL_GREEK[text]
    return text


def line_markup(line) -> str:
    """The line's text in left-to-right order, with sub- and superscripts tagged.

    ``Eu2O3`` set with small lowered digits becomes ``Eu<sub>2</sub>O<sub>3</sub>``;
    a raised citation ``process.22)`` becomes ``process.<sup>22)</sup>``.
    """
    from pdfminer.layout import LTChar

    # Where pdfminer put a space (in its own order), to keep word breaks.
    spaced: set[int] = set()
    pending_space = False
    for item in line:
        if isinstance(item, LTChar) and item.get_text().strip():
            if pending_space:
                spaced.add(id(item))
            pending_space = False
        elif item.get_text().isspace() and item.get_text() != "\n":
            pending_space = True
    chars = sorted(
        (c for c in line if isinstance(c, LTChar) and c.get_text().strip()),
        key=lambda c: c.x0,
    )
    if not chars:
        return line.get_text()
    sizes = Counter(round(c.size, 1) for c in chars)
    main = max(size for size, count in sizes.items() if count == max(sizes.values()))
    # The box bottom sits a font-specific fraction of the size below the baseline;
    # learn it from the line's regular characters. (Text rise is not in ``matrix``.)
    regular = [c for c in chars if abs(c.size - main) < 0.3] or chars
    descent = median((c.y0 - c.matrix[5]) / max(c.size, 1e-6) for c in regular)
    base = median(c.y0 - descent * c.size for c in regular)
    origin = median(c.matrix[5] for c in regular)

    def style(char) -> str:
        if char.size > main * SCRIPT_SIZE:
            return ""
        # Scripts placed by moving the text position show in ``matrix``; those
        # raised with text rise only in the box (whose depth depends on the font).
        offset = char.matrix[5] - origin
        if abs(offset) < 0.05 * main:
            offset = char.y0 - descent * char.size - base
        if offset > 0.15 * main:
            return "sup"
        if offset < -0.05 * main:
            return "sub"
        return ""

    text = ""
    current = ""
    previous = None
    for char in chars:
        kind = style(char)
        if previous is not None:
            gap = char.x0 - previous.x1
            size = min(previous.size, char.size)
            if gap > 0.3 * size or (id(char) in spaced and gap > 0.03 * size):
                if current:
                    text += f"</{current}>"
                    current = ""
                text += " "
        if kind != current:
            if current:
                text += f"</{current}>"
            if kind:
                text += f"<{kind}>"
            current = kind
        text += _char_text(char)
        previous = char
    if current:
        text += f"</{current}>"
    # Math fonts space parentheses widely: "μ (x )" -> "μ(x)".
    text = re.sub(r"(?<![\w>])([^\W\d_]) \((?=\S{1,3} ?\))", r"\1(", text)
    return re.sub(r"\((\S{1,3}) \)", r"(\1)", text)


def reading_order(lines: list) -> list:
    """Top to bottom; pieces of one line (pdfminer splits some) left to right."""
    return sorted(lines, key=lambda line: (-round((line.y0 + line.y1) / 2 / 3), line.x0))


def _block_from_lines(index: int, lines: list, page_width: float) -> TextBlock | None:
    from pdfminer.layout import LTChar

    chars = [c for line in lines for c in line if isinstance(c, LTChar)]
    if not chars:
        return None
    text = join_lines([line_markup(line) for line in reading_order(lines)])
    plain = strip_tags(text)
    if not should_translate(plain) or looks_like_reference(plain):
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


def _bbox(lines: list) -> tuple[float, float, float, float]:
    return (
        min(line.x0 for line in lines),
        min(line.y0 for line in lines),
        max(line.x1 for line in lines),
        max(line.y1 for line in lines),
    )


def merge_contained(boxes: list[list]) -> list[list]:
    """Fold a text box that lies inside another one back into it, in reading order.

    pdfminer sometimes splits off part of a line (e.g. "time. Furthermore,") into
    its own box inside a paragraph; translated separately, it would be drawn on top
    of the paragraph and be missing from it.
    """
    merged = [list(lines) for lines in boxes]
    changed = True
    while changed:
        changed = False
        for i, inner in enumerate(merged):
            ix0, iy0, ix1, iy1 = _bbox(inner)
            area = max(1e-6, (ix1 - ix0) * (iy1 - iy0))
            for j, outer in enumerate(merged):
                if i == j:
                    continue
                ox0, oy0, ox1, oy1 = _bbox(outer)
                overlap_w = min(ix1, ox1) - max(ix0, ox0)
                overlap_h = min(iy1, oy1) - max(iy0, oy0)
                if overlap_w <= 0 or overlap_h <= 0 or (ox1 - ox0) * (oy1 - oy0) < area:
                    continue
                if overlap_w * overlap_h / area >= 0.8:
                    outer.extend(inner)
                    # Top to bottom, then left to right within a line.
                    outer.sort(key=lambda line: (-round((line.y0 + line.y1) / 2 / 3), line.x0))
                    del merged[i]
                    changed = True
                    break
            if changed:
                break
    return merged


def extract_text_pages(
    pdf_bytes: bytes, page_indexes: list[int]
) -> dict[int, tuple[list[TextBlock], int, float]]:
    """Text blocks per page, with the page's character count and image coverage.

    Text inside figures is not grouped into blocks, so figure labels stay as they are.
    """
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LAParams, LTChar, LTFigure, LTImage, LTTextBox

    # all_texts also groups text inside forms, for pages that are wrapped in one.
    params = LAParams(
        line_margin=0.5, char_margin=2.0, word_margin=0.1, boxes_flow=0.5, all_texts=True
    )
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
                if item.width * item.height >= PAGE_WRAPPER_COVERAGE * page_area:
                    for child in item:
                        walk(child, inside_figure)
                    return
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
                # Formulas stay in the original; the prose around them is translated.
                boxes.extend(split_math(lines))
                return
            if hasattr(item, "__iter__"):
                for child in item:
                    walk(child, inside_figure)

        boxes: list[list] = []
        for element in layout:
            walk(element)
        for lines in merge_orphans(merge_contained(boxes)):
            for group in split_by_size(lines):
                block = _block_from_lines(index, group, page_width)
                if block is not None:
                    blocks.append(block)
        result[index] = (blocks, char_count, min(1.0, image_area / page_area))
    return result


def is_scanned(char_count: int, image_coverage: float) -> bool:
    return char_count < 30 and image_coverage > 0.4


class PageRenderer:
    """Renders pages of one PDF to PIL images (``scale`` = pixels per point)."""

    def __init__(self, pdf_bytes: bytes):
        import pypdfium2 as pdfium

        with PDFIUM_LOCK:
            self._document = pdfium.PdfDocument(pdf_bytes)

    def render(self, index: int, scale: float):
        with PDFIUM_LOCK:
            page = self._document[index]
            try:
                return page.render(scale=scale).to_pil().convert("RGB")
            finally:
                page.close()

    def close(self) -> None:
        with PDFIUM_LOCK:
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
