from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QThread, Signal, Slot
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from movie_slide_notes.core.preflight import (
    LEVEL_ERROR,
    LEVEL_INFO,
    LEVEL_WARNING,
    PreflightReport,
    run_preflight,
)
from movie_slide_notes.paths import resource_path

from .i18n import tr

_LEVEL_ORDER = {LEVEL_ERROR: 0, LEVEL_WARNING: 1, LEVEL_INFO: 2}
_LEVEL_ICON = {
    LEVEL_ERROR: "status-error",
    LEVEL_WARNING: "status-warning",
    LEVEL_INFO: "status-info",
}


class PreflightWorker(QObject):
    finished = Signal(object)

    def __init__(self, videos: list[Path], output_dir: Path, options):
        super().__init__()
        self.videos, self.output_dir, self.options = videos, output_dir, options

    @Slot()
    def run(self) -> None:
        try:
            report = run_preflight(self.videos, self.output_dir, self.options)
        except Exception as exc:  # pragma: no cover - defensive
            report = PreflightReport()
            report.add(LEVEL_WARNING, "settings", "事前チェックを完了できませんでした", str(exc))
        self.finished.emit(report)


class PreflightDialog(QDialog):
    """Shows pre-run checks. Accepting the dialog means "start processing"."""

    def __init__(self, parent=None, *, start_mode: bool = True):
        super().__init__(parent)
        self.start_mode = start_mode
        self.report: PreflightReport | None = None
        self._thread: QThread | None = None
        self._worker: PreflightWorker | None = None
        self.setWindowTitle(tr("処理前チェック"))
        self.setMinimumWidth(520)
        self.resize(560, 480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)
        header = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(QIcon(str(resource_path("assets", "icons", "preflight.svg"))).pixmap(32))
        header.addWidget(icon)
        title = QLabel(tr("処理前チェック"))
        title.setObjectName("SectionTitle")
        header.addWidget(title, 1)
        layout.addLayout(header)
        self.summary = QLabel(tr("モデル、GPU、空き容量、音声トラックを確認しています…"))
        self.summary.setObjectName("SecondaryText")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.busy = QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.setTextVisible(False)
        layout.addWidget(self.busy)

        self.rows_host = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_host)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(8)
        self.rows_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(self.rows_host)
        layout.addWidget(scroll, 1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.back_button = QPushButton(tr("戻る" if start_mode else "閉じる"))
        self.start_button = QPushButton(tr("このまま開始"))
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.setEnabled(False)
        self.start_button.setVisible(start_mode)
        actions.addWidget(self.back_button)
        actions.addWidget(self.start_button)
        layout.addLayout(actions)
        self.back_button.clicked.connect(self.reject)
        self.start_button.clicked.connect(self.accept)

    def run(self, videos: list[Path], output_dir: Path, options) -> None:
        self._thread = QThread(self)
        self._worker = PreflightWorker(videos, output_dir, options)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self.show_report)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.start()

    def show_report(self, report: PreflightReport) -> None:
        self.report = report
        self.busy.setVisible(False)
        while self.rows_layout.count() > 1:
            item = self.rows_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        items = sorted(report.items, key=lambda item: _LEVEL_ORDER.get(item.level, 3))
        for position, item in enumerate(items):
            self.rows_layout.insertWidget(position, check_row(item))
        if report.has_errors:
            self.summary.setText(tr("このままでは処理できません。赤い項目を解決してください。"))
            self.start_button.setEnabled(False)
        elif report.has_warnings:
            self.summary.setText(tr("注意点があります。内容を確認して開始してください。"))
            self.start_button.setEnabled(True)
        else:
            self.summary.setText(tr("問題は見つかりませんでした。"))
            self.start_button.setEnabled(True)
        if self.start_mode and not report.needs_attention:
            self.accept()

    def done(self, result: int) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(5000)
        super().done(result)


def check_row(item) -> QFrame:
    row = QFrame()
    row.setObjectName("CheckRow")
    row.setProperty("level", item.level)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(12)
    icon = QLabel()
    icon_name = _LEVEL_ICON.get(item.level, "status-ok")
    icon.setPixmap(
        QIcon(str(resource_path("assets", "icons", f"{icon_name}.svg"))).pixmap(QSize(22, 22))
    )
    icon.setAlignment(Qt.AlignTop)
    layout.addWidget(icon)
    text = QVBoxLayout()
    text.setSpacing(2)
    title = QLabel(tr(item.title))
    title.setWordWrap(True)
    if item.level == LEVEL_ERROR:
        title.setObjectName("ErrorText")
    elif item.level == LEVEL_WARNING:
        title.setObjectName("WarningText")
    text.addWidget(title)
    if item.detail:
        detail = QLabel(tr(item.detail))
        detail.setObjectName("SecondaryText")
        detail.setWordWrap(True)
        text.addWidget(detail)
    layout.addLayout(text, 1)
    return row
