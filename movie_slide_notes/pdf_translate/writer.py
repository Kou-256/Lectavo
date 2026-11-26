"""Writing translated text back onto the original pages."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from movie_slide_notes.pdf_translate.markup import SCRIPT, strip_tags
from movie_slide_notes.pdf_translate.models import PageInfo, TextBlock

FONTS_DIR = Path(r"C:\Windows\Fonts")
# (registered name, candidates in order of preference). All are TrueType-outline
# fonts that ship with Windows 10/11, so nothing extra is bundled.
FONT_CANDIDATES = {
    "LectavoGothic": ["BIZ-UDGothicR.ttc", "YuGothR.ttc", "meiryo.ttc", "msgothic.ttc"],
    "LectavoGothicBold": ["BIZ-UDGothicB.ttc", "YuGothB.ttc", "meiryob.ttc", "msgothic.ttc"],
    "LectavoMincho": ["BIZ-UDMinchoM.ttc", "yumin.ttf", "msmincho.ttc"],
    "LectavoHans": ["msyh.ttc", "simsun.ttc"],
    "LectavoHansBold": ["msyhbd.ttc", "msyh.ttc"],
    "LectavoHansSerif": ["simsun.ttc", "msyh.ttc"],
    "LectavoHant": ["msjh.ttc", "mingliub.ttc"],
    "LectavoHantBold": ["msjhbd.ttc", "msjh.ttc"],
    "LectavoHantSerif": ["mingliub.ttc", "msjh.ttc"],
    "LectavoKorean": ["malgun.ttf"],
    "LectavoKoreanBold": ["malgunbd.ttf", "malgun.ttf"],
    "LectavoKoreanSerif": ["malgun.ttf"],
    "LectavoSans": ["segoeui.ttf", "arial.ttf"],
    "LectavoSansBold": ["segoeuib.ttf", "arialbd.ttf"],
    "LectavoSerif": ["times.ttf", "georgia.ttf"],
    "LectavoSymbols": ["seguisym.ttf", "cambria.ttc", "arial.ttf"],
}
# Tried in order for characters the chosen font lacks (e.g. superscripts like ⁺).
FALLBACK_FONTS = ("LectavoSans", "LectavoSymbols", "LectavoGothic", "LectavoHans", "LectavoKorean")
# Font families per script: (regular, bold, serif).
SCRIPT_FONTS = {
    "ja": ("LectavoGothic", "LectavoGothicBold", "LectavoMincho"),
    "zh-hans": ("LectavoHans", "LectavoHansBold", "LectavoHansSerif"),
    "zh-hant": ("LectavoHant", "LectavoHantBold", "LectavoHantSerif"),
    "ko": ("LectavoKorean", "LectavoKoreanBold", "LectavoKoreanSerif"),
    "latin": ("LectavoSans", "LectavoSansBold", "LectavoSerif"),
}
FALLBACK_CID_FONT = "HeiseiKakuGo-W5"

LEADING = 1.3
# Sub- and superscripts: size relative to the text, and baseline shift.
SCRIPT_SCALE = 0.7
SUPERSCRIPT_RISE = 0.36
SUBSCRIPT_DROP = 0.14
# Each tagged span is carried through wrapping as one private-use character.
_SPAN_BASE = 0xF0000
MIN_LEADING = 1.12
MIN_FONT_SIZE = 4.5
BILINGUAL_GAP = 0.0

# Characters that must not start a line (Japanese line-breaking rules).
NO_LINE_START = set(
    "、。，．,.・：；？！!?)）]］｝〕〉》」』】ー‐–—…‥ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ々%％"
)
NO_LINE_END = set("(（[［｛〔〈《「『【")
# One CJK character (kana, Han, full-width punctuation) per token; any other run of
# non-space characters (Latin, Cyrillic, Hangul words, numbers) stays whole.
_CJK_CHAR = r"　-ヿ㐀-鿿豈-﫿＀-￯"
_TOKEN = re.compile(rf"[{_CJK_CHAR}]|[^\s{_CJK_CHAR}]+|\s+", re.DOTALL)


@cache
def font_name(kind: str) -> str:
    """Register (once) and return a Japanese font for ``kind``."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont

    for candidate in FONT_CANDIDATES.get(kind, FONT_CANDIDATES["LectavoGothic"]):
        path = FONTS_DIR / candidate
        if not path.exists():
            continue
        try:
            if path.suffix.lower() == ".ttc":
                pdfmetrics.registerFont(TTFont(kind, str(path), subfontIndex=0))
            else:
                pdfmetrics.registerFont(TTFont(kind, str(path)))
            return kind
        except Exception:
            continue
    if kind != "LectavoGothic":
        return font_name("LectavoGothic")
    pdfmetrics.registerFont(UnicodeCIDFont(FALLBACK_CID_FONT))
    return FALLBACK_CID_FONT


