from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from movie_slide_notes.core.processor import BatchResult, ProcessResult
from movie_slide_notes.gui.main_window import MainWindow, set_combo_data


class OutputModeUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_combined_is_default_and_per_video_choice_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_dir = Path(directory)
            with patch(
                "movie_slide_notes.gui.main_window.user_config_dir",
                return_value=config_dir,
            ):
                first = MainWindow()
                self.assertEqual(first.output_mode_combo.currentData(), "combined")
                set_combo_data(first.output_mode_combo, "per_video")
                first._save_settings()
                first.settings.sync()
                first.close()

                second = MainWindow()
                self.assertEqual(second.output_mode_combo.currentData(), "per_video")
                second.close()

    def test_cancel_requires_confirmation_before_stopping_worker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "movie_slide_notes.gui.main_window.user_config_dir",
                return_value=Path(directory),
            ):
                window = MainWindow()
                worker = MagicMock()
                window.worker = worker

                with patch.object(QMessageBox, "question", return_value=QMessageBox.No):
                    window.cancel_processing()
                worker.cancel.assert_not_called()

                with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
                    window.cancel_processing()
                worker.cancel.assert_called_once_with()
                self.assertEqual(window.running_stage.text(), "キャンセル中")
                self.assertEqual(window.running_detail.text(), "実行中の処理を停止しています。")

                window.worker = None
                window.close()

    def test_cancelled_batch_returns_to_empty_home(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "movie_slide_notes.gui.main_window.user_config_dir",
                return_value=Path(directory),
            ):
                window = MainWindow()
                video = Path(directory) / "講義-1.mp4"
                window.videos = [video]
                window.video_statuses[video] = "処理中"
                window._set_workflow_state("running")
                batch = BatchResult(
                    results=[
                        ProcessResult(
                            video_path=video,
                            output_dir=Path(directory),
                            cancelled=True,
                        )
                    ]
                )

                window.handle_finished(batch)

                self.assertEqual(window.workflow_state, "empty")
                self.assertEqual(window.videos, [])
                self.assertIs(window.page_stack.currentWidget(), window.home_page)
                window.close()

    def test_cancel_request_wins_when_worker_finishes_at_the_same_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "movie_slide_notes.gui.main_window.user_config_dir",
                return_value=Path(directory),
            ):
                window = MainWindow()
                video = Path(directory) / "講義-1.mp4"
                window.videos = [video]
                window._cancel_requested = True
                window._set_workflow_state("running")

                window.handle_finished(BatchResult(results=[]))

                self.assertEqual(window.workflow_state, "empty")
                self.assertEqual(window.videos, [])
                self.assertIs(window.page_stack.currentWidget(), window.home_page)
                window.close()

    def test_individual_history_entry_can_be_removed_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_dir = Path(directory)
            with patch(
                "movie_slide_notes.gui.main_window.user_config_dir",
                return_value=config_dir,
            ):
                window = MainWindow()
                window.history = [
                    {
                        "name": "lecture-01.mp4",
                        "path": str(config_dir / "lecture-01.mp4"),
                        "output": str(config_dir / "output-01"),
                        "status": "完了",
                        "time": "2026-07-15 10:00",
                    },
                    {
                        "name": "lecture-02.mp4",
                        "path": str(config_dir / "lecture-02.mp4"),
                        "output": str(config_dir / "output-02"),
                        "status": "完了",
                        "time": "2026-07-15 11:00",
                    },
                ]
                window._delete_history_entry(str(config_dir / "lecture-01.mp4"))
                window.settings.sync()

                self.assertEqual([entry["name"] for entry in window.history], ["lecture-02.mp4"])
                self.assertFalse(window.clear_history_button.isHidden())
                window.close()

                restored = MainWindow()
                self.assertEqual(
                    [entry["name"] for entry in restored.history],
                    ["lecture-02.mp4"],
                )
                restored.close()

    def test_clear_history_requires_confirmation_and_preserves_empty_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "movie_slide_notes.gui.main_window.user_config_dir",
                return_value=Path(directory),
            ):
                window = MainWindow()
                window.history = [
                    {
                        "name": "lecture.mp4",
                        "path": str(Path(directory) / "lecture.mp4"),
                        "output": str(Path(directory) / "output"),
                        "status": "完了",
                        "time": "2026-07-15 10:00",
                    }
                ]
                window._refresh_recent()
                window._refresh_history()

                with patch.object(QMessageBox, "question", return_value=QMessageBox.No):
                    window.clear_history()
                self.assertEqual(len(window.history), 1)

                with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
                    window.clear_history()
                self.assertEqual(window.history, [])
                self.assertTrue(window.clear_history_button.isHidden())
                self.assertEqual(window.recent_layout.count(), 1)
                self.assertEqual(window.history_layout.count(), 1)
                window.close()


if __name__ == "__main__":
    unittest.main()
