from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from pypdf import PdfReader

from movie_slide_notes.core.output_naming import OutputNamingConfig
from movie_slide_notes.core.processor import (
    BatchResult,
    OperationOptions,
    ProcessingOptions,
    ProcessResult,
    finalize_combined_outputs,
    process_batch,
)
from movie_slide_notes.slide_detector import DetectionStats, SlideRecord
from movie_slide_notes.transcriber import (
    TranscriptionResult,
    TranscriptionSegment,
)


class CombinedOutputTests(unittest.TestCase):
    def test_batch_processing_uses_natural_filename_order(self) -> None:
        requested = [
            Path("lecture-13-10.mp4"),
            Path("lecture-13-2.mp4"),
            Path("lecture-13-9.mp4"),
            Path("lecture-13-3.mp4"),
        ]
        processed: list[str] = []

        def fake_process_video(**kwargs) -> ProcessResult:
            video_path = Path(kwargs["video_path"])
            processed.append(video_path.name)
            return ProcessResult(video_path=video_path, output_dir=Path(kwargs["output_dir"]))

        options = ProcessingOptions(
            operations=OperationOptions(
                extract_slides=False,
                generate_pdf=False,
                transcribe_audio=False,
                generate_markdown=False,
                generate_combined_pdf=False,
            ),
            output_mode="per_video",
        )
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "movie_slide_notes.core.processor.process_video",
                side_effect=fake_process_video,
            ):
                process_batch(requested, Path(directory), options)

        self.assertEqual(
            processed,
            [
                "lecture-13-2.mp4",
                "lecture-13-3.mp4",
                "lecture-13-9.mp4",
                "lecture-13-10.mp4",
            ],
        )

    def test_outputs_are_combined_and_cross_video_duplicates_are_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            output = root / "output"
            source.mkdir()
            output.mkdir()

            first = self._slide(source / "first.png", "INTRO", 32)
            second = self._slide(source / "second.png", "DETAIL", 190)
            duplicate = source / "duplicate.png"
            duplicate.write_bytes(first.read_bytes())

            result_one = self._result(
                root / "講義 01.mp4",
                [
                    SlideRecord(1, 0.0, first, "講義 01.mp4"),
                    SlideRecord(2, 5.0, second, "講義 01.mp4"),
                ],
                [
                    TranscriptionSegment(0.0, 4.0, "最初の説明"),
                    TranscriptionSegment(5.0, 9.0, "詳細の説明"),
                ],
            )
            result_two = self._result(
                root / "講義 02.mp4",
                [SlideRecord(1, 0.0, duplicate, "講義 02.mp4")],
                [TranscriptionSegment(0.0, 4.0, "再表示した説明")],
            )
            batch = BatchResult(
                results=[result_one, result_two],
                output_mode="combined",
            )
            options = ProcessingOptions(
                operations=OperationOptions(
                    extract_slides=True,
                    generate_pdf=True,
                    transcribe_audio=True,
                    generate_markdown=True,
                    generate_combined_pdf=True,
                ),
                output_mode="combined",
            )

            finalize_combined_outputs(batch, output, options, None, None, 200, 2)

            self.assertEqual(batch.combined_slide_count, 2)
            self.assertEqual(batch.combined_duplicate_count, 1)
            self.assertEqual(len(list((output / "slides").glob("*.png"))), 2)
            self.assertIsNotNone(batch.combined_pdf_path)
            self.assertEqual(len(PdfReader(batch.combined_pdf_path).pages), 2)

            transcript = (output / "all_transcript.txt").read_text(encoding="utf-8")
            self.assertIn("# 講義 01", transcript)
            self.assertIn("# 講義 02", transcript)
            srt = (output / "all_transcript.srt").read_text(encoding="utf-8")
            self.assertIn("00:00:10,000 --> 00:00:14,000", srt)
            markdown = (output / "all_lecture_notes.md").read_text(encoding="utf-8")
            self.assertEqual(markdown.count("## Slide "), 2)
            self.assertIn("講義 01 - 00:00:00", markdown)
            self.assertIn("講義 02 - 00:00:00", markdown)
            self.assertIn("再表示した説明", markdown)

            named_output = root / "named"
            named_output.mkdir()
            named_options = replace(
                options,
                output_naming=OutputNamingConfig(
                    "custom",
                    custom_name="復習 資料(第3回)",
                    date_style="datetime",
                    timestamp=datetime(2026, 10, 18, 15, 4, 5),
                ),
            )
            finalize_combined_outputs(batch, named_output, named_options, None, None, 200, 2)
            prefix = "復習 資料(第3回)_2026-10-18_15-04-05"
            self.assertEqual(batch.combined_pdf_path.name, f"{prefix}_slides.pdf")
            self.assertEqual(len(PdfReader(batch.combined_pdf_path).pages), 2)
            self.assertEqual(batch.combined_transcript_path.name, f"{prefix}_transcript.txt")
            self.assertTrue(batch.combined_srt_path.is_file())
            named_markdown = batch.combined_markdown_path.read_text(encoding="utf-8")
            self.assertIn(f"{prefix}_images/", named_markdown)
            self.assertIn(f"(<{prefix}_images/", named_markdown)
            self.assertEqual(len(list((named_output / f"{prefix}_images").glob("*.png"))), 2)

    @staticmethod
    def _slide(path: Path, title: str, shade: int) -> Path:
        image = np.full((360, 640, 3), 255, dtype=np.uint8)
        cv2.rectangle(image, (30, 30), (610, 330), (shade, shade, shade), 8)
        cv2.putText(
            image,
            title,
            (90, 200),
            cv2.FONT_HERSHEY_SIMPLEX,
            2.0,
            (shade, shade, shade),
            5,
            cv2.LINE_AA,
        )
        self_saved = cv2.imwrite(str(path), image)
        if not self_saved:
            raise RuntimeError(f"Failed to create test image: {path}")
        return path

    @staticmethod
    def _result(
        video_path: Path,
        slides: list[SlideRecord],
        segments: list[TranscriptionSegment],
    ) -> ProcessResult:
        transcription = TranscriptionResult(
            segments=segments,
            text_path=video_path.with_suffix(".txt"),
            srt_path=video_path.with_suffix(".srt"),
            audio_path=None,
        )
        return ProcessResult(
            video_path=video_path,
            output_dir=video_path.parent,
            slides=slides,
            stats=DetectionStats(duration_seconds=10.0),
            transcription=transcription,
        )


if __name__ == "__main__":
    unittest.main()
