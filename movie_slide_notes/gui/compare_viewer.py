"""Full-window, side-by-side comparison of the original and translated PDFs.

Each side shows all pages one below the other, like a PDF reader, so the wheel
scrolls smoothly from one page into the next. With "scroll together" on, the
other side follows to the same place on the same page; with it off, each side
scrolls on its own.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
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


@dataclass
class PageChar:
    """One character of a shown page; ``rect`` is (left, top, right, bottom) in points."""

    text: str
    rect: tuple[float, float, float, float] | None


KEEP_TEXT_PAGES = 64
# Put between paragraphs, so copied text keeps them apart.
PARAGRAPH_BREAK = " "
_CJK = "　-ヿ㐀-鿿豈-﫿＀-￯"
# A line break with Japanese or Chinese text on either side of it.
_WRAPPED_CJK = re.compile(rf"(?<=[{_CJK}])\n(?=\S)|(?<=\S)\n(?=[{_CJK}])")


def _bounds(chars: list[PageChar]) -> tuple[float, float, float, float]:
    rects = [char.rect for char in chars if char.rect]
    return (
        min(r[0] for r in rects),
        min(r[1] for r in rects),
        max(r[2] for r in rects),
        max(r[3] for r in rects),
    )


def reading_order(chars: list[PageChar], width: float) -> list[PageChar]:
    """Characters in the order they are read: column by column on two-column pages.

    A PDF stores text in drawing order, which on a translated page puts every
    translation after the original text. Characters are grouped into lines and
    paragraphs, and paragraphs are ordered: a full-width one starts a new band,
    and within a band the left column comes before the right one.
    """
    lines: list[list[PageChar]] = []
    for char in chars:
        if char.rect is None:
            if lines:
                lines[-1].append(char)  # line breaks and spaces stay with their line
            continue
        if lines and any(c.rect for c in lines[-1]):
            left, top, right, bottom = _bounds(lines[-1])
            height = max(1.0, bottom - top)
            center = (char.rect[1] + char.rect[3]) / 2
            same_line = top - 0.3 * height <= center <= bottom + 0.3 * height
            if same_line and char.rect[0] >= left - height:
                lines[-1].append(char)
                continue
        lines.append([char])
    lines = [line for line in lines if any(c.rect for c in line)]

    blocks: list[list[list[PageChar]]] = []
    for line in lines:
        if blocks:
            left, top, right, bottom = _bounds(blocks[-1][-1])
            l2, t2, r2, b2 = _bounds(line)
            height = max(1.0, bottom - top)
            overlap = min(right, r2) - max(left, l2)
            below = -0.5 * height <= t2 - bottom <= 1.2 * height
            if below and overlap > 0.3 * min(right - left, r2 - l2):
                blocks[-1].append(line)
                continue
        blocks.append([line])

    def column(box) -> int:
        left, _top, right, _bottom = box
        if left < 0.4 * width and right > 0.6 * width:
            return 0  # full width
        return 1 if (left + right) / 2 < width / 2 else 2

    boxes = [_bounds([c for line in block for c in line]) for block in blocks]
    full_tops = sorted(box[1] for box in boxes if column(box) == 0)

    def key(position: int):
        box = boxes[position]
        band = sum(1 for top in full_tops if top <= box[1] + 0.5)
        return band, column(box), box[1], position

    ordered: list[PageChar] = []
    for position in sorted(range(len(blocks)), key=key):
        if ordered:
            ordered.append(PageChar(PARAGRAPH_BREAK, None))
        for line in blocks[position]:
            ordered.extend(line)
    return ordered


class _PageSource:
    def __init__(self, side: PdfSide):
        self.side = side
        self._texts: dict[int, list[PageChar]] = {}
        self._covers = None
        self.open()

    def open(self) -> None:
        import pypdfium2 as pdfium

        # Read into memory: an open file could not be replaced when the PDF is edited.
        self.document = pdfium.PdfDocument(self.side.path.read_bytes())
        self._texts.clear()
        self._covers = None

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

    def _hidden(self, index: int):
        """(characters of the original page, areas covered by the translation).

        A translated PDF still contains the original text under the patches
        that hide it; those characters must not be selected or copied.
        """
        if self._covers is None:
            self._covers = {}
            try:
                from movie_slide_notes.pdf_translate import edits

                document = edits.find(self.side.path)
                if document is not None:
                    import pypdfium2 as pdfium

                    original = pdfium.PdfDocument(document.original_path.read_bytes())
                    try:
                        for position, page in enumerate(document.pages):
                            textpage = original[page.index].get_textpage()
                            count = textpage.count_chars()
                            textpage.close()
                            rects = [
                                (item.x0 - 1, item.y0 - 1, item.x1 + 1, item.y1 + 1)
                                for item in page.items
                                if item.covers()
                            ]
                            self._covers[position] = (count, rects)
                    finally:
                        original.close()
            except Exception:
                logging.getLogger("lectavo").exception("Edit data could not be read")
        return self._covers.get(index)

    def chars(self, index: int) -> list[PageChar]:
        """The page's characters in content order; hidden ones have no text or box."""
        if index in self._texts:
            return self._texts[index]
        page = self.document[self.side.first_index + index]
        try:
            width, height = page.get_width(), page.get_height()
            textpage = page.get_textpage()
            try:
                chars = self._read_chars(textpage, index, width, height)
            finally:
                textpage.close()
        finally:
            page.close()
        if len(self._texts) >= KEEP_TEXT_PAGES:
            self._texts.pop(next(iter(self._texts)))
        self._texts[index] = chars
        return chars

    def _read_chars(self, textpage, index: int, width: float, height: float) -> list[PageChar]:
        hidden = self._hidden(index)
        right_half = self.side.half == "right"
        offset = width / 2 if right_half else 0.0
        # A bilingual sheet holds the original twice before the translation.
        originals = hidden[0] * (2 if right_half else 1) if hidden is not None else 0
        chars = []
        for position in range(textpage.count_chars()):
            text = textpage.get_text_range(position, 1)
            left, bottom, right, top = textpage.get_charbox(position)
            rect = None
            if right > left or top > bottom:
                center_x, center_y = (left + right) / 2, (bottom + top) / 2
                rect = (left - offset, height - top, right - offset, height - bottom)
                if right_half and center_x < offset:
                    text, rect = "", None  # the original half of the sheet
                elif hidden is not None and position < originals:
                    x = center_x - offset
                    if any(x0 <= x <= x1 and y0 <= center_y <= y1 for x0, y0, x1, y1 in hidden[1]):
                        text, rect = "", None
            chars.append(PageChar(text, rect))
        return reading_order([char for char in chars if char.text], width)

    def close(self) -> None:
        self.document.close()


