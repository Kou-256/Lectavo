from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import imagehash
import numpy as np
from PIL import Image
from skimage.metrics import structural_similarity

from .slide_detector import crop_slide_content


@dataclass
class DuplicateConfig:
    phash_threshold: int = 8
    ssim_threshold: float = 0.97
    phash_ssim_threshold: float = 0.90
    compare_width: int = 320
    hash_size: int = 8


@dataclass(frozen=True)
class DuplicateMatch:
    path: Path
    reason: str
    phash_distance: int
    ssim_score: float
    timestamp: float


@dataclass(frozen=True)
class _Fingerprint:
    path: Path
    timestamp: float
    phash: imagehash.ImageHash
    image: np.ndarray


class DuplicateFilter:
    def __init__(self, config: DuplicateConfig):
        self.config = config
        self._saved: list[_Fingerprint] = []

    @property
    def saved_count(self) -> int:
        return len(self._saved)

    def find_duplicate(self, frame: np.ndarray) -> DuplicateMatch | None:
        if not self._saved:
            return None

        candidate = self._fingerprint(frame, Path("<candidate>"), 0.0)
        best_match: DuplicateMatch | None = None

        for saved in self._saved:
            phash_distance = int(candidate.phash - saved.phash)
            ssim_score = compare_ssim(candidate.image, saved.image)

            # A short pHash distance alone is not enough: slides with the same
            # layout can collide even when their text and diagrams differ.
            # Require visual similarity as corroboration before pHash removes
            # a candidate. A near-identical SSIM match remains sufficient.
            phash_duplicate = (
                phash_distance <= self.config.phash_threshold
                and ssim_score >= self.config.phash_ssim_threshold
            )
            ssim_duplicate = ssim_score >= self.config.ssim_threshold
            if not phash_duplicate and not ssim_duplicate:
                continue

            reason = "phash+ssim" if phash_duplicate else "ssim"
            match = DuplicateMatch(
                path=saved.path,
                reason=reason,
                phash_distance=phash_distance,
                ssim_score=ssim_score,
                timestamp=saved.timestamp,
            )
            if best_match is None:
                best_match = match
            elif match.phash_distance < best_match.phash_distance:
                best_match = match
            elif match.ssim_score > best_match.ssim_score:
                best_match = match

        return best_match

    def add(self, frame: np.ndarray, path: Path, timestamp: float) -> None:
        self._saved.append(self._fingerprint(frame, path, timestamp))

    def _fingerprint(self, frame: np.ndarray, path: Path, timestamp: float) -> _Fingerprint:
        image = canonical_image(frame, self.config.compare_width)
        pil_image = Image.fromarray(image)
        phash = imagehash.phash(pil_image, hash_size=self.config.hash_size)
        return _Fingerprint(path=path, timestamp=timestamp, phash=phash, image=image)


def canonical_image(frame: np.ndarray, compare_width: int) -> np.ndarray:
    if frame.ndim == 3:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    else:
        gray = frame
    gray = crop_slide_content(gray)
    height, width = gray.shape[:2]
    scale = compare_width / float(width)
    compare_height = max(1, int(round(height * scale)))
    return cv2.resize(gray, (compare_width, compare_height), interpolation=cv2.INTER_AREA)


def compare_ssim(left: np.ndarray, right: np.ndarray) -> float:
    if left.shape != right.shape:
        right = cv2.resize(right, (left.shape[1], left.shape[0]), interpolation=cv2.INTER_AREA)
    return float(structural_similarity(left, right, data_range=255))