def block_font(block: TextBlock, script: str = "ja") -> str:
    regular, bold, serif = SCRIPT_FONTS.get(script, SCRIPT_FONTS["ja"])
    if block.bold:
        return font_name(bold)
    if block.serif:
        return font_name(serif)
    return font_name(regular)


def _has_glyph(font: str, char: str) -> bool:
    from reportlab.pdfbase import pdfmetrics

    face = getattr(pdfmetrics.getFont(font), "face", None)
    glyphs = getattr(face, "charToGlyph", None)
    return glyphs is None or ord(char) in glyphs or char.isspace()


def font_runs(text: str, font: str) -> list[tuple[str, str]]:
    """Split ``text`` into (font, text) runs, using a fallback font for missing glyphs."""
    runs: list[tuple[str, str]] = []
    for char in text:
        chosen = font
        if not _has_glyph(font, char):
            for kind in FALLBACK_FONTS:
                candidate = font_name(kind)
                if _has_glyph(candidate, char):
                    chosen = candidate
                    break
        if runs and runs[-1][0] == chosen:
            runs[-1] = (chosen, runs[-1][1] + char)
        else:
            runs.append((chosen, char))
    return runs


def draw_with_fallback(canvas, x: float, y: float, text: str, font: str, size: float) -> None:
    from reportlab.pdfbase.pdfmetrics import stringWidth

    for run_font, run in font_runs(text, font):
        canvas.setFont(run_font, size)
        canvas.drawString(x, y, run)
        x += stringWidth(run, run_font, size)
    canvas.setFont(font, size)


Spans = dict[str, tuple[str, str]]


def encode_scripts(text: str) -> tuple[str, Spans]:
    """Replace each ``<sub>…</sub>``/``<sup>…</sup>`` with one placeholder character."""
    spans: Spans = {}

    def replace(match) -> str:
        char = chr(_SPAN_BASE + len(spans))
        spans[char] = (match.group(1), match.group(2))
        return char

    encoded = SCRIPT.sub(replace, text)
    return strip_tags(encoded), spans  # drop tags the translator left unpaired


def text_width(text: str, font: str, size: float, spans: Spans | None = None) -> float:
    from reportlab.pdfbase.pdfmetrics import stringWidth

    if not spans:
        return stringWidth(text, font, size)
    width = 0.0
    plain = ""
    for char in text:
        if char in spans:
            width += stringWidth(spans[char][1], font, size * SCRIPT_SCALE)
        else:
            plain += char
    return width + stringWidth(plain, font, size)


def draw_line(canvas, x: float, y: float, line: str, font: str, size: float, spans: Spans) -> None:
    """Draw one line, with sub- and superscripts smaller and off the baseline."""
    run = ""
    for char in line:
        if char not in spans:
            run += char
            continue
        if run:
            draw_with_fallback(canvas, x, y, run, font, size)
            x += text_width(run, font, size)
            run = ""
        kind, content = spans[char]
        small = size * SCRIPT_SCALE
        shift = size * SUPERSCRIPT_RISE if kind == "sup" else -size * SUBSCRIPT_DROP
        draw_with_fallback(canvas, x, y + shift, content, font, small)
        x += text_width(content, font, small)
    if run:
        draw_with_fallback(canvas, x, y, run, font, size)


