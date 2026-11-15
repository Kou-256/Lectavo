from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
from PySide6.QtCore import QObject, QPoint, Qt, Signal
from PySide6.QtGui import QImage
from PySide6.QtNetwork import QNetworkReply
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from movie_slide_notes.core.processor import (
    ProcessingOptions,
    discover_videos,
    process_batch,
)
from movie_slide_notes.gui.main_window import MainWindow
from movie_slide_notes.gui.region_selector import RegionCanvas
from movie_slide_notes.gui.update_manager import UpdateManager
from movie_slide_notes.slide_detector import SlideDetectionConfig, SlideDetector
from movie_slide_notes.transcriber import TranscriptionResult, TranscriptionSegment
from movie_slide_notes.updates import UpdateInfo, parse_release


class Reply(QObject):
    readyRead = Signal()
    finished = Signal()

    def __init__(self, data):
        super().__init__()
        self.data = data
        self.code = QNetworkReply.NoError

    def readAll(self):
        data, self.data = self.data, b""
        return data

    def error(self):
        return self.code

    def abort(self):
        self.code = QNetworkReply.OperationCanceledError
        self.finished.emit()


class NewWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def info(self, content=b"installer"):
        return UpdateInfo(
            "0.4.0",
            "https://github.com/Kou-256/Lectavo/releases/download/v0.4.0/Lectavo-Setup.exe",
            len(content),
            hashlib.sha256(content).hexdigest(),
        )

    def test_release_version_and_trusted_asset_validation(self):
        info = self.info()
        release = {
            "tag_name": "v0.4.0",
            "assets": [
                {
                    "name": "Lectavo-Setup.exe",
                    "size": info.size,
                    "digest": "sha256:" + info.sha256,
                    "browser_download_url": info.url,
                }
            ],
        }
        self.assertEqual(parse_release(release, "0.3.0"), info)
        self.assertIsNone(parse_release(release, "0.4.0"))
        self.assertIsNone(parse_release({**release, "prerelease": True}, "0.3.0"))
        release["assets"][0]["browser_download_url"] = "https://example.com/installer.exe"
        with self.assertRaises(ValueError):
            parse_release(release, "0.3.0")

    def test_update_download_checks_hash_and_cancel_never_commits(self):
        for content, cancel in [(b"installer", False), (b"corrupted", False), (b"partial", True)]:
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                manager = UpdateManager()
                reply = Reply(content)
                completed, failed, cancelled = [], [], []
                manager.downloaded.connect(completed.append)
                manager.failed.connect(failed.append)
                manager.cancelled.connect(lambda: cancelled.append(True))

                def request(url):
                    manager.reply = reply
                    return reply

                with (
                    patch.object(manager, "_request", side_effect=request),
                    patch(
                        "movie_slide_notes.gui.update_manager.user_cache_dir",
                        return_value=Path(directory),
                    ),
                ):
                    manager.download(self.info())
                    if cancel:
                        manager.cancel()
                    else:
                        reply.readyRead.emit()
                        reply.finished.emit()
                if content == b"installer":
                    self.assertEqual(Path(completed[0]).read_bytes(), content)
                else:
                    self.assertFalse(completed)
                    self.assertFalse((Path(directory) / "0.4.0" / "Lectavo-Setup.exe").exists())
                    self.assertTrue(cancelled if cancel else failed)

    def test_declining_update_never_downloads_or_executes(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "movie_slide_notes.gui.main_window.user_config_dir", return_value=Path(directory)
            ),
        ):
            window = MainWindow()
            try:
                with (
                    patch.object(QMessageBox, "question", return_value=QMessageBox.No),
                    patch.object(window.updater, "download") as download,
                ):
                    window._update_available(self.info())
                    download.assert_not_called()
                with (
                    patch.object(QMessageBox, "question", return_value=QMessageBox.No),
                    patch("movie_slide_notes.gui.main_window.QProcess.startDetached") as launch,
                ):
                    window._update_downloaded("installer.exe")
                    launch.assert_not_called()
            finally:
                window.close()

    def test_audio_batch_skips_slides_and_retains_full_duration_offsets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = [root / f"audio-{i}{ext}" for i, ext in enumerate((".mp3", ".m4a", ".wav"))]
            for path in inputs:
                path.touch()
            self.assertEqual(len(discover_videos(root)), 3)

            def transcribe(**kwargs):
                base = Path(kwargs["output_dir"]) / kwargs["video_stem"]
                txt, srt = base.with_suffix(".txt"), base.with_suffix(".srt")
                txt.write_text("audio test")
                srt.write_text("audio test")
                return TranscriptionResult(
                    [TranscriptionSegment(0, 1, "Audio test")], txt, srt, None, duration_seconds=5
                )

            with (
                patch("movie_slide_notes.core.processor.transcribe_video", side_effect=transcribe),
                patch.object(
                    SlideDetector,
                    "extract_slides",
                    side_effect=AssertionError("Audio must not use slide detection"),
                ),
            ):
                result = process_batch(
                    inputs, root / "output", ProcessingOptions(output_mode="combined")
                )
            self.assertEqual(result.success_count, 3)
            self.assertIsNone(result.combined_pdf_path)
            self.assertIn("00:00:10,000", result.combined_srt_path.read_text())
            self.assertIn("Audio test", result.combined_markdown_path.read_text())
            self.assertEqual(result.combined_markdown_path.read_text().count("## Transcript"), 3)

    def test_canvas_letterbox_mapping_and_real_cropped_png(self):
        canvas = RegionCanvas()
        canvas.resize(600, 400)
        canvas.image = QImage(800, 400, QImage.Format_RGB888)
        canvas.image.fill(Qt.white)
        canvas.show()
        QTest.mousePress(canvas, Qt.LeftButton, pos=QPoint(150, 125))
        QTest.mouseMove(canvas, QPoint(450, 275))
        QTest.mouseRelease(canvas, Qt.LeftButton, pos=QPoint(450, 275))
        for actual, expected in zip(canvas.slide, (0.25, 0.25, 0.5, 0.5)):
            self.assertAlmostEqual(actual, expected)
        canvas.close()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.avi"
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 1, (320, 180))
            writer.write(np.full((180, 320, 3), 220, dtype=np.uint8))
            writer.write(np.full((180, 320, 3), 220, dtype=np.uint8))
            writer.release()
            slides, _ = SlideDetector(
                SlideDetectionConfig(slide_region=canvas.slide)
            ).extract_slides(path, Path(directory) / "slides", "sample", show_progress=False)
            self.assertEqual(cv2.imread(str(slides[0].path)).shape[:2], (90, 160))

    def test_gui_startup_does_not_import_processing_libraries(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys;from movie_slide_notes.gui.main_window import MainWindow;from PySide6.QtWidgets import QApplication;app=QApplication([]);w=MainWindow();assert not any(name in sys.modules for name in ['numpy','cv2','skimage','ctranslate2']);w.close()",
            ],
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))

    def test_second_launch_activates_existing_process(self):
        from PySide6.QtNetwork import QLocalSocket

        with tempfile.TemporaryDirectory() as directory:
            env = {
                **os.environ,
                "APPDATA": directory,
                "LOCALAPPDATA": directory,
                "QT_QPA_PLATFORM": "offscreen",
            }
            identity = (
                "Lectavo-"
                + hashlib.sha256(str(Path(directory) / "Lectavo").encode("utf-8")).hexdigest()[:16]
            )
            first = subprocess.Popen(
                [sys.executable, "app.py"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            try:
                connected = False
                for _ in range(60):
                    socket = QLocalSocket()
                    socket.connectToServer(identity)
                    if socket.waitForConnected(50):
                        socket.disconnectFromServer()
                        connected = True
                        break
                    time.sleep(0.05)
                self.assertTrue(connected)
                second = subprocess.run(
                    [sys.executable, "app.py"], env=env, capture_output=True, timeout=10
                )
                self.assertEqual(
                    second.returncode, 0, second.stderr.decode("utf-8", errors="replace")
                )
                self.assertIsNone(first.poll())
            finally:
                first.terminate()
                first.communicate(timeout=10)
