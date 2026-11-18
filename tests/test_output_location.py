from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from test_crop_timeline import slide_frame, write_video

from movie_slide_notes.core.processor import OperationOptions, ProcessingOptions, process_batch
from movie_slide_notes.gui.main_window import MainWindow
from movie_slide_notes.gui.settings_widget import ProcessingSettingsWidget, animate_panel


def options(mode: str) -> ProcessingOptions:
    return ProcessingOptions(
        operations=OperationOptions(True, True, False, False, mode == "combined"),
        output_mode=mode,
        output_next_to_input=True,
    )


class NextToInputTests(unittest.TestCase):
    def test_per_video_outputs_go_next_to_each_video(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a").mkdir()
            (root / "b").mkdir()
            first = write_video(root / "a" / "one.avi", [slide_frame("Intro", "left", 30)])
            second = write_video(root / "b" / "two.avi", [slide_frame("Goal", "left", 200)])
            elsewhere = root / "unused"
            batch = process_batch([first, second], elsewhere, options("per_video"))
            self.assertEqual(batch.results[0].output_dir.parent, root / "a")
            self.assertEqual(batch.results[1].output_dir.parent, root / "b")
            self.assertTrue(batch.results[0].pdf_path.is_file())
            self.assertFalse(elsewhere.exists())

    def test_combined_run_folder_is_created_next_to_the_first_video(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = write_video(root / "lecture-01.avi", [slide_frame("Intro", "left", 30)])
            batch = process_batch([video], root / "unused", options("combined"))
            self.assertEqual(batch.output_dir.parent, root)
            self.assertTrue(batch.combined_pdf_path.is_file())
            self.assertFalse((root / "unused").exists())


class OutputLocationUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def window(self, directory: str) -> MainWindow:
        for target in ("user_config_dir", "user_cache_dir"):
            patcher = patch(
                f"movie_slide_notes.gui.main_window.{target}", return_value=Path(directory)
            )
            patcher.start()
            self.addCleanup(patcher.stop)
        return MainWindow()

    def test_next_to_input_is_on_by_default_and_kept_in_sync(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            window = self.window(directory)
            self.assertTrue(window.next_to_input_check.isChecked())
            self.assertTrue(window.settings_next_to_input_check.isChecked())
            self.assertFalse(window.output_path.isEnabled())

            video = Path(directory) / "clips" / "lecture.mp4"
            video.parent.mkdir()
            video.write_bytes(b"x")
            window.add_paths([video])
            self.assertEqual(window._resolved_output(), video.resolve().parent)
            self.assertIn(str(video.resolve().parent), window.output_location_hint.text())
            self.assertTrue(window._processing_options(datetime.now()).output_next_to_input)

            window.settings_next_to_input_check.setChecked(False)
            self.assertFalse(window.next_to_input_check.isChecked())
            self.assertTrue(window.output_path.isEnabled())
            self.assertEqual(window._resolved_output(), video.resolve().parent / "Lectavo_output")
            window.settings.sync()
            window.close()

            restored = self.window(directory)
            self.assertFalse(restored.next_to_input_check.isChecked())
            restored.close()


class DisclosureAnimationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_panel_expands_and_collapses_smoothly(self) -> None:
        widget = ProcessingSettingsWidget()
        widget.resize(600, 400)
        widget.show()
        panel = widget.slide_detail_panel
        widget.slide_detail_button.setChecked(True)
        self.assertTrue(panel.isVisible())
        self.assertLess(panel.maximumHeight(), panel.sizeHint().height())
        QTest.qWait(320)
        self.assertTrue(panel.isVisible())
        self.assertGreater(panel.maximumHeight(), 10000)
        widget.slide_detail_button.setChecked(False)
        self.assertTrue(panel.isVisible())  # still animating out
        QTest.qWait(260)
        self.assertFalse(panel.isVisible())
        widget.close()

    def test_rapid_toggle_ends_in_the_last_state(self) -> None:
        holder = QWidget()
        panel = QWidget(holder)
        holder.show()
        for state in (True, False, True):
            animate_panel(panel, state)
        QTest.qWait(320)
        self.assertTrue(panel.isVisible())
        holder.close()


if __name__ == "__main__":
    unittest.main()
