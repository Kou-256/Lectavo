from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Translation styles offered in the UI. Keys are stored in settings.
STYLES: dict[str, str] = {
    "natural": "意訳",
    "literal": "直訳",
    "plain": "やさしい日本語",
}


def style_label(style: str, target: str) -> str:
    if style == "plain":
        return "やさしい日本語" if target == "ja" else "やさしい表現"
    return STYLES[style]


OCR_MODES: dict[str, str] = {
    "auto": "自動（文字のないページだけ）",
    "always": "すべてのページ",
    "off": "使わない",
}


@dataclass
class TextBlock:
    """One paragraph-like run of text on a page, in PDF points (origin bottom-left)."""

    page_index: int
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    font_size: float
    bold: bool = False
    serif: bool = False
    color: tuple[float, float, float] = (0.0, 0.0, 0.0)
    centered: bool = False
    source: str = "text"  # "text" or "ocr"
    translated: str = ""

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass
class PageInfo:
    index: int
    width: float
    height: float
    ocr: bool = False
    blocks: list[TextBlock] = field(default_factory=list)


@dataclass
class PdfTranslationOptions:
    style: str = "natural"
    ocr_mode: str = "auto"
    make_japanese: bool = True
    make_bilingual: bool = True
    first_page: int = 1
    last_page: int = 0  # 0 = through the last page
    source_lang: str = "auto"
    target_lang: str = "ja"


@dataclass
class PdfTranslationResult:
    source: Path
    japanese_pdf: Path | None
    bilingual_pdf: Path | None
    pages: int
    ocr_pages: int
    translated_blocks: int
    cached_blocks: int
