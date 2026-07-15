from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any

import cv2
import numpy as np
from skimage.metrics import structural_similarity

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
ProgressCallback = Callable[[str, int, int | None, str], None]


@dataclass(frozen=True)
class SlideRecord:
    index: int
    timestamp: float
    path: Path
    video_name: str


@dataclass
class SlideDetectionConfig:
    interval: float = 1.0
    min_gap: float = 1.0
    compare_width: int = 320
    diff_threshold: int = 30
    min_component_area: int = 800
    diff_ratio_threshold: float = 0.025
    ssim_threshold: float = 0.92
    page_region: tuple[float, float, float, float] = (0.75, 0.80, 0.25, 0.20)
    page_diff_ratio_threshold: float = 0.012
    page_min_component_area: int = 8


@dataclass(frozen=True)
class DifferenceStats:
    diff_ratio: float
    raw_diff_ratio: float
    component_count: int
    changed_area: int
    largest_component_area: int


@dataclass(frozen=True)
class SlideChangeDetail:
    changed: bool
    reason: str
    ssim_score: float
    overall: DifferenceStats
    page: DifferenceStats


@dataclass
class DetectionStats:
    frames_checked: int = 0
    candidates: int = 0
    duplicates: int = 0
    saved: int = 0
    frames_suppressed_by_gap: int = 0
    duration_seconds: float | None = None


class VideoReadError(RuntimeError):
    pass


class SlideDetectionCancelled(RuntimeError):
    pass


