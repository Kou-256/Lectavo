"""Preview of the selected video or audio file, with sound, before processing."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from movie_slide_notes.gui.i18n import tr
from movie_slide_notes.gui.icons import icon as svg_icon
from movie_slide_notes.gui.icons import icon_pixmap as svg_pixmap
from movie_slide_notes.media import AUDIO_EXTENSIONS

PREVIEW_HEIGHT = 300


def format_clock(milliseconds: int) -> str:
    seconds = max(0, milliseconds) // 1000
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class _AspectBox(QWidget):
    """Keeps the video at 16:9 within the available width."""

    def __init__(self, child: QWidget):
        super().__init__()
        self.child = child
        child.setParent(self)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(PREVIEW_HEIGHT)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        height = self.height()
        width = min(self.width(), round(height * 16 / 9))
        self.child.setGeometry((self.width() - width) // 2, 0, width, height)


class MediaPreview(QFrame):
    """A small player: picture (or an audio placeholder), play, seek and mute."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SoftPanel")
        self.path: Path | None = None
        self.player = None
        self.audio = None
        self._seeking = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(8)
        self.stack = QStackedWidget()
        self.stack.setFixedHeight(PREVIEW_HEIGHT)

        self.screen = QWidget()
        self.screen.setStyleSheet("background: #000;")
        self.stack.addWidget(_AspectBox(self.screen))

        self.audio_face = QLabel()
        self.audio_face.setAlignment(Qt.AlignCenter)
        self.audio_face.setPixmap(svg_pixmap("file-audio", 72))
        self.stack.addWidget(self.audio_face)

        self.message = QLabel()
        self.message.setAlignment(Qt.AlignCenter)
        self.message.setWordWrap(True)
        self.message.setObjectName("SecondaryText")
        self.stack.addWidget(self.message)
        layout.addWidget(self.stack)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.play_button = QPushButton()
        self.play_button.setIcon(svg_icon("play", 18))
        self.play_button.setIconSize(QSize(18, 18))
        self.play_button.setAccessibleName(tr("再生"))
        self.play_button.setToolTip(tr("再生"))
        self.seek = QSlider(Qt.Horizontal)
        self.seek.setRange(0, 0)
        self.seek.setAccessibleName(tr("再生位置"))
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setObjectName("SecondaryText")
        self.mute_button = QPushButton()
        self.mute_button.setCheckable(True)
        self.mute_button.setIcon(svg_icon("volume", 18))
        self.mute_button.setIconSize(QSize(18, 18))
        self.mute_button.setAccessibleName(tr("消音"))
        self.mute_button.setToolTip(tr("消音"))
        controls.addWidget(self.play_button)
        controls.addWidget(self.seek, 1)
        controls.addWidget(self.time_label)
        controls.addWidget(self.mute_button)
        layout.addLayout(controls)

        self.play_button.clicked.connect(self.toggle)
        self.seek.sliderPressed.connect(self._seek_started)
        self.seek.sliderReleased.connect(self._seek_finished)
        self.seek.sliderMoved.connect(self._seek_moved)
        self.mute_button.toggled.connect(self._mute_toggled)
        self._set_enabled(False)

    # player ---------------------------------------------------------------------

    def _ensure_player(self) -> bool:
        if self.player is not None:
            return True
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            from PySide6.QtMultimediaWidgets import QVideoWidget
        except Exception:
            logging.getLogger("lectavo").exception("Qt Multimedia is not available")
            return False
        self.video = QVideoWidget(self.screen)
        self.video.setAspectRatioMode(Qt.KeepAspectRatio)
        screen_layout = QVBoxLayout(self.screen)
        screen_layout.setContentsMargins(0, 0, 0, 0)
        screen_layout.addWidget(self.video)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.8)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.positionChanged.connect(self._position_changed)
        self.player.playbackStateChanged.connect(self._state_changed)
        self.player.mediaStatusChanged.connect(self._status_changed)
        self.player.errorOccurred.connect(self._failed)
        return True

    def set_source(self, path: Path | None) -> None:
        """Show ``path`` (paused at its start), or nothing."""
        if path == self.path:
            return
        self.path = path
        if self.player is not None:
            self.player.stop()
            # Let go of the previous file, so it can be moved or deleted.
            self.player.setSource(QUrl())
        if path is None:
            self._show_message(tr("ファイルを選ぶと、ここで再生して確認できます。"))
            self._set_enabled(False)
            return
        if not self._ensure_player():
            self._show_message(tr("このPCではプレビューを表示できません（処理はできます）。"))
            self._set_enabled(False)
            return
        audio_only = path.suffix.lower() in AUDIO_EXTENSIONS
        self.stack.setCurrentIndex(1 if audio_only else 0)
        self.seek.setRange(0, 0)
        self.time_label.setText("00:00 / 00:00")
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self._set_enabled(True)

    def stop(self) -> None:
        if self.player is not None:
            self.player.pause()

    def toggle(self) -> None:
        if self.player is None or self.path is None:
            return
        from PySide6.QtMultimedia import QMediaPlayer

        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    @property
    def playing(self) -> bool:
        if self.player is None:
            return False
        from PySide6.QtMultimedia import QMediaPlayer

        return self.player.playbackState() == QMediaPlayer.PlayingState

    # signals ----------------------------------------------------------------------

    def _status_changed(self, status) -> None:
        from PySide6.QtMultimedia import QMediaPlayer

        if status == QMediaPlayer.LoadedMedia and not self.playing:
            # Pausing a freshly loaded video shows its first frame.
            self.player.pause()

    def _duration_changed(self, duration: int) -> None:
        self.seek.setRange(0, max(0, duration))
        self._update_time()

    def _position_changed(self, position: int) -> None:
        if not self._seeking:
            self.seek.setValue(position)
        self._update_time()

    def _state_changed(self, _state) -> None:
        name = "pause" if self.playing else "play"
        label = tr("一時停止") if self.playing else tr("再生")
        self.play_button.setIcon(svg_icon(name, 18))
        self.play_button.setAccessibleName(label)
        self.play_button.setToolTip(label)

    def _failed(self, _error, message: str = "") -> None:
        logging.getLogger("lectavo").warning("Preview failed for %s: %s", self.path, message)
        self._show_message(tr("このファイルはプレビューできません（処理はできます）。"))
        self._set_enabled(False)

    def _seek_started(self) -> None:
        self._seeking = True

    def _seek_moved(self, value: int) -> None:
        self._update_time(value)

    def _seek_finished(self) -> None:
        self._seeking = False
        if self.player is not None:
            self.player.setPosition(self.seek.value())

    def _mute_toggled(self, muted: bool) -> None:
        if self.audio is not None:
            self.audio.setMuted(muted)
        self.mute_button.setIcon(svg_icon("volume-off" if muted else "volume", 18))
        label = tr("消音を解除") if muted else tr("消音")
        self.mute_button.setAccessibleName(label)
        self.mute_button.setToolTip(label)

    # helpers ------------------------------------------------------------------------

    def _update_time(self, position: int | None = None) -> None:
        current = self.seek.value() if position is None else position
        self.time_label.setText(f"{format_clock(current)} / {format_clock(self.seek.maximum())}")

    def _show_message(self, text: str) -> None:
        self.message.setText(text)
        self.stack.setCurrentIndex(2)

    def _set_enabled(self, enabled: bool) -> None:
        for widget in (self.play_button, self.seek, self.mute_button):
            widget.setEnabled(enabled)
