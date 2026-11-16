from __future__ import annotations

import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from pypdf import PdfReader

from movie_slide_notes.core.checkpoint import CheckpointStore
from movie_slide_notes.core.preflight import (
    LEVEL_ERROR,
    LEVEL_INFO,
    LEVEL_OK,
    LEVEL_WARNING,
    GpuStatus,
    MediaInfo,
    run_preflight,
)
from movie_slide_notes.core.processor import (
    OperationOptions,
    ProcessingOptions,
    process_batch,
)
from movie_slide_notes.core.slide_review import (
    ReviewSlide,
    apply_review,
    build_review_targets,
    insert_chronologically,
    renumbered_name,
)
from movie_slide_notes.slide_detector import SlideDetectionConfig
from movie_slide_notes.transcriber import (
    TranscriptionConfig,
    TranscriptionResult,
    TranscriptionSegment,
)


def make_video(path: Path, shades: list[int], seconds_per_slide: int = 3) -> Path:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 2, (320, 180))
    for index, shade in enumerate(shades):
        frame = np.full((180, 320, 3), 255, dtype=np.uint8)
        cv2.rectangle(frame, (20, 20), (300, 160), (shade, shade, shade), -1)
        cv2.putText(
            frame, f"S{index}", (120, 110), cv2.FONT_HERSHEY_SIMPLEX, 2, (255 - shade,) * 3, 4
        )
        for _ in range(seconds_per_slide * 2):
            writer.write(frame)
    writer.release()
    return path


def fake_transcribe(segments: list[TranscriptionSegment]):
    def run(**kwargs) -> TranscriptionResult:
        output_dir = Path(kwargs["output_dir"])
        stem = kwargs["video_stem"]
        text_path = output_dir / f"{stem}_transcript.txt"
        srt_path = output_dir / f"{stem}_transcript.srt"
        text_path.write_text("\n".join(item.text for item in segments), encoding="utf-8")
        srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nx\n", encoding="utf-8")
        return TranscriptionResult(segments, text_path, srt_path, None, duration_seconds=9.0)

    return run


def options(mode: str = "per_video", checkpoint: Path | None = None) -> ProcessingOptions:
    return ProcessingOptions(
        operations=OperationOptions(True, True, True, True, mode == "combined"),
        slide_config=SlideDetectionConfig(interval=1.0, min_gap=1.0),
        transcription_config=TranscriptionConfig(model_size="tiny"),
        output_mode=mode,
        checkpoint_dir=checkpoint,
    )


SEGMENTS = [
    TranscriptionSegment(0.0, 2.5, "はじめ"),
    TranscriptionSegment(3.0, 5.5, "なか"),
    TranscriptionSegment(6.0, 8.5, "おわり"),
]


