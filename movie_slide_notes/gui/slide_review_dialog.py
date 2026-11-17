from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QImage,
    QKeySequence,
    QPainter,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from movie_slide_notes.core.processor import BatchResult
from movie_slide_notes.core.slide_review import (
    ReviewSlide,
    ReviewTarget,
    added_slide_name,
    apply_review,
    build_review_targets,
    capture_frame,
    insert_chronologically,
    save_capture,
    video_duration,
)
from movie_slide_notes.media import VIDEO_EXTENSIONS
from movie_slide_notes.paths import resource_path

from .i18n import tr

THUMB = QSize(208, 117)
CAPTION_HEIGHT = 26
CELL = QSize(THUMB.width(), THUMB.height() + CAPTION_HEIGHT)
_ROLE = Qt.UserRole + 7


def icon(name: str) -> QIcon:
    return QIcon(str(resource_path("assets", "icons", f"{name}.svg")))


def format_clock(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def move_to_trash(path: Path) -> None:
    from PySide6.QtCore import QFile

    try:
        result = QFile.moveToTrash(str(path))
        ok = result[0] if isinstance(result, tuple) else bool(result)
    except Exception:
        ok = False
    if not ok:
        path.unlink(missing_ok=True)


def bgr_to_qimage(frame) -> QImage:
    import cv2

    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    return QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format_RGB888).copy()


