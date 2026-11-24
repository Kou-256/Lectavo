"""Layout-preserving English to Japanese PDF translation.

The pipeline reads each page (text layer, or OCR for scanned pages), sends the
prose blocks to a translation engine, then writes the Japanese text back over
the original positions. Figures, images and formulas are left untouched.
"""

from movie_slide_notes.pdf_translate.models import (
    OCR_MODES,
    STYLES,
    PdfTranslationOptions,
    PdfTranslationResult,
    TextBlock,
)

__all__ = [
    "OCR_MODES",
    "STYLES",
    "PdfTranslationOptions",
    "PdfTranslationResult",
    "TextBlock",
]