class SlideDetector:
    def __init__(self, config: SlideDetectionConfig):
        self.config = config

    def extract_slides(
        self,
        video_path: Path,
        output_dir: Path,
        video_stem: str,
        duplicate_filter: Any | None = None,
        show_progress: bool = True,
        progress_callback: ProgressCallback | None = None,
        cancel_event: Event | None = None,
        filename_prefix: str | None = None,
    ) -> tuple[list[SlideRecord], DetectionStats]:
        output_dir.mkdir(parents=True, exist_ok=True)
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise VideoReadError(f"動画を開けませんでした: {video_path}")

        stats = DetectionStats(duration_seconds=self._duration_seconds(cap))
        slides: list[SlideRecord] = []
        last_reference: np.ndarray | None = None
        last_saved_time = -float("inf")
        sample_time = 0.0

        progress = self._progress_bar(video_path.name, stats.duration_seconds, show_progress)
        total_frames = self._estimated_sample_count(stats.duration_seconds)
        try:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise SlideDetectionCancelled("スライド解析をキャンセルしました。")

                if (
                    stats.duration_seconds is not None
                    and sample_time > stats.duration_seconds + 0.1
                ):
                    break

                cap.set(cv2.CAP_PROP_POS_MSEC, sample_time * 1000.0)
                ok, frame = cap.read()
                if not ok:
                    if stats.frames_checked == 0:
                        raise VideoReadError(f"動画フレームを読み込めませんでした: {video_path}")
                    break

                stats.frames_checked += 1
                prepared = prepare_frame(frame, self.config.compare_width)

                if last_reference is None:
                    saved = self._save_new_slide(
                        frame,
                        output_dir,
                        video_stem,
                        sample_time,
                        slides,
                        duplicate_filter,
                        filename_prefix,
                    )
                    last_reference = prepared
                    last_saved_time = sample_time
                    stats.candidates += 1
                    stats.saved += int(saved)
                    self._progress_update(progress)
                    self._emit_progress(progress_callback, stats.frames_checked, total_frames)
                    sample_time += self.config.interval
                    continue

                if sample_time - last_saved_time < self.config.min_gap:
                    stats.frames_suppressed_by_gap += 1
                    self._progress_update(progress)
                    self._emit_progress(progress_callback, stats.frames_checked, total_frames)
                    sample_time += self.config.interval
                    continue

                detail = self.compare_frames(last_reference, prepared)
                if not detail.changed:
                    self._progress_update(progress)
                    self._emit_progress(progress_callback, stats.frames_checked, total_frames)
                    sample_time += self.config.interval
                    continue

                stats.candidates += 1
                duplicate_match = (
                    duplicate_filter.find_duplicate(frame) if duplicate_filter is not None else None
                )
                if duplicate_match is not None:
                    stats.duplicates += 1
                    last_reference = prepared
                    self._progress_update(progress)
                    self._emit_progress(progress_callback, stats.frames_checked, total_frames)
                    sample_time += self.config.interval
                    continue

                saved = self._save_new_slide(
                    frame,
                    output_dir,
                    video_stem,
                    sample_time,
                    slides,
                    duplicate_filter,
                    filename_prefix,
                )
                if saved:
                    stats.saved += 1
                    last_reference = prepared
                    last_saved_time = sample_time

                self._progress_update(progress)
                self._emit_progress(progress_callback, stats.frames_checked, total_frames)
                sample_time += self.config.interval
        finally:
            cap.release()
            self._progress_close(progress)

        return slides, stats

    def compare_frames(self, previous: np.ndarray, current: np.ndarray) -> SlideChangeDetail:
        overall = component_diff_stats(
            previous,
            current,
            threshold=self.config.diff_threshold,
            min_component_area=self.config.min_component_area,
        )
        prev_page = crop_page_region(previous, self.config.page_region)
        curr_page = crop_page_region(current, self.config.page_region)
        if curr_page.shape != prev_page.shape:
            curr_page = cv2.resize(
                curr_page,
                (prev_page.shape[1], prev_page.shape[0]),
                interpolation=cv2.INTER_AREA,
            )
        page = component_diff_stats(
            prev_page,
            curr_page,
            threshold=self.config.diff_threshold,
            min_component_area=self.config.page_min_component_area,
        )
        ssim_score = float(structural_similarity(previous, current, data_range=255))

        structural_change = (
            overall.diff_ratio >= self.config.diff_ratio_threshold
            and ssim_score <= self.config.ssim_threshold
        )
        page_change = page.diff_ratio >= self.config.page_diff_ratio_threshold

        if structural_change:
            reason = "structure"
        elif page_change:
            reason = "page-region"
        else:
            reason = "unchanged"

        return SlideChangeDetail(
            changed=structural_change or page_change,
            reason=reason,
            ssim_score=ssim_score,
            overall=overall,
            page=page,
        )

    def _save_new_slide(
        self,
        frame: np.ndarray,
        output_dir: Path,
        video_stem: str,
        timestamp: float,
        slides: list[SlideRecord],
        duplicate_filter: Any | None,
        filename_prefix: str | None,
    ) -> bool:
        slide_index = len(slides) + 1
        prefix = video_stem if filename_prefix is None else filename_prefix
        prefix_part = f"{prefix}_" if prefix else ""
        filename = (
            f"{prefix_part}slide_{slide_index:03d}_{format_timestamp_for_filename(timestamp)}.png"
        )
        path = output_dir / filename
        write_frame_png(frame, path)
        record = SlideRecord(
            index=slide_index,
            timestamp=timestamp,
            path=path,
            video_name=video_stem,
        )
        slides.append(record)
        if duplicate_filter is not None:
            duplicate_filter.add(frame, path, timestamp)
        return True

    def _duration_seconds(self, cap: cv2.VideoCapture) -> float | None:
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        if fps and fps > 0 and frame_count and frame_count > 0:
            return float(frame_count / fps)
        return None

    def _estimated_sample_count(self, duration: float | None) -> int | None:
        if duration is None or self.config.interval <= 0:
            return None
        return int(duration / self.config.interval) + 1

    def _progress_bar(self, name: str, duration: float | None, enabled: bool) -> Any | None:
        if not enabled:
            return None
        try:
            from tqdm import tqdm
        except ImportError:
            return None
        total = None
        if duration is not None and self.config.interval > 0:
            total = int(duration / self.config.interval) + 1
        return tqdm(total=total, desc=f"frames {name}", unit="frame")

    @staticmethod
    def _progress_update(progress: Any | None) -> None:
        if progress is not None:
            progress.update(1)

    @staticmethod
    def _emit_progress(
        progress_callback: ProgressCallback | None,
        current: int,
        total: int | None,
    ) -> None:
        if progress_callback is not None:
            progress_callback("slide_detection", current, total, "スライド解析中")

    @staticmethod
    def _progress_close(progress: Any | None) -> None:
        if progress is not None:
            progress.close()


