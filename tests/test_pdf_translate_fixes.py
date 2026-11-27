"""Reference lists, pages wrapped in a form, split lines, shifted answers, and the viewer."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from movie_slide_notes.pdf_translate.engines import Engine, EngineConfig
from movie_slide_notes.pdf_translate.layout import (
    extract_text_pages,
    looks_like_reference,
    merge_contained,
    normalize_pdf,
    should_translate,
)
from movie_slide_notes.pdf_translate.models import PdfTranslationOptions
from movie_slide_notes.pdf_translate.pipeline import misaligned, translate_pdf

PROSE = (
    "Rare-earth doped phosphors are widely used in lighting and display applications "
    "because of their sharp emission lines and high quantum efficiency."
)


def make_wrapped_page(path: Path) -> None:
    """A page whose whole content sits inside one form XObject, like publisher cover pages."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen.canvas import Canvas

    width, height = A4
    canvas = Canvas(str(path), pagesize=A4)
    canvas.beginForm("page")
    canvas.setFont("Helvetica", 16)
    canvas.drawString(40, height - 120, "Accelerating the optimization of phosphor materials")
    canvas.setFont("Helvetica", 9)
    canvas.drawString(40, height - 150, "View the article online for updates and enhancements.")
    canvas.endForm()
    canvas.doForm("page")
    canvas.showPage()
    canvas.save()


class ReferenceTests(unittest.TestCase):
    def test_bibliography_entries_are_kept(self) -> None:
        for entry in (
            "12) X.-D. Xiang, X. Sun, G. Briceño, Y. Lou, and P. G. Schultz, Science 268, "
            "1738 (1995).",
            "I. Takeuchi et al., Nat. Mater. 2, 180 (2003).",
            "H. Koinuma, J. Comb. Chem. 6, 50 (2004).",
            "T. Chikyo, and S. Suzuki, Handbook of Advanced Ceramics: Materials, Applications, "
            "Processing, and Properties (Elsevier Inc, 2013) 2nd ed., p. 1103.",
            "38) Available from: https://github.com/bayesian-optimization/",
            "[4] A. Smith, B. Jones, and C. Brown, Phys. Rev. B 51, 14103 (1995).",
        ):
            self.assertTrue(looks_like_reference(entry), entry)

    def test_prose_with_citations_is_translated(self) -> None:
        for text in (
            PROSE,
            "As shown by Smith et al. (2020), the emission depends on the Eu content.",
            "4. Conclusions",
            "Acknowledgments",
        ):
            self.assertFalse(looks_like_reference(text), text)

    def test_identifiers_are_not_headings(self) -> None:
        self.assertFalse(should_translate("BayesianOptimization."))
        self.assertTrue(should_translate("Introduction"))


