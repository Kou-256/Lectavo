from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from movie_slide_notes.pdf_translate.engines import (
    Engine,
    EngineConfig,
    TranslationError,
    create_engine,
    parse_translations,
    supports_style,
)
from movie_slide_notes.pdf_translate.layout import join_lines, should_translate
from movie_slide_notes.pdf_translate.models import PdfTranslationOptions
from movie_slide_notes.pdf_translate.pipeline import translate_pdf
from movie_slide_notes.pdf_translate.writer import NO_LINE_START, font_name, wrap_text

PARAGRAPH = (
    "Rare-earth doped phosphors are widely used in lighting and display applications because "
    "of their sharp emission lines and high quantum efficiency."
)


def make_paper(path: Path, pages: int = 2) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen.canvas import Canvas

    canvas = Canvas(str(path), pagesize=A4)
    width, height = A4
    for _ in range(pages):
        canvas.setFont("Helvetica-Bold", 18)
        canvas.drawCentredString(width / 2, height - 70, "Luminescence Properties")
        canvas.setFont("Helvetica-Bold", 12)
        canvas.drawString(60, height - 120, "1. Introduction")
        canvas.setFont("Times-Roman", 10)
        y = height - 140
        for line in (
            "Rare-earth doped phosphors are widely used in lighting and display",
            "applications because of their sharp emission lines and high quantum",
            "efficiency. We studied samples prepared by a solid-state reaction.",
        ):
            canvas.drawString(60, y, line)
            y -= 13
        canvas.setFont("Times-Italic", 14)
        canvas.drawCentredString(width / 2, y - 30, "I = I0 e^(-t/t0)")
        canvas.showPage()
    canvas.save()


