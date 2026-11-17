from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pypdf import PdfReader
from PySide6.QtWidgets import QApplication, QMessageBox
from test_resume_preflight_review import SEGMENTS, fake_transcribe, make_video, options

from movie_slide_notes.core.preflight import LEVEL_OK, LEVEL_WARNING, PreflightReport
from movie_slide_notes.core.processor import BatchResult, ProcessResult, process_batch
from movie_slide_notes.gui.main_window import MainWindow
from movie_slide_notes.gui.preflight_dialog import PreflightDialog
from movie_slide_notes.gui.slide_review_dialog import SlideReviewDialog


class ResumeUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def window(self, directory: str) -> MainWindow:
        patcher_config = patch(
            "movie_slide_notes.gui.main_window.user_config_dir", return_value=Path(directory)
        )
        patcher_cache = patch(
            "movie_slide_notes.gui.main_window.user_cache_dir",
            return_value=Path(directory) / "cache",
        )
        patcher_config.start()
        patcher_cache.start()
        self.addCleanup(patcher_config.stop)
        self.addCleanup(patcher_cache.stop)
        return MainWindow()

    def test_unfinished_job_shows_resume_banner_and_resumes_with_same_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "lecture.mp4"
            video.write_bytes(b"x")
            window = self.window(directory)
            self.assertTrue(window.resume_banner.isHidden())
            window.videos = [video]
            moment = datetime(2026, 11, 16, 9, 30, 0)
            window._save_pending_job(Path(directory) / "out", moment)
            window.close()

            restored = self.window(directory)
            self.assertFalse(restored.resume_banner.isHidden())
            self.assertIn("lecture.mp4", restored.resume_detail.text())
            with patch.object(MainWindow, "start_processing") as start:
                restored.resume_pending_job()
            start.assert_called_once()
            self.assertEqual(restored.videos, [video.resolve()])
            self.assertEqual(restored._naming_moment, moment)
            self.assertEqual(restored.output_path.text(), str(Path(directory) / "out"))

            restored.handle_finished(
                BatchResult(results=[ProcessResult(video_path=video, output_dir=Path(directory))])
            )
            self.assertIsNone(restored.pending_job)
            self.assertTrue(restored.resume_banner.isHidden())
            restored.close()

    def test_failed_batch_keeps_job_and_retry_reuses_previous_moment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "lecture.mp4"
            video.write_bytes(b"x")
            window = self.window(directory)
            window.videos = [video]
            moment = datetime(2026, 11, 16, 9, 30, 0)
            window._set_output_path(Path(directory) / "out")
            window._save_pending_job(Path(directory) / "out", moment)
            window._last_moment = moment
            window.handle_finished(
                BatchResult(
                    results=[
                        ProcessResult(video_path=video, output_dir=Path(directory), error="boom")
                    ]
                )
            )
            self.assertEqual(window.workflow_state, "error")
            self.assertIsNotNone(window.pending_job)
            with patch.object(MainWindow, "_open_preflight") as preflight:
                window._retry_processing()
            options = preflight.call_args.args[1]
            self.assertEqual(options.output_naming.timestamp, moment)
            self.assertIsNotNone(options.checkpoint_dir)
            window.close()

    def test_disabling_resume_turns_off_checkpoints(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            window = self.window(directory)
            window.resume_enabled_check.setChecked(False)
            self.assertIsNone(window._processing_options(datetime.now()).checkpoint_dir)
            window.close()


class PreflightDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_clean_report_starts_automatically_and_warnings_wait(self) -> None:
        dialog = PreflightDialog()
        clean = PreflightReport()
        clean.add(LEVEL_OK, "disk", "ok")
        dialog.show_report(clean)
        self.assertEqual(dialog.result(), PreflightDialog.Accepted)

        dialog = PreflightDialog()
        warned = PreflightReport()
        warned.add(LEVEL_WARNING, "gpu", "GPUを使えないため、CPUで処理します")
        dialog.show_report(warned)
        self.assertEqual(dialog.result(), 0)
        self.assertTrue(dialog.start_button.isEnabled())
        dialog.close()


class SlideReviewDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_delete_undo_move_and_apply(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = make_video(root / "lecture.avi", [30, 120, 210])
            with patch(
                "movie_slide_notes.core.processor.transcribe_video",
                side_effect=fake_transcribe(SEGMENTS),
            ):
                batch = process_batch([video], root / "out", options())
            original = len(batch.results[0].slides)
            dialog = SlideReviewDialog(batch)
            self.assertEqual(dialog.grid.count(), original)

            dialog.grid.setCurrentRow(0)
            dialog.delete_selected()
            self.assertEqual(dialog.grid.count(), original - 1)
            dialog.undo_delete()
            self.assertEqual(dialog.grid.count(), original)

            dialog.grid.setCurrentRow(0)
            dialog._move_selected(1)
            self.assertEqual(dialog.grid.currentRow(), 1)
            dialog.grid.setCurrentRow(original - 1)
            dialog.delete_selected()
            with patch("movie_slide_notes.gui.slide_review_dialog.move_to_trash") as trash:
                trash.side_effect = lambda path: path.unlink()
                dialog.apply()
            self.assertEqual(dialog.result(), SlideReviewDialog.Accepted)
            self.assertEqual(len(batch.results[0].slides), original - 1)
            self.assertEqual(len(PdfReader(batch.results[0].pdf_path).pages), original - 1)

    def test_cancel_with_changes_asks_before_discarding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = make_video(root / "lecture.avi", [30, 210])
            batch = process_batch(
                [video],
                root / "out",
                options().__class__(
                    operations=options().operations.__class__(True, True, False, False, False),
                    slide_config=options().slide_config,
                ),
            )
            dialog = SlideReviewDialog(batch)
            dialog.grid.setCurrentRow(0)
            dialog.delete_selected()
            with patch.object(QMessageBox, "question", return_value=QMessageBox.No):
                dialog.reject()
            self.assertTrue(dialog.dirty)
            self.assertEqual(dialog.result(), 0)
            with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
                dialog.reject()
            self.assertTrue(all(slide.path.exists() for slide in batch.results[0].slides))


if __name__ == "__main__":
    unittest.main()