class FramePickerDialog(QDialog):
    """Pick a moment in a source video to add as a slide."""

    def __init__(
        self, videos: list[tuple[int, Path]], start: tuple[int, float], region, parent=None
    ):
        super().__init__(parent)
        self.videos = videos
        self.region = region
        self.setWindowTitle(tr("取り逃した場面を追加"))
        self.resize(760, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)
        hint = QLabel(
            tr(
                "スライダーで場面を探し、「この場面を追加」を押します。検出範囲の設定で切り抜かれます。"
            )
        )
        hint.setObjectName("SecondaryText")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.video_combo = QComboBox()
        for index, path in videos:
            self.video_combo.addItem(path.name, index)
        layout.addWidget(self.video_combo)
        self.preview = QLabel()
        self.preview.setObjectName("SlidePreview")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(300)
        self.preview.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        layout.addWidget(self.preview, 1)
        controls = QHBoxLayout()
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setAccessibleName(tr("プレビューの時刻"))
        self.time_label = QLabel("00:00")
        self.time_label.setMinimumWidth(110)
        buttons = []
        for text, delta in (("-5秒", -5), ("-1秒", -1), ("+1秒", 1), ("+5秒", 5)):
            button = QPushButton(tr(text))
            button.clicked.connect(
                lambda _c=False, d=delta: self.slider.setValue(self.slider.value() + d)
            )
            buttons.append(button)
        controls.addWidget(buttons[0])
        controls.addWidget(buttons[1])
        controls.addWidget(self.slider, 1)
        controls.addWidget(buttons[2])
        controls.addWidget(buttons[3])
        controls.addWidget(self.time_label)
        layout.addLayout(controls)
        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = QPushButton(tr("キャンセル"))
        self.add_button = QPushButton(tr("この場面を追加"))
        self.add_button.setObjectName("PrimaryButton")
        actions.addWidget(cancel)
        actions.addWidget(self.add_button)
        layout.addLayout(actions)
        cancel.clicked.connect(self.reject)
        self.add_button.clicked.connect(self.accept)

        self._image = QImage()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._load_frame)
        self.slider.valueChanged.connect(self._slider_moved)
        self.video_combo.currentIndexChanged.connect(self._video_changed)
        combo_index = max(0, self.video_combo.findData(start[0]))
        self.video_combo.setCurrentIndex(combo_index)
        self._video_changed()
        self.slider.setValue(int(start[1]))

    @property
    def source_index(self) -> int:
        return int(self.video_combo.currentData())

    @property
    def timestamp(self) -> float:
        return float(self.slider.value())

    @property
    def video_path(self) -> Path:
        return self.videos[self.video_combo.currentIndex()][1]

    def _video_changed(self) -> None:
        duration = video_duration(self.video_path)
        self.slider.setRange(0, max(0, int(duration) - 1))
        self._load_frame()

    def _slider_moved(self) -> None:
        self.time_label.setText(
            f"{format_clock(self.timestamp)} / {format_clock(self.slider.maximum())}"
        )
        self._timer.start()

    def _load_frame(self) -> None:
        try:
            frame = capture_frame(self.video_path, self.timestamp, self.region)
        except Exception as exc:
            self.preview.setText(tr(str(exc)))
            self.add_button.setEnabled(False)
            return
        self.add_button.setEnabled(True)
        self._image = bgr_to_qimage(frame)
        self._show_image()

    def _show_image(self) -> None:
        if self._image.isNull():
            return
        self.preview.setPixmap(
            QPixmap.fromImage(self._image).scaled(
                self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._show_image()


class SlideReviewDialog(QDialog):
    def __init__(self, batch: BatchResult, slide_region=None, parent=None):
        super().__init__(parent)
        self.batch = batch
        self.slide_region = slide_region
        self.targets: list[ReviewTarget] = build_review_targets(batch)
        self.edits: dict[int, list[ReviewSlide]] = {
            index: list(target.slides) for index, target in enumerate(self.targets)
        }
        self.dirty: set[int] = set()
        self.undo_stack: list[tuple[int, int, ReviewSlide]] = []
        self._capture_dir = tempfile.TemporaryDirectory(prefix="lectavo_review_")
        self._thumb_cache: dict[Path, QPixmap] = {}
        self.outcomes = []
        self.current = 0

        self.setWindowTitle(tr("スライドの確認・修正"))
        self.resize(1120, 720)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(10)
        title = QLabel(tr("スライドの確認・修正"))
        title.setObjectName("PageTitle")
        root.addWidget(title)
        hint = QLabel(
            tr(
                "不要なページを削除し、ドラッグで順序を変え、取り逃した場面を追加してから「PDFを確定」を押します。"
            )
        )
        hint.setObjectName("SecondaryText")
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.target_combo = QComboBox()
        for target in self.targets:
            self.target_combo.addItem(tr(target.label))
        self.target_combo.setVisible(len(self.targets) > 1)
        root.addWidget(self.target_combo)

        splitter = QSplitter(Qt.Horizontal)
        self.grid = QListWidget()
        self.grid.setObjectName("SlideGrid")
        # ListMode with a wrapping left-to-right flow looks like a grid but,
        # unlike IconMode, reorders model rows when items are dragged.
        self.grid.setViewMode(QListView.ListMode)
        self.grid.setFlow(QListView.LeftToRight)
        self.grid.setWrapping(True)
        self.grid.setIconSize(CELL)
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setUniformItemSizes(True)
        self.grid.setDragEnabled(True)
        self.grid.setDragDropMode(QAbstractItemView.InternalMove)
        self.grid.setDefaultDropAction(Qt.MoveAction)
        self.grid.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.grid.setSpacing(8)
        self.grid.setAccessibleName(tr("抽出したスライド"))
        splitter.addWidget(self.grid)

        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(12, 0, 0, 0)
        side_layout.setSpacing(10)
        self.preview = QLabel()
        self.preview.setObjectName("SlidePreview")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(280, 180)
        self.preview.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        side_layout.addWidget(self.preview, 1)
        self.info = QLabel()
        self.info.setObjectName("SecondaryText")
        self.info.setWordWrap(True)
        side_layout.addWidget(self.info)
        move_row = QHBoxLayout()
        self.move_left = QPushButton(tr("前へ"))
        self.move_left.setIcon(icon("move-left"))
        self.move_right = QPushButton(tr("後ろへ"))
        self.move_right.setIcon(icon("move-right"))
        move_row.addWidget(self.move_left)
        move_row.addWidget(self.move_right)
        side_layout.addLayout(move_row)
        self.delete_button = QPushButton(tr("選択を削除"))
        self.delete_button.setIcon(icon("trash"))
        self.delete_button.setToolTip(tr("Deleteキーでも削除できます"))
        self.undo_button = QPushButton(tr("削除を元に戻す"))
        self.add_button = QPushButton(tr("取り逃した場面を追加"))
        self.add_button.setIcon(icon("add-frame"))
        for button in (self.delete_button, self.undo_button, self.add_button):
            side_layout.addWidget(button)
        splitter.addWidget(side)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([700, 380])
        root.addWidget(splitter, 1)

        footer = QHBoxLayout()
        self.count_label = QLabel()
        self.count_label.setObjectName("MutedText")
        footer.addWidget(self.count_label, 1)
        self.cancel_button = QPushButton(tr("キャンセル"))
        self.apply_button = QPushButton(tr("PDFを確定"))
        self.apply_button.setObjectName("PrimaryButton")
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.apply_button)
        root.addLayout(footer)

        self.target_combo.currentIndexChanged.connect(self._target_changed)
        self.grid.currentItemChanged.connect(self._selection_changed)
        self.grid.model().rowsMoved.connect(self._rows_moved)
        self.move_left.clicked.connect(lambda: self._move_selected(-1))
        self.move_right.clicked.connect(lambda: self._move_selected(1))
        self.delete_button.clicked.connect(self.delete_selected)
        self.undo_button.clicked.connect(self.undo_delete)
        self.add_button.clicked.connect(self.add_frame)
        self.cancel_button.clicked.connect(self.reject)
        self.apply_button.clicked.connect(self.apply)
        QShortcut(QKeySequence.Delete, self.grid, activated=self.delete_selected)
        QShortcut(QKeySequence.Undo, self, activated=self.undo_delete)

        self._populate()

    # State --------------------------------------------------------------------

    @property
    def target(self) -> ReviewTarget:
        return self.targets[self.current]

    @property
    def slides(self) -> list[ReviewSlide]:
        return self.edits[self.current]

    def _populate(self, select: int | None = None) -> None:
        self.grid.blockSignals(True)
        self.grid.clear()
        for position, slide in enumerate(self.slides):
            caption = self._caption(position, slide)
            item = QListWidgetItem(self._thumbnail(slide.path, caption, slide.added), "")
            item.setData(_ROLE, position)
            item.setData(Qt.AccessibleTextRole, caption)
            item.setToolTip(self._describe(slide))
            item.setFlags(item.flags() & ~Qt.ItemIsDropEnabled)
            self.grid.addItem(item)
        self.grid.blockSignals(False)
        if self.slides:
            row = min(select if select is not None else 0, len(self.slides) - 1)
            self.grid.setCurrentRow(row)
        self._selection_changed()
        self._update_buttons()

    def _caption(self, position: int, slide: ReviewSlide) -> str:
        mark = "  ＋" + tr("追加") if slide.added else ""
        return f"{position + 1}.  {format_clock(slide.timestamp)}{mark}"

    def _describe(self, slide: ReviewSlide) -> str:
        video = self.batch.results[slide.source_index].video_path.name
        return f"{video}  {format_clock(slide.timestamp)}"

    def _thumbnail(self, path: Path, caption: str, added: bool) -> QIcon:
        base = self._thumb_cache.get(path)
        if base is None:
            base = QPixmap(str(path))
            if not base.isNull():
                base = base.scaled(THUMB, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self._thumb_cache[path] = base
        canvas = QPixmap(CELL)
        canvas.fill(Qt.transparent)
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(0, 0, THUMB.width(), THUMB.height(), QColor("#111719"))
        if not base.isNull():
            painter.drawPixmap(
                (THUMB.width() - base.width()) // 2, (THUMB.height() - base.height()) // 2, base
            )
        font = QFont(painter.font())
        font.setPixelSize(12)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#0f8f8a") if added else QColor("#6b7280"))
        painter.drawText(0, THUMB.height(), CELL.width(), CAPTION_HEIGHT, Qt.AlignCenter, caption)
        painter.end()
        return QIcon(canvas)

    def _sync_from_grid(self) -> None:
        order = [self.grid.item(row).data(_ROLE) for row in range(self.grid.count())]
        previous = self.slides
        reordered = [previous[index] for index in order]
        if reordered != previous:
            self.edits[self.current] = reordered
            self.dirty.add(self.current)

    def _rows_moved(self, *_args) -> None:
        QTimer.singleShot(0, self._after_move)

    def _after_move(self) -> None:
        row = self.grid.currentRow()
        self._sync_from_grid()
        self._populate(select=row)

    def _selection_changed(self, *_args) -> None:
        item = self.grid.currentItem()
        if item is None:
            self.preview.clear()
            self.preview.setText(tr("スライドがありません"))
            self.info.setText("")
            return
        slide = self.slides[item.data(_ROLE)]
        pixmap = QPixmap(str(slide.path))
        self._preview_pixmap = pixmap
        self._show_preview()
        status = tr("追加した場面") if slide.added else tr("自動抽出")
        self.info.setText(
            f"{tr('スライド')} {self.grid.currentRow() + 1} / {len(self.slides)}\n"
            f"{self._describe(slide)}\n{status}"
        )

    def _show_preview(self) -> None:
        pixmap = getattr(self, "_preview_pixmap", None)
        if pixmap is None or pixmap.isNull():
            return
        self.preview.setPixmap(
            pixmap.scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._show_preview()

    def _update_buttons(self) -> None:
        has = bool(self.slides)
        self.delete_button.setEnabled(has)
        self.move_left.setEnabled(has)
        self.move_right.setEnabled(has)
        self.undo_button.setEnabled(any(entry[0] == self.current for entry in self.undo_stack))
        self.add_button.setEnabled(bool(self._source_videos()))
        removed = len(self.target.slides) - sum(1 for slide in self.slides if not slide.added)
        added = sum(1 for slide in self.slides if slide.added)
        changes = []
        if removed:
            changes.append(tr(f"削除 {removed} 枚"))
        if added:
            changes.append(tr(f"追加 {added} 枚"))
        if self.current in self.dirty and not changes:
            changes.append(tr("順序を変更"))
        text = tr(f"{len(self.slides)} 枚")
        if changes:
            text += "  ·  " + " / ".join(changes)
        self.count_label.setText(text)

    def _target_changed(self, index: int) -> None:
        self._sync_from_grid()
        self.current = index
        self._populate()

    # Actions --------------------------------------------------------------------

    def _move_selected(self, delta: int) -> None:
        row = self.grid.currentRow()
        target = row + delta
        if row < 0 or not 0 <= target < len(self.slides):
            return
        slides = self.slides
        slides[row], slides[target] = slides[target], slides[row]
        self.dirty.add(self.current)
        self._populate(select=target)

    def delete_selected(self) -> None:
        rows = sorted({self.grid.row(item) for item in self.grid.selectedItems()}, reverse=True)
        if not rows:
            return
        for row in rows:
            slide = self.slides.pop(row)
            self.undo_stack.append((self.current, row, slide))
        self.dirty.add(self.current)
        self._populate(select=min(rows))

    def undo_delete(self) -> None:
        for position in range(len(self.undo_stack) - 1, -1, -1):
            target_index, row, slide = self.undo_stack[position]
            if target_index == self.current:
                del self.undo_stack[position]
                self.slides.insert(min(row, len(self.slides)), slide)
                self._populate(select=row)
                return

    def _source_videos(self) -> list[tuple[int, Path]]:
        return [
            (index, self.batch.results[index].video_path)
            for index in self.target.result_indices
            if self.batch.results[index].video_path.suffix.lower() in VIDEO_EXTENSIONS
            and self.batch.results[index].video_path.exists()
        ]

    def add_frame(self) -> None:
        videos = self._source_videos()
        if not videos:
            return
        item = self.grid.currentItem()
        if item is not None:
            current = self.slides[item.data(_ROLE)]
            start = (current.source_index, current.timestamp + 2)
        else:
            start = (videos[0][0], 0.0)
        region = (
            self.batch.region_for(self.batch.results[start[0]].video_path, start[1])
            if self.batch.region_schedules
            else self.slide_region
        )
        picker = FramePickerDialog(videos, start, region, self)
        if picker.exec() != QDialog.Accepted:
            return
        try:
            region = (
                self.batch.region_for(picker.video_path, picker.timestamp)
                if self.batch.region_schedules
                else self.slide_region
            )
            frame = capture_frame(picker.video_path, picker.timestamp, region)
        except Exception as exc:
            QMessageBox.warning(self, tr("場面を追加"), tr(str(exc)))
            return
        name = added_slide_name(self.target, self.batch, picker.source_index, picker.timestamp)
        folder = Path(self._capture_dir.name)
        path = folder / name
        counter = 1
        while path.exists():
            path = folder / f"{Path(name).stem}_{counter}.png"
            counter += 1
        save_capture(frame, folder, path.name)
        slide = ReviewSlide(
            path=path,
            timestamp=picker.timestamp,
            source_index=picker.source_index,
            occurrences=((picker.source_index, picker.timestamp),),
            added=True,
        )
        position = insert_chronologically(self.slides, slide)
        self.dirty.add(self.current)
        self._populate(select=position)

    def apply(self) -> None:
        self._sync_from_grid()
        if not self.dirty:
            self.accept()
            return
        empty = [index for index in self.dirty if not self.edits[index]]
        if empty:
            answer = QMessageBox.question(
                self,
                tr("PDFを確定"),
                tr("すべてのスライドを削除すると、PDFも削除されます。続けますか？"),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        try:
            for index in sorted(self.dirty):
                self.outcomes.append(
                    apply_review(self.batch, self.targets[index], self.edits[index], move_to_trash)
                )
        except Exception as exc:
            QMessageBox.warning(self, tr("PDFを確定"), tr(str(exc)))
            return
        self.accept()

    def done(self, result: int) -> None:
        if result != QDialog.Accepted and self.dirty:
            answer = QMessageBox.question(
                self,
                tr("スライドの確認・修正"),
                tr("変更を破棄して閉じますか？"),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        self._capture_dir.cleanup()
        super().done(result)
