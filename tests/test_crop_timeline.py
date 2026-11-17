from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from movie_slide_notes.core.processor import (
    OperationOptions,
    ProcessingOptions,
    process_batch,
    slide_config_for,
)
from movie_slide_notes.gui.region_selector import RegionSelector, video_key
from movie_slide_notes.gui.settings_widget import ProcessingSettingsWidget
from movie_slide_notes.slide_detector import SlideDetectionConfig, SlideDetector, region_at

LEFT = (0.0, 0.0, 0.5, 1.0)
RIGHT = (0.5, 0.0, 0.5, 1.0)


def slide_frame(label: str, side: str, shade: int) -> np.ndarray:
    """A 640x360 frame with one slide on the left or right half."""
    frame = np.full((360, 640, 3), 40, dtype=np.uint8)
    x0 = 0 if side == "left" else 320
    cv2.rectangle(frame, (x0 + 10, 10), (x0 + 310, 350), (255, 255, 255), -1)
    cv2.rectangle(frame, (x0 + 30, 40), (x0 + 290, 120), (shade, 80, 255 - shade), -1)
    cv2.putText(frame, label, (x0 + 60, 250), cv2.FONT_HERSHEY_SIMPLEX, 1.8, (0, 0, 0), 5)
    # Label-dependent blocks so consecutive slides differ like real slides do.
    seed = sum(map(ord, label))
    for block in range(3):
        x = x0 + 30 + (seed * (block + 3)) % 200
        y = 140 + (seed * (block + 5)) % 160
        cv2.rectangle(frame, (x, y), (x + 70, y + 40), ((seed * 37 * block) % 200, 60, 120), -1)
    return frame


def write_video(path: Path, frames: list[np.ndarray], seconds_each: int = 3) -> Path:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 2, (640, 360))
    for frame in frames:
        for _ in range(seconds_each * 2):
            writer.write(frame)
    writer.release()
    return path


class RegionAtTests(unittest.TestCase):
    def test_schedule_picks_latest_start(self) -> None:
        config = SlideDetectionConfig(
            slide_region=LEFT, region_schedule=((0.0, LEFT), (6.0, RIGHT), (9.0, None))
        )
        self.assertEqual(region_at(config, 0.0), LEFT)
        self.assertEqual(region_at(config, 5.9), LEFT)
        self.assertEqual(region_at(config, 6.0), RIGHT)
        self.assertIsNone(region_at(config, 10.0))
        self.assertEqual(region_at(SlideDetectionConfig(slide_region=RIGHT), 3.0), RIGHT)


class DetectorTimelineTests(unittest.TestCase):
    def test_crop_switches_at_the_scheduled_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = write_video(
                root / "layout.avi",
                [
                    slide_frame("Intro", "left", 30),
                    slide_frame("Goal", "left", 200),
                    slide_frame("Data", "right", 90),
                    slide_frame("Wrap", "right", 160),
                ],
            )
            config = SlideDetectionConfig(region_schedule=((0.0, LEFT), (6.0, RIGHT)))
            slides, stats = SlideDetector(config).extract_slides(
                video, root / "out", "layout", show_progress=False
            )
            times = [slide.timestamp for slide in slides]
            self.assertEqual(len(slides), 4, times)
            self.assertIn(6.0, times)
            for slide in slides:
                image = cv2.imread(str(slide.path))
                self.assertEqual(image.shape[:2], (360, 320))
                # Every saved crop contains the white slide, not the dark background.
                self.assertGreater(float(image.mean()), 150)

    def test_page_number_check_can_be_turned_off(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frames = []
            for page in ("1", "2"):
                frame = np.full((360, 640, 3), 255, dtype=np.uint8)
                cv2.putText(
                    frame, "Same slide", (60, 160), cv2.FONT_HERSHEY_SIMPLEX, 2, (0,) * 3, 5
                )
                cv2.putText(frame, page, (560, 330), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0,) * 3, 3)
                frames.append(frame)
            video = write_video(root / "pages.avi", frames)
            with_page = SlideDetector(SlideDetectionConfig()).extract_slides(
                video, root / "a", "pages", show_progress=False
            )[0]
            without_page = SlideDetector(
                SlideDetectionConfig(use_page_region=False)
            ).extract_slides(video, root / "b", "pages", show_progress=False)[0]
            self.assertEqual(len(with_page), 2)
            self.assertEqual(len(without_page), 1)


