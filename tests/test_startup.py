from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from movie_slide_notes.gui.i18n import set_ui_language
from movie_slide_notes.gui.main_window import MainWindow, set_combo_data
from movie_slide_notes.gui.splash import (
    SPLASH_HEIGHT,
    SPLASH_WIDTH,
    show_startup_splash,
    theme_is_dark,
)
from movie_slide_notes.paths import app_icon_path


class SplashTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_splash_module_is_light_and_does_not_pull_in_the_main_window(self) -> None:
        code = (
            "import sys\n"
            "import movie_slide_notes.gui.splash\n"
            "heavy = [m for m in ('movie_slide_notes.gui.main_window', 'movie_slide_notes.core.processor',"
            " 'numpy', 'cv2') if m in sys.modules]\n"
            "assert not heavy, heavy\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            timeout=60,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))

    def test_splash_is_shown_with_the_expected_size_for_both_themes(self) -> None:
        for dark in (True, False):
            splash = show_startup_splash(self.app, app_icon_path(), dark, "起動しています…")
            try:
                self.assertTrue(splash.isVisible())
                pixmap = splash.pixmap()
                self.assertEqual(
                    (
                        round(pixmap.width() / pixmap.devicePixelRatio()),
                        round(pixmap.height() / pixmap.devicePixelRatio()),
                    ),
                    (SPLASH_WIDTH, SPLASH_HEIGHT),
                )
                # The background matches the theme, so there is no bright flash in dark mode.
                corner = pixmap.toImage().pixelColor(4, 4)
                self.assertEqual(corner.lightness() < 128, dark)
            finally:
                splash.close()

    def test_theme_choice_decides_the_splash_colors(self) -> None:
        self.assertTrue(theme_is_dark("dark"))
        self.assertFalse(theme_is_dark("light"))
        with patch("movie_slide_notes.gui.splash.system_prefers_dark", return_value=True):
            self.assertTrue(theme_is_dark("system"))
        with patch("movie_slide_notes.gui.splash.system_prefers_dark", return_value=False):
            self.assertFalse(theme_is_dark("system"))


class LanguageSwitchAfterFastStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        set_ui_language("ja")

    def window(self, directory: str) -> MainWindow:
        for target in ("user_config_dir", "user_cache_dir"):
            patcher = patch(
                f"movie_slide_notes.gui.main_window.{target}", return_value=Path(directory)
            )
            patcher.start()
            self.addCleanup(patcher.stop)
        return MainWindow()

    def test_startup_in_japanese_leaves_text_alone_and_switching_still_translates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            window = self.window(directory)
            self.assertEqual(window.start_button.text(), "処理を開始")

            set_combo_data(window.settings_widget.ui_language_combo, "en")
            self.assertEqual(window.start_button.text(), "Start processing")
            self.assertEqual(window.add_media_button.text(), "Add files")

            set_combo_data(window.settings_widget.ui_language_combo, "ja")
            self.assertEqual(window.start_button.text(), "処理を開始")
            window.close()

    def test_startup_in_english_translates_everything(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = self.window(directory)
            set_combo_data(first.settings_widget.ui_language_combo, "en")
            first.settings.sync()
            first.close()
            set_ui_language("en")

            restored = self.window(directory)
            self.assertEqual(restored.start_button.text(), "Start processing")
            restored.close()


class DeferredWorkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_checkpoint_folder_is_scanned_when_settings_opens_not_at_startup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for target in ("user_config_dir", "user_cache_dir"):
                patcher = patch(
                    f"movie_slide_notes.gui.main_window.{target}", return_value=Path(directory)
                )
                patcher.start()
                self.addCleanup(patcher.stop)
            with patch(
                "movie_slide_notes.core.checkpoint.CheckpointStore.size_bytes", return_value=2048
            ) as scan:
                window = MainWindow()
                scan.assert_not_called()
                window._select_page("settings")
                scan.assert_called_once()
                self.assertIn("MB", window.checkpoint_size_label.text())
                window.close()


if __name__ == "__main__":
    unittest.main()
