"""Editing a translated PDF: rewrite or move the translations, cut out, move,
replace or remove figures, add text and images, and erase areas.

The page is shown as it will be saved; boxes over it mark what can be edited.
Saving writes the translated (and bilingual) PDF again from the edited items.
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from movie_slide_notes.gui.i18n import tr
from movie_slide_notes.gui.icons import icon
from movie_slide_notes.pdf_translate import edits
from movie_slide_notes.pdf_translate.edits import EditDocument, EditItem

HANDLE = 7  # pixels
MIN_ITEM = 4.0  # points
UNDO_LIMIT = 100
CUT_SCALE = 4.0  # figures are cut out at about 300 dpi
TOOLS = {
    "select": (
        "選択・移動",
        "項目をクリックして選び、ドラッグで移動、角をドラッグで大きさを変えます",
    ),
    "text": ("テキストを追加", "文字を入れたい範囲をドラッグします"),
    "cut": ("図を切り出す", "図の範囲をドラッグすると、移動・拡大・削除できる画像になります"),
    "erase": ("範囲を消す", "消したい範囲をドラッグすると、まわりの色で塗りつぶします"),
}
KIND_COLORS = {"text": "#2f7fd8", "image": "#14a39b", "erase": "#8a96a0"}


def _qcolor(rgb) -> QColor:
    return QColor.fromRgbF(*rgb) if rgb is not None else QColor(Qt.transparent)


def _rgb(color: QColor) -> tuple[float, float, float]:
    return (color.redF(), color.greenF(), color.blueF())


def _pixmap(image) -> QPixmap:
    rgb = image.convert("RGB")
    data = rgb.tobytes()
    qimage = QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format_RGB888)
    return QPixmap.fromImage(qimage.copy())


def border_color(image) -> tuple[float, float, float]:
    """The usual color around the edge of a region: what an erased area is painted."""
    import numpy as np

    pixels = np.asarray(image.convert("RGB")).astype(np.int32)
    if pixels.size == 0:
        return (1.0, 1.0, 1.0)
    edge = np.concatenate([pixels[0], pixels[-1], pixels[:, 0], pixels[:, -1]])
    return tuple(float(v) / 255 for v in np.median(edge, axis=0))


class _Canvas(QWidget):
    """The page and the boxes of its items; mouse editing happens here."""

    def __init__(self, editor: PdfEditor):
        super().__init__()
        self.editor = editor
        self.pixmap: QPixmap | None = None
        self.zoom = 1.0
        self.preview: QRectF | None = None  # the box being drawn with a tool
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self._drag = None  # (mode, start point, original item copy)
        self._hover: int | None = None

    # coordinates -----------------------------------------------------------------

    @property
    def page(self):
        return self.editor.page

    def to_page(self, point: QPointF) -> tuple[float, float]:
        return point.x() / self.zoom, self.page.height - point.y() / self.zoom

    def item_rect(self, item: EditItem) -> QRectF:
        z = self.zoom
        return QRectF(
            item.x0 * z, (self.page.height - item.y1) * z, item.width * z, item.height * z
        )

    def set_zoom(self, zoom: float) -> None:
        self.zoom = zoom
        self.setFixedSize(QSize(round(self.page.width * zoom), round(self.page.height * zoom)))
        self.update()

    # drawing ----------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.white)
        if self.pixmap is not None:
            painter.drawPixmap(self.rect(), self.pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        selected = self.editor.selected
        for position, item in enumerate(self.page.items):
            rect = self.item_rect(item)
            color = QColor(KIND_COLORS.get(item.kind, "#2f7fd8"))
            if position == selected:
                painter.setPen(QPen(color, 2))
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(rect)
                painter.setBrush(color)
                for corner in self._corners(rect).values():
                    painter.drawRect(corner)
                continue
            color.setAlpha(170 if position == self._hover else 70)
            pen = QPen(color, 1, Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect)
        if self.preview is not None:
            color = QColor(KIND_COLORS.get(self.editor.tool, "#2f7fd8"))
            painter.setPen(QPen(color, 1.5, Qt.DashLine))
            fill = QColor(color)
            fill.setAlpha(30)
            painter.setBrush(fill)
            painter.drawRect(self.preview.normalized())
        painter.end()

    @staticmethod
    def _corners(rect: QRectF) -> dict[str, QRectF]:
        h = HANDLE
        return {
            "tl": QRectF(rect.left() - h / 2, rect.top() - h / 2, h, h),
            "tr": QRectF(rect.right() - h / 2, rect.top() - h / 2, h, h),
            "bl": QRectF(rect.left() - h / 2, rect.bottom() - h / 2, h, h),
            "br": QRectF(rect.right() - h / 2, rect.bottom() - h / 2, h, h),
        }

    # mouse -----------------------------------------------------------------------

    def hit(self, point: QPointF) -> int | None:
        """The item under the point; text is on top, then images, then erased areas."""
        order = {"text": 0, "image": 1, "erase": 2}
        found = [
            (order.get(item.kind, 3), -position, position)
            for position, item in enumerate(self.page.items)
            if self.item_rect(item).adjusted(-2, -2, 2, 2).contains(point)
        ]
        return min(found)[2] if found else None

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        self.setFocus()
        point = event.position()
        editor = self.editor
        if editor.tool != "select":
            self._drag = ("create", point, None)
            self.preview = QRectF(point, point)
            return
        if editor.selected is not None:
            rect = self.item_rect(self.page.items[editor.selected])
            for name, corner in self._corners(rect).items():
                if corner.adjusted(-3, -3, 3, 3).contains(point):
                    item = self.page.items[editor.selected]
                    self._drag = (name, point, copy.deepcopy(item))
                    return
        position = self.hit(point)
        editor.select(position)
        if position is not None:
            self._drag = ("move", point, copy.deepcopy(self.page.items[position]))

    def mouseMoveEvent(self, event) -> None:
        point = event.position()
        if self._drag is None:
            hover = self.hit(point) if self.editor.tool == "select" else None
            if hover != self._hover:
                self._hover = hover
                self.update()
            self._update_cursor(point)
            return
        mode, start, original = self._drag
        if mode == "create":
            self.preview = QRectF(start, point)
            self.update()
            return
        item = self.page.items[self.editor.selected]
        if not self.editor.changing:
            self.editor.begin_change()
        dx = (point.x() - start.x()) / self.zoom
        dy = -(point.y() - start.y()) / self.zoom
        if mode == "move":
            item.x0, item.x1 = original.x0 + dx, original.x1 + dx
            item.y0, item.y1 = original.y0 + dy, original.y1 + dy
        else:
            if "l" in mode:
                item.x0 = min(original.x0 + dx, original.x1 - MIN_ITEM)
            if "r" in mode:
                item.x1 = max(original.x1 + dx, original.x0 + MIN_ITEM)
            if "t" in mode:
                item.y1 = max(original.y1 + dy, original.y0 + MIN_ITEM)
            if "b" in mode:
                item.y0 = min(original.y0 + dy, original.y1 - MIN_ITEM)
            if item.kind == "image" and event.modifiers() & Qt.ShiftModifier:
                # Shift keeps the image's proportions.
                ratio = original.height / max(1e-6, original.width)
                height = item.width * ratio
                if "t" in mode:
                    item.y1 = item.y0 + height
                else:
                    item.y0 = item.y1 - height
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton or self._drag is None:
            return
        mode, start, _original = self._drag
        self._drag = None
        if mode == "create":
            rect = QRectF(start, event.position()).normalized()
            self.preview = None
            self.update()
            if rect.width() / self.zoom >= MIN_ITEM and rect.height() / self.zoom >= MIN_ITEM:
                x0, y1 = self.to_page(rect.topLeft())
                x1, y0 = self.to_page(rect.bottomRight())
                self.editor.create_item(self.editor.tool, x0, y0, x1, y1)
            return
        if self.editor.changing:
            self.editor.end_change()

    def mouseDoubleClickEvent(self, event) -> None:
        position = self.hit(event.position())
        if position is not None and self.page.items[position].kind == "text":
            self.editor.select(position)
            self.editor.text_edit.setFocus()
            self.editor.text_edit.selectAll()

    def _update_cursor(self, point: QPointF) -> None:
        if self.editor.tool != "select":
            self.setCursor(Qt.CrossCursor)
            return
        selected = self.editor.selected
        if selected is not None:
            corners = self._corners(self.item_rect(self.page.items[selected]))
            for name, corner in corners.items():
                if corner.adjusted(-3, -3, 3, 3).contains(point):
                    diagonal = name in ("tl", "br")
                    self.setCursor(Qt.SizeFDiagCursor if diagonal else Qt.SizeBDiagCursor)
                    return
        self.setCursor(Qt.SizeAllCursor if self.hit(point) is not None else Qt.ArrowCursor)

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.ControlModifier:
            self.editor.change_zoom(1.15 if event.angleDelta().y() > 0 else 1 / 1.15)
            event.accept()
            return
        super().wheelEvent(event)


class PdfEditor(QDialog):
    """Window for editing the pages of one translated PDF."""

    saved = Signal()

    def __init__(self, document: EditDocument, start: int = 0, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("翻訳後のPDFを編集"))
        self.setWindowFlags(Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.document = document
        self.composer = edits.PageComposer(document)
        self.position = max(0, min(len(document.pages) - 1, start))
        self.selected: int | None = None
        self.tool = "select"
        self.dirty = False
        self.changing = False
        self.undo_stack: list[tuple[int, list[dict], int | None]] = []
        self.fit_width = True
        self._loading_panel = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(10)
        layout.addLayout(self._toolbar())
        body = QHBoxLayout()
        body.setSpacing(12)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("PageScroll")
        self.scroll.setAlignment(Qt.AlignCenter)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.canvas = _Canvas(self)
        self.scroll.setWidget(self.canvas)
        body.addWidget(self.scroll, 1)
        body.addWidget(self._panel())
        layout.addLayout(body, 1)
        self.status = QLabel()
        self.status.setObjectName("SecondaryText")
        layout.addWidget(self.status)

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(120)
        self._render_timer.timeout.connect(self.render_page)
        for keys, slot in [
            (QKeySequence.Undo, self.undo),
            (QKeySequence.Save, self.save),
            (QKeySequence.MoveToNextPage, lambda: self.go_to(self.position + 1)),
            (QKeySequence.MoveToPreviousPage, lambda: self.go_to(self.position - 1)),
        ]:
            QShortcut(keys, self, activated=slot)
        self.resize(1400, 920)
        self.set_tool("select")
        self.go_to(self.position)

    # building ---------------------------------------------------------------------

    def _toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.prev_button = QPushButton()
        self.prev_button.setIcon(icon("move-left", 18))
        self.prev_button.setToolTip(tr("前のページ"))
        self.page_label = QLabel()
        self.page_label.setMinimumWidth(70)
        self.page_label.setAlignment(Qt.AlignCenter)
        self.next_button = QPushButton()
        self.next_button.setIcon(icon("move-right", 18))
        self.next_button.setToolTip(tr("次のページ"))
        for widget in (self.prev_button, self.page_label, self.next_button):
            bar.addWidget(widget)
        bar.addSpacing(16)
        self.tool_group = QButtonGroup(self)
        self.tool_buttons: dict[str, QPushButton] = {}
        for key, (label, hint) in TOOLS.items():
            button = QPushButton(tr(label))
            button.setCheckable(True)
            button.setToolTip(tr(hint))
            self.tool_group.addButton(button)
            self.tool_buttons[key] = button
            bar.addWidget(button)
            button.clicked.connect(lambda _checked=False, k=key: self.set_tool(k))
        self.image_button = QPushButton(tr("画像を挿入…"))
        bar.addWidget(self.image_button)
        bar.addSpacing(16)
        self.undo_button = QPushButton(tr("元に戻す"))
        self.undo_button.setToolTip(tr("元に戻す（Ctrl+Z）"))
        bar.addWidget(self.undo_button)
        self.zoom_out_button = QPushButton("−")
        self.zoom_in_button = QPushButton("+")
        self.fit_button = QPushButton(tr("幅に合わせる"))
        for widget in (self.zoom_out_button, self.zoom_in_button, self.fit_button):
            bar.addWidget(widget)
        bar.addStretch(1)
        self.save_button = QPushButton(tr("保存"))
        self.save_button.setObjectName("PrimaryButton")
        self.save_button.setToolTip(tr("翻訳後のPDFに上書き保存します（Ctrl+S）"))
        self.close_button = QPushButton(tr("閉じる"))
        bar.addWidget(self.save_button)
        bar.addWidget(self.close_button)

        self.prev_button.clicked.connect(lambda: self.go_to(self.position - 1))
        self.next_button.clicked.connect(lambda: self.go_to(self.position + 1))
        self.image_button.clicked.connect(self.insert_image)
        self.undo_button.clicked.connect(self.undo)
        self.zoom_in_button.clicked.connect(lambda: self.change_zoom(1.2))
        self.zoom_out_button.clicked.connect(lambda: self.change_zoom(1 / 1.2))
        self.fit_button.clicked.connect(self.fit_to_width)
        self.save_button.clicked.connect(self.save)
        self.close_button.clicked.connect(self.close)
        return bar

    def _panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("SoftPanel")
        panel.setFixedWidth(320)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        self.panel_title = QLabel()
        self.panel_title.setObjectName("SectionTitle")
        layout.addWidget(self.panel_title)
        self.panel_stack = QStackedWidget()
        layout.addWidget(self.panel_stack, 1)

        # Nothing selected.
        empty = QLabel(
            tr(
                "編集したい文字や図をクリックして選びます。\n\n"
                "・文字：ダブルクリックで書き換え\n"
                "・移動：ドラッグ\n"
                "・大きさ：選んだ枠の角をドラッグ\n"
                "・削除：Deleteキー\n\n"
                "上のボタンで、テキストの追加、図の切り出し、範囲の消去、画像の挿入ができます。"
            )
        )
        empty.setWordWrap(True)
        empty.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        empty.setObjectName("SecondaryText")
        self.panel_stack.addWidget(empty)

        # Text.
        text_panel = QWidget()
        text_layout = QVBoxLayout(text_panel)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(8)
        self.text_edit = QPlainTextEdit()
        self.text_edit.setAccessibleName(tr("文字"))
        self.text_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        text_layout.addWidget(self.text_edit, 1)
        hint = QLabel(
            tr("下付き文字は <sub>2</sub>、上付き文字は <sup>3+</sup> のように書けます。")
        )
        hint.setTextFormat(Qt.PlainText)
        hint.setWordWrap(True)
        hint.setObjectName("Hint")
        text_layout.addWidget(hint)
        size_row = QHBoxLayout()
        size_row.addWidget(QLabel(tr("文字の大きさ")))
        self.size_spin = QDoubleSpinBox()
        self.size_spin.setRange(3.0, 96.0)
        self.size_spin.setSingleStep(0.5)
        self.size_spin.setSuffix(" pt")
        size_row.addWidget(self.size_spin, 1)
        text_layout.addLayout(size_row)
        self.auto_size_check = QCheckBox(tr("枠に収まるように自動で縮める"))
        self.bold_check = QCheckBox(tr("太字"))
        self.serif_check = QCheckBox(tr("明朝体"))
        self.center_check = QCheckBox(tr("中央ぞろえ"))
        for widget in (self.auto_size_check, self.bold_check, self.serif_check, self.center_check):
            text_layout.addWidget(widget)
        color_row = QHBoxLayout()
        self.text_color_button = QPushButton(tr("文字の色"))
        self.fill_check = QCheckBox(tr("背景を塗る"))
        self.fill_color_button = QPushButton(tr("背景の色"))
        color_row.addWidget(self.text_color_button)
        color_row.addWidget(self.fill_color_button)
        text_layout.addLayout(color_row)
        text_layout.addWidget(self.fill_check)
        self.panel_stack.addWidget(text_panel)

        # Image.
        image_panel = QWidget()
        image_layout = QVBoxLayout(image_panel)
        image_layout.setContentsMargins(0, 0, 0, 0)
        image_hint = QLabel(
            tr(
                "ドラッグで移動、角をドラッグで大きさを変えます（Shiftを押しながらで縦横比を保ちます）。"
            )
        )
        image_hint.setWordWrap(True)
        image_hint.setObjectName("SecondaryText")
        image_layout.addWidget(image_hint)
        self.replace_image_button = QPushButton(tr("画像を差し替え…"))
        image_layout.addWidget(self.replace_image_button)
        image_layout.addStretch(1)
        self.panel_stack.addWidget(image_panel)

        # Erased area.
        erase_panel = QWidget()
        erase_layout = QVBoxLayout(erase_panel)
        erase_layout.setContentsMargins(0, 0, 0, 0)
        erase_hint = QLabel(tr("この範囲を、選んだ色で塗りつぶします。"))
        erase_hint.setWordWrap(True)
        erase_hint.setObjectName("SecondaryText")
        erase_layout.addWidget(erase_hint)
        self.erase_color_button = QPushButton(tr("塗る色"))
        erase_layout.addWidget(self.erase_color_button)
        erase_layout.addStretch(1)
        self.panel_stack.addWidget(erase_panel)

        self.delete_button = QPushButton(tr("削除"))
        self.delete_button.setIcon(icon("trash", 18))
        layout.addWidget(self.delete_button)

        self.text_edit.textChanged.connect(self._text_changed)
        self.size_spin.valueChanged.connect(lambda v: self._set_property("font_size", v))
        self.auto_size_check.toggled.connect(lambda v: self._set_property("auto_size", v))
        self.bold_check.toggled.connect(lambda v: self._set_property("bold", v))
        self.serif_check.toggled.connect(lambda v: self._set_property("serif", v))
        self.center_check.toggled.connect(lambda v: self._set_property("centered", v))
        self.fill_check.toggled.connect(self._fill_toggled)
        self.text_color_button.clicked.connect(lambda: self._pick_color("color"))
        self.fill_color_button.clicked.connect(lambda: self._pick_color("background"))
        self.erase_color_button.clicked.connect(lambda: self._pick_color("background"))
        self.replace_image_button.clicked.connect(self.replace_image)
        self.delete_button.clicked.connect(self.delete_selected)
        return panel

    # pages and rendering ------------------------------------------------------------

    @property
    def page(self) -> edits.EditPage:
        return self.document.pages[self.position]

    @property
    def item(self) -> EditItem | None:
        return self.page.items[self.selected] if self.selected is not None else None

    def go_to(self, position: int) -> None:
        position = max(0, min(len(self.document.pages) - 1, position))
        self._drop_empty_text()
        self.position = position
        self.select(None)
        self.page_label.setText(f"{position + 1} / {len(self.document.pages)}")
        self.prev_button.setEnabled(position > 0)
        self.next_button.setEnabled(position < len(self.document.pages) - 1)
        if self.fit_width:
            self.fit_to_width()
        else:
            self.canvas.set_zoom(self.canvas.zoom)
        self.render_page()

    def render_page(self) -> None:
        ratio = self.devicePixelRatioF()
        try:
            image = self.composer.render(self.position, self.canvas.zoom * ratio)
        except Exception:
            logging.getLogger("lectavo").exception("Editor preview failed")
            self.status.setText(tr("ページを表示できませんでした"))
            return
        pixmap = _pixmap(image)
        pixmap.setDevicePixelRatio(ratio)
        self.canvas.pixmap = pixmap
        self.canvas.update()

    def schedule_render(self) -> None:
        self._render_timer.start()

    def fit_to_width(self) -> None:
        self.fit_width = True
        available = max(200, self.scroll.viewport().width() - 24)
        self.canvas.set_zoom(available / max(1.0, self.page.width))
        self.schedule_render()

    def change_zoom(self, factor: float) -> None:
        self.fit_width = False
        self.canvas.set_zoom(max(0.3, min(6.0, self.canvas.zoom * factor)))
        self.schedule_render()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.fit_width and hasattr(self, "canvas"):
            self.fit_to_width()

    # selection and tools ---------------------------------------------------------------

    def set_tool(self, tool: str) -> None:
        self.tool = tool
        self.tool_buttons[tool].setChecked(True)
        if tool != "select":
            self.select(None)
        self.status.setText(tr(TOOLS[tool][1]))
        self.canvas.setCursor(Qt.CrossCursor if tool != "select" else Qt.ArrowCursor)

    def select(self, position: int | None) -> None:
        if position != self.selected:
            self._drop_empty_text(keep=position)
        if position is not None and position >= len(self.page.items):
            position = None
        self._typing = False
        self.selected = position
        self._load_panel()
        self.canvas.update()

    def _drop_empty_text(self, keep: int | None = None) -> None:
        """A text box left empty is removed when another item is chosen."""
        position = self.selected
        if position is None or position == keep or position >= len(self.page.items):
            return
        item = self.page.items[position]
        if item.kind == "text" and not item.text.strip() and item.background is None:
            del self.page.items[position]
            self.selected = None
            if keep is not None and keep > position:
                keep -= 1

    def _load_panel(self) -> None:
        item = self.item
        self._loading_panel = True
        try:
            titles = {"text": "テキスト", "image": "画像", "erase": "消した範囲"}
            self.panel_title.setText(tr(titles.get(item.kind, "編集")) if item else tr("編集"))
            self.delete_button.setEnabled(item is not None)
            if item is None:
                self.panel_stack.setCurrentIndex(0)
            elif item.kind == "text":
                self.panel_stack.setCurrentIndex(1)
                self.text_edit.setPlainText(item.text)
                self.size_spin.setValue(item.font_size)
                self.auto_size_check.setChecked(item.auto_size)
                self.bold_check.setChecked(item.bold)
                self.serif_check.setChecked(item.serif)
                self.center_check.setChecked(item.centered)
                self.fill_check.setChecked(item.background is not None)
                self.fill_color_button.setEnabled(item.background is not None)
            elif item.kind == "image":
                self.panel_stack.setCurrentIndex(2)
            else:
                self.panel_stack.setCurrentIndex(3)
        finally:
            self._loading_panel = False

    # changes -----------------------------------------------------------------------

    def snapshot(self) -> None:
        state = (self.position, [item.to_dict() for item in self.page.items], self.selected)
        self.undo_stack.append(state)
        del self.undo_stack[:-UNDO_LIMIT]
        self.undo_button.setEnabled(True)

    def begin_change(self) -> None:
        self.snapshot()
        self.changing = True

    def end_change(self) -> None:
        self.changing = False
        self.mark_dirty()

    def mark_dirty(self) -> None:
        self.dirty = True
        self.setWindowTitle(tr("翻訳後のPDFを編集") + " *")
        self.schedule_render()
        self.canvas.update()

    def undo(self) -> None:
        if not self.undo_stack:
            return
        position, items, selected = self.undo_stack.pop()
        self.document.pages[position].items = [EditItem.from_dict(item) for item in items]
        if position != self.position:
            self.go_to(position)
        self.selected = selected if selected is not None and selected < len(items) else None
        self._load_panel()
        self.mark_dirty()
        self.undo_button.setEnabled(bool(self.undo_stack))

    def _set_property(self, name: str, value) -> None:
        item = self.item
        if self._loading_panel or item is None or getattr(item, name) == value:
            return
        self.snapshot()
        setattr(item, name, value)
        if name == "font_size":
            # Choosing a size means using it as it is.
            item.auto_size = False
            self._loading_panel = True
            self.auto_size_check.setChecked(False)
            self._loading_panel = False
        self.mark_dirty()

    def _text_changed(self) -> None:
        item = self.item
        if self._loading_panel or item is None or item.kind != "text":
            return
        text = self.text_edit.toPlainText()
        if text == item.text:
            return
        # One undo step per editing session of a box, not per key.
        last = self.undo_stack[-1] if self.undo_stack else None
        if not (last and last[0] == self.position and last[2] == self.selected and self._typing):
            self.snapshot()
        self._typing = True
        item.text = text
        self.mark_dirty()

    _typing = False

    def _fill_toggled(self, on: bool) -> None:
        item = self.item
        if self._loading_panel or item is None:
            return
        self.fill_color_button.setEnabled(on)
        self._set_property("background", (1.0, 1.0, 1.0) if on else None)

    def _pick_color(self, name: str) -> None:
        item = self.item
        if item is None:
            return
        current = getattr(item, name) or (1.0, 1.0, 1.0)
        color = QColorDialog.getColor(_qcolor(current), self, tr("色を選択"))
        if color.isValid():
            self._set_property(name, _rgb(color))

    def create_item(self, tool: str, x0: float, y0: float, x1: float, y1: float) -> None:
        self.snapshot()
        items = self.page.items
        if tool == "text":
            items.append(EditItem("text", x0, y0, x1, y1, font_size=10.0, auto_size=False))
            self.set_tool("select")
            self.select(len(items) - 1)
            self.text_edit.setFocus()
        elif tool == "erase":
            region = self._original_region(x0, y0, x1, y1, 1.0)
            items.append(EditItem("erase", x0, y0, x1, y1, background=border_color(region)))
            self.set_tool("select")
            self.select(len(items) - 1)
        elif tool == "cut":
            region = self._original_region(x0, y0, x1, y1, CUT_SCALE)
            name = self.document.add_image(region)
            # The figure is hidden where it was and comes back as a movable image.
            items.insert(0, EditItem("erase", x0, y0, x1, y1, background=border_color(region)))
            items.append(EditItem("image", x0, y0, x1, y1, image=name))
            self.set_tool("select")
            self.select(len(items) - 1)
        self.mark_dirty()

    def _original_region(self, x0: float, y0: float, x1: float, y1: float, scale: float):
        """That part of the original page (without the translation), as an image."""
        image = self.composer.render(self.position, scale, with_items=False)
        height = self.page.height
        box = (
            max(0, round(x0 * scale)),
            max(0, round((height - y1) * scale)),
            min(image.width, round(x1 * scale)),
            min(image.height, round((height - y0) * scale)),
        )
        return image.crop(box)

    def _choose_image(self) -> Path | None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("画像を選択"), "", tr("画像 (*.png *.jpg *.jpeg *.bmp *.gif)")
        )
        return Path(path) if path else None

    def insert_image(self) -> None:
        path = self._choose_image()
        if path is None:
            return
        try:
            name = self.document.import_image(path)
        except Exception as exc:
            QMessageBox.warning(self, tr("編集"), tr("画像を読み込めませんでした。") + f"\n\n{exc}")
            return
        from PIL import Image

        with Image.open(self.document.images_dir / name) as image:
            ratio = image.height / max(1, image.width)
        width = self.page.width * 0.4
        height = width * ratio
        x0 = (self.page.width - width) / 2
        y0 = (self.page.height - height) / 2
        self.snapshot()
        self.page.items.append(EditItem("image", x0, y0, x0 + width, y0 + height, image=name))
        self.set_tool("select")
        self.select(len(self.page.items) - 1)
        self.mark_dirty()

    def replace_image(self) -> None:
        item = self.item
        if item is None or item.kind != "image":
            return
        path = self._choose_image()
        if path is None:
            return
        try:
            name = self.document.import_image(path)
        except Exception as exc:
            QMessageBox.warning(self, tr("編集"), tr("画像を読み込めませんでした。") + f"\n\n{exc}")
            return
        self._set_property("image", name)

    def delete_selected(self) -> None:
        if self.selected is None:
            return
        self.snapshot()
        del self.page.items[self.selected]
        self.selected = None
        self._load_panel()
        self.mark_dirty()

    def nudge(self, dx: float, dy: float) -> None:
        item = self.item
        if item is None:
            return
        self.snapshot()
        item.x0 += dx
        item.x1 += dx
        item.y0 += dy
        item.y1 += dy
        self.mark_dirty()

    def keyPressEvent(self, event) -> None:
        if self.text_edit.hasFocus():
            super().keyPressEvent(event)
            return
        step = 10.0 if event.modifiers() & Qt.ShiftModifier else 1.0
        moves = {
            Qt.Key_Left: (-step, 0),
            Qt.Key_Right: (step, 0),
            Qt.Key_Up: (0, step),
            Qt.Key_Down: (0, -step),
        }
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_selected()
        elif event.key() in moves and self.selected is not None:
            self.nudge(*moves[event.key()])
        elif event.key() == Qt.Key_Escape:
            if self.tool != "select":
                self.set_tool("select")
            else:
                self.select(None)
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    # saving -------------------------------------------------------------------------

    def save(self) -> bool:
        self._drop_empty_text()
        self._typing = False
        try:
            self.document.save()
            edits.write(self.document)
        except PermissionError:
            QMessageBox.warning(
                self,
                tr("保存"),
                tr(
                    "PDFがほかのアプリで開かれているため保存できません。閉じてからもう一度保存してください。"
                ),
            )
            return False
        except Exception as exc:
            logging.getLogger("lectavo").exception("Saving the edited PDF failed")
            QMessageBox.warning(self, tr("保存"), tr("保存できませんでした。") + f"\n\n{exc}")
            return False
        self.dirty = False
        self.setWindowTitle(tr("翻訳後のPDFを編集"))
        self.status.setText(tr("保存しました"))
        self.saved.emit()
        return True

    def closeEvent(self, event) -> None:
        if self.dirty:
            answer = QMessageBox.question(
                self,
                tr("翻訳後のPDFを編集"),
                tr("変更を保存しますか？"),
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            )
            if answer == QMessageBox.Cancel or (answer == QMessageBox.Save and not self.save()):
                event.ignore()
                return
        super().closeEvent(event)


def open_editor(path: Path, start: int = 0, parent: QWidget | None = None) -> PdfEditor | None:
    """Open the editor for a translated PDF, or explain why it cannot be edited."""
    document = edits.find(path)
    if document is None:
        QMessageBox.information(
            parent,
            tr("翻訳後のPDFを編集"),
            tr(
                "このPDFには編集用のデータが残っていません。"
                "このバージョンのLectavoで翻訳し直すと、編集できるようになります。"
            ),
        )
        return None
    editor = PdfEditor(document, start, parent)
    editor.showMaximized()
    return editor