class ProcessorTimelineTests(unittest.TestCase):
    def options(self, schedules=None, mode="per_video") -> ProcessingOptions:
        return ProcessingOptions(
            operations=OperationOptions(True, True, False, False, mode == "combined"),
            slide_config=SlideDetectionConfig(slide_region=LEFT),
            output_mode=mode,
            region_schedules=schedules or {},
        )

    def test_schedule_applies_only_to_its_video(self) -> None:
        a, b = Path("a.mp4").resolve(), Path("b.mp4").resolve()
        options = self.options({video_key(a): ((0.0, LEFT), (6.0, RIGHT))})
        self.assertEqual(slide_config_for(options, a).region_schedule[1], (6.0, RIGHT))
        self.assertEqual(slide_config_for(options, b).region_schedule, ())
        self.assertEqual(slide_config_for(options, b).slide_region, LEFT)

    def test_combined_output_goes_into_one_run_folder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = write_video(
                root / "lecture-01.avi",
                [slide_frame("A1", "left", 30), slide_frame("A2", "left", 200)],
            )
            output = root / "out"
            batch = process_batch([video], output, self.options(mode="combined"))
            self.assertEqual(batch.output_dir.parent, output)
            self.assertEqual([p.name for p in output.iterdir()], [batch.output_dir.name])
            self.assertEqual(batch.combined_pdf_path.parent, batch.output_dir)
            self.assertTrue((batch.output_dir / "slides").is_dir())
            self.assertEqual(batch.results[0].output_dir, batch.output_dir)

    def test_batch_reports_the_region_used_at_a_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = write_video(
                root / "layout.avi",
                [slide_frame("A1", "left", 30), slide_frame("B1", "right", 90)],
            )
            schedules = {video_key(video): ((0.0, LEFT), (3.0, RIGHT))}
            batch = process_batch([video], root / "out", self.options(schedules))
            self.assertEqual(batch.region_for(video, 1.0), LEFT)
            self.assertEqual(batch.region_for(video, 4.0), RIGHT)
            self.assertEqual(batch.region_for(root / "other.avi", 4.0), LEFT)


class RegionSelectorTimelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_dialog_can_maximize_and_edit_a_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            video = write_video(
                Path(directory) / "layout.avi",
                [slide_frame("A1", "left", 30), slide_frame("B1", "right", 90)],
            )
            with patch.object(RegionSelector, "load_frame"):
                dialog = RegionSelector([video], LEFT, (0.75, 0.8, 0.25, 0.2))
            self.assertTrue(dialog.windowFlags() & Qt.WindowMaximizeButtonHint)
            self.assertEqual(dialog.timeline.count(), 1)
            self.assertFalse(dialog.has_own_schedule())

            dialog.duration = 6.0
            dialog.seek.setValue(600)  # 3.6 s
            dialog.split_here()
            self.assertTrue(dialog.has_own_schedule())
            self.assertEqual(dialog.timeline.count(), 2)
            self.assertEqual(dialog.segment_index, 1)
            dialog._selection_changed(RIGHT)
            schedule = dialog.result_schedules()[video_key(video)]
            self.assertEqual(schedule, [[0.0, list(LEFT)], [3.6, list(RIGHT)]])
            self.assertEqual(dialog.common_region, LEFT)

            dialog.seek.setValue(100)
            self.assertEqual(dialog.segment_index, 0)
            dialog.no_page_check.setChecked(True)
            self.assertFalse(dialog.use_page_region)
            self.assertFalse(dialog.canvas.show_page)

            dialog.use_common_region()
            self.assertEqual(dialog.result_schedules(), {})
            dialog.close()


class SettingsPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_timelines_and_page_option_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "s.ini"), QSettings.IniFormat)
            widget = ProcessingSettingsWidget()
            widget.region_schedules = {"C:/v.mp4": [[0.0, list(LEFT)], [12.5, None]]}
            widget.no_page_number_check.setChecked(True)
            widget.save_settings(settings)
            settings.sync()

            restored = ProcessingSettingsWidget()
            restored.load_settings(settings)
            self.assertEqual(restored.region_schedules, widget.region_schedules)
            self.assertTrue(restored.no_page_number_check.isChecked())
            self.assertFalse(restored.slide_config().use_page_region)
            self.assertEqual(
                restored.region_schedule_map()["C:/v.mp4"], ((0.0, LEFT), (12.5, None))
            )


if __name__ == "__main__":
    unittest.main()