def prepare_frame(frame: np.ndarray, compare_width: int) -> np.ndarray:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape[:2]
    scale = compare_width / float(width)
    compare_height = max(1, int(round(height * scale)))
    return cv2.resize(gray, (compare_width, compare_height), interpolation=cv2.INTER_AREA)


def component_diff_stats(
    previous: np.ndarray,
    current: np.ndarray,
    threshold: int,
    min_component_area: int,
) -> DifferenceStats:
    diff = cv2.absdiff(previous, current)
    _, mask = cv2.threshold(diff, threshold, 255, cv2.THRESH_BINARY)
    raw_area = int(np.count_nonzero(mask))
    if raw_area == 0:
        return DifferenceStats(0.0, 0.0, 0, 0, 0)

    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    del labels

    kept_areas: list[int] = []
    for label in range(1, component_count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area >= min_component_area:
            kept_areas.append(area)

    changed_area = int(sum(kept_areas))
    total_area = float(mask.shape[0] * mask.shape[1])
    return DifferenceStats(
        diff_ratio=changed_area / total_area,
        raw_diff_ratio=raw_area / total_area,
        component_count=len(kept_areas),
        changed_area=changed_area,
        largest_component_area=max(kept_areas) if kept_areas else 0,
    )


def crop_region(image: np.ndarray, region: tuple[float, float, float, float]) -> np.ndarray:
    x_ratio, y_ratio, w_ratio, h_ratio = region
    height, width = image.shape[:2]
    x1 = min(width - 1, max(0, int(round(width * x_ratio))))
    y1 = min(height - 1, max(0, int(round(height * y_ratio))))
    x2 = min(width, max(x1 + 1, int(round(width * (x_ratio + w_ratio)))))
    y2 = min(height, max(y1 + 1, int(round(height * (y_ratio + h_ratio)))))
    return image[y1:y2, x1:x2]


def crop_page_region(
    image: np.ndarray,
    region: tuple[float, float, float, float],
) -> np.ndarray:
    """Crop the configured page-number region inside the detected slide surface."""
    return crop_region(crop_slide_content(image), region)


def crop_slide_content(image: np.ndarray) -> np.ndarray:
    """Return the most likely bright slide surface, or the original image.

    Lecture recordings often place a 4:3 or 16:9 slide next to a participant
    pane. Page-number coordinates and duplicate hashes should be relative to
    the slide itself rather than to the entire video frame. The detector is
    intentionally conservative and falls back to the full image for dark or
    ambiguous content.
    """
    if image.ndim not in {2, 3}:
        return image

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    height, width = gray.shape[:2]
    if height < 8 or width < 8:
        return image

    _, bright_mask = cv2.threshold(gray, 170, 255, cv2.THRESH_BINARY)
    # Keep the closing kernel small so a bright mouse cursor or caption just
    # outside the slide does not become connected to the slide rectangle.
    kernel_size = max(3, int(round(min(height, width) * 0.005)))
    if kernel_size % 2 == 0:
        kernel_size += 1
    kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
    bright_mask = cv2.morphologyEx(bright_mask, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(
        bright_mask,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    frame_area = float(height * width)
    candidates: list[tuple[int, int, int, int, int]] = []
    for contour in contours:
        x, y, candidate_width, candidate_height = cv2.boundingRect(contour)
        area = candidate_width * candidate_height
        if candidate_width < width * 0.35 or candidate_height < height * 0.35:
            continue
        if area < frame_area * 0.20:
            continue
        fill_ratio = cv2.countNonZero(
            bright_mask[y : y + candidate_height, x : x + candidate_width]
        ) / float(area)
        if fill_ratio < 0.55:
            continue
        candidates.append((area, x, y, candidate_width, candidate_height))

    if not candidates:
        return image

    _, x, y, candidate_width, candidate_height = max(candidates)
    return image[y : y + candidate_height, x : x + candidate_width]


def write_frame_png(frame: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", frame)
    if not ok:
        raise RuntimeError(f"画像のPNGエンコードに失敗しました: {path}")
    encoded.tofile(str(path))


def format_timestamp_for_filename(seconds: float) -> str:
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}h{minutes:02d}m{secs:02d}s"
    return f"{minutes:02d}m{secs:02d}s"


def format_timestamp(seconds: float) -> str:
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"
