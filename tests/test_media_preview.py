"""The processing page plays the selected video or audio file."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def make_clip(path: Path, seconds: int = 2) -> None:
    import av
    import numpy as np

    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=10)
        stream.width, stream.height, stream.pix_fmt = 160, 90, "yuv420p"
        for index in range(seconds * 10):
            image = np.full((90, 160, 3), index * 10 % 255, dtype=np.uint8)
            for packet in stream.encode(av.VideoFrame.from_ndarray(image, format="rgb24")):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


def wait_for(app, condition, timeout: float = 8.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return condition()


def _deletable(path: Path) -> bool:
    try:
        path.rename(path.with_suffix(".moved"))
    except OSError:
        return False
    path.with_suffix(".moved").rename(path)
    return True


class MediaPreviewTests(unittest.TestCase):
    def test_a_video_is_loaded_and_can_be_played_and_paused(self) -> None:
        from PySide6.QtWidgets import QApplication

        from movie_slide_notes.gui.media_preview import MediaPreview, format_clock

        app = QApplication.instance() or QApplication([])
        self.assertEqual(format_clock(65_000), "01:05")
        self.assertEqual(format_clock(3_725_000), "1:02:05")
        with tempfile.TemporaryDirectory() as folder:
            clip = Path(folder) / "lecture.mp4"
            make_clip(clip)
            preview = MediaPreview()
            preview.set_source(None)
            self.assertFalse(preview.play_button.isEnabled())
            preview.set_source(clip)
            if preview.player is None:
                self.skipTest("Qt Multimedia is not available here")
            self.assertTrue(wait_for(app, lambda: preview.seek.maximum() > 0))
            self.assertIn(":", preview.time_label.text())
            self.assertEqual(preview.stack.currentIndex(), 0)
            frames = []
            preview.video.videoSink().videoFrameChanged.connect(
                lambda frame: frames.append(frame.width())
            )
            preview.toggle()
            self.assertTrue(wait_for(app, lambda: preview.playing))
            # Decoded pictures reach the video widget at the clip's size.
            self.assertTrue(wait_for(app, lambda: 160 in frames))
            preview.stop()
            self.assertTrue(wait_for(app, lambda: not preview.playing))
            # Clearing the preview releases the file, so it can be deleted.
            preview.set_source(None)
            self.assertTrue(wait_for(app, lambda: _deletable(clip)))


if __name__ == "__main__":
    unittest.main()