def wrap_text(
    text: str, font: str, size: float, width: float, spans: Spans | None = None
) -> list[str]:
    """Wrap Japanese text to ``width``, keeping Latin words whole and following kinsoku."""

    def stringWidth(value: str, font: str, size: float) -> float:  # noqa: N802
        return text_width(value, font, size, spans)

    lines: list[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for token in _TOKEN.findall(paragraph):
            if token.isspace():
                if current:
                    current += " "
                continue
            candidate = current + token
            if stringWidth(candidate, font, size) <= width or not current.strip():
                if stringWidth(candidate, font, size) > width and len(token) > 1:
                    # A single word longer than the line: break it by character.
                    for char in token:
                        if current and stringWidth(current + char, font, size) > width:
                            lines.append(current.rstrip())
                            current = ""
                        current += char
                else:
                    current = candidate
                continue
            if token[0] in NO_LINE_START:
                current = candidate  # hang the punctuation on this line
                continue
            carry = ""
            while current and current[-1] in NO_LINE_END:
                carry = current[-1] + carry
                current = current[:-1]
            lines.append(current.rstrip())
            current = carry + token
        if current.strip():
            lines.append(current.rstrip())
    return lines or [""]


@dataclass
class FittedText:
    lines: list[str]
    size: float
    leading: float = LEADING


def fit_text(text: str, font: str, block: TextBlock, spans: Spans | None = None) -> FittedText:
    """Largest size (up to the original) at which ``text`` fits inside ``block``.

    The text never runs below the block, where it would overlap the next
    paragraph; line spacing is tightened (down to ``MIN_LEADING``) before the
    font is made smaller.
    """
    width = max(4.0, block.width)
    height = max(block.height, block.font_size) + 0.5
    size = max(MIN_FONT_SIZE, min(block.font_size, 40.0))
    while True:
        lines = wrap_text(text, font, size, width, spans)
        if len(lines) == 1:
            if size <= height or size <= MIN_FONT_SIZE:
                return FittedText(lines, size)
        else:
            # Room needed: the first line, then one step per further line.
            leading = (height - size) / ((len(lines) - 1) * size)
            if leading >= MIN_LEADING or size <= MIN_FONT_SIZE:
                return FittedText(lines, size, max(MIN_LEADING, min(LEADING, leading)))
        size = max(MIN_FONT_SIZE, size * 0.95)


def build_overlay(
    pages: list[PageInfo], colors: dict[int, list[tuple[tuple, tuple]]], script: str = "ja"
) -> bytes:
    """One overlay page per input page: background patches, then Japanese text."""
    from reportlab.lib.colors import Color
    from reportlab.pdfgen.canvas import Canvas

    buffer = io.BytesIO()
    canvas = Canvas(buffer, pageCompression=1)
    for page in pages:
        canvas.setPageSize((page.width, page.height))
        blocks = [b for b in page.blocks if b.translated.strip()]
        page_colors = colors.get(page.index, [])
        for block, (background, _text_color) in zip(blocks, page_colors, strict=False):
            canvas.setFillColor(Color(*background))
            canvas.setStrokeColor(Color(*background))
            pad = 1.0
            canvas.rect(
                block.x0 - pad,
                block.y0 - pad,
                block.width + 2 * pad,
                block.height + 2 * pad,
                stroke=0,
                fill=1,
            )
        for block, (_background, text_color) in zip(blocks, page_colors, strict=False):
            font = block_font(block, script)
            text, spans = encode_scripts(block.translated)
            fitted = fit_text(text, font, block, spans)
            canvas.setFillColor(Color(*text_color))
            canvas.setFont(font, fitted.size)
            baseline = block.y1 - fitted.size * 0.88
            for line in fitted.lines:
                if block.centered:
                    line_width = text_width(line, font, fitted.size, spans)
                    x = block.x0 + (block.width - line_width) / 2
                else:
                    x = block.x0
                draw_line(canvas, x, baseline, line, font, fitted.size, spans)
                baseline -= fitted.size * fitted.leading
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def write_outputs(
    normalized: bytes,
    overlay: bytes,
    page_indexes: list[int],
    japanese_path: Path | None,
    bilingual_path: Path | None,
) -> None:
    """Merge the overlay into copies of the pages and save the requested PDFs."""
    from pypdf import PdfReader, PdfWriter, Transformation

    reader = PdfReader(io.BytesIO(normalized))
    overlay_reader = PdfReader(io.BytesIO(overlay))

    if japanese_path is not None:
        writer = PdfWriter()
        for position, index in enumerate(page_indexes):
            # Pages are merged after being attached to the writer, never on the reader.
            page = writer.add_page(reader.pages[index])
            page.merge_page(overlay_reader.pages[position])
        _save(writer, japanese_path)

    if bilingual_path is not None:
        writer = PdfWriter()
        for position, index in enumerate(page_indexes):
            original = reader.pages[index]
            width = float(original.mediabox.width)
            height = float(original.mediabox.height)
            shift = Transformation().translate(width + BILINGUAL_GAP, 0)
            sheet = writer.add_blank_page(width=width * 2 + BILINGUAL_GAP, height=height)
            sheet.merge_transformed_page(original, Transformation())
            sheet.merge_transformed_page(original, shift)
            sheet.merge_transformed_page(overlay_reader.pages[position], shift)
        _save(writer, bilingual_path)


def _save(writer, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    writer.compress_identical_objects(remove_duplicates=True, remove_unreferenced=True)
    with temporary.open("wb") as handle:
        writer.write(handle)
    temporary.replace(path)
