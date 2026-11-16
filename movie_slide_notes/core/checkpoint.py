"""Per-video checkpoints so interrupted or failed jobs can resume.

Completed slide analysis and transcription results are stored outside the
output folder, keyed by the input file (path, size, modification time) and by
the settings that affect each result. A later run with the same file and the
same settings restores the stored result instead of recomputing it, so a
failed transcription can be retried without repeating slide analysis.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path

from ..slide_detector import DetectionStats, SlideRecord
from ..transcriber import TranscriptionResult, TranscriptionSegment

CHECKPOINT_VERSION = 1
DEFAULT_MAX_AGE_DAYS = 14
DEFAULT_MAX_BYTES = 5 * 1024**3
_SLIDES_MANIFEST = "slides.json"


def media_fingerprint(path: Path) -> str:
    path = Path(path)
    try:
        stat = path.stat()
        identity = f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"
    except OSError:
        identity = str(path)
    return hashlib.sha1(identity.encode("utf-8")).hexdigest()[:20]


def config_fingerprint(*parts: object) -> str:
    normalized = [asdict(part) if is_dataclass(part) else part for part in parts]
    payload = json.dumps(
        [CHECKPOINT_VERSION, normalized], sort_keys=True, default=str, ensure_ascii=False
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


class CheckpointStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def video_dir(self, video_path: Path) -> Path:
        return self.root / media_fingerprint(video_path)

    # Slides -----------------------------------------------------------------

    def slides_dir(self, video_path: Path, key: str) -> Path:
        return self.video_dir(video_path) / f"slides_{key}"

    def begin_slides(self, video_path: Path, key: str) -> Path:
        """Return an empty folder for a fresh slide analysis."""
        folder = self.slides_dir(video_path, key)
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True, exist_ok=True)
        self._touch(video_path)
        return folder

    def save_slides(
        self,
        video_path: Path,
        key: str,
        slides: list[SlideRecord],
        stats: DetectionStats,
    ) -> None:
        folder = self.slides_dir(video_path, key)
        manifest = {
            "version": CHECKPOINT_VERSION,
            "source": str(video_path),
            "slides": [
                {"index": slide.index, "timestamp": slide.timestamp, "file": slide.path.name}
                for slide in slides
            ],
            "stats": asdict(stats),
            "saved_at": time.time(),
        }
        _write_json(folder / _SLIDES_MANIFEST, manifest)

    def load_slides(
        self, video_path: Path, key: str, video_name: str
    ) -> tuple[list[SlideRecord], DetectionStats] | None:
        folder = self.slides_dir(video_path, key)
        manifest = _read_json(folder / _SLIDES_MANIFEST)
        if not manifest or manifest.get("version") != CHECKPOINT_VERSION:
            return None
        try:
            slides = [
                SlideRecord(
                    index=int(item["index"]),
                    timestamp=float(item["timestamp"]),
                    path=folder / str(item["file"]),
                    video_name=video_name,
                )
                for item in manifest["slides"]
            ]
            stats = DetectionStats(**manifest["stats"])
        except (KeyError, TypeError, ValueError):
            return None
        if not all(slide.path.is_file() for slide in slides):
            return None
        self._touch(video_path)
        return slides, stats

    # Transcripts ------------------------------------------------------------

    def transcript_path(self, video_path: Path, key: str) -> Path:
        return self.video_dir(video_path) / f"transcript_{key}.json"

    def save_transcript(self, video_path: Path, key: str, result: TranscriptionResult) -> None:
        try:
            text = result.text_path.read_text(encoding="utf-8")
            srt = result.srt_path.read_text(encoding="utf-8")
        except OSError:
            return
        payload = {
            "version": CHECKPOINT_VERSION,
            "source": str(video_path),
            "segments": [[item.start, item.end, item.text] for item in result.segments],
            "warning": result.warning,
            "duration_seconds": result.duration_seconds,
            "status": result.status,
            "text": text,
            "srt": srt,
            "saved_at": time.time(),
        }
        self.video_dir(video_path).mkdir(parents=True, exist_ok=True)
        _write_json(self.transcript_path(video_path, key), payload)
        self._touch(video_path)

    def load_transcript(
        self, video_path: Path, key: str, output_dir: Path, video_stem: str
    ) -> TranscriptionResult | None:
        payload = _read_json(self.transcript_path(video_path, key))
        if not payload or payload.get("version") != CHECKPOINT_VERSION:
            return None
        try:
            segments = [
                TranscriptionSegment(float(start), float(end), str(text))
                for start, end, text in payload["segments"]
            ]
            text_path = output_dir / f"{video_stem}_transcript.txt"
            srt_path = output_dir / f"{video_stem}_transcript.srt"
            output_dir.mkdir(parents=True, exist_ok=True)
            text_path.write_text(str(payload["text"]), encoding="utf-8")
            srt_path.write_text(str(payload["srt"]), encoding="utf-8")
            duration = payload.get("duration_seconds")
            result = TranscriptionResult(
                segments=segments,
                text_path=text_path,
                srt_path=srt_path,
                audio_path=None,
                warning=payload.get("warning"),
                duration_seconds=float(duration) if duration is not None else None,
                status=str(payload.get("status", "transcribed")),
            )
        except (KeyError, TypeError, ValueError):
            return None
        self._touch(video_path)
        return result

    # Maintenance ------------------------------------------------------------

    def has_any(self, video_path: Path) -> bool:
        folder = self.video_dir(video_path)
        return folder.is_dir() and any(folder.iterdir())

    def size_bytes(self) -> int:
        if not self.root.is_dir():
            return 0
        total = 0
        for path in self.root.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except OSError:
                continue
        return total

    def clear(self) -> None:
        if self.root.is_dir():
            shutil.rmtree(self.root, ignore_errors=True)

    def prune(
        self,
        max_age_days: float = DEFAULT_MAX_AGE_DAYS,
        max_bytes: int = DEFAULT_MAX_BYTES,
        now: float | None = None,
    ) -> int:
        """Remove old checkpoints. Returns the number of removed video folders."""
        if not self.root.is_dir():
            return 0
        now = time.time() if now is None else now
        entries: list[tuple[float, int, Path]] = []
        for folder in self.root.iterdir():
            if not folder.is_dir():
                continue
            try:
                modified = folder.stat().st_mtime
            except OSError:
                continue
            size = sum(item.stat().st_size for item in folder.rglob("*") if item.is_file())
            entries.append((modified, size, folder))
        removed = 0
        kept: list[tuple[float, int, Path]] = []
        for modified, size, folder in entries:
            if now - modified > max_age_days * 86400:
                shutil.rmtree(folder, ignore_errors=True)
                removed += 1
            else:
                kept.append((modified, size, folder))
        total = sum(size for _, size, _ in kept)
        for _modified, size, folder in sorted(kept):
            if total <= max_bytes:
                break
            shutil.rmtree(folder, ignore_errors=True)
            total -= size
            removed += 1
        return removed

    def _touch(self, video_path: Path) -> None:
        folder = self.video_dir(video_path)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            now = time.time()
            os.utime(folder, (now, now))
        except OSError:
            pass


def restore_slides_to(
    slides: list[SlideRecord], destination: Path, filename_prefix: str = ""
) -> list[SlideRecord]:
    """Copy checkpointed slide images into the output folder."""
    destination.mkdir(parents=True, exist_ok=True)
    prefix_part = f"{filename_prefix}_" if filename_prefix else ""
    restored: list[SlideRecord] = []
    for slide in slides:
        target = destination / f"{prefix_part}{slide.path.name}"
        if slide.path.resolve() != target.resolve():
            shutil.copy2(slide.path, target)
        restored.append(
            SlideRecord(
                index=slide.index,
                timestamp=slide.timestamp,
                path=target,
                video_name=slide.video_name,
            )
        )
    return restored


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
