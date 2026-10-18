from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from movie_slide_notes.core.output_naming import OutputNamingConfig, common_video_name, output_stem
from movie_slide_notes.gui.main_window import MainWindow, set_combo_data


class OutputNamingTests(unittest.TestCase):
    def test_common_name_removes_only_sequence_suffix(self) -> None:
        self.assertEqual(
            common_video_name([Path("講義-13-2.mp4"), Path("講義-13-10.mp4")]), "講義-13"
        )
        self.assertEqual(
            common_video_name([Path("lecture10.mp4"), Path("lecture11.mp4")]), "lecture"
        )
        self.assertEqual(common_video_name([Path("講義 動画.mp4")]), "講義 動画")
        self.assertEqual(common_video_name([Path("講義 (入門).mp4")]), "講義 (入門)")
        self.assertEqual(common_video_name([Path("apple.mp4"), Path("banana.mp4")]), "Lectavo")

    def test_safe_custom_names_and_consistent_timestamp(self) -> None:
        config = OutputNamingConfig(
            "custom", "復習:資料/第3回", "datetime", datetime(2026, 10, 18, 15, 4, 5)
        )
        self.assertEqual(
            output_stem([], config, combined=True), "復習_資料_第3回_2026-10-18_15-04-05"
        )
        self.assertEqual(
            output_stem([], OutputNamingConfig("custom", "CON"), combined=True), "_CON"
        )
        with self.assertRaises(ValueError):
            output_stem([], OutputNamingConfig("custom", "  "), combined=True)

    def test_individual_custom_names_keep_source_name_for_uniqueness(self) -> None:
        config = OutputNamingConfig("custom", "復習")
        first = output_stem([Path("講義01.mp4")], config, combined=False)
        second = output_stem([Path("講義02.mp4")], config, combined=False)
        self.assertEqual(first, "復習_講義01")
        self.assertNotEqual(first, second)

    def test_gui_preview_and_settings_are_restored(self) -> None:
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "movie_slide_notes.gui.main_window.user_config_dir", return_value=Path(directory)
            ):
                window = MainWindow()
                window.videos = [Path("講義-13-2.mp4"), Path("講義-13-10.mp4")]
                set_combo_data(window.naming_mode, "common")
                self.assertIn("講義-13_slides.pdf", window.naming_preview.text())
                set_combo_data(window.naming_mode, "custom")
                window.custom_output_name.setText("復習資料")
                self.assertIn("復習資料_transcript.txt", window.naming_preview.text())
                set_combo_data(window.naming_date, "datetime")
                window.settings.sync()
                window.close()
                restored = MainWindow()
                self.assertEqual(restored.naming_mode.currentData(), "custom")
                self.assertEqual(restored.naming_date.currentData(), "datetime")
                self.assertEqual(restored.custom_output_name.text(), "復習資料")
                restored.close()
        app.processEvents()


if __name__ == "__main__":
    unittest.main()