class LayoutTests(unittest.TestCase):
    def test_text_of_a_page_wrapped_in_a_form_is_found(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "cover.pdf"
            make_wrapped_page(source)
            blocks, chars, _coverage = extract_text_pages(normalize_pdf(source), [0])[0]
        self.assertGreater(chars, 40)
        texts = [block.text for block in blocks]
        self.assertIn("Accelerating the optimization of phosphor materials", texts)

    def test_a_box_inside_a_paragraph_is_folded_back_in_reading_order(self) -> None:
        def line(x0, y0, x1, text):
            return SimpleNamespace(x0=x0, y0=y0, x1=x1, y1=y0 + 9, text=text)

        paragraph = [
            line(305, 220, 548, "first line"),
            line(305, 196, 395, "longer measurement"),
            line(305, 184, 548, "the temperature dependence"),
        ]
        fragment = [line(397, 196, 475, "time. Furthermore,")]
        merged = merge_contained([paragraph, fragment])
        self.assertEqual(len(merged), 1)
        self.assertEqual(
            [item.text for item in merged[0]],
            [
                "first line",
                "longer measurement",
                "time. Furthermore,",
                "the temperature dependence",
            ],
        )

    def test_separate_columns_stay_separate(self) -> None:
        left = [SimpleNamespace(x0=47, y0=100, x1=291, y1=300)]
        right = [SimpleNamespace(x0=305, y0=100, x1=548, y1=300)]
        self.assertEqual(len(merge_contained([left, right])), 2)


class ShiftedAnswerTests(unittest.TestCase):
    def test_answers_that_belong_to_another_item_are_found(self) -> None:
        sources = [PROSE, "4. Conclusions", "Acknowledgments"]
        self.assertEqual(misaligned(sources, ["4. 結論", "4. 結論", "謝辞"]), [0, 1])
        self.assertEqual(
            misaligned(
                sources, ["希土類を添加した蛍光体は照明や表示に広く使われる。", "4. 結論", "謝辞"]
            ),
            [],
        )

    def test_shifted_answers_are_asked_again_one_by_one(self) -> None:
        class ShiftingEngine(Engine):
            def __init__(self):
                super().__init__(EngineConfig("local"))
                self.single_calls = 0

            def translate(self, texts, style, context="", source="auto", target="ja"):
                if len(texts) == 1:
                    self.single_calls += 1
                    return [f"訳：{texts[0][:30]}の内容を日本語で説明した文章です。"]
                # Every answer moves up by one item, the last one is repeated.
                answers = [f"訳：{text[:30]}の内容を日本語で説明した文章です。" for text in texts]
                return answers[1:] + answers[-1:]

        from test_pdf_translate import make_paper

        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "paper.pdf"
            make_paper(source, pages=1)
            engine = ShiftingEngine()
            translate_pdf(source, Path(folder), PdfTranslationOptions(), engine, "shift")
        self.assertGreaterEqual(engine.single_calls, 2)


def make_heading_page(path: Path) -> None:
    """Bold section headings set right above and between paragraphs, as in journal papers."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen.canvas import Canvas

    width, height = A4
    canvas = Canvas(str(path), pagesize=A4)
    y = height - 100
    for heading, paragraph in (
        (
            "3.1. Phosphor properties of the composition-gradient film",
            (
                "An oxide composition-gradient film with a thickness of 80 nm was prepared using",
                "combinatorial pulsed laser deposition. Measurements of the film revealed that the",
                "composition was continuously varied with respect to the sample position on it.",
            ),
        ),
        (
            "3.2. Bayesian optimization of phosphor properties",
            (
                "Bayesian optimization is an established method for black-box optimization and",
                "was used here to find the composition with the strongest emission in few steps.",
            ),
        ),
    ):
        canvas.setFont("Helvetica-Bold", 10)
        canvas.drawString(60, y, heading)
        canvas.setFont("Times-Roman", 10)
        for line in paragraph:
            y -= 12
            canvas.drawString(60, y, line)
        y -= 12
    canvas.showPage()
    canvas.save()


class DroppedTextTests(unittest.TestCase):
    def test_a_heading_is_split_off_its_paragraph(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "heading.pdf"
            make_heading_page(source)
            blocks, _chars, _coverage = extract_text_pages(normalize_pdf(source), [0])[0]
        texts = sorted((block.text for block in blocks), key=lambda t: t[:3])
        self.assertEqual(len(texts), 4, texts)
        starts = sorted(text[:12] for text in texts)
        self.assertEqual(
            starts, ["3.1. Phospho", "3.2. Bayesia", "An oxide com", "Bayesian opt"], texts
        )

    def test_an_answer_that_drops_most_terms_or_text_is_found(self) -> None:
        source = (
            "An ⟦0⟧ film was grown on ⟦1⟧. The lattice constant of ⟦2⟧ increased with the "
            "Eu content because the radius of ⟦3⟧ is larger than that of ⟦4⟧."
        )
        self.assertEqual(misaligned([source], ["⟦0⟧膜の性質"]), [0])
        whole = "⟦0⟧膜を⟦1⟧上に成長させた。⟦3⟧の半径が⟦4⟧より大きいので⟦2⟧の格子定数が増えた。"
        self.assertEqual(misaligned([source], [whole]), [])
        long_source = PROSE * 4
        self.assertEqual(misaligned([long_source], ["蛍光体は照明に使われる。" * 2]), [0])

    def test_a_section_heading_is_not_given_as_the_document_title(self) -> None:
        from movie_slide_notes.pdf_translate.models import PageInfo, TextBlock
        from movie_slide_notes.pdf_translate.pipeline import document_context

        def page(*blocks):
            info = PageInfo(index=0, width=595, height=842)
            info.blocks = [TextBlock(0, 0, 0, 100, 10, text, size) for text, size in blocks]
            return info

        body = [(PROSE, 10.0)] * 3
        heading = page(("2. Experimental methods", 10.5), *body)
        self.assertEqual(document_context([heading]), "")
        titled = page(("Accelerating the optimization of phosphor materials", 16.0), *body)
        self.assertEqual(
            document_context([titled]), "Accelerating the optimization of phosphor materials"
        )

    def test_a_paragraph_answered_only_in_part_is_translated_sentence_by_sentence(self) -> None:
        from movie_slide_notes.pdf_translate.pipeline import sentence_chunks

        class HeadingOnlyEngine(Engine):
            """Answers a long item with a few words, as small models sometimes do."""

            def __init__(self):
                super().__init__(EngineConfig("local"))
                self.pieces = 0

            def translate(self, texts, style, context="", source="auto", target="ja"):
                if len(texts) == 1 and len(texts[0]) > 300:
                    return ["見出し"]
                self.pieces += len(texts)
                return [f"訳：{text}" for text in texts]

        paragraph = " ".join(
            f"Sentence number {n} describes the measured photoluminescence spectra in detail."
            for n in range(12)
        )
        self.assertGreater(len(sentence_chunks(paragraph)), 1)
        from movie_slide_notes.pdf_translate.models import TextBlock

        with tempfile.TemporaryDirectory() as folder:
            from test_pdf_translate import make_paper

            source = Path(folder) / "paper.pdf"
            make_paper(source, pages=1)
            engine = HeadingOnlyEngine()
            from unittest.mock import patch

            from movie_slide_notes.pdf_translate import layout

            block = TextBlock(0, 50, 100, 550, 700, paragraph, 10.0)
            with patch.object(
                layout,
                "extract_text_pages",
                return_value={0: ([block], 500, 0.0)},
            ):
                translate_pdf(source, Path(folder), PdfTranslationOptions(), engine, "partial")
        self.assertIn("Sentence number 11", block.translated)
        self.assertNotIn("見出し", block.translated)
        self.assertGreater(engine.pieces, 1)


class CompareViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _viewer(self, folder: str, pages: int = 4):
        from test_pdf_translate import make_paper

        from movie_slide_notes.gui.compare_viewer import CompareViewer, PdfSide

        source = Path(folder) / "paper.pdf"
        make_paper(source, pages=pages)
        viewer = CompareViewer(PdfSide(source), PdfSide(source), pages=pages)
        viewer.resize(900, 700)
        viewer.show()
        self.app.processEvents()
        viewer.set_fit("width")
        self.app.processEvents()
        return viewer

    def test_pages_scroll_continuously_and_the_page_number_follows(self) -> None:
        from PySide6.QtWidgets import QAbstractSpinBox

        with tempfile.TemporaryDirectory() as folder:
            viewer = self._viewer(folder)
            self.assertEqual(viewer.page_spin.buttonSymbols(), QAbstractSpinBox.NoButtons)
            left, right = viewer.panes
            # All pages sit one below the other in one scroll range.
            self.assertGreater(left.bar.maximum(), 2 * left.pages[0].height())
            self.assertLess(left.pages[0].y(), left.pages[1].y())

            left.bar.setValue(left.pages[2].y() + 10)
            self.app.processEvents()
            self.assertEqual(viewer.index, 2)
            self.assertEqual(viewer.page_spin.value(), 3)
            self.assertEqual(right.position()[0], 2)  # scrolled together
            self.assertIsNotNone(left.pages[2].pixmap())

            viewer.go_to(1)
            self.assertEqual(left.current_page(), 1)
            self.assertEqual(right.current_page(), 1)
            viewer.close()

    def test_sides_scroll_on_their_own_when_not_linked(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            viewer = self._viewer(folder)
            left, right = viewer.panes
            viewer.sync_check.setChecked(False)
            before = right.bar.value()
            left.bar.setValue(left.pages[2].y())
            self.app.processEvents()
            self.assertEqual(right.bar.value(), before)
            self.assertEqual(viewer.index, 2)
            # Zooming keeps each side where it was.
            viewer.zoom_in()
            self.assertEqual(left.position()[0], 2)
            self.assertEqual(right.position()[0], 0)
            # Linking again brings the other side to the same page.
            viewer.sync_check.setChecked(True)
            self.assertEqual(right.position()[0], 2)
            viewer.close()


def make_formula_page(path: Path) -> None:
    """Prose, a displayed equation in math fonts with its number, then more prose."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen.canvas import Canvas

    width, height = A4
    canvas = Canvas(str(path), pagesize=A4)
    y = height - 100
    canvas.setFont("Times-Roman", 10)
    for line in (
        "The upper confidence bound is a typical acquisition function in",
        "Bayesian optimization and is defined as follows:",
    ):
        canvas.drawString(60, y, line)
        y -= 12
    x = 120
    for font, text in [
        ("Times-Roman", "UCB"),
        ("Symbol", "("),
        ("Times-Italic", "x"),
        ("Symbol", ") = m("),
        ("Times-Italic", "x"),
        ("Symbol", ") + ks("),
        ("Times-Italic", "x"),
        ("Symbol", ")"),
    ]:
        canvas.setFont(font, 10)
        canvas.drawString(x, y, text)
        x += canvas.stringWidth(text, font, 10) + 1
    canvas.setFont("Times-Roman", 10)
    canvas.drawString(270, y, "(1)")
    y -= 16
    for line in (
        "where the predicted mean and the standard deviation come from the",
        "Gaussian process regression of all measurements made so far.",
    ):
        canvas.drawString(60, y, line)
        y -= 12
    canvas.showPage()
    canvas.save()


class FormulaAndFittingTests(unittest.TestCase):
    def test_displayed_formulas_are_left_as_they_are(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "formula.pdf"
            make_formula_page(source)
            blocks, _chars, _coverage = extract_text_pages(normalize_pdf(source), [0])[0]
        texts = [block.text for block in blocks]
        self.assertTrue(any("defined as follows:" in text for text in texts), texts)
        self.assertTrue(any("Gaussian process regression" in text for text in texts), texts)
        for text in texts:
            self.assertNotIn("=", text)
            self.assertNotIn("(1)", text)

    def test_translated_text_stays_inside_its_box(self) -> None:
        from movie_slide_notes.pdf_translate.models import TextBlock
        from movie_slide_notes.pdf_translate.writer import fit_text, font_name

        font = font_name("LectavoGothic")
        block = TextBlock(page_index=0, x0=50, y0=400, x1=290, y1=610, text="x", font_size=10)
        for repeat in (5, 12, 20, 40):
            text = "ベイズ最適化によって組成の探索を加速することが期待されている。" * repeat
            fitted = fit_text(text, font, block)
            used = fitted.size + (len(fitted.lines) - 1) * fitted.size * fitted.leading
            self.assertLessEqual(used, block.height + 0.5 + 1e-6, repeat)


def make_script_page(path: Path) -> None:
    """Chemical formulas with lowered digits, an ion charge and a raised citation."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen.canvas import Canvas

    width, height = A4
    canvas = Canvas(str(path), pagesize=A4)
    text = canvas.beginText(60, height - 100)
    for content, size, rise, font in [
        ("Thin films of Eu", 10, 0, "Times-Roman"),
        ("2", 7, -2, "Times-Roman"),
        ("O", 10, 0, "Times-Roman"),
        ("3", 7, -2, "Times-Roman"),
        (" doped with Eu", 10, 0, "Times-Roman"),
        ("3+", 7, 4, "Times-Roman"),
        (" were grown and the mean was taken.", 10, 0, "Times-Roman"),
        ("12)", 7, 4, "Times-Roman"),
    ]:
        text.setFont(font, size)
        text.setRise(rise)
        text.textOut(content)
    canvas.drawText(text)
    canvas.showPage()
    canvas.save()


class ScriptTests(unittest.TestCase):
    def test_sub_and_superscripts_are_tagged_and_kept_through_translation(self) -> None:
        from movie_slide_notes.pdf_translate.protect import protect, restore

        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "scripts.pdf"
            make_script_page(source)
            blocks, _chars, _coverage = extract_text_pages(normalize_pdf(source), [0])[0]
        self.assertEqual(len(blocks), 1)
        text = blocks[0].text
        self.assertIn("Eu<sub>2</sub>O<sub>3</sub>", text)
        self.assertIn("Eu<sup>3+</sup>", text)
        self.assertTrue(text.endswith("taken.<sup>12)</sup>"), text)
        # Letters of a Symbol font without a Unicode map are Greek.
        from movie_slide_notes.pdf_translate.layout import _char_text

        symbol = SimpleNamespace(get_text=lambda: "m", fontname="ABCDEF+EuclidSymbol-Italic")
        self.assertEqual(_char_text(symbol), "μ")

        protected, tokens = protect(text)
        self.assertNotIn("<su", protected)
        self.assertIn("Eu<sub>2</sub>O<sub>3</sub>", tokens)
        self.assertEqual(restore(protected, tokens), text)
        # An ordinary word before a citation mark is still translated.
        protected, tokens = protect("as reported previously.<sup>22,23)</sup>")
        self.assertIn("previously.", protected)

    def test_scripts_are_drawn_smaller_and_off_the_baseline(self) -> None:
        from movie_slide_notes.pdf_translate.writer import (
            SCRIPT_SCALE,
            encode_scripts,
            font_name,
            text_width,
            wrap_text,
        )

        font = font_name("LectavoGothic")
        encoded, spans = encode_scripts(
            "薄膜Eu<sub>2</sub>O<sub>3</sub>とEu<sup>3+</sup>イオン<sub>"
        )
        self.assertNotIn("<", encoded)  # the stray tag is dropped
        self.assertEqual([kind for kind, _ in spans.values()], ["sub", "sub", "sup"])
        one = text_width(next(iter(spans)), font, 10, spans)
        self.assertAlmostEqual(one, text_width("2", font, 10 * SCRIPT_SCALE), places=3)
        lines = wrap_text(encoded * 6, font, 10, 80, spans)
        self.assertEqual("".join(lines), encoded * 6)  # a formula is never split inside a span

    def test_deepl_gets_tags_as_xml(self) -> None:
        from movie_slide_notes.pdf_translate.markup import from_xml, to_xml

        text = "If f > τ, Eu<sub>2</sub>O<sub>3</sub> & Y<sub>2</sub>O<sub>3</sub>"
        xml = to_xml(text)
        self.assertIn("f &gt; τ", xml)
        self.assertIn("<sub>2</sub>", xml)
        self.assertEqual(from_xml(xml), text)


class BatchTranslationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_several_pdfs_are_translated_side_by_side(self) -> None:
        import threading
        import time
        from unittest.mock import patch

        from test_pdf_translate import make_paper

        from movie_slide_notes.gui import pdf_translate_page

        class SlowEngine(Engine):
            def __init__(self):
                super().__init__(EngineConfig("local"))
                self.lock = threading.Lock()
                self.active = 0
                self.most = 0
                self.sources = set()

            def translate(self, texts, style, context="", source="auto", target="ja"):
                with self.lock:
                    self.active += 1
                    self.most = max(self.most, self.active)
                time.sleep(0.15)
                with self.lock:
                    self.active -= 1
                return ["翻訳された文章です。" * max(1, len(text) // 20) for text in texts]

        engine = SlowEngine()
        with tempfile.TemporaryDirectory() as folder:
            jobs = []
            for name in ("a", "b", "c"):
                source = Path(folder) / f"{name}.pdf"
                make_paper(source, pages=1)
                jobs.append((source, Path(folder) / "out"))
            worker = pdf_translate_page.PdfTranslateWorker(
                jobs, PdfTranslationOptions(make_bilingual=False), EngineConfig("local")
            )
            finished, done = [], []
            worker.finished.connect(finished.append)
            worker.file_done.connect(lambda position, error: done.append((position, error)))
            with (
                patch.object(pdf_translate_page, "create_engine", return_value=engine),
                patch.object(pdf_translate_page, "translation_cache_path", return_value=None),
            ):
                worker.run()
            self.assertEqual(len(finished), 1)
            results = [result for result, _error in finished[0]]
            self.assertEqual([r.source.name for r in results], ["a.pdf", "b.pdf", "c.pdf"])
            self.assertTrue(all(r.japanese_pdf.exists() for r in results))
            self.assertEqual(sorted(position for position, _ in done), [0, 1, 2])
        self.assertGreaterEqual(engine.most, 2)  # two PDFs were translated at once

    def test_choosing_several_pdfs_hides_the_page_range(self) -> None:
        from PySide6.QtCore import QSettings
        from test_pdf_translate import make_paper

        from movie_slide_notes.gui.pdf_translate_page import EngineSettings, PdfTranslatePage

        with tempfile.TemporaryDirectory() as folder:
            settings = QSettings(str(Path(folder) / "s.ini"), QSettings.IniFormat)
            page = PdfTranslatePage(settings, EngineSettings(settings))
            sources = []
            for name in ("a", "b"):
                sources.append(Path(folder) / f"{name}.pdf")
                make_paper(sources[-1], pages=1)
            self.assertTrue(page.set_sources(sources))
            self.assertEqual(page.sources, sources)
            self.assertTrue(page.range_row.isHidden())
            self.assertIn("2", page.file_name.text())
            self.assertTrue(page.set_sources(sources[:1]))
            self.assertFalse(page.range_row.isHidden())
            del page, settings

    def test_pdfs_can_be_added_removed_and_previewed(self) -> None:
        from PySide6.QtCore import QSettings
        from test_pdf_translate import make_paper

        from movie_slide_notes.gui.pdf_translate_page import EngineSettings, PdfTranslatePage

        with tempfile.TemporaryDirectory() as folder:
            settings = QSettings(str(Path(folder) / "s.ini"), QSettings.IniFormat)
            page = PdfTranslatePage(settings, EngineSettings(settings))
            sources = []
            for name, pages in (("a", 1), ("b", 2), ("c", 1)):
                sources.append(Path(folder) / f"{name}.pdf")
                make_paper(sources[-1], pages=pages)
            self.assertTrue(page.set_sources(sources[:1]))
            self.assertTrue(page.add_sources(sources[1:]))
            self.assertEqual(page.sources, sources)
            self.assertEqual(page.file_list.count(), 3)
            self.assertIsNotNone(page.source_pane.image.pixmap())
            self.assertFalse(page.source_pane.image.pixmap().isNull())

            # The PDF clicked in the list is shown, and its pages can be turned.
            page.file_list.setCurrentRow(1)
            self.assertEqual(page.source_pane.caption.text(), "b.pdf")
            self.assertEqual(page.source_page_label.text(), "1 / 2")
            page.source_next_button.click()
            self.assertEqual(page.source_page_label.text(), "2 / 2")
            self.assertFalse(page.source_next_button.isEnabled())

            page.file_list.clearSelection()
            page.file_list.item(1).setSelected(True)
            page.remove_selected_pdfs()
            self.assertEqual(page.sources, [sources[0], sources[2]])
            del page, settings


class DefaultModelTests(unittest.TestCase):
    def test_gemma_12b_is_the_default_and_a_downloaded_model_is_used_meanwhile(self) -> None:
        from unittest.mock import patch

        from PySide6.QtCore import QSettings

        from movie_slide_notes.gui.pdf_translate_page import EngineSettings
        from movie_slide_notes.pdf_translate import local_llm
        from movie_slide_notes.pdf_translate.engines import DEFAULT_MODELS

        self.assertEqual(local_llm.DEFAULT_MODEL_KEY, "gemma-4-12b")
        self.assertEqual(DEFAULT_MODELS["builtin"], "gemma-4-12b")
        self.assertEqual(local_llm.model_by_key("gemma-4-12b").tier, "おすすめ")

        with tempfile.TemporaryDirectory() as folder:
            settings = QSettings(str(Path(folder) / "s.ini"), QSettings.IniFormat)
            engine_settings = EngineSettings(settings)

            def installed(*keys):
                return patch.object(
                    local_llm, "is_installed", side_effect=lambda model: model.key in keys
                )

            with installed():
                self.assertEqual(engine_settings.local_model_key, "gemma-4-12b")
            with installed("qwen3-4b-2507"):
                self.assertEqual(engine_settings.local_model_key, "qwen3-4b-2507")
            with installed("qwen3-4b-2507", "gemma-4-12b"):
                self.assertEqual(engine_settings.local_model_key, "gemma-4-12b")
            engine_settings.set_local_model("gemma-4-e4b")
            with installed("qwen3-4b-2507", "gemma-4-12b"):
                self.assertEqual(engine_settings.local_model_key, "gemma-4-e4b")
            del settings


if __name__ == "__main__":
    unittest.main()


class FigureLabelAndInkTests(unittest.TestCase):
    def test_tick_numbers_stay_out_of_the_axis_title(self) -> None:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen.canvas import Canvas

        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "axis.pdf"
            canvas = Canvas(str(source), pagesize=A4)
            canvas.setFont("Helvetica", 7)
            for x, label in ((300, "10"), (320, "15")):
                canvas.drawCentredString(x, 500, label)
            canvas.drawCentredString(310, 490, "Eu content (%)")
            canvas.setFont("Times-Roman", 10)
            canvas.drawString(60, 700, PROSE)
            canvas.showPage()
            canvas.save()
            blocks, _chars, _coverage = extract_text_pages(normalize_pdf(source), [0])[0]
        texts = [block.text for block in blocks]
        self.assertIn("Eu content (%)", texts)
        self.assertFalse(any("10" in text or "15" in text for text in texts), texts)

    def test_the_cover_reaches_text_drawn_past_the_reported_box(self) -> None:
        from PIL import Image, ImageDraw

        from movie_slide_notes.pdf_translate.layout import extend_to_ink
        from movie_slide_notes.pdf_translate.models import TextBlock

        image = Image.new("RGB", (300, 200), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 50, 100, 58), fill="black")  # the reported line
        draw.rectangle((102, 50, 110, 58), fill="black")  # "t." drawn past it
        draw.rectangle((180, 50, 260, 58), fill="black")  # the next column
        block = TextBlock(0, 20, 200 - 60, 100, 200 - 48, "text", 10.0)
        extend_to_ink(image, 1.0, 200, block, (1.0, 1.0, 1.0))
        self.assertGreaterEqual(block.x1, 110)
        self.assertLess(block.x1, 180)
