from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

from movie_slide_notes.transcriber import (
    TranscriptionCancelled,
    extract_audio,
    inspect_cuda_runtime,
)


class _AudioStream:
    type = "audio"


class _CancellingContainer:
    def __init__(self, cancel_event: Event):
        self.cancel_event = cancel_event
        self.streams = [_AudioStream()]
        self.closed = False

    def decode(self, _stream):
        self.cancel_event.set()
        yield object()

    def close(self) -> None:
        self.closed = True


class TranscriberRuntimeTests(unittest.TestCase):
    def test_pyav_audio_decode_is_cancelled_without_external_process(self) -> None:
        cancel_event = Event()
        container = _CancellingContainer(cancel_event)
        with tempfile.TemporaryDirectory() as directory:
            with patch("av.open", return_value=container):
                with self.assertRaises(TranscriptionCancelled):
                    extract_audio(
                        Path("講義 動画.mp4"),
                        Path(directory) / "音声.wav",
                        cancel_event=cancel_event,
                    )
        self.assertTrue(container.closed)

    def test_cuda_diagnostic_lists_missing_runtime_dlls(self) -> None:
        with patch("ctranslate2.get_cuda_device_count", return_value=1):
            with patch("movie_slide_notes.transcriber.can_load_windows_dll", return_value=False):
                status = inspect_cuda_runtime()

        self.assertFalse(status.available)
        self.assertEqual(status.device_count, 1)
        self.assertEqual(status.missing_dlls, ("cublas64_12.dll", "cudnn64_9.dll"))


if __name__ == "__main__":
    unittest.main()
