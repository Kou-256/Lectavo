"""Offline check that the translation pipeline works in this build.

Used by ``Lectavo.exe --smoke``: it builds a one-page PDF, translates it with a
stand-in engine (no network), renders the result and runs OCR on it, so a
packaged build that is missing the PDF renderer, fonts or OCR models fails.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from movie_slide_notes.pdf_translate.engines import Engine, EngineConfig
from movie_slide_notes.pdf_translate.models import PdfTranslationOptions


class _EchoEngine(Engine):
    def translate(self, texts, style, context="", source="auto", target="ja"):
        return ["発光特性を調べた。" for _ in texts]


def run_selfcheck() -> None:
    from reportlab.pdfgen.canvas import Canvas

    from movie_slide_notes.pdf_translate import layout, ocr
    from movie_slide_notes.pdf_translate.pipeline import translate_pdf

    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "selfcheck.pdf"
        canvas = Canvas(str(source), pagesize=(420, 300))
        canvas.setFont("Helvetica", 16)
        canvas.drawString(30, 240, "Luminescence properties of phosphors")
        canvas.drawString(30, 215, "were measured at room temperature.")
        canvas.showPage()
        canvas.save()
        result = translate_pdf(
            source,
            Path(folder),
            PdfTranslationOptions(),
            _EchoEngine(EngineConfig("local")),
            "selfcheck",
        )
        if result.japanese_pdf is None or not result.japanese_pdf.exists():
            raise RuntimeError("PDF translation self-check produced no output")
        image = layout.render_page(source.read_bytes(), 0, 2.0)
        lines = ocr.recognize_lines(image)
        if not any("phosphors" in line.text.lower() for line in lines):
            raise RuntimeError("OCR self-check did not read the test page")
