from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from .controls import NoWheelComboBox, NoWheelDoubleSpinBox
from .i18n import tr
from .icons import icon


class FrameLoader(QThread):
    loaded = Signal(QImage, float)
    failed = Signal(str)

    def __init__(self, path: Path, ratio: float, parent=None):
        super().__init__(parent)
        self.path, self.ratio = path, ratio

    def run(self):
        import cv2

        cap = cv2.VideoCapture(str(self.path))
        try:
            if not cap.isOpened():
                raise ValueError(tr("動画を読み込めませんでした。"))
            fps = cap.get(cv2.CAP_PROP_FPS)
            duration = cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps if fps > 0 else 0
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0, duration - 0.1) * self.ratio * 1000)
            ok, frame = cap.read()
            if not ok:
                raise ValueError(tr("動画フレームを読み込めませんでした。"))
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = QImage(
                rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format_RGB888
            ).copy()
            self.loaded.emit(image, duration)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            cap.release()


class RegionCanvas(QWidget):
    selected = Signal(tuple)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = QImage()
        self.slide = (0.0, 0.0, 1.0, 1.0)
        self.page = (0.75, 0.8, 0.25, 0.2)
        self.mode = "slide"
        self.anchor = None
        self.show_page = True
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAccessibleName(tr("スライド範囲プレビュー"))
        self.setCursor(Qt.CrossCursor)

    def image_rect(self):
        if self.image.isNull():
            return QRectF()
        size = self.image.size().scaled(self.size(), Qt.KeepAspectRatio)
        return QRectF(
            (self.width() - size.width()) / 2,
            (self.height() - size.height()) / 2,
            size.width(),
            size.height(),
        )

    def slide_rect(self):
        return self.map_region(self.image_rect(), self.slide)

    @staticmethod
    def map_region(bounds, region):
        x, y, width, height = region
        return QRectF(
            bounds.x() + x * bounds.width(),
            bounds.y() + y * bounds.height(),
            width * bounds.width(),
            height * bounds.height(),
        )

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#111719"))
        if self.image.isNull():
            return
        painter.drawImage(self.image_rect(), self.image)
        painter.setPen(QPen(QColor("#2ed1c5"), 2))
        painter.drawRect(self.slide_rect())
        if self.show_page:
            painter.setPen(QPen(QColor("#ffcb6b"), 2, Qt.DashLine))
            painter.drawRect(self.map_region(self.slide_rect(), self.page))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self.image.isNull():
            self.anchor = event.position()

    def mouseMoveEvent(self, event):
        if self.anchor is None:
            return
        bounds = self.image_rect() if self.mode == "slide" else self.slide_rect()
        rect = QRectF(self.anchor, event.position()).normalized().intersected(bounds)
        if rect.width() < 4 or rect.height() < 4:
            return
        region = (
            (rect.x() - bounds.x()) / bounds.width(),
            (rect.y() - bounds.y()) / bounds.height(),
            rect.width() / bounds.width(),
            rect.height() / bounds.height(),
        )
        if self.mode == "slide":
            self.slide = region
        else:
            self.page = region
        self.selected.emit(region)
        self.update()

    def mouseReleaseEvent(self, event):
        if self.anchor is not None:
            self.mouseMoveEvent(event)
        self.anchor = None


