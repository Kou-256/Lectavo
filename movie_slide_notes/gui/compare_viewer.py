"""Full-window, side-by-side comparison of the original and translated PDFs.

Each side shows all pages one below the other, like a PDF reader, so the wheel
scrolls smoothly from one page into the next. With "scroll together" on, the
other side follows to the same place on the same page; with it off, each side
scrolls on its own.
"""

from __future__ import annotations

import logging
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
PAGE_GAP = 16
# Pages this far from the visible ones keep no image, to bound memory use.
KEEP_RENDERED = 3


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
    """A caption and a scroll area holding every page of one PDF."""

    def __init__(self, caption: str, pages: int):
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
        self.scroll.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.content = QWidget()
        column = QVBoxLayout(self.content)
        column.setContentsMargins(PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)
        column.setSpacing(PAGE_GAP)
        column.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.pages: list[QLabel] = []
        for _ in range(pages):
            page = QLabel()
            page.setAlignment(Qt.AlignCenter)
            # White until rendered, so the layout does not jump while scrolling.
            page.setStyleSheet("background: white;")
            column.addWidget(page, 0, Qt.AlignHCenter)
            self.pages.append(page)
        self.scroll.setWidget(self.content)
        # The zoom each page's image was rendered at (absent = not rendered).
        self.rendered: dict[int, float] = {}
        layout.addWidget(self.caption)
        layout.addWidget(self.scroll, 1)

    @property
    def bar(self):
        return self.scroll.verticalScrollBar()

    def position(self) -> tuple[int, float]:
        """The page at the top of the view and how far down it the view starts (0..1)."""
        top = self.bar.value()
        for index, page in enumerate(self.pages):
            # A page owns the gap below it.
            span = page.height() + PAGE_GAP
            if top < page.y() + span or index == len(self.pages) - 1:
                return index, max(0.0, (top - page.y()) / max(1, span))
        return 0, 0.0

    def scroll_to(self, index: int, fraction: float = 0.0) -> None:
        page = self.pages[index]
        self.bar.setValue(max(0, page.y() + round(fraction * (page.height() + PAGE_GAP))))

    def show_page(self, index: int) -> None:
        """Scroll so page ``index`` starts just below the top, with a little margin."""
        self.bar.setValue(max(0, self.pages[index].y() - PAGE_MARGIN // 2))

    def current_page(self) -> int:
        """The page under the upper third of the view: the one being read."""
        mark = self.bar.value() + self.scroll.viewport().height() // 3
        for index, page in enumerate(self.pages):
            if mark < page.y() + page.height() + PAGE_GAP:
                return index
        return len(self.pages) - 1

    def visible_pages(self) -> range:
        top = self.bar.value()
        bottom = top + self.scroll.viewport().height()
        shown = [
            index
            for index, page in enumerate(self.pages)
            if page.y() < bottom and page.y() + page.height() > top
        ]
        if not shown:
            return range(0)
        return range(shown[0], shown[-1] + 1)


class CompareViewer(QDialog):
    """Maximized window with the two PDFs side by side, zoomed together."""

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
        self.pages = max(1, pages)
        self.sizes = [[source.size(i) for i in range(self.pages)] for source in self.sources]
        self.index = max(0, min(self.pages - 1, start_index))
        self.fit_mode = "width"  # "width", "page" or "" (manual zoom)
        self.zoom = 1.0
        self._applied_zoom = 0.0
        self._syncing = False
        # The pane the user scrolled last; the page number follows it.
        self._leader = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(10)
        layout.addLayout(self._toolbar())
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.panes = [_Pane(tr(captions[0]), self.pages), _Pane(tr(captions[1]), self.pages)]
        for pane in self.panes:
            self.splitter.addWidget(pane)
            pane.scroll.viewport().installEventFilter(self)
            pane.bar.valueChanged.connect(self._scrolled)
            pane.scroll.horizontalScrollBar().valueChanged.connect(self._scrolled_sideways)
        layout.addWidget(self.splitter, 1)

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(40)
        self._render_timer.timeout.connect(self._render_visible)
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.setInterval(80)
        self._layout_timer.timeout.connect(self._apply_zoom)

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
        self._update_controls()

    # toolbar ------------------------------------------------------------------

    def _toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.prev_button = QPushButton()
        self.prev_button.setIcon(icon("move-left", 18))
        self.prev_button.setToolTip(tr("前のページ"))
        self.page_spin = NoWheelSpinBox()
        self.page_spin.setRange(1, self.pages)
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
        self.sync_check.toggled.connect(self._sync_toggled)
        self.full_screen_button.clicked.connect(self.toggle_full_screen)
        self.close_button.clicked.connect(self.close)
        return bar

    # navigation and zoom ------------------------------------------------------

    def go_to(self, index: int) -> None:
        """Scroll both sides to the top of page ``index``."""
        index = max(0, min(self.pages - 1, index))
        self._ensure_layout()
        self._syncing = True
        try:
            for pane in self.panes:
                pane.show_page(index)
        finally:
            self._syncing = False
        self.index = index
        self._update_controls()
        self._render_visible()

    def next_page(self) -> None:
        self.go_to(self.index + 1)

    def previous_page(self) -> None:
        self.go_to(self.index - 1)

    def set_fit(self, mode: str) -> None:
        self.fit_mode = mode
        self._apply_zoom()

    def _effective_zoom(self) -> float:
        if not self.fit_mode:
            return self.zoom
        viewport = self.panes[0].scroll.viewport()
        # Leave room for the vertical scroll bar so fitting never adds a horizontal one.
        bar = self.panes[0].bar.sizeHint().width()
        available_w = max(100, viewport.width() - 2 * PAGE_MARGIN - bar - 4)
        available_h = max(100, viewport.height() - 2 * PAGE_MARGIN)
        width = max(w for sizes in self.sizes for w, _h in sizes)
        zoom = available_w / width
        if self.fit_mode == "page":
            height = max(h for sizes in self.sizes for _w, h in sizes)
            zoom = min(zoom, available_h / height)
        return max(0.1, zoom)

    def zoom_in(self) -> None:
        current = self._effective_zoom()
        self.zoom = next((step for step in ZOOM_STEPS if step > current + 0.01), ZOOM_STEPS[-1])
        self.fit_mode = ""
        self._apply_zoom()

    def zoom_out(self) -> None:
        current = self._effective_zoom()
        smaller = [step for step in ZOOM_STEPS if step < current - 0.01]
        self.zoom = smaller[-1] if smaller else ZOOM_STEPS[0]
        self.fit_mode = ""
        self._apply_zoom()

    def toggle_full_screen(self) -> None:
        if self.isFullScreen():
            self.showMaximized()
        else:
            self.showFullScreen()

    # layout and rendering -----------------------------------------------------

    def _ensure_layout(self) -> None:
        if not self._applied_zoom:
            self._apply_zoom()

    def _apply_zoom(self) -> None:
        """Size every page for the current zoom, keeping each side at the same place."""
        zoom = self._effective_zoom()
        changed = abs(zoom - self._applied_zoom) > 1e-4
        places = [pane.position() for pane in self.panes] if self._applied_zoom else None
        if changed:
            for pane, sizes in zip(self.panes, self.sizes, strict=True):
                for page, (width, height) in zip(pane.pages, sizes, strict=True):
                    page.setFixedSize(max(1, round(width * zoom)), max(1, round(height * zoom)))
                pane.content.layout().activate()
                pane.content.adjustSize()
            self._applied_zoom = zoom
        self._syncing = True
        try:
            for position, pane in enumerate(self.panes):
                if places is not None:
                    pane.scroll_to(*places[position])
                else:
                    pane.show_page(self.index)
        finally:
            self._syncing = False
        self.zoom_label.setText(f"{round(zoom * 100)}%")
        self.fit_width_button.setChecked(self.fit_mode == "width")
        self.fit_page_button.setChecked(self.fit_mode == "page")
        self._render_visible()

    def _render_visible(self) -> None:
        zoom = self._applied_zoom
        if not zoom:
            return
        ratio = self.devicePixelRatioF()
        for pane, source in zip(self.panes, self.sources, strict=True):
            visible = pane.visible_pages()
            if not visible:
                continue
            wanted = range(max(0, visible.start - 1), min(self.pages, visible.stop + 1))
            for index in wanted:
                if abs(pane.rendered.get(index, 0.0) - zoom) < 1e-4:
                    continue
                try:
                    pixmap = source.render(index, zoom * ratio)
                except Exception:
                    logging.getLogger("lectavo").exception("Comparison rendering failed")
                    pane.pages[index].setText(tr("プレビューを表示できません"))
                    pane.rendered[index] = zoom
                    continue
                pixmap.setDevicePixelRatio(ratio)
                pane.pages[index].setPixmap(pixmap)
                pane.rendered[index] = zoom
            for index in list(pane.rendered):
                if index < wanted.start - KEEP_RENDERED or index >= wanted.stop + KEEP_RENDERED:
                    pane.pages[index].clear()
                    del pane.rendered[index]

    def _update_controls(self) -> None:
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(self.index + 1)
        self.page_spin.blockSignals(False)
        self.prev_button.setEnabled(self.index > 0)
        self.next_button.setEnabled(self.index < self.pages - 1)

    # scrolling ----------------------------------------------------------------

    def _scrolled(self, _value: int) -> None:
        if self._syncing:
            return
        sender = self.sender()
        source = next((p for p in self.panes if p.bar is sender), self.panes[0])
        self._leader = self.panes.index(source)
        if self.sync_check.isChecked():
            self._follow(source)
        self.index = source.current_page()
        self._update_controls()
        self._render_timer.start()

    def _follow(self, source: _Pane) -> None:
        """Move the other side to the same place on the same page."""
        place = source.position()
        self._syncing = True
        try:
            for pane in self.panes:
                if pane is not source:
                    pane.scroll_to(*place)
        finally:
            self._syncing = False

    def _scrolled_sideways(self, _value: int) -> None:
        if self._syncing or not self.sync_check.isChecked():
            return
        sender = self.sender()
        self._syncing = True
        try:
            for pane in self.panes:
                bar = pane.scroll.horizontalScrollBar()
                if bar is not sender:
                    span = max(1, sender.maximum())
                    bar.setValue(round(sender.value() / span * bar.maximum()))
        finally:
            self._syncing = False

    def _sync_toggled(self, on: bool) -> None:
        if on:
            self._follow(self.panes[self._leader])
            self._render_timer.start()

    # events -------------------------------------------------------------------

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Wheel and event.modifiers() & Qt.ControlModifier:
            if event.angleDelta().y() > 0:
                self.zoom_in()
            elif event.angleDelta().y() < 0:
                self.zoom_out()
            return True
        return super().eventFilter(watched, event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._applied_zoom:
            self._apply_zoom()
        self._layout_timer.start()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.fit_mode:
            self._layout_timer.start()
        else:
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