class _PageLabel(QLabel):
    """A rendered page that paints the selected text over it."""

    def __init__(self):
        super().__init__()
        self.page_size = (1.0, 1.0)
        self.highlights: list[tuple[float, float, float, float]] = []
        self.setCursor(Qt.IBeamCursor)

    def scale(self) -> float:
        return self.width() / max(1.0, self.page_size[0])

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self.highlights:
            return
        painter = QPainter(self)
        color = QColor(self.palette().highlight().color())
        color.setAlpha(90)
        scale = self.scale()
        for left, top, right, bottom in self.highlights:
            painter.fillRect(
                QRectF(left * scale, top * scale, (right - left) * scale, (bottom - top) * scale),
                color,
            )
        painter.end()


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
        self.pages: list[_PageLabel] = []
        for _ in range(pages):
            page = _PageLabel()
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
    """Maximized window with the two PDFs side by side, zoomed together.

    Text is selected by dragging over it and copied with Ctrl+C.
    """

    edit_requested = Signal()

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
        # Text selection: (pane, (page, character) where it started, and where it ends).
        self.selection: tuple[int, tuple[int, int], tuple[int, int]] | None = None
        self._drag_point = None
        self._press_point = None
        self._dragging = False

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
            for page in pane.pages:
                page.installEventFilter(self)
                page.setContextMenuPolicy(Qt.CustomContextMenu)
                page.customContextMenuRequested.connect(self._page_menu)
        layout.addWidget(self.splitter, 1)

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(40)
        self._render_timer.timeout.connect(self._render_visible)
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.setInterval(80)
        self._layout_timer.timeout.connect(self._apply_zoom)
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setInterval(30)
        self._scroll_timer.timeout.connect(self._autoscroll)

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
            (QKeySequence.Copy, self.copy_selection),
            (QKeySequence.SelectAll, self.select_all),
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
        self.edit_button = QPushButton(tr("翻訳後のPDFを編集"))
        self.edit_button.setIcon(icon("edit", 18))
        self.edit_button.setToolTip(tr("訳文の書き換え、図の移動・差し替えなどができます"))
        self.edit_button.setVisible(False)
        bar.addWidget(self.edit_button)
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
        self.edit_button.clicked.connect(self.edit_requested.emit)
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
                    page.page_size = (width, height)
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
        if isinstance(watched, _PageLabel) and self._page_mouse(watched, event):
            return True
        if event.type() == QEvent.Wheel and event.modifiers() & Qt.ControlModifier:
            if event.angleDelta().y() > 0:
                self.zoom_in()
            elif event.angleDelta().y() < 0:
                self.zoom_out()
            return True
        return super().eventFilter(watched, event)

    # text selection -------------------------------------------------------------

    def _locate(self, label: _PageLabel) -> tuple[int, int] | None:
        for pane_index, pane in enumerate(self.panes):
            if label in pane.pages:
                return pane_index, pane.pages.index(label)
        return None

    def char_at(self, pane_index: int, page_index: int, x: float, y: float) -> int | None:
        """The character under or nearest to (x, y), in page points from the top left."""
        chars = self.sources[pane_index].chars(page_index)
        best, best_distance = None, float("inf")
        for position, char in enumerate(chars):
            if char.rect is None or not char.text.strip():
                continue
            left, top, right, bottom = char.rect
            if left <= x <= right and top <= y <= bottom:
                return position
            # Prefer characters on the same line: vertical distance weighs more.
            dx = max(left - x, 0.0, x - right)
            dy = max(top - y, 0.0, y - bottom)
            distance = dx + dy * 4
            if distance < best_distance:
                best, best_distance = position, distance
        return best

    def _page_under(self, pane_index: int, global_point) -> tuple[int, float, float]:
        """The page of a side nearest to a screen point, and the point on it in points."""
        pane = self.panes[pane_index]
        best, best_distance = 0, float("inf")
        for index, label in enumerate(pane.pages):
            top = label.mapToGlobal(label.rect().topLeft()).y()
            bottom = top + label.height()
            distance = max(top - global_point.y(), 0, global_point.y() - bottom)
            if distance < best_distance:
                best, best_distance = index, distance
        label = pane.pages[best]
        local = label.mapFromGlobal(global_point)
        scale = label.scale()
        x = min(max(local.x(), 0), label.width()) / scale
        y = min(max(local.y(), 0), label.height()) / scale
        return best, x, y

    def _extend_selection(self, global_point) -> None:
        if self.selection is None:
            return
        pane_index, anchor, _focus = self.selection
        page_index, x, y = self._page_under(pane_index, global_point)
        position = self.char_at(pane_index, page_index, x, y)
        if position is not None:
            self.selection = (pane_index, anchor, (page_index, position))
            self._show_selection()

    def _page_mouse(self, label: _PageLabel, event: QEvent) -> bool:
        kind = event.type()
        if kind not in (QEvent.MouseButtonPress, QEvent.MouseMove, QEvent.MouseButtonRelease):
            return False
        if kind != QEvent.MouseMove and event.button() != Qt.LeftButton:
            return False
        where = self._locate(label)
        if where is None:
            return False
        pane_index, page_index = where
        scale = label.scale()
        global_point = label.mapToGlobal(event.position().toPoint())
        if kind == QEvent.MouseButtonPress:
            self.clear_selection()
            x, y = event.position().x() / scale, event.position().y() / scale
            position = self.char_at(pane_index, page_index, x, y)
            if position is not None:
                self.selection = (pane_index, (page_index, position), (page_index, position))
                self._press_point = global_point
                self._dragging = True
            return True
        if kind == QEvent.MouseMove:
            if not self._dragging or self.selection is None:
                return False
            # The pressed page keeps the mouse, so the point may be over another page.
            self._drag_point = global_point
            self._extend_selection(global_point)
            self._autoscroll()
            return True
        # Release: a click without dragging selects nothing.
        self._dragging = False
        self._scroll_timer.stop()
        if self.selection is not None:
            moved = (global_point - self._press_point).manhattanLength()
            if moved < 3:
                self.clear_selection()
        return True

    def _autoscroll(self) -> None:
        """Scroll while the mouse is dragged above or below the view."""
        if not self._dragging or self.selection is None:
            self._scroll_timer.stop()
            return
        pane = self.panes[self.selection[0]]
        viewport = pane.scroll.viewport()
        y = viewport.mapFromGlobal(self._drag_point).y()
        if y < 0:
            step = y
        elif y > viewport.height():
            step = y - viewport.height()
        else:
            self._scroll_timer.stop()
            return
        pane.bar.setValue(pane.bar.value() + round(max(-60, min(60, step))))
        self._extend_selection(self._drag_point)
        if not self._scroll_timer.isActive():
            self._scroll_timer.start()

    def _selected_pages(self):
        """(page, first character, last character) for every page of the selection."""
        if self.selection is None:
            return []
        pane_index, anchor, focus = self.selection
        start, end = sorted((anchor, focus))
        parts = []
        for page_index in range(start[0], end[0] + 1):
            chars = self.sources[pane_index].chars(page_index)
            first = start[1] if page_index == start[0] else 0
            last = end[1] if page_index == end[0] else len(chars) - 1
            if chars and last >= first:
                parts.append((page_index, first, last))
        return parts

    def _show_selection(self) -> None:
        selected = {page: (first, last) for page, first, last in self._selected_pages()}
        pane_index = self.selection[0] if self.selection else -1
        for index, pane in enumerate(self.panes):
            for page_index, label in enumerate(pane.pages):
                highlights = []
                if index == pane_index and page_index in selected:
                    first, last = selected[page_index]
                    chars = self.sources[index].chars(page_index)
                    highlights = [
                        chars[i].rect
                        for i in range(first, last + 1)
                        if chars[i].rect and chars[i].text.strip()
                    ]
                if highlights or label.highlights:
                    label.highlights = highlights
                    label.update()

    def clear_selection(self) -> None:
        self.selection = None
        self._show_selection()

    def selected_text(self) -> str:
        if self.selection is None:
            return ""
        pane_index = self.selection[0]
        pages = []
        for page_index, first, last in self._selected_pages():
            chars = self.sources[pane_index].chars(page_index)
            pages.append("".join(chars[i].text for i in range(first, last + 1)).strip())
        paragraphs = []
        for page in pages:
            for paragraph in page.split(PARAGRAPH_BREAK):
                paragraph = paragraph.replace("\r\n", "\n").replace("\r", "\n")
                # Spaces left around hidden text make blank lines; they are dropped.
                lines = [line.strip() for line in paragraph.split("\n") if line.strip()]
                if lines:
                    # Lines of a Japanese (or Chinese) paragraph are joined: a break
                    # inside one is only where the page wrapped it.
                    paragraphs.append(_WRAPPED_CJK.sub("", "\n".join(lines)))
        return "\n".join(paragraphs)

    def copy_selection(self) -> None:
        text = self.selected_text()
        if text:
            QApplication.clipboard().setText(text)

    def _text_ends(self, pane_index: int, page_index: int) -> tuple[int, int] | None:
        chars = self.sources[pane_index].chars(page_index)
        shown = [i for i, char in enumerate(chars) if char.text.strip()]
        return (shown[0], shown[-1]) if shown else None

    def select_page(self, pane_index: int | None = None, page_index: int | None = None) -> None:
        """Select all text of a page (by default the current page of the side used last)."""
        if pane_index is None:
            pane_index = self.selection[0] if self.selection else self._leader
        if page_index is None:
            page_index = self.panes[pane_index].current_page()
        ends = self._text_ends(pane_index, page_index)
        if ends is None:
            return
        self.selection = (pane_index, (page_index, ends[0]), (page_index, ends[1]))
        self._show_selection()

    def select_all(self, pane_index: int | None = None) -> None:
        """Select the text of every page of one side."""
        if pane_index is None:
            pane_index = self.selection[0] if self.selection else self._leader
        pages = [
            (index, ends)
            for index in range(self.pages)
            if (ends := self._text_ends(pane_index, index)) is not None
        ]
        if not pages:
            return
        first, last = pages[0], pages[-1]
        self.selection = (pane_index, (first[0], first[1][0]), (last[0], last[1][1]))
        self._show_selection()

    def _page_menu(self, point) -> None:
        label = self.sender()
        where = self._locate(label)
        if where is None:
            return
        menu = QMenu(self)
        copy = menu.addAction(tr("コピー"))
        copy.setShortcut(QKeySequence.Copy)
        copy.setEnabled(bool(self.selected_text()))
        copy.triggered.connect(self.copy_selection)
        select_page = menu.addAction(tr("このページの文字をすべて選択"))
        select_page.triggered.connect(lambda: self.select_page(*where))
        select_all = menu.addAction(tr("すべてのページの文字を選択"))
        select_all.setShortcut(QKeySequence.SelectAll)
        select_all.triggered.connect(lambda: self.select_all(where[0]))
        menu.exec(label.mapToGlobal(point))

    # editing ------------------------------------------------------------------

    def reload_after(self) -> None:
        """Show the translated side again after it was edited."""
        source = self.sources[1]
        source.close()
        source.open()
        pane = self.panes[1]
        for index in list(pane.rendered):
            pane.pages[index].clear()
        pane.rendered.clear()
        self.clear_selection()
        self._render_visible()

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