class ResumeTests(unittest.TestCase):
    def test_completed_stages_are_reused_and_failed_transcription_is_retried(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = make_video(root / "lecture.avi", [30, 120, 210])
            checkpoint = root / "checkpoints"
            output = root / "out"

            with patch(
                "movie_slide_notes.core.processor.transcribe_video",
                side_effect=RuntimeError("model download failed"),
            ):
                first = process_batch([video], output, options(checkpoint=checkpoint))
            self.assertEqual(first.failed_count, 1)
            self.assertGreaterEqual(len(first.results[0].slides), 2)
            self.assertFalse(first.results[0].reused_slides)

            with (
                patch(
                    "movie_slide_notes.core.processor.SlideDetector.extract_slides",
                    side_effect=AssertionError("slides should be reused"),
                ),
                patch(
                    "movie_slide_notes.core.processor.transcribe_video",
                    side_effect=fake_transcribe(SEGMENTS),
                ) as transcribe,
            ):
                second = process_batch([video], output, options(checkpoint=checkpoint))
            self.assertEqual(second.failed_count, 0)
            self.assertTrue(second.results[0].reused_slides)
            self.assertFalse(second.results[0].reused_transcript)
            transcribe.assert_called_once()
            self.assertTrue(second.results[0].pdf_path.is_file())
            self.assertEqual(
                [slide.path.name for slide in second.results[0].slides],
                [slide.path.name for slide in first.results[0].slides],
            )

            with patch(
                "movie_slide_notes.core.processor.transcribe_video",
                side_effect=AssertionError("transcript should be reused"),
            ):
                third = process_batch([video], output, options(checkpoint=checkpoint))
            self.assertTrue(third.results[0].reused_transcript)
            self.assertEqual(third.reused_count, 1)
            self.assertIn("なか", third.results[0].transcription.text_path.read_text("utf-8"))

    def test_changed_settings_do_not_reuse_stale_slides(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = make_video(root / "lecture.avi", [30, 210])
            checkpoint = root / "checkpoints"
            base = replace(
                options(checkpoint=checkpoint),
                operations=OperationOptions(True, True, False, False, False),
            )
            process_batch([video], root / "a", base)
            changed = replace(base, slide_config=SlideDetectionConfig(interval=0.5, min_gap=1.0))
            result = process_batch([video], root / "b", changed)
            self.assertFalse(result.results[0].reused_slides)

    def test_prune_removes_old_checkpoints(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CheckpointStore(Path(directory))
            old = Path(directory) / "old"
            old.mkdir()
            (old / "x.bin").write_bytes(b"1" * 10)
            fresh = Path(directory) / "fresh"
            fresh.mkdir()
            (fresh / "y.bin").write_bytes(b"1" * 10)
            import os

            past = time.time() - 30 * 86400
            os.utime(old, (past, past))
            self.assertEqual(store.prune(max_age_days=14), 1)
            self.assertFalse(old.exists())
            self.assertTrue(fresh.exists())


class PreflightTests(unittest.TestCase):
    def run_check(self, files, **overrides):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            for name in files:
                path = root / name
                path.write_bytes(b"data")
                paths.append(path)
            kwargs = dict(
                media_prober=lambda path: MediaInfo(True, True, True, 3600.0),
                model_checker=lambda size: True,
                gpu_inspector=lambda: GpuStatus(False, "CUDA DLL missing"),
                free_space=lambda path: 50 * 1024**3,
                transcriber_installed=True,
            )
            kwargs.update(overrides.pop("kwargs", {}))
            opts = overrides.pop("opts", options())
            return run_preflight(paths, root / "out", opts, **kwargs)

    def levels(self, report, category):
        return [item.level for item in report.items if item.category == category]

    def test_all_good_has_no_attention_items(self) -> None:
        report = self.run_check(["a.mp4"])
        self.assertFalse(report.needs_attention, report.items)
        self.assertIn(LEVEL_OK, self.levels(report, "disk"))

    def test_missing_audio_track_warns_for_video_and_blocks_audio_file(self) -> None:
        report = self.run_check(
            ["a.mp4"], kwargs={"media_prober": lambda p: MediaInfo(True, True, False, 60.0)}
        )
        self.assertEqual(self.levels(report, "audio"), [LEVEL_WARNING])
        report = self.run_check(
            ["a.mp3"], kwargs={"media_prober": lambda p: MediaInfo(True, False, False, 60.0)}
        )
        self.assertEqual(self.levels(report, "audio"), [LEVEL_ERROR])
        self.assertTrue(report.has_errors)

    def test_low_disk_space_is_an_error(self) -> None:
        report = self.run_check(["a.mp4"], kwargs={"free_space": lambda p: 100 * 1024**2})
        self.assertIn(LEVEL_ERROR, self.levels(report, "disk"))

    def test_model_download_and_gpu_fallback_are_warnings(self) -> None:
        opts = options()
        opts.transcription_config = TranscriptionConfig(model_size="small", device="cuda")
        report = self.run_check(["a.mp4"], opts=opts, kwargs={"model_checker": lambda s: False})
        self.assertEqual(self.levels(report, "model"), [LEVEL_WARNING])
        self.assertEqual(self.levels(report, "gpu"), [LEVEL_WARNING])
        self.assertFalse(report.has_errors)

    def test_cpu_with_available_gpu_suggests_switching(self) -> None:
        report = self.run_check(["a.mp4"], kwargs={"gpu_inspector": lambda: GpuStatus(True)})
        self.assertEqual(self.levels(report, "gpu"), [LEVEL_INFO])

    def test_unreadable_input_is_an_error(self) -> None:
        report = self.run_check(
            ["broken.mp4"], kwargs={"media_prober": lambda p: MediaInfo(False, error="bad")}
        )
        self.assertEqual(self.levels(report, "input")[0], LEVEL_ERROR)


class SlideReviewTests(unittest.TestCase):
    def test_per_video_review_deletes_reorders_and_adds_slides(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = make_video(root / "lecture.avi", [30, 120, 210])
            with patch(
                "movie_slide_notes.core.processor.transcribe_video",
                side_effect=fake_transcribe(SEGMENTS),
            ):
                batch = process_batch([video], root / "out", options())
            targets = build_review_targets(batch)
            self.assertEqual(len(targets), 1)
            target = targets[0]
            original = list(target.slides)
            self.assertGreaterEqual(len(original), 3)
            removed_path = original[1].path

            capture = root / "capture" / "slide_000_00m07s_added.png"
            capture.parent.mkdir()
            cv2.imwrite(str(capture), np.full((180, 320, 3), 90, dtype=np.uint8))
            edited = [original[2], original[0], *original[3:]]
            insert_chronologically(edited, ReviewSlide(capture, 7.0, 0, ((0, 7.0),), added=True))
            outcome = apply_review(batch, target, edited)

            self.assertEqual(outcome.removed, 1)
            self.assertEqual(outcome.added, 1)
            self.assertFalse(removed_path.exists())
            names = [record.path.name for record in outcome.slides]
            self.assertTrue(
                all(name.startswith(f"slide_{i:03d}_") for i, name in enumerate(names, 1))
            )
            self.assertTrue(all(record.path.is_file() for record in outcome.slides))
            self.assertEqual(len(PdfReader(outcome.pdf_path).pages), len(edited))
            markdown = outcome.markdown_path.read_text(encoding="utf-8")
            self.assertEqual(markdown.count("## Slide "), len(edited))
            self.assertIn("おわり", markdown)
            self.assertEqual(len(list(target.slides_dir.glob("*.png"))), len(edited))

    def test_combined_review_keeps_transcripts_after_deleting_a_slide(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            videos = [
                make_video(root / "part-1.avi", [30, 120]),
                make_video(root / "part-2.avi", [210, 160]),
            ]
            with patch(
                "movie_slide_notes.core.processor.transcribe_video",
                side_effect=fake_transcribe(SEGMENTS),
            ):
                batch = process_batch(videos, root / "out", options("combined"))
            target = build_review_targets(batch)[0]
            self.assertTrue(target.combined)
            count = len(target.slides)
            self.assertGreaterEqual(count, 3)
            outcome = apply_review(batch, target, target.slides[:1] + target.slides[2:])
            self.assertEqual(len(outcome.slides), count - 1)
            self.assertEqual(batch.combined_slide_count, count - 1)
            self.assertEqual(len(PdfReader(batch.combined_pdf_path).pages), count - 1)
            markdown = batch.combined_markdown_path.read_text(encoding="utf-8")
            self.assertEqual(markdown.count("## Slide "), count - 1)
            # Speech from the removed slide moves to the previous kept slide.
            self.assertIn("なか", markdown)

    def test_renumbered_name_keeps_prefix_and_time(self) -> None:
        self.assertEqual(renumbered_name("lec_slide_004_01m02s.png", 2), "lec_slide_002_01m02s.png")
        self.assertEqual(renumbered_name("other.png", 1, "p_"), "p_slide_001_other.png")


if __name__ == "__main__":
    unittest.main()
