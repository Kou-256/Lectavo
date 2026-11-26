"""Full-window, side-by-side comparison of the original and translated PDFs."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from movie_slide_notes.gui.controls import NoWheelSpinBox
from movie_slide_notes.gui.i18n import tr
from movie_slide_notes.gui.icons import icon

ZOOM_STEPS = [0.25, 0.33, 0.5, 0.67, 0.75, 0.9, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0]
PAGE_MARGIN = 24
# After a page turn by scrolling, wheel events are ignored briefly so one flick turns one page.
PAGE_TURN_PAUSE = 0.35


@dataclass
class PdfSide:
    """One side of the comparison: a PDF, its first page, and an optional half to show."""

    path: Path
    first_index: int = 0
    half: str = ""  # "" = whole page, "right" = right half (bilingual sheets)


class _PageSource:
    def __init__(self, side: PdfSide):
        import pypdfium2 as pdfium

        self.side = side
        self.document = pdfium.PdfDocument(str(side.path))

    def size(self, index: int) -> tuple[float, float]:
        page = self.document[self.side.first_index + index]
        try:
            width, height = page.get_width(), page.get_height()
        finally:
            page.close()
        if self.side.half:
            width /= 2
        return width, height

    def render(self, index: int, scale: float) -> QPixmap:
        page = self.document[self.side.first_index + index]
        try:
            image = page.render(scale=scale).to_pil().convert("RGB")
        finally:
            page.close()
        if self.side.half == "right":
            image = image.crop((image.width // 2, 0, image.width, image.height))
        data = image.tobytes()
        qimage = QImage(data, image.width, image.height, image.width * 3, QImage.Format_RGB888)
        return QPixmap.fromImage(qimage.copy())

    def close(self) -> None:
        self.document.close()


class _Pane(QFrame):
    def __init__(self, caption: str):
        super().__init__()
        self.setObjectName("PreviewPane")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(6)
        self.caption = QLabel(caption)
        self.caption.setObjectName("PreviewCaption")
        self.caption.setAlignment(Qt.AlignCenter)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("PageScroll")
        self.scroll.setAlignment(Qt.AlignCenter)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setContentsMargins(PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)
        self.scroll.setWidget(self.image)
        self.scroll.setWidgetResizable(True)
        layout.addWidget(self.caption)
        layout.addWidget(self.scroll, 1)


class CompareViewer(QDialog):
    """Maximized window with the two PDFs side by side, zoomed and scrolled together."""

    def __init__(
        self,
        before: PdfSide,
        after: PdfSide,
        pages: int,
        start_index: int = 0,
        captions: tuple[str, str] = ("翻訳前", "翻訳後"),
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(tr("翻訳前と翻訳後を比較"))
        self.setWindowFlags(Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.sources = [_PageSource(before), _PageSource(after)]
        self.pages = pages
        self.index = max(0, min(pages - 1, start_index))
        self.fit_mode = "width"  # "width", "page" or "" (manual zoom)
        self.zoom = 1.0
        self._syncing = False
        self._last_turn = 0.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(10)
        layout.addLayout(self._toolbar())
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.panes = [_Pane(tr(captions[0])), _Pane(tr(captions[1]))]
        for pane in self.panes:
            self.splitter.addWidget(pane)
            pane.scroll.viewport().installEventFilter(self)
            for bar in (pane.scroll.verticalScrollBar(), pane.scroll.horizontalScrollBar()):
                bar.valueChanged.connect(self._sync_scroll)
        layout.addWidget(self.splitter, 1)

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(80)
        self._render_timer.timeout.connect(self._render)

        for keys, slot in [
            (QKeySequence.MoveToNextPage, self.next_page),
            (QKeySequence.MoveToPreviousPage, self.previous_page),
            (QKeySequence(Qt.Key_Right), self.next_page),
            (QKeySequence(Qt.Key_Left), self.previous_page),
            (QKeySequence.ZoomIn, self.zoom_in),
            (QKeySequence("Ctrl+="), self.zoom_in),
            (QKeySequence.ZoomOut, self.zoom_out),
            (QKeySequence("Ctrl+0"), lambda: self.set_fit("width")),
            (QKeySequence(Qt.Key_F11), self.toggle_full_screen),
        ]:
            QShortcut(keys, self, activated=slot)
        self.resize(1400, 900)

    # toolbar ------------------------------------------------------------------

    def _toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.prev_button = QPushButton()
        self.prev_button.setIcon(icon("move-left", 18))
        self.prev_button.setToolTip(tr("前のページ"))
        self.page_spin = NoWheelSpinBox()
        self.page_spin.setRange(1, max(1, self.pages))
        self.page_spin.setValue(self.index + 1)
        self.page_spin.setAccessibleName(tr("ページ"))
        # The arrows next to the number duplicated the previous/next buttons.
        self.page_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.page_spin.setAlignment(Qt.AlignCenter)
        self.page_spin.setFixedWidth(64)
        self.page_total = QLabel(f"/ {self.pages}")
        self.next_button = QPushButton()
        self.next_button.setIcon(icon("move-right", 18))
        self.next_button.setToolTip(tr("次のページ"))
        for widget in (self.prev_button, self.page_spin, self.page_total, self.next_button):
            bar.addWidget(widget)
        bar.addSpacing(16)
        self.fit_width_button = QPushButton(tr("幅に合わせる"))
        self.fit_page_button = QPushButton(tr("ページ全体"))
        for button in (self.fit_width_button, self.fit_page_button):
            button.setCheckable(True)
        self.zoom_out_button = QPushButton("−")
        self.zoom_out_button.setToolTip(tr("縮小（Ctrl+マウスホイールでも操作できます）"))
        self.zoom_label = QLabel()
        self.zoom_label.setMinimumWidth(52)
        self.zoom_label.setAlignment(Qt.AlignCenter)
        self.zoom_in_button = QPushButton("+")
        self.zoom_in_button.setToolTip(tr("拡大（Ctrl+マウスホイールでも操作できます）"))
        for widget in (
            self.fit_width_button,
            self.fit_page_button,
            self.zoom_out_button,
            self.zoom_label,
            self.zoom_in_button,
        ):
            bar.addWidget(widget)
        bar.addSpacing(16)
        self.sync_check = QCheckBox(tr("スクロールを連動"))
        self.sync_check.setChecked(True)
        bar.addWidget(self.sync_check)
        bar.addStretch(1)
        self.full_screen_button = QPushButton(tr("全画面（F11）"))
        self.close_button = QPushButton(tr("閉じる"))
        bar.addWidget(self.full_screen_button)
        bar.addWidget(self.close_button)

        self.prev_button.clicked.connect(self.previous_page)
        self.next_button.clicked.connect(self.next_page)
        self.page_spin.valueChanged.connect(lambda value: self.go_to(value - 1))
        self.fit_width_button.clicked.connect(lambda: self.set_fit("width"))
        self.fit_page_button.clicked.connect(lambda: self.set_fit("page"))
        self.zoom_in_button.clicked.connect(self.zoom_in)
        self.zoom_out_button.clicked.connect(self.zoom_out)
        self.full_screen_button.clicked.connect(self.toggle_full_screen)
        self.close_button.clicked.connect(self.close)
        return bar

    # navigation and zoom ------------------------------------------------------

    def go_to(self, index: int, at_end: bool = False) -> None:
        """Show page ``index``, scrolled to its top (or to its bottom with ``at_end``)."""
        index = max(0, min(self.pages - 1, index))
        if index == self.index and self.panes[0].image.pixmap() is not None:
            return
        self.index = index
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(index + 1)
        self.page_spin.blockSignals(False)
        self._render()
        self._scroll_to(at_end)
        if at_end:
            # The scroll range follows the new page size only after the layout runs.
            QTimer.singleShot(0, lambda: self._scroll_to(True))

    def _scroll_to(self, at_end: bool) -> None:
        self._syncing = True
        try:
            for pane in self.panes:
                bar = pane.scroll.verticalScrollBar()
                bar.setValue(bar.maximum() if at_end else 0)
        finally:
            self._syncing = False

    def next_page(self) -> None:
        self.go_to(self.index + 1)

    def previous_page(self) -> None:
        self.go_to(self.index - 1)

    def set_fit(self, mode: str) -> None:
        self.fit_mode = mode
        self._render()

    def _effective_zoom(self) -> float:
        if not self.fit_mode:
            return self.zoom
        viewport = self.panes[0].scroll.viewport()
        # Leave room for the vertical scroll bar so fitting never adds a horizontal one.
        bar = self.panes[0].scroll.verticalScrollBar().sizeHint().width()
        available_w = max(100, viewport.width() - 2 * PAGE_MARGIN - bar - 4)
        available_h = max(100, viewport.height() - 2 * PAGE_MARGIN)
        zooms = []
        for source in self.sources:
            width, height = source.size(self.index)
            zoom = available_w / width
            if self.fit_mode == "page":
                zoom = min(zoom, available_h / height)
            zooms.append(zoom)
        return max(0.1, min(zooms))

    def zoom_in(self) -> None:
        current = self._effective_zoom()
        self.zoom = next((step for step in ZOOM_STEPS if step > current + 0.01), ZOOM_STEPS[-1])
        self.fit_mode = ""
        self._render()

    def zoom_out(self) -> None:
        current = self._effective_zoom()
        smaller = [step for step in ZOOM_STEPS if step < current - 0.01]
        self.zoom = smaller[-1] if smaller else ZOOM_STEPS[0]
        self.fit_mode = ""
        self._render()

    def toggle_full_screen(self) -> None:
        if self.isFullScreen():
            self.showMaximized()
        else:
            self.showFullScreen()

    # rendering ----------------------------------------------------------------

    def _render(self) -> None:
        zoom = self._effective_zoom()
        ratio = self.devicePixelRatioF()
        for pane, source in zip(self.panes, self.sources, strict=True):
            try:
                pixmap = source.render(self.index, zoom * ratio)
            except Exception:
                logging.getLogger("lectavo").exception("Comparison rendering failed")
                pane.image.setText(tr("プレビューを表示できません"))
                continue
            pixmap.setDevicePixelRatio(ratio)
            pane.image.setPixmap(pixmap)
        self.zoom_label.setText(f"{round(zoom * 100)}%")
        self.fit_width_button.setChecked(self.fit_mode == "width")
        self.fit_page_button.setChecked(self.fit_mode == "page")
        self.prev_button.setEnabled(self.index > 0)
        self.next_button.setEnabled(self.index < self.pages - 1)

    def _sync_scroll(self, _value: int) -> None:
        if self._syncing or not self.sync_check.isChecked():
            return
        sender = self.sender()
        self._syncing = True
        try:
            for pane in self.panes:
                for bar in (pane.scroll.verticalScrollBar(), pane.scroll.horizontalScrollBar()):
                    if bar is sender or bar.orientation() != sender.orientation():
                        continue
                    span = max(1, sender.maximum())
                    bar.setValue(round(sender.value() / span * bar.maximum()))
        finally:
            self._syncing = False
        self._last_turn = 0.0

    # events -------------------------------------------------------------------

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Wheel:
            if event.modifiers() & Qt.ControlModifier:
                if event.angleDelta().y() > 0:
                    self.zoom_in()
                else:
                    self.zoom_out()
                return True
            if self._turn_page_by_wheel(watched, event.angleDelta().y()):
                return True
        return super().eventFilter(watched, event)

    def _turn_page_by_wheel(self, viewport: QObject, delta: int) -> bool:
        """Scrolling past the bottom of a page shows the next one, past the top the previous."""
        pane = next((p for p in self.panes if p.scroll.viewport() is viewport), None)
        if pane is None or delta == 0:
            return False
        bar = pane.scroll.verticalScrollBar()
        down = delta < 0
        if down and (bar.value() < bar.maximum() or self.index >= self.pages - 1):
            return False
        if not down and (bar.value() > bar.minimum() or self.index <= 0):
            return False
        now = time.monotonic()
        if now - self._last_turn >= PAGE_TURN_PAUSE:
            self._last_turn = now
            if down:
                self.next_page()
            else:
                self.go_to(self.index - 1, at_end=True)
        return True

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._render_timer.start()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.fit_mode:
            self._render_timer.start()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape and self.isFullScreen():
            self.showMaximized()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        for source in self.sources:
            source.close()
        super().closeEvent(event)