class FakeEngine(Engine):
    def __init__(self):
        super().__init__(EngineConfig("local"))
        self.calls = 0
        self.targets = []

    def translate(self, texts, style, context="", source="auto", target="ja"):
        self.calls += 1
        self.targets.append(target)
        # About as long as a real Japanese translation of the text.
        sentence = "希土類添加蛍光体の発光特性を調べた。"
        return [f"訳{len(text)}：" + sentence * max(1, len(text) // 50) for text in texts]


class TextRulesTests(unittest.TestCase):
    def test_prose_and_headings_are_translated_but_formulas_are_kept(self) -> None:
        self.assertTrue(should_translate(PARAGRAPH))
        self.assertTrue(should_translate("1. Introduction"))
        self.assertTrue(should_translate("Fig. 1. PL spectra of the samples"))
        self.assertFalse(should_translate("I = I0 e^(-t/t0)"))
        self.assertFalse(should_translate("12.5  13.8  14.1  15.0"))
        self.assertFalse(should_translate("https://doi.org/10.1000/xyz123"))
        self.assertTrue(should_translate("希土類添加蛍光体の発光特性"))
        self.assertTrue(should_translate("Les propriétés de luminescence"))
        self.assertFalse(should_translate("x"))

    def test_wrapped_lines_are_joined_and_hyphenation_is_undone(self) -> None:
        self.assertEqual(
            join_lines(["luminescence prop-", "erties of Eu3+", "ions"]),
            "luminescence properties of Eu3+ ions",
        )
        self.assertEqual(join_lines(["A self-", "Consistent model"]), "A self- Consistent model")

    def test_japanese_wrapping_follows_line_start_rules(self) -> None:
        font = font_name("LectavoGothic")
        text = (
            "希土類添加蛍光体は、照明やディスプレイに広く使われている。本研究では発光特性を調べた。"
            * 3
        )
        lines = wrap_text(text, font, 10, 120)
        self.assertGreater(len(lines), 3)
        self.assertEqual("".join(lines), text)
        for line in lines[1:]:
            self.assertNotIn(line[0], NO_LINE_START)

    def test_latin_words_are_not_split(self) -> None:
        font = font_name("LectavoGothic")
        lines = wrap_text("試料はEu3+ doped phosphorとして作製した。", font, 10, 60)
        self.assertTrue(any("phosphor" in line for line in lines))


class EngineTests(unittest.TestCase):
    def test_translations_are_read_by_id(self) -> None:
        raw = 'ok {"translations": [{"id": 1, "text": "二"}, {"id": 0, "text": "一"}]}'
        self.assertEqual(parse_translations(raw, 2), ["一", "二"])

    def test_missing_translations_are_an_error(self) -> None:
        with self.assertRaises(TranslationError):
            parse_translations('{"translations": [{"id": 0, "text": "一"}]}', 2)
        with self.assertRaises(TranslationError):
            parse_translations("not json", 1)

    def test_api_key_is_required_except_for_local_models(self) -> None:
        with self.assertRaises(TranslationError):
            create_engine(EngineConfig("claude"))
        self.assertIsNotNone(create_engine(EngineConfig("local")))

    def test_deepl_does_not_offer_easy_japanese(self) -> None:
        self.assertFalse(supports_style("deepl", "plain"))
        self.assertTrue(supports_style("deepl", "natural"))
        self.assertTrue(supports_style("claude", "plain"))

    @unittest.skipUnless(os.name == "nt", "DPAPI is Windows only")
    def test_api_keys_are_stored_encrypted(self) -> None:
        from movie_slide_notes.pdf_translate.secret_store import protect, unprotect

        stored = protect("sk-ant-example")
        self.assertNotIn("sk-ant", stored)
        self.assertEqual(unprotect(stored), "sk-ant-example")
        self.assertEqual(unprotect("garbage"), "")


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "paper.pdf"
        make_paper(self.source)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_japanese_and_bilingual_pdfs_keep_the_page_layout(self) -> None:
        from pypdf import PdfReader

        engine = FakeEngine()
        cache = self.root / "cache.sqlite3"
        result = translate_pdf(
            self.source,
            self.root / "out",
            PdfTranslationOptions(),
            engine,
            "fake",
            cache_path=cache,
        )
        self.assertEqual(result.pages, 2)
        self.assertEqual(result.ocr_pages, 0)
        japanese = PdfReader(str(result.japanese_pdf))
        bilingual = PdfReader(str(result.bilingual_pdf))
        original = PdfReader(str(self.source))
        self.assertEqual(len(japanese.pages), 2)
        self.assertEqual(japanese.pages[0].mediabox.width, original.pages[0].mediabox.width)
        self.assertAlmostEqual(
            float(bilingual.pages[0].mediabox.width), float(original.pages[0].mediabox.width) * 2
        )
        self.assertIn("希土類添加蛍光体", japanese.pages[0].extract_text())

        # A second run reuses every cached translation and calls the engine no more.
        calls = engine.calls
        again = translate_pdf(
            self.source,
            self.root / "out2",
            PdfTranslationOptions(),
            engine,
            "fake",
            cache_path=cache,
        )
        self.assertEqual(engine.calls, calls)
        self.assertEqual(again.cached_blocks, again.translated_blocks)

    def test_page_range_and_single_output(self) -> None:
        options = PdfTranslationOptions(make_bilingual=False, first_page=2, last_page=2)
        result = translate_pdf(self.source, self.root / "out", options, FakeEngine(), "fake")
        self.assertIsNone(result.bilingual_pdf)
        self.assertEqual(result.pages, 1)

    def test_scanned_pages_are_read_with_ocr(self) -> None:
        import pypdfium2 as pdfium
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen.canvas import Canvas

        document = pdfium.PdfDocument(str(self.source))
        image_path = self.root / "scan.png"
        document[0].render(scale=200 / 72).to_pil().convert("L").save(image_path)
        document.close()
        scanned = self.root / "scanned.pdf"
        canvas = Canvas(str(scanned), pagesize=A4)
        canvas.drawImage(str(image_path), 0, 0, *A4)
        canvas.showPage()
        canvas.save()

        result = translate_pdf(
            scanned, self.root / "out", PdfTranslationOptions(), FakeEngine(), "fake"
        )
        self.assertEqual(result.ocr_pages, 1)
        self.assertGreaterEqual(result.translated_blocks, 2)


class PageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_start_needs_a_key_and_a_supported_style(self) -> None:
        from PySide6.QtCore import QSettings

        from movie_slide_notes.gui.pdf_translate_page import EngineSettings, PdfTranslatePage

        with tempfile.TemporaryDirectory() as folder:
            settings = QSettings(str(Path(folder) / "s.ini"), QSettings.IniFormat)
            engine_settings = EngineSettings(settings)
            page = PdfTranslatePage(settings, engine_settings)
            source = Path(folder) / "paper.pdf"
            make_paper(source, pages=1)
            self.assertTrue(page.set_source(source))
            self.assertIs(page.stack.currentWidget(), page.ready_page)
            from unittest.mock import patch

            from movie_slide_notes.pdf_translate import local_llm

            with patch.object(local_llm, "is_installed", return_value=False):
                page.refresh_engine()
                self.assertFalse(page.start_button.isEnabled())  # no local model yet

            engine_settings.save("local", "qwen3:8b", "", None)
            page.refresh_engine()
            self.assertTrue(page.start_button.isEnabled())

            if os.name == "nt":
                engine_settings.save("deepl", "", "", "key:fx")
                page.style_buttons["plain"].setChecked(True)
                self.assertFalse(page.start_button.isEnabled())
                page.style_buttons["literal"].setChecked(True)
                self.assertTrue(page.start_button.isEnabled())
            del settings


if __name__ == "__main__":
    unittest.main()
