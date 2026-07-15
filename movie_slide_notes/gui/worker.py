from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, Signal, Slot

from movie_slide_notes.core.processor import (
    ProcessingOptions,
    ProgressEvent,
    process_batch,
)


class ProcessingWorker(QObject):
    progress = Signal(object)
    log = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, videos: list[Path], output_dir: Path, options: ProcessingOptions):
        super().__init__()
        self.videos = videos
        self.output_dir = output_dir
        self.options = options
        self.cancel_event = Event()

    @Slot()
    def run(self) -> None:
        try:
            result = process_batch(
                self.videos,
                self.output_dir,
                self.options,
                progress_callback=self._progress,
                log_callback=self._log,
                cancel_event=self.cancel_event,
            )
            self.finished.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc) or exc.__class__.__name__)

    @Slot()
    def cancel(self) -> None:
        self.cancel_event.set()
        self._log("キャンセル要求を受け付けました")

    def _progress(self, event: ProgressEvent) -> None:
        self.progress.emit(event)

    def _log(self, message: str) -> None:
        logging.getLogger("lectavo").info(message)
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log.emit(f"{timestamp} {message}")
