from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QVBoxLayout, QWidget

from movie_slide_notes.gui.i18n import (
    normalize_ui_language,
    set_ui_language,
    translate_widget_tree,
)


class I18nTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        set_ui_language("ja")

    def test_japanese_is_the_default_and_unknown_values_fall_back(self) -> None:
        self.assertEqual(normalize_ui_language(None), "ja")
        self.assertEqual(normalize_ui_language("unknown"), "ja")

    def test_widgets_switch_between_japanese_and_english(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        label = QLabel("新しい処理")
        combo = QComboBox()
        combo.addItem("日本語", "ja")
        combo.addItem("英語", "en")
        layout.addWidget(label)
        layout.addWidget(combo)

        set_ui_language("en")
        translate_widget_tree(root)
        self.assertEqual(label.text(), "New job")
        self.assertEqual(combo.itemText(0), "Japanese")

        set_ui_language("ja")
        translate_widget_tree(root)
        self.assertEqual(label.text(), "新しい処理")
        self.assertEqual(combo.itemText(0), "日本語")
