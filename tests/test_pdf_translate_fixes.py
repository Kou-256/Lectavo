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


class CompareViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_scrolling_past_a_page_turns_it_and_the_spin_box_has_no_arrows(self) -> None:
        from PySide6.QtWidgets import QAbstractSpinBox
        from test_pdf_translate import make_paper

        from movie_slide_notes.gui import compare_viewer
        from movie_slide_notes.gui.compare_viewer import CompareViewer, PdfSide

        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "paper.pdf"
            make_paper(source, pages=3)
            viewer = CompareViewer(PdfSide(source), PdfSide(source), pages=3)
            viewer.resize(900, 700)
            viewer.show()
            self.app.processEvents()
            viewer.go_to(0)
            self.assertEqual(viewer.page_spin.buttonSymbols(), QAbstractSpinBox.NoButtons)

            viewport = viewer.panes[0].scroll.viewport()
            bar = viewer.panes[0].scroll.verticalScrollBar()
            bar.setValue(bar.maximum())
            self.assertTrue(viewer._turn_page_by_wheel(viewport, -120))
            self.assertEqual(viewer.index, 1)
            self.assertEqual(bar.value(), 0)

            # A second flick right away does not skip a page.
            bar.setValue(bar.maximum())
            viewer._turn_page_by_wheel(viewport, -120)
            self.assertEqual(viewer.index, 1)

            viewer._last_turn -= compare_viewer.PAGE_TURN_PAUSE
            bar.setValue(bar.minimum())
            self.assertTrue(viewer._turn_page_by_wheel(viewport, 120))
            self.assertEqual(viewer.index, 0)
            self.app.processEvents()
            self.assertEqual(bar.value(), bar.maximum())

            # Within a page, the wheel scrolls as usual.
            if bar.maximum() > 0:
                bar.setValue(bar.maximum() // 2)
                self.assertFalse(viewer._turn_page_by_wheel(viewport, -120))
            viewer.close()


if __name__ == "__main__":
    unittest.main()
