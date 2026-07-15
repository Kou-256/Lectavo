from __future__ import annotations

import unittest
from pathlib import Path

import cv2
import imagehash
import numpy as np

from movie_slide_notes.duplicate_filter import (
    DuplicateConfig,
    DuplicateFilter,
    _Fingerprint,
)
from movie_slide_notes.slide_detector import (
    SlideDetectionConfig,
    SlideDetector,
    crop_slide_content,
)


def lecture_frame() -> np.ndarray:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[98:638, 0:960] = 255
    return frame


class SlideRegionTests(unittest.TestCase):
    def test_default_minimum_save_gap_keeps_consecutive_slides(self) -> None:
        self.assertEqual(SlideDetectionConfig().min_gap, 1.0)

    def test_bright_slide_is_cropped_away_from_participant_pane(self) -> None:
        cropped = crop_slide_content(lecture_frame())
        self.assertEqual(cropped.shape[:2], (540, 960))

    def test_cursor_below_slide_does_not_expand_detected_surface(self) -> None:
        frame = lecture_frame()
        frame[645:650, 600:605] = 255
        cropped = crop_slide_content(frame)
        self.assertEqual(cropped.shape[:2], (540, 960))

    def test_page_region_is_relative_to_detected_slide(self) -> None:
        previous = lecture_frame()
        current = lecture_frame()
        current[590:620, 870:900] = 0
        detector = SlideDetector(
            SlideDetectionConfig(
                min_component_area=20_000,
                diff_ratio_threshold=0.5,
                page_min_component_area=8,
                page_diff_ratio_threshold=0.01,
            )
        )

        detail = detector.compare_frames(
            cv2.cvtColor(previous, cv2.COLOR_BGR2GRAY),
            cv2.cvtColor(current, cv2.COLOR_BGR2GRAY),
        )

        self.assertTrue(detail.changed)
        self.assertEqual(detail.reason, "page-region")
        self.assertGreater(detail.page.diff_ratio, 0.01)


class DuplicateFilterTests(unittest.TestCase):
    def test_phash_collision_without_visual_similarity_is_kept(self) -> None:
        duplicate_filter = DuplicateFilter(
            DuplicateConfig(
                phash_threshold=8,
                ssim_threshold=0.97,
                phash_ssim_threshold=0.90,
            )
        )
        saved_hash = imagehash.hex_to_hash("0000000000000000")
        candidate_hash = imagehash.hex_to_hash("00000000000000ff")
        duplicate_filter._saved.append(
            _Fingerprint(
                path=Path("saved.png"),
                timestamp=98.0,
                phash=saved_hash,
                image=np.zeros((64, 64), dtype=np.uint8),
            )
        )
        duplicate_filter._fingerprint = lambda *_args: _Fingerprint(
            path=Path("candidate.png"),
            timestamp=0.0,
            phash=candidate_hash,
            image=np.full((64, 64), 255, dtype=np.uint8),
        )

        self.assertIsNone(duplicate_filter.find_duplicate(np.zeros((1, 1), dtype=np.uint8)))

    def test_phash_match_with_visual_similarity_is_removed(self) -> None:
        duplicate_filter = DuplicateFilter(DuplicateConfig())
        saved = np.tile(np.arange(64, dtype=np.uint8), (64, 1))
        duplicate_filter.add(saved, Path("saved.png"), 10.0)

        match = duplicate_filter.find_duplicate(saved.copy())

        self.assertIsNotNone(match)
        self.assertEqual(match.reason, "phash+ssim")


if __name__ == "__main__":
    unittest.main()
