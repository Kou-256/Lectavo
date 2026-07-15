from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from movie_slide_notes.gui.settings_widget import ProcessingSettingsWidget


class SettingsMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def settings(self, directory: str) -> QSettings:
        return QSettings(str(Path(directory) / "settings.ini"), QSettings.IniFormat)

    def write_legacy_fast_preset(self, settings: QSettings) -> None:
        values = {
            "preset": "conservative",
            "interval": 1.0,
            "min_gap": 7.0,
            "compare_width": 320,
            "diff_threshold": 30,
            "min_component_area": 1500,
            "diff_ratio_threshold": 0.04,
            "ssim_threshold": 0.92,
            "region_x": 0.75,
            "region_y": 0.80,
            "region_w": 0.25,
            "region_h": 0.20,
            "page_diff_ratio_threshold": 0.025,
            "page_min_component_area": 8,
            "phash_threshold": 8,
            "duplicate_ssim_threshold": 0.97,
        }
        for key, value in values.items():
            settings.setValue(key, value)
        settings.sync()

    def test_unchanged_legacy_fast_preset_migrates_to_standard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(directory)
            self.write_legacy_fast_preset(settings)
            widget = ProcessingSettingsWidget()

            widget.load_settings(settings)

            self.assertEqual(widget.preset_combo.currentData(), "standard")
            self.assertEqual(widget.slide_config().min_gap, 1.0)
            self.assertEqual(widget.slide_config().min_component_area, 800)

    def test_customized_fast_preset_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(directory)
            self.write_legacy_fast_preset(settings)
            settings.setValue("interval", 0.5)
            settings.sync()
            widget = ProcessingSettingsWidget()

            widget.load_settings(settings)

            self.assertEqual(widget.preset_combo.currentData(), "conservative")
            self.assertEqual(widget.slide_config().interval, 0.5)
            self.assertEqual(widget.slide_config().min_gap, 7.0)

    def test_ui_language_defaults_to_japanese_and_is_saved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(directory)
            widget = ProcessingSettingsWidget()
            widget.load_settings(settings)

            self.assertEqual(widget.ui_language_combo.currentData(), "ja")
            widget.ui_language_combo.setCurrentIndex(widget.ui_language_combo.findData("en"))
            widget.save_settings(settings)
            settings.sync()
            self.assertEqual(settings.value("ui_language"), "en")


if __name__ == "__main__":
    unittest.main()
