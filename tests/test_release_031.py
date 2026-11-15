from __future__ import annotations

import json
import os
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from movie_slide_notes.core.processor import ProcessingOptions, process_batch
from movie_slide_notes.gui.main_window import MainWindow
from movie_slide_notes.transcriber import (
    TranscriptionConfig,
    create_whisper_model,
    is_silent_audio,
    model_files_readable,
    repair_model,
    transcribe_video,
)
from movie_slide_notes.updates import parse_release


class Release031Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def model_files(self, folder):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "model.bin").write_bytes(b"x" * 2048)
        for name in ("config.json", "tokenizer.json"):
            (folder / name).write_text(json.dumps({}), encoding="utf-8")

    def test_missing_model_is_repaired_without_deleting_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "models--Systran--faster-whisper-tiny" / "snapshots" / "revision"
            original.mkdir(parents=True)

            def download(repo_id, **kwargs):
                self.assertEqual(repo_id, "Systran/faster-whisper-tiny")
                self.assertTrue(kwargs["force_download"])
                self.model_files(Path(kwargs["local_dir"]))

            with (
                patch("movie_slide_notes.transcriber.user_cache_dir", return_value=root),
                patch("huggingface_hub.snapshot_download", side_effect=download),
            ):
                repaired = repair_model(TranscriptionConfig(model_size="tiny"), original)
            self.assertTrue(original.exists())
            self.assertTrue(model_files_readable(repaired))

    def test_native_file_open_error_retries_with_regular_local_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "models--Systran--faster-whisper-tiny" / "snapshots" / "revision"
            self.model_files(original)
            loader = MagicMock(
                side_effect=[RuntimeError("Unable to open file 'model.bin'"), "model"]
            )
            with (
                patch("faster_whisper.utils.download_model", return_value=str(original)),
                patch("movie_slide_notes.transcriber.user_cache_dir", return_value=root),
                patch("huggingface_hub.snapshot_download") as download,
            ):
                result = create_whisper_model(
                    loader, TranscriptionConfig(model_size="tiny"), "cpu", "int8"
                )
            self.assertEqual(result, "model")
            download.assert_not_called()
            repaired = Path(loader.call_args.args[0])
            self.assertNotEqual(repaired, original)
            self.assertFalse((repaired / "model.bin").is_symlink())

    def test_silence_does_not_load_or_download_a_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "無音 サンプル.wav"
            with wave.open(str(source), "wb") as audio:
                audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                audio.writeframes(b"\x00\x00" * 32000)
            self.assertTrue(is_silent_audio(source))
            with patch("movie_slide_notes.transcriber.create_whisper_model") as model:
                result = transcribe_video(
                    source, root, "silent", TranscriptionConfig(model_size="medium")
                )
            model.assert_not_called()
            self.assertEqual(result.status, "silence")
            self.assertIn("無音", result.text_path.read_text(encoding="utf-8"))

    def test_failed_audio_batch_does_not_generate_empty_success_notes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.wav"
            source.touch()
            with patch(
                "movie_slide_notes.core.processor.transcribe_video",
                side_effect=RuntimeError("model unavailable"),
            ):
                batch = process_batch(
                    [source], root / "output", ProcessingOptions(output_mode="combined")
                )
            self.assertEqual(batch.failed_count, 1)
            self.assertIsNone(batch.combined_markdown_path)
            self.assertFalse(list((root / "output").glob("*.md")))

    def test_non_silent_audio_with_no_segments_is_not_called_silence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sound.wav"
            with wave.open(str(source), "wb") as audio:
                audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                audio.writeframes(b"\xff\x0f" * 16000)
            model = MagicMock()
            model.transcribe.return_value = (iter([]), None)
            with patch("movie_slide_notes.transcriber.create_whisper_model", return_value=model):
                result = transcribe_video(source, root, "sound", TranscriptionConfig())
            self.assertEqual(result.status, "no_speech")
            self.assertIn("無音とは限りません", result.text_path.read_text(encoding="utf-8"))

    def test_home_process_separation_and_ctrl_multiselect(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "movie_slide_notes.gui.main_window.user_config_dir", return_value=Path(directory)
            ),
        ):
            root = Path(directory)
            paths = [root / f"sample-{i}.wav" for i in range(3)]
            for path in paths:
                path.touch()
            window = MainWindow()
            try:
                window.resize(1100, 720)
                window.show()
                window.add_paths(paths)
                QApplication.processEvents()
                self.assertIs(window.page_stack.currentWidget(), window.processing_page)
                QTest.mouseClick(
                    window.file_tabs.viewport(),
                    Qt.LeftButton,
                    Qt.NoModifier,
                    window.file_tabs.visualItemRect(window.file_tabs.item(0)).center(),
                )
                QTest.mouseClick(
                    window.file_tabs.viewport(),
                    Qt.LeftButton,
                    Qt.ControlModifier,
                    window.file_tabs.visualItemRect(window.file_tabs.item(2)).center(),
                )
                self.assertEqual(len(window.file_tabs.selectedItems()), 2)
                window._select_page("home")
                self.assertIs(window.page_stack.currentWidget(), window.home_page)
                self.assertEqual(len(window.videos), 3)
                window._select_page("processing")
                window.remove_selected_files()
                self.assertEqual(window.videos, [paths[1]])
            finally:
                window.close()

    def test_versioned_installer_is_preferred_over_legacy_alias(self):
        version = "0.3.1"
        names = ["Lectavo-Setup.exe", f"Lectavo-setup-{version}-x64.exe"]
        assets = [
            {
                "name": name,
                "size": 10,
                "digest": "sha256:" + "a" * 64,
                "browser_download_url": f"https://github.com/Kou-256/Lectavo/releases/download/v{version}/{name}",
            }
            for name in names
        ]
        result = parse_release({"tag_name": "v" + version, "assets": assets}, "0.3.0")
        self.assertTrue(result.url.endswith(names[1]))
