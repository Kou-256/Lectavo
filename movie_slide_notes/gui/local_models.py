"""Download, choose and delete the models of the built-in local LLM."""

from __future__ import annotations

from threading import Event

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from movie_slide_notes.gui.i18n import tr
from movie_slide_notes.pdf_translate import local_llm
from movie_slide_notes.pdf_translate.local_llm import LocalModel


class DownloadWorker(QObject):
    progress = Signal(int, int)
    finished = Signal()
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, model: LocalModel):
        super().__init__()
        self.model = model
        self.cancel_event = Event()

    @Slot()
    def run(self) -> None:
        try:
            local_llm.download_model(self.model, self.progress.emit, self.cancel_event)
            self.finished.emit()
        except local_llm.DownloadCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc) or exc.__class__.__name__)


def format_gb(value: int) -> str:
    return f"{value / 1_000_000_000:.1f} GB"


class ModelRow(QFrame):
    def __init__(self, model: LocalModel, group: QButtonGroup):
        super().__init__()
        self.model = model
        self.setObjectName("SoftPanel")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)
        self.radio = QRadioButton()
        self.radio.setAccessibleName(model.name)
        group.addButton(self.radio)
        layout.addWidget(self.radio, 0, Qt.AlignVCenter)

        text = QVBoxLayout()
        text.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        name = QLabel(model.name)
        name.setObjectName("SectionTitle")
        badge = QLabel(tr(model.tier))
        badge.setObjectName("Badge")
        title_row.addWidget(name)
        title_row.addWidget(badge)
        title_row.addStretch(1)
        text.addLayout(title_row)
        description = QLabel(tr(model.description))
        description.setObjectName("SecondaryText")
        description.setWordWrap(True)
        text.addWidget(description)
        self.detail = QLabel()
        self.detail.setObjectName("Hint")
        text.addWidget(self.detail)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.progress.setVisible(False)
        text.addWidget(self.progress)
        layout.addLayout(text, 1)

        self.download_button = QPushButton(tr("ダウンロード"))
        self.download_button.setObjectName("PrimaryButton")
        self.cancel_button = QPushButton(tr("キャンセル"))
        self.delete_button = QPushButton(tr("削除"))
        self.delete_button.setObjectName("LinkButton")
        for button in (self.download_button, self.cancel_button, self.delete_button):
            layout.addWidget(button, 0, Qt.AlignVCenter)
        self.refresh(downloading=False)

    def refresh(self, downloading: bool) -> None:
        installed = local_llm.is_installed(self.model)
        base = tr("{size} · メモリ{ram}GB以上").format(
            size=format_gb(self.model.size_bytes), ram=self.model.min_ram_gb
        )
        if downloading:
            self.detail.setText(base)
        elif installed:
            self.detail.setText(base + tr(" · ダウンロード済み"))
        else:
            partial = local_llm.model_path(self.model).with_suffix(".part")
            resumable = partial.exists()
            self.detail.setText(base + (tr(" · 途中まで保存済み") if resumable else ""))
        self.progress.setVisible(downloading)
        self.download_button.setVisible(not installed and not downloading)
        self.cancel_button.setVisible(downloading)
        self.delete_button.setVisible(installed and not downloading)
        self.radio.setEnabled(installed)

    def show_progress(self, done: int, total: int) -> None:
        self.progress.setValue(int(1000 * done / max(1, total)))
        self.detail.setText(
            tr("ダウンロード中 {done} / {total}").format(
                done=format_gb(done), total=format_gb(total)
            )
        )


class LocalModelsWidget(QWidget):
    """Model list for the built-in local LLM."""

    changed = Signal()

    def __init__(self, engine_settings, parent: QWidget | None = None):
        super().__init__(parent)
        self.engine_settings = engine_settings
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.rows: dict[str, ModelRow] = {}
        self._thread: QThread | None = None
        self._worker: DownloadWorker | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        for model in local_llm.CATALOG:
            row = ModelRow(model, self.group)
            row.download_button.clicked.connect(lambda _=False, m=model: self.download(m))
            row.cancel_button.clicked.connect(self.cancel_download)
            row.delete_button.clicked.connect(lambda _=False, m=model: self.delete(m))
            row.radio.toggled.connect(lambda checked, m=model: checked and self._select(m))
            self.rows[model.key] = row
            layout.addWidget(row)
        self.status = QLabel()
        self.status.setObjectName("Hint")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.refresh()

    @property
    def busy(self) -> bool:
        return self._worker is not None

    def refresh(self) -> None:
        selected = self.engine_settings.local_model_key
        downloading = self._worker.model.key if self._worker else None
        for key, row in self.rows.items():
            row.refresh(downloading=key == downloading)
            row.radio.blockSignals(True)
            row.radio.setChecked(key == selected and local_llm.is_installed(row.model))
            row.radio.blockSignals(False)
        if not local_llm.runtime_available():
            self.status.setText(
                tr("内蔵のローカルLLMが見つかりません。Lectavoを再インストールしてください。")
            )
        elif not local_llm.is_installed(local_llm.model_by_key(selected)):
            self.status.setText(
                tr("使うモデルをダウンロードしてください。ダウンロードは初回だけです。")
            )
        else:
            self.status.setText(
                tr("ダウンロードしたモデルはこのPCに保存され、オフラインで使えます。")
            )

    def _select(self, model: LocalModel) -> None:
        self.engine_settings.set_local_model(model.key)
        self.refresh()
        self.changed.emit()

    def download(self, model: LocalModel) -> None:
        if self._worker is not None:
            return
        self._thread = QThread(self)
        self._worker = DownloadWorker(model)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self.rows[model.key].show_progress)
        self._worker.finished.connect(lambda: self._done(model, None))
        self._worker.failed.connect(lambda message: self._done(model, message))
        self._worker.cancelled.connect(lambda: self._done(model, ""))
        self._thread.start()
        self.refresh()

    def cancel_download(self) -> None:
        if self._worker is not None:
            self._worker.cancel_event.set()

    def _done(self, model: LocalModel, error: str | None) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait()
            self._thread.deleteLater()
        self._thread = None
        self._worker = None
        if error is None and not local_llm.is_installed(
            local_llm.model_by_key(self.engine_settings.local_model_key)
        ):
            # The first downloaded model becomes the one in use.
            self.engine_settings.set_local_model(model.key)
        self.refresh()
        if error:
            self.status.setText(tr(error))
        self.changed.emit()

    def delete(self, model: LocalModel) -> None:
        answer = QMessageBox.question(
            self,
            tr("モデルを削除"),
            tr(
                "{name} を削除しますか？（{size}）\n使うときはもう一度ダウンロードが必要です。"
            ).format(name=model.name, size=format_gb(model.size_bytes)),
        )
        if answer != QMessageBox.Yes:
            return
        local_llm.delete_model(model)
        self.refresh()
        self.changed.emit()

    def shutdown(self) -> None:
        if self._worker is not None:
            self._worker.cancel_event.set()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(5000)