def format_time(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def video_key(path: Path) -> str:
    return str(Path(path).resolve())


class RegionSelector(QDialog):
    """Choose the slide crop, optionally changing it at given times per video.

    ``common_region`` applies to every video without its own timeline.
    ``schedules`` maps a resolved video path to ``[[start_seconds, region], ...]``
    where region is ``(x, y, w, h)`` or ``None`` for automatic detection.
    """

    def __init__(
        self,
        videos: list[Path],
        slide_region,
        page_region,
        parent=None,
        schedules: dict[str, list] | None = None,
        use_page_region: bool = True,
    ):
        super().__init__(parent)
        self.setWindowTitle(tr("画面で検出範囲を指定"))
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        self.setWindowFlag(Qt.WindowMinimizeButtonHint, True)
        self.setSizeGripEnabled(True)
        self.resize(1080, 720)
        self.setMinimumSize(640, 480)
        self.loader = None
        self.pending_ratio = None
        self.closing = False
        self.auto_detect = False
        self.videos = videos
        self.common_region = tuple(slide_region) if slide_region else None
        self.schedules: dict[str, list] = {
            key: [[float(start), tuple(region) if region else None] for start, region in value]
            for key, value in (schedules or {}).items()
        }
        self.use_page_region = use_page_region
        self.duration = 0.0
        self.segment_index = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        self.source = NoWheelComboBox()
        for video in videos:
            self.source.addItem(video.name, video)
        layout.addWidget(self.source)

        body = QHBoxLayout()
        body.setSpacing(14)
        self.canvas = RegionCanvas()
        self.canvas.page = page_region
        self.canvas.show_page = use_page_region
        body.addWidget(self.canvas, 1)

        side = QVBoxLayout()
        side.setSpacing(8)
        timeline_title = QLabel(tr("時間ごとの範囲"))
        timeline_title.setObjectName("SectionTitle")
        side.addWidget(timeline_title)
        self.scope_label = QLabel()
        self.scope_label.setObjectName("SecondaryText")
        self.scope_label.setWordWrap(True)
        side.addWidget(self.scope_label)
        self.timeline = QListWidget()
        self.timeline.setObjectName("Timeline")
        self.timeline.setAccessibleName(tr("時間ごとの範囲"))
        self.timeline.setMinimumWidth(240)
        side.addWidget(self.timeline, 1)
        self.split_button = QPushButton(tr("この時刻から範囲を変える"))
        self.split_button.setIcon(icon("crop", 18))
        self.split_button.setToolTip(
            tr("動画の途中で画面の配置が変わる場合に、表示中の時刻から別の範囲を使います。")
        )
        self.remove_split_button = QPushButton(tr("区切りを削除"))
        self.common_button = QPushButton(tr("共通の範囲に戻す"))
        self.common_button.setToolTip(
            tr("このファイル専用の範囲を削除し、全ファイル共通の範囲を使います。")
        )
        for button in (self.split_button, self.remove_split_button, self.common_button):
            side.addWidget(button)
        side_widget = QWidget()
        side_widget.setLayout(side)
        side_widget.setFixedWidth(280)
        body.addWidget(side_widget)
        layout.addLayout(body, 1)

        seek_row = QHBoxLayout()
        self.seek = QSlider(Qt.Horizontal)
        self.seek.setRange(0, 1000)
        self.seek.setAccessibleName(tr("プレビューの時刻"))
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setMinimumWidth(120)
        seek_row.addWidget(self.seek, 1)
        seek_row.addWidget(self.time_label)
        layout.addLayout(seek_row)
        self.status = QLabel(tr("動画を読み込んでいます…"))
        self.status.setObjectName("SecondaryText")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        controls = QHBoxLayout()
        self.mode = NoWheelComboBox()
        self.mode.addItem(tr("抽出するスライド範囲（緑）"), "slide")
        self.mode.addItem(tr("ページ番号の範囲（黄）"), "page")
        controls.addWidget(self.mode, 1)
        self.no_page_check = QCheckBox(tr("ページ番号なし"))
        self.no_page_check.setToolTip(
            tr("スライドにページ番号がない場合はオンにします。ページ番号の変化では判定しません。")
        )
        self.no_page_check.setChecked(not use_page_region)
        controls.addWidget(self.no_page_check)
        layout.addLayout(controls)

        numeric = QHBoxLayout()
        self.fields = []
        for title in ("X", "Y", "幅", "高さ"):
            form = QFormLayout()
            field = NoWheelDoubleSpinBox()
            field.setRange(0, 1)
            field.setDecimals(3)
            field.setSingleStep(0.01)
            field.setButtonSymbols(QAbstractSpinBox.NoButtons)
            field.setAccessibleName(tr(title))
            form.addRow(tr(title), field)
            numeric.addLayout(form)
            self.fields.append(field)
            field.valueChanged.connect(self._numeric_changed)
        layout.addLayout(numeric)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText(tr("適用"))
        self.buttons.button(QDialogButtonBox.Cancel).setText(tr("キャンセル"))
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        reset = QPushButton(tr("自動検出に戻す"))
        reset.setToolTip(tr("選択中の区間を、範囲を指定しない自動検出に戻します。"))
        self.buttons.addButton(reset, QDialogButtonBox.ResetRole)
        layout.addWidget(self.buttons)

        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        reset.clicked.connect(self._reset)
        self.mode.currentIndexChanged.connect(self._change_mode)
        self.no_page_check.toggled.connect(self._page_toggled)
        self.canvas.selected.connect(self._selection_changed)
        self.split_button.clicked.connect(self.split_here)
        self.remove_split_button.clicked.connect(self.remove_split)
        self.common_button.clicked.connect(self.use_common_region)
        self.timeline.itemClicked.connect(self._timeline_clicked)
        self.seek_timer = QTimer(self)
        self.seek_timer.setSingleShot(True)
        self.seek_timer.setInterval(200)
        self.seek_timer.timeout.connect(lambda: self.load_frame(self.seek.value() / 1000))
        self.seek.valueChanged.connect(self._seek_moved)
        self.source.currentIndexChanged.connect(self._source_changed)
        self._page_toggled(self.no_page_check.isChecked())
        self._sync_segment()
        self.load_frame(0)

    # Timeline model ---------------------------------------------------------------

    @property
    def current_key(self) -> str:
        return video_key(self.source.currentData())

    def has_own_schedule(self) -> bool:
        return self.current_key in self.schedules

    def segments(self) -> list:
        if self.has_own_schedule():
            return self.schedules[self.current_key]
        return [[0.0, self.common_region]]

    def current_time(self) -> float:
        return self.duration * self.seek.value() / 1000

    def _segment_index_at(self, seconds: float) -> int:
        index = 0
        for position, (start, _region) in enumerate(self.segments()):
            if start <= seconds + 1e-6:
                index = position
        return index

    def _current_region(self):
        return self.segments()[self.segment_index][1]

    def _set_current_region(self, region) -> None:
        region = tuple(region) if region is not None else None
        if self.has_own_schedule():
            self.schedules[self.current_key][self.segment_index][1] = region
        else:
            self.common_region = region
        self._refresh_timeline()

    def _sync_segment(self) -> None:
        self.segment_index = min(
            self._segment_index_at(self.current_time()), len(self.segments()) - 1
        )
        region = self._current_region()
        self.canvas.slide = region if region is not None else (0.0, 0.0, 1.0, 1.0)
        self.canvas.update()
        self._refresh_timeline()
        self._change_mode()

    def _refresh_timeline(self) -> None:
        self.timeline.blockSignals(True)
        self.timeline.clear()
        for start, region in self.segments():
            text = (
                tr("自動検出") if region is None else ", ".join(f"{value:.2f}" for value in region)
            )
            self.timeline.addItem(QListWidgetItem(f"{format_time(start)}〜   {text}"))
        self.timeline.setCurrentRow(self.segment_index)
        self.timeline.blockSignals(False)
        own = self.has_own_schedule()
        self.scope_label.setText(
            tr("このファイル専用の範囲です。")
            if own
            else tr("全ファイル共通の範囲です。途中で配置が変わる場合は区切りを追加します。")
        )
        self.remove_split_button.setEnabled(own and self.segment_index > 0)
        self.common_button.setEnabled(own)

    def split_here(self) -> None:
        seconds = round(self.current_time(), 1)
        if seconds < 0.5:
            QMessageBox.information(
                self,
                tr("時間ごとの範囲"),
                tr("スライダーで範囲を変えたい時刻に移動してから押してください。"),
            )
            return
        segments = [list(item) for item in self.segments()]
        if any(abs(start - seconds) < 0.5 for start, _ in segments):
            return
        current = segments[self._segment_index_at(seconds)][1]
        segments.append([seconds, current])
        segments.sort(key=lambda item: item[0])
        self.schedules[self.current_key] = segments
        self._sync_segment()
        self.status.setText(
            tr("新しい区間を追加しました。緑の枠をドラッグして、この時刻以降の範囲を指定します。")
        )

    def remove_split(self) -> None:
        if not self.has_own_schedule() or self.segment_index == 0:
            return
        del self.schedules[self.current_key][self.segment_index]
        self._sync_segment()

    def use_common_region(self) -> None:
        self.schedules.pop(self.current_key, None)
        self._sync_segment()

    def result_schedules(self) -> dict[str, list]:
        return {
            key: [[start, list(region) if region is not None else None] for start, region in value]
            for key, value in self.schedules.items()
            if value
        }

    def _timeline_clicked(self, item) -> None:
        row = self.timeline.row(item)
        start = self.segments()[row][0]
        if self.duration > 0:
            self.seek.setValue(int(min(1000, (start + 0.2) / self.duration * 1000)))
        self.segment_index = row
        self._sync_segment()

    # UI events ----------------------------------------------------------------------

    def _seek_moved(self) -> None:
        self.time_label.setText(
            f"{format_time(self.current_time())} / {format_time(self.duration)}"
        )
        self._sync_segment()
        self.seek_timer.start()

    def _source_changed(self) -> None:
        self.duration = 0.0
        self._sync_segment()
        self.load_frame(self.seek.value() / 1000)

    def _page_toggled(self, no_page: bool) -> None:
        self.use_page_region = not no_page
        self.canvas.show_page = not no_page
        model = self.mode.model()
        item = model.item(1) if hasattr(model, "item") else None
        if item is not None:
            item.setEnabled(not no_page)
        if no_page and self.mode.currentData() == "page":
            self.mode.setCurrentIndex(0)
        self.canvas.update()

    def _reset(self):
        self._set_current_region(None)
        self._sync_segment()

    def _change_mode(self):
        self.canvas.mode = self.mode.currentData()
        self._show_fields(self.canvas.slide if self.canvas.mode == "slide" else self.canvas.page)

    def _show_fields(self, region) -> None:
        for field, value in zip(self.fields, region):
            field.blockSignals(True)
            field.setValue(value)
            field.blockSignals(False)

    def _selection_changed(self, region):
        if self.canvas.mode == "slide":
            self._set_current_region(region)
        self._show_fields(region)
        if not self.canvas.image.isNull():
            self.buttons.button(QDialogButtonBox.Ok).setEnabled(True)

    def _numeric_changed(self):
        from movie_slide_notes.core.processor import valid_region

        region = tuple(field.value() for field in self.fields)
        if not valid_region(region):
            self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
            self.status.setText(tr("範囲が画面をはみ出しています。"))
            return
        if self.canvas.mode == "slide":
            self.canvas.slide = region
            self._set_current_region(region)
        else:
            self.canvas.page = region
        self.canvas.update()
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(not self.canvas.image.isNull())

    def load_frame(self, ratio):
        if self.loader is not None and self.loader.isRunning():
            self.pending_ratio = ratio
            return
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        self.status.setText(tr("動画を読み込んでいます…"))
        self.loader = FrameLoader(self.source.currentData(), ratio, self)
        self.loader.loaded.connect(lambda image, duration: self._loaded(image, duration, ratio))
        self.loader.failed.connect(self.status.setText)
        self.loader.finished.connect(self._loader_finished)
        self.loader.start()

    def _loaded(self, image, duration, ratio):
        self.canvas.image = image
        self.duration = float(duration)
        self.time_label.setText(f"{format_time(self.current_time())} / {format_time(duration)}")
        self._sync_segment()
        self.status.setText(
            tr("ドラッグまたは数値で指定できます。ページ番号の位置は緑の範囲の内側です。")
        )
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(True)

    def _loader_finished(self):
        if self.closing:
            super().done(self._pending_result)
        elif self.pending_ratio is not None:
            ratio, self.pending_ratio = self.pending_ratio, None
            self.load_frame(ratio)

    def done(self, result):
        self.seek_timer.stop()
        if self.loader is not None and self.loader.isRunning():
            self.closing = True
            self.pending_ratio = None
            self._pending_result = result
            return
        super().done(result)
