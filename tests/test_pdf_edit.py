"""Selecting text in the comparison view, and editing translated PDFs."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from test_pdf_translate import FakeEngine, make_paper

from movie_slide_notes.pdf_translate import edits
from movie_slide_notes.pdf_translate.models import PdfTranslationOptions
from movie_slide_notes.pdf_translate.pipeline import translate_pdf


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _page_text(path: Path, index: int = 0) -> str:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(path.read_bytes())
    try:
        textpage = document[index].get_textpage()
        return textpage.get_text_range()
    finally:
        document.close()


class EditTestCase(unittest.TestCase):
    """Translates a small paper with the edit data kept in a temporary folder."""

    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        patcher = patch.object(edits, "_root", return_value=self.root / "edits")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.source = self.root / "paper.pdf"
        make_paper(self.source, pages=2)
        self.result = translate_pdf(
            self.source,
            self.root / "out",
            PdfTranslationOptions(),
            FakeEngine(),
            "fake",
            edit_store=True,
        )

    def tearDown(self) -> None:
        self.folder.cleanup()


class EditDataTests(EditTestCase):
    def test_the_translation_is_kept_as_editable_items(self) -> None:
        document = edits.find(self.result.japanese_pdf)
        self.assertIsNotNone(document)
        self.assertEqual(len(document.pages), 2)
        texts = [item for item in document.pages[0].items if item.kind == "text"]
        self.assertTrue(texts)
        self.assertTrue(all(item.background is not None for item in texts))
        self.assertTrue(document.original_path.exists())
        # The bilingual PDF finds the same data.
        other = edits.find(self.result.bilingual_pdf)
        self.assertEqual(other.folder, document.folder)

    def test_writing_the_document_again_applies_the_changes(self) -> None:
        document = edits.find(self.result.japanese_pdf)
        document.pages[0].items[0].text = "書き換えた見出し"
        document.pages[1].items.clear()
        edits.write(document)
        self.assertIn("書き換えた見出し", _page_text(self.result.japanese_pdf, 0))
        self.assertNotIn("希土類", _page_text(self.result.japanese_pdf, 1).replace("\n", ""))


class SelectionTests(EditTestCase):
    def _viewer(self):
        from movie_slide_notes.gui.compare_viewer import CompareViewer, PdfSide

        _app()
        viewer = CompareViewer(PdfSide(self.source), PdfSide(self.result.japanese_pdf), pages=2)
        viewer.resize(1200, 800)
        viewer.show()
        _app().processEvents()
        self.addCleanup(viewer.close)
        return viewer

    def test_hidden_original_text_is_not_part_of_the_translated_page(self) -> None:
        viewer = self._viewer()
        shown = "".join(char.text for char in viewer.sources[1].chars(0))
        self.assertIn("希土類", shown)
        self.assertNotIn("Rare-earth", shown)
        # The formula was not translated and stays selectable.
        self.assertIn("I0", shown)
        original = "".join(char.text for char in viewer.sources[0].chars(0))
        self.assertIn("Rare-earth", original)

    def test_dragging_selects_text_and_ctrl_c_copies_it(self) -> None:
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest

        viewer = self._viewer()
        chars = viewer.sources[0].chars(0)
        word = "".join(char.text for char in chars).index("Rare-earth")
        label = viewer.panes[0].pages[0]
        scale = label.scale()

        def point(position: int, right: bool = False) -> QPoint:
            left, top, end, bottom = chars[position].rect
            x = (end - 0.3 if right else left + 0.3) * scale
            return QPoint(round(x), round((top + bottom) / 2 * scale))

        start, finish = point(word), point(word + len("Rare-earth") - 1, right=True)
        QTest.mousePress(label, Qt.LeftButton, Qt.NoModifier, start)
        QTest.mouseMove(label, finish)
        QTest.mouseRelease(label, Qt.LeftButton, Qt.NoModifier, finish)
        self.assertEqual(viewer.selected_text(), "Rare-earth")
        self.assertTrue(label.highlights)
        viewer.copy_selection()
        from PySide6.QtWidgets import QApplication

        self.assertEqual(QApplication.clipboard().text(), "Rare-earth")

        # A click without dragging clears the selection.
        QTest.mouseClick(label, Qt.LeftButton, Qt.NoModifier, start)
        self.assertEqual(viewer.selected_text(), "")

    def test_a_page_can_be_selected_whole(self) -> None:
        viewer = self._viewer()
        viewer.select_page(1, 0)
        text = viewer.selected_text()
        self.assertIn("希土類", text)
        self.assertNotIn("Rare-earth", text)

    def test_dragging_into_the_next_page_selects_across_pages(self) -> None:
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest

        viewer = self._viewer()
        # Small enough that both pages are in view (dragging past the view scrolls it).
        viewer.zoom, viewer.fit_mode = 0.4, ""
        viewer._apply_zoom()
        _app().processEvents()
        pane = viewer.panes[0]
        first, second = pane.pages[0], pane.pages[1]
        chars = viewer.sources[0].chars(1)
        text = "".join(char.text for char in chars)
        owners = [i for i, char in enumerate(chars) for _ in char.text]
        target = owners[text.index("efficiency") + len("efficiency") - 1]
        left, top, right, bottom = chars[target].rect
        scale = second.scale()
        # The pressed page keeps the mouse: the end point is given relative to it.
        over_second = first.mapFromGlobal(
            second.mapToGlobal(QPoint(round(right * scale), round((top + bottom) / 2 * scale)))
        )
        start = QPoint(5, 5)  # the top of page 1: its first character
        QTest.mousePress(first, Qt.LeftButton, Qt.NoModifier, start)
        QTest.mouseMove(first, over_second)
        QTest.mouseRelease(first, Qt.LeftButton, Qt.NoModifier, over_second)
        selected = viewer.selected_text()
        self.assertTrue(selected.startswith("Luminescence"), selected[:40])
        self.assertTrue(selected.endswith("efficiency"), selected[-40:])
        self.assertEqual(selected.count("Luminescence Properties"), 2)
        self.assertTrue(first.highlights)
        self.assertTrue(second.highlights)

        viewer.select_all(1)
        everything = viewer.selected_text()
        self.assertGreaterEqual(everything.count("希土類"), 2)  # both pages
        self.assertNotIn("Rare-earth", everything)


class EditorTests(EditTestCase):
    def _editor(self):
        from movie_slide_notes.gui.pdf_editor import PdfEditor

        _app()
        editor = PdfEditor(edits.find(self.result.japanese_pdf))
        editor.resize(1200, 800)
        editor.show()
        _app().processEvents()
        self.addCleanup(lambda: setattr(editor, "dirty", False) or editor.close())
        return editor

    def test_text_is_rewritten_moved_and_saved(self) -> None:
        editor = self._editor()
        first = next(i for i, item in enumerate(editor.page.items) if item.kind == "text")
        editor.select(first)
        editor.text_edit.setPlainText("編集した訳文 Eu<sub>2</sub>O<sub>3</sub>")
        before = (editor.item.x0, editor.item.y0)
        editor.nudge(10, -5)
        self.assertEqual((editor.item.x0, editor.item.y0), (before[0] + 10, before[1] - 5))
        self.assertTrue(editor.dirty)
        self.assertTrue(editor.save())
        self.assertFalse(editor.dirty)
        text = _page_text(self.result.japanese_pdf, 0)
        self.assertIn("編集した訳文", text)
        self.assertIn("Eu2O3", text.replace(" ", ""))
        # The bilingual PDF is written again too, and the edits are kept.
        self.assertIn("編集した訳文", _page_text(self.result.bilingual_pdf, 0))
        kept = edits.find(self.result.japanese_pdf)
        self.assertIn("編集した訳文", kept.pages[0].items[first].text)

    def test_text_and_images_can_be_added_cut_out_and_undone(self) -> None:
        editor = self._editor()
        count = len(editor.page.items)
        editor.create_item("text", 60, 300, 300, 340)
        editor.text_edit.setPlainText("追加したテキスト")
        self.assertEqual(len(editor.page.items), count + 1)

        # Cutting out a figure hides it where it was and adds a movable image.
        editor.create_item("cut", 200, 600, 400, 680)
        self.assertEqual(editor.page.items[0].kind, "erase")
        image = editor.item
        self.assertEqual(image.kind, "image")
        self.assertTrue((editor.document.images_dir / image.image).exists())
        editor.nudge(0, -200)

        editor.create_item("erase", 60, 100, 200, 140)
        self.assertEqual(editor.item.kind, "erase")
        editor.undo()
        self.assertFalse(any(i.kind == "erase" and i.y0 == 100 for i in editor.page.items))

        self.assertTrue(editor.save())
        self.assertIn("追加したテキスト", _page_text(self.result.japanese_pdf, 0))
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(self.result.japanese_pdf.read_bytes())
        kinds = [obj.type for obj in document[0].get_objects(max_depth=4)]
        document.close()
        self.assertIn(pdfium.raw.FPDF_PAGEOBJ_IMAGE, kinds)

    def test_deleting_a_translation_shows_the_original_again(self) -> None:
        editor = self._editor()
        while editor.page.items:
            editor.select(0)
            editor.delete_selected()
        self.assertTrue(editor.save())
        text = _page_text(self.result.japanese_pdf, 0)
        self.assertIn("Rare-earth", text)
        self.assertNotIn("希土類", text)


if __name__ == "__main__":
    unittest.main()
