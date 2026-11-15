from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from .controls import NoWheelComboBox, NoWheelDoubleSpinBox
from .i18n import tr


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
        self.setMinimumHeight(180)
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


class RegionSelector(QDialog):
    def __init__(self, videos: list[Path], slide_region, page_region, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("画面で検出範囲を指定"))
        self.resize(900, 680)
        self.setMinimumSize(540, 460)
        self.loader = None
        self.pending_ratio = None
        self.closing = False
        self.auto_detect = False
        self.videos = videos
        layout = QVBoxLayout(self)
        self.source = NoWheelComboBox()
        for video in videos:
            self.source.addItem(video.name, video)
        layout.addWidget(self.source)
        self.canvas = RegionCanvas()
        self.canvas.slide = slide_region or (0, 0, 1, 1)
        self.canvas.page = page_region
        layout.addWidget(self.canvas, 1)
        self.status = QLabel(tr("動画を読み込んでいます…"))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.seek = QSlider(Qt.Horizontal)
        self.seek.setRange(0, 1000)
        self.seek.setAccessibleName(tr("プレビューの時刻"))
        layout.addWidget(self.seek)
        self.mode = NoWheelComboBox()
        self.mode.addItem(tr("抽出するスライド範囲（緑）"), "slide")
        self.mode.addItem(tr("ページ番号の範囲（黄）"), "page")
        layout.addWidget(self.mode)
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
        self.buttons.addButton(reset, QDialogButtonBox.ResetRole)
        layout.addWidget(self.buttons)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        reset.clicked.connect(self._reset)
        self.mode.currentIndexChanged.connect(self._change_mode)
        self.canvas.selected.connect(self._selection_changed)
        self.seek_timer = QTimer(self)
        self.seek_timer.setSingleShot(True)
        self.seek_timer.setInterval(200)
        self.seek_timer.timeout.connect(lambda: self.load_frame(self.seek.value() / 1000))
        self.seek.valueChanged.connect(lambda: self.seek_timer.start())
        self.source.currentIndexChanged.connect(lambda: self.load_frame(self.seek.value() / 1000))
        self._change_mode()
        self.load_frame(0)

    def _reset(self):
        self.auto_detect = True
        self.accept()

    def _change_mode(self):
        self.canvas.mode = self.mode.currentData()
        self._selection_changed(
            self.canvas.slide if self.canvas.mode == "slide" else self.canvas.page
        )

    def _selection_changed(self, region):
        for field, value in zip(self.fields, region):
            field.blockSignals(True)
            field.setValue(value)
            field.blockSignals(False)
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
        self.canvas.update()
        self.status.setText(
            tr("ドラッグまたは数値で指定できます。ページ番号の位置は緑の範囲の内側です。")
            + f"  {duration * ratio:.1f}s / {duration:.1f}s"
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
