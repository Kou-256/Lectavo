"""PDF translation page and the translation-engine settings."""

from __future__ import annotations

import logging
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, QSettings, QSize, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from movie_slide_notes.gui.controls import ElidedLabel
from movie_slide_notes.gui.controls import NoWheelComboBox as QComboBox
from movie_slide_notes.gui.controls import NoWheelSpinBox as QSpinBox
from movie_slide_notes.gui.design_tokens import SPACING
from movie_slide_notes.gui.i18n import tr
from movie_slide_notes.gui.icons import icon_pixmap as svg_pixmap
from movie_slide_notes.gui.local_models import LocalModelsWidget
from movie_slide_notes.gui.settings_widget import form_layout, section_header
from movie_slide_notes.paths import user_cache_dir
from movie_slide_notes.pdf_translate import local_llm
from movie_slide_notes.pdf_translate.engines import (
    DEFAULT_BASE_URLS,
    DEFAULT_MODELS,
    DEFAULT_PROVIDER,
    MODEL_SUGGESTIONS,
    PROVIDERS,
    EngineConfig,
    TranslationError,
    create_engine,
    needs_api_key,
    supports_language,
    supports_style,
)
from movie_slide_notes.pdf_translate.languages import (
    AUTO,
    DEFAULT_SOURCE,
    DEFAULT_TARGET,
    LANGUAGES,
    is_known,
    language,
)
from movie_slide_notes.pdf_translate.models import (
    OCR_MODES,
    STYLES,
    PdfTranslationOptions,
    PdfTranslationResult,
    style_label,
)

STYLE_DESCRIPTIONS = {
    "natural": "自然で読みやすい訳文。論文・資料をすばやく理解したいとき",
    "literal": "原文の構成と表現に忠実。正確さを確かめたいとき",
    "plain": "短い文とやさしい言葉。専門用語には説明を添えます",
}
CACHE_FILE = "translation_cache.sqlite3"
PREVIEW_HEIGHT = 420
# PDFs translated at the same time; one is read while another is being translated.
PARALLEL_FILES = 2


def translation_cache_path() -> Path:
    return user_cache_dir() / CACHE_FILE


# --- engine settings ---------------------------------------------------------


class EngineSettings:
    """Reads and writes the translation engine choice in QSettings."""

    def __init__(self, settings: QSettings):
        self.settings = settings

    @property
    def provider(self) -> str:
        value = str(self.settings.value("translate/provider", DEFAULT_PROVIDER))
        return value if value in PROVIDERS else DEFAULT_PROVIDER

    @property
    def local_model_key(self) -> str:
        """The chosen model; without a choice, the default or else a model already downloaded."""
        if self.settings.contains("translate/model_builtin"):
            return local_llm.model_by_key(self.model("builtin")).key
        default = local_llm.model_by_key(local_llm.DEFAULT_MODEL_KEY)
        if local_llm.is_installed(default):
            return default.key
        installed = next((m for m in local_llm.CATALOG if local_llm.is_installed(m)), None)
        return (installed or default).key

    def set_local_model(self, key: str) -> None:
        self.settings.setValue("translate/model_builtin", key)

    @property
    def use_gpu(self) -> bool:
        return self.settings.value("translate/use_gpu", True, type=bool)

    def set_use_gpu(self, value: bool) -> None:
        self.settings.setValue("translate/use_gpu", value)

    def ready(self, provider: str | None = None) -> bool:
        """True when ``provider`` can translate right now."""
        provider = provider or self.provider
        if provider == "builtin":
            return local_llm.runtime_available() and local_llm.is_installed(
                local_llm.model_by_key(self.local_model_key)
            )
        return self.has_key(provider)

    def model(self, provider: str) -> str:
        return str(self.settings.value(f"translate/model_{provider}", DEFAULT_MODELS[provider]))

    def base_url(self, provider: str) -> str:
        return str(
            self.settings.value(
                f"translate/base_url_{provider}", DEFAULT_BASE_URLS.get(provider, "")
            )
        )

    def api_key(self, provider: str) -> str:
        from movie_slide_notes.pdf_translate.secret_store import unprotect

        return unprotect(str(self.settings.value(f"translate/key_{provider}", "")))

    def has_key(self, provider: str) -> bool:
        return not needs_api_key(provider) or bool(
            self.settings.value(f"translate/key_{provider}", "")
        )

    def save(self, provider: str, model: str, base_url: str, api_key: str | None) -> None:
        from movie_slide_notes.pdf_translate.secret_store import protect

        self.settings.setValue("translate/provider", provider)
        self.settings.setValue(
            f"translate/model_{provider}", model.strip() or DEFAULT_MODELS[provider]
        )
        if provider in DEFAULT_BASE_URLS:
            self.settings.setValue(
                f"translate/base_url_{provider}", base_url.strip() or DEFAULT_BASE_URLS[provider]
            )
        if api_key is not None:
            self.settings.setValue(f"translate/key_{provider}", protect(api_key.strip()))

    def config(self) -> EngineConfig:
        provider = self.provider
        return EngineConfig(
            provider=provider,
            model=self.model(provider),
            api_key=self.api_key(provider),
            base_url=self.base_url(provider),
            use_gpu=self.use_gpu,
        )

    def summary(self) -> str:
        provider = self.provider
        name = tr(PROVIDERS[provider])
        if provider == "builtin":
            return f"{name} · {local_llm.model_by_key(self.local_model_key).name}"
        model = self.model(provider)
        return f"{name} · {model}" if model else name


class ConnectionTestWorker(QObject):
    finished = Signal(bool, str)

    def __init__(self, config: EngineConfig):
        super().__init__()
        self.config = config

    @Slot()
    def run(self) -> None:
        started = time.monotonic()
        try:
            engine = create_engine(self.config)
            engine.prepare()
            result = engine.translate(["Hello, world."], "natural")
            elapsed = time.monotonic() - started
            self.finished.emit(True, f"{result[0]}（{elapsed:.1f}秒）")
        except TranslationError as exc:
            self.finished.emit(False, str(exc))
        except Exception as exc:  # pragma: no cover - network dependent
            self.finished.emit(False, str(exc) or exc.__class__.__name__)


class EngineSettingsWidget(QWidget):
    """Settings-page section: provider, API key, model, endpoint, connection test."""

    changed = Signal()

    def __init__(self, engine_settings: EngineSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self.engine_settings = engine_settings
        self._thread: QThread | None = None
        self._worker: ConnectionTestWorker | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING.md)
        form = form_layout()

        self.provider_combo = QComboBox()
        for key, label in PROVIDERS.items():
            self.provider_combo.addItem(label, key)
        form.addRow("翻訳エンジン", self.provider_combo)

        key_row = QHBoxLayout()
        key_row.setSpacing(8)
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText("APIキーを貼り付け")
        self.key_edit.setAccessibleName("APIキー")
        self.show_key_button = QPushButton("表示")
        self.show_key_button.setCheckable(True)
        key_row.addWidget(self.key_edit, 1)
        key_row.addWidget(self.show_key_button)
        self.key_label = QLabel("APIキー")
        form.addRow(self.key_label, key_row)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setAccessibleName("モデル")
        self.model_label = QLabel("モデル")
        form.addRow(self.model_label, self.model_combo)

        self.base_url_edit = QLineEdit()
        self.base_url_label = QLabel("接続先URL")
        form.addRow(self.base_url_label, self.base_url_edit)

        test_row = QHBoxLayout()
        test_row.setSpacing(12)
        self.save_button = QPushButton("保存")
        self.save_button.setObjectName("PrimaryButton")
        self.test_button = QPushButton("接続を確認")
        self.test_status = QLabel("")
        self.test_status.setObjectName("Hint")
        self.test_status.setWordWrap(True)
        test_row.addWidget(self.save_button)
        test_row.addWidget(self.test_button)
        test_row.addWidget(self.test_status, 1)
        form.addRow("", test_row)
        layout.addLayout(form)

        self.note = QLabel()
        self.note.setObjectName("Hint")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

        self.local_panel = QWidget()
        local_layout = QVBoxLayout(self.local_panel)
        local_layout.setContentsMargins(0, 0, 0, 0)
        local_layout.setSpacing(SPACING.sm)
        self.local_models = LocalModelsWidget(engine_settings)
        self.gpu_check = QCheckBox("GPUを使って高速化する（使えない場合は自動でCPU）")
        self.gpu_check.setChecked(engine_settings.use_gpu)
        local_layout.addWidget(self.local_models)
        local_layout.addWidget(self.gpu_check)
        layout.addWidget(self.local_panel)
        self.local_models.changed.connect(self.changed.emit)
        self.gpu_check.toggled.connect(self._gpu_changed)

        self.provider_combo.currentIndexChanged.connect(self._provider_changed)
        self.show_key_button.toggled.connect(
            lambda shown: self.key_edit.setEchoMode(
                QLineEdit.Normal if shown else QLineEdit.Password
            )
        )
        self.save_button.clicked.connect(self.save)
        self.test_button.clicked.connect(self.test_connection)
        self.load()

    def load(self) -> None:
        index = self.provider_combo.findData(self.engine_settings.provider)
        self.provider_combo.blockSignals(True)
        self.provider_combo.setCurrentIndex(max(0, index))
        self.provider_combo.blockSignals(False)
        self._provider_changed()

    def current_provider(self) -> str:
        return str(self.provider_combo.currentData())

    def _provider_changed(self) -> None:
        provider = self.current_provider()
        self.model_combo.clear()
        for model in MODEL_SUGGESTIONS[provider]:
            self.model_combo.addItem(model)
        self.model_combo.setEditText(self.engine_settings.model(provider))
        builtin = provider == "builtin"
        self.local_panel.setVisible(builtin)
        if builtin:
            self.local_models.refresh()
        has_models = provider not in ("deepl", "builtin")
        self.model_combo.setVisible(has_models)
        self.model_label.setVisible(has_models)
        uses_url = provider in DEFAULT_BASE_URLS
        self.base_url_edit.setVisible(uses_url)
        self.base_url_label.setVisible(uses_url)
        self.base_url_edit.setText(self.engine_settings.base_url(provider))
        uses_key = needs_api_key(provider)
        self.key_edit.setVisible(uses_key)
        self.show_key_button.setVisible(uses_key)
        self.key_label.setVisible(uses_key)
        self.key_edit.clear()
        self.key_edit.setPlaceholderText(
            tr("保存済み（変更する場合のみ入力）")
            if self.engine_settings.has_key(provider) and uses_key
            else tr("APIキーを貼り付け")
        )
        self.test_status.clear()
        self.note.setText(tr(provider_note(provider)))

    def _gpu_changed(self, value: bool) -> None:
        self.engine_settings.set_use_gpu(value)
        self.changed.emit()

    def save(self) -> None:
        provider = self.current_provider()
        if provider == "builtin":
            self.engine_settings.settings.setValue("translate/provider", provider)
            self.test_status.setText(tr("保存しました"))
            self.changed.emit()
            return
        key = self.key_edit.text().strip()
        self.engine_settings.save(
            provider,
            self.model_combo.currentText(),
            self.base_url_edit.text(),
            key if key else None,
        )
        self.key_edit.clear()
        self.key_edit.setPlaceholderText(
            tr("保存済み（変更する場合のみ入力）")
            if self.engine_settings.has_key(provider) and needs_api_key(provider)
            else tr("APIキーを貼り付け")
        )
        self.test_status.setText(tr("保存しました"))
        self.changed.emit()

    def test_connection(self) -> None:
        if self._thread is not None:
            return
        self.save()
        self.test_status.setText(tr("確認しています…"))
        self.test_button.setEnabled(False)
        self._thread = QThread(self)
        self._worker = ConnectionTestWorker(self.engine_settings.config())
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._test_finished)
        self._thread.start()

    def _test_finished(self, ok: bool, message: str) -> None:
        prefix = tr("接続できました: ") if ok else tr("接続できません: ")
        self.test_status.setText(prefix + tr(message))
        self.test_button.setEnabled(True)
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait()
            self._thread.deleteLater()
        self._thread = None
        self._worker = None


def provider_note(provider: str) -> str:
    return {
        "builtin": "このPCの中だけで翻訳します。PDFの本文は外部に送信されず、料金もかかりません。初回だけモデルのダウンロード（2〜7GB）が必要です。",
        "claude": "AnthropicのAPIキー（sk-ant-…）を使います。PDFの本文はAnthropicに送信されます。",
        "openai": "OpenAIのAPIキーを使います。PDFの本文はOpenAIに送信されます。",
        "gemini": "Google AI StudioのAPIキーを使います。PDFの本文はGoogleに送信されます。",
        "deepl": "DeepL APIのキーを使います（Free版は末尾が :fx）。「やさしい表現」には対応していません。",
        "local": "このPC上のOllamaやLM Studioで翻訳します。PDFの本文は外部に送信されません。",
    }[provider]


# --- worker ------------------------------------------------------------------


class PdfTranslateWorker(QObject):
    """Translates one or more PDFs, ``PARALLEL_FILES`` at a time, with one engine."""

    progress = Signal(int, object)  # position of the PDF, TranslateProgress
    log = Signal(str)
    file_done = Signal(int, str)  # position, "" when translated or the error message
    finished = Signal(object)  # list of (PdfTranslationResult | None, error message)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        jobs: list[tuple[Path, Path]],
        options: PdfTranslationOptions,
        config: EngineConfig,
    ):
        super().__init__()
        self.jobs = jobs  # (source PDF, output folder)
        self.options = options
        self.config = config
        self.cancel_event = Event()

    @Slot()
    def run(self) -> None:
        from movie_slide_notes.pdf_translate.pipeline import TranslationCancelled

        try:
            engine = create_engine(self.config)
        except (TranslationError, ValueError) as exc:
            self.failed.emit(str(exc))
            return
        outcomes: list[tuple[PdfTranslationResult | None, str]] = [(None, "")] * len(self.jobs)
        with ThreadPoolExecutor(max_workers=min(PARALLEL_FILES, len(self.jobs))) as pool:
            futures = {
                pool.submit(self._translate, position, engine): position
                for position in range(len(self.jobs))
            }
            for future in as_completed(futures):
                position = futures[future]
                try:
                    outcomes[position] = (future.result(), "")
                except TranslationCancelled:
                    continue
                except (TranslationError, ValueError) as exc:
                    outcomes[position] = (None, str(exc))
                except Exception as exc:
                    logging.getLogger("lectavo").exception("PDF translation failed")
                    outcomes[position] = (None, str(exc) or exc.__class__.__name__)
                self.file_done.emit(position, outcomes[position][1])
        if self.cancel_event.is_set():
            self.cancelled.emit()
        elif not any(result for result, _error in outcomes):
            self.failed.emit(next((error for _result, error in outcomes if error), ""))
        else:
            self.finished.emit(outcomes)

    def _translate(self, position: int, engine) -> PdfTranslationResult:
        from movie_slide_notes.pdf_translate.pipeline import translate_pdf

        source, output_dir = self.jobs[position]
        prefix = f"{source.name}: " if len(self.jobs) > 1 else ""
        return translate_pdf(
            source,
            output_dir,
            self.options,
            engine,
            self.config.cache_key,
            progress=lambda event: self.progress.emit(position, event),
            log=lambda message: self._log(prefix + message),
            cancel_event=self.cancel_event,
            cache_path=translation_cache_path(),
        )

    def cancel(self) -> None:
        self.cancel_event.set()

    def _log(self, message: str) -> None:
        logging.getLogger("lectavo").info(message)
        self.log.emit(f"{datetime.now():%H:%M:%S} {message}")


# --- small helpers -----------------------------------------------------------


def _label(text: str, name: str = "", wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    label.setWordWrap(wrap)
    return label


def _scroll_page() -> tuple[QScrollArea, QVBoxLayout]:
    scroll = QScrollArea()
    scroll.setObjectName("PageScroll")
    scroll.setWidgetResizable(True)
    content = QWidget()
    content.setObjectName("PageContent")
    layout = QVBoxLayout(content)
    layout.setContentsMargins(28, 32, 28, 32)
    layout.setSpacing(SPACING.md)
    scroll.setWidget(content)
    return scroll, layout


def pil_to_pixmap(image) -> QPixmap:
    rgb = image.convert("RGB")
    data = rgb.tobytes()
    qimage = QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format_RGB888)
    return QPixmap.fromImage(qimage.copy())


def render_pdf_page(path: Path, index: int, height: int, ratio: float) -> QPixmap | None:
    try:
        import pypdfium2 as pdfium

        from movie_slide_notes.pdf_translate.layout import PDFIUM_LOCK

        with PDFIUM_LOCK:
            document = pdfium.PdfDocument(str(path))
            try:
                if index >= len(document):
                    return None
                page = document[index]
                scale = height * ratio / max(1.0, page.get_height())
                image = page.render(scale=scale).to_pil()
                page.close()
            finally:
                document.close()
    except Exception:
        logging.getLogger("lectavo").exception("Preview rendering failed")
        return None
    pixmap = pil_to_pixmap(image)
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


class PreviewPane(QFrame):
    """One rendered page with a caption above it."""

    def __init__(self, caption: str):
        super().__init__()
        self.setObjectName("PreviewPane")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(10)
        self.caption = _label(caption, "PreviewCaption")
        self.caption.setAlignment(Qt.AlignCenter)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setMinimumHeight(160)
        self.image.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.caption)
        layout.addWidget(self.image, 1)

    double_clicked = None

    def mouseDoubleClickEvent(self, event) -> None:
        if self.double_clicked is not None:
            self.double_clicked()
            return
        super().mouseDoubleClickEvent(event)

    def set_pixmap(self, pixmap: QPixmap | None) -> None:
        if pixmap is None:
            self.image.setText(tr("プレビューを表示できません"))
        else:
            self.image.setPixmap(pixmap)


# --- page --------------------------------------------------------------------


class PdfTranslatePage(QWidget):
    """Choose a PDF, pick a style and outputs, translate, preview, save."""

    open_settings_requested = Signal()
    history_entry = Signal(dict)
    busy_changed = Signal(bool)

    def __init__(
        self, settings: QSettings, engine_settings: EngineSettings, parent: QWidget | None = None
    ):
        super().__init__(parent)
        self.settings = settings
        self.engine_settings = engine_settings
        self.sources: list[Path] = []
        self.source_info: dict = {}
        self.result: PdfTranslationResult | None = None
        self.results: list[PdfTranslationResult] = []
        self.failure_text = ""
        self.job_rows: list[tuple[QLabel, QProgressBar]] = []
        self.job_fractions: list[float] = []
        self.preview_index = 0
        self.thread: QThread | None = None
        self.worker: PdfTranslateWorker | None = None
        self.started_at = 0.0
        self.close_after_cancel = None
        self.last_options = PdfTranslationOptions()
        self.compare_viewer = None
        self.setAcceptDrops(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack)
        self._build_empty()
        self._build_ready()
        self._build_running()
        self._build_done()
        self._build_error()
        self._load_options()
        self.show_state("empty")

    # building -----------------------------------------------------------------

    def _header(self, layout: QVBoxLayout, title: str, text: str) -> None:
        layout.addWidget(_label(title, "PageTitle", wrap=True))
        layout.addWidget(_label(text, "SecondaryText", wrap=True))

    def _build_empty(self) -> None:
        page, layout = _scroll_page()
        layout.setSpacing(24)
        self._header(layout, "PDF翻訳", "PDFを、レイアウトを保ったまま17の言語に翻訳します。")
        drop = QFrame()
        drop.setObjectName("DropZone")
        drop.setMinimumHeight(300)
        drop_layout = QVBoxLayout(drop)
        drop_layout.setContentsMargins(32, 30, 32, 30)
        drop_layout.setSpacing(14)
        drop_layout.setAlignment(Qt.AlignCenter)
        hero = QLabel()
        hero.setPixmap(svg_pixmap("translate", 64, "#14a39b"))
        hero.setAlignment(Qt.AlignCenter)
        title = _label("PDFをここにドロップ（複数可）", "SectionTitle")
        title.setAlignment(Qt.AlignCenter)
        self.choose_button = QPushButton("PDFを選択")
        self.choose_button.setObjectName("PrimaryButton")
        hint = _label("論文・資料・スキャンしたPDF（OCRで読み取り）に対応", "SecondaryText")
        hint.setAlignment(Qt.AlignCenter)
        for widget in (hero, title):
            drop_layout.addWidget(widget)
        drop_layout.addWidget(self.choose_button, alignment=Qt.AlignCenter)
        drop_layout.addWidget(hint)
        layout.addWidget(drop, 1)

        layout.addWidget(_label("できること", "SectionTitle"))
        features = QGridLayout()
        features.setHorizontalSpacing(12)
        features.setVerticalSpacing(12)
        for index, (icon_name, heading, body) in enumerate(
            [
                (
                    "file-pdf",
                    "レイアウト保持",
                    "図表・数式・段組みはそのまま、本文だけを日本語に置き換えます。",
                ),
                (
                    "slides-review",
                    "日英対訳PDF",
                    "原文と訳文を左右に並べたPDFで、見比べながら読めます。",
                ),
                (
                    "preflight",
                    "スキャンPDFもOCR",
                    "文字情報のないページは自動で読み取ってから翻訳します。",
                ),
                (
                    "translate",
                    "3つの訳し方",
                    "意訳・直訳・やさしい日本語から、目的に合わせて選べます。",
                ),
            ]
        ):
            features.addWidget(_feature_card(icon_name, heading, body), index // 2, index % 2)
        layout.addLayout(features)
        layout.addStretch(1)
        self.stack.addWidget(page)
        self.empty_page = page
        self.choose_button.clicked.connect(self.choose_pdf)

    def _build_ready(self) -> None:
        page, layout = _scroll_page()
        self._header(layout, "PDF翻訳", "訳し方と出力を選んでから翻訳を開始します。")

        section_header(layout, "翻訳するPDF")
        card = QFrame()
        card.setObjectName("SoftPanel")
        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(14)
        file_icon = QLabel()
        file_icon.setPixmap(svg_pixmap("file-pdf", 32, "#14a39b"))
        card_layout.addWidget(file_icon, 0, Qt.AlignVCenter)
        file_text = QVBoxLayout()
        file_text.setSpacing(2)
        self.file_name = ElidedLabel("")
        self.file_name.setObjectName("SectionTitle")
        self.file_detail = _label("", "SecondaryText")
        file_text.addWidget(self.file_name)
        file_text.addWidget(self.file_detail)
        card_layout.addLayout(file_text, 1)
        self.change_file_button = QPushButton("変更")
        self.change_file_button.setObjectName("LinkButton")
        card_layout.addWidget(self.change_file_button)
        layout.addWidget(card)

        section_header(layout, "言語")
        language_row = QHBoxLayout()
        language_row.setSpacing(10)
        self.source_combo = QComboBox()
        self.source_combo.setAccessibleName("翻訳元の言語")
        self.source_combo.addItem("自動で判定", AUTO)
        self.target_combo = QComboBox()
        self.target_combo.setAccessibleName("翻訳先の言語")
        for item in LANGUAGES:
            self.source_combo.addItem(item.label, item.code)
            self.target_combo.addItem(item.label, item.code)
        self.swap_button = QPushButton("⇄")
        self.swap_button.setToolTip("翻訳元と翻訳先を入れ替える")
        self.swap_button.setAccessibleName("翻訳元と翻訳先を入れ替える")
        language_row.addWidget(_label("翻訳元", "SecondaryText"))
        language_row.addWidget(self.source_combo, 1)
        language_row.addWidget(self.swap_button)
        language_row.addWidget(_label("翻訳先", "SecondaryText"))
        language_row.addWidget(self.target_combo, 1)
        layout.addLayout(language_row)

        section_header(layout, "訳し方")
        self.style_group = QButtonGroup(self)
        self.style_group.setExclusive(True)
        style_row = QHBoxLayout()
        style_row.setSpacing(10)
        self.style_buttons: dict[str, QPushButton] = {}
        for key, label in STYLES.items():
            button = QPushButton(label)
            button.setObjectName("ChoiceCard")
            button.setCheckable(True)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.style_group.addButton(button)
            self.style_buttons[key] = button
            style_row.addWidget(button)
        layout.addLayout(style_row)
        self.style_hint = _label("", "Hint", wrap=True)
        layout.addWidget(self.style_hint)

        section_header(layout, "出力")
        form = form_layout()
        outputs = QVBoxLayout()
        outputs.setSpacing(8)
        self.japanese_check = QCheckBox("日本語PDF（元のレイアウトのまま日本語に）")
        self.bilingual_check = QCheckBox("日英対訳PDF（原文と訳文を左右に並べる）")
        outputs.addWidget(self.japanese_check)
        outputs.addWidget(self.bilingual_check)
        form.addRow("作成するPDF", outputs)

        self.ocr_combo = QComboBox()
        for key, label in OCR_MODES.items():
            self.ocr_combo.addItem(label, key)
        form.addRow("文字の読み取り（OCR）", self.ocr_combo)

        range_row = QHBoxLayout()
        range_row.setSpacing(8)
        self.all_pages_check = QCheckBox("すべてのページ")
        self.first_page = QSpinBox()
        self.first_page.setMinimum(1)
        self.last_page = QSpinBox()
        self.last_page.setMinimum(1)
        self.range_separator = QLabel("〜")
        range_row.addWidget(self.all_pages_check)
        range_row.addSpacing(8)
        range_row.addWidget(self.first_page)
        range_row.addWidget(self.range_separator)
        range_row.addWidget(self.last_page)
        range_row.addStretch(1)
        range_row.setContentsMargins(0, 0, 0, 0)
        self.range_row = QWidget()
        self.range_row.setLayout(range_row)
        form.addRow("ページ", self.range_row)
        self.options_form = form

        self.next_to_source_check = QCheckBox("PDFと同じフォルダに保存する")
        form.addRow("保存先", self.next_to_source_check)
        folder_row = QHBoxLayout()
        folder_row.setSpacing(8)
        self.output_folder = QLineEdit()
        self.output_folder.setReadOnly(True)
        self.output_browse = QPushButton("変更")
        folder_row.addWidget(self.output_folder, 1)
        folder_row.addWidget(self.output_browse)
        self.output_folder_row = QWidget()
        self.output_folder_row.setLayout(folder_row)
        folder_row.setContentsMargins(0, 0, 0, 0)
        form.addRow("", self.output_folder_row)
        layout.addLayout(form)

        section_header(layout, "翻訳エンジン")
        engine_row = QHBoxLayout()
        engine_row.setSpacing(12)
        self.engine_label = _label("", "", wrap=True)
        self.engine_settings_button = QPushButton("設定を変更")
        self.engine_settings_button.setObjectName("LinkButton")
        engine_row.addWidget(self.engine_label, 1)
        engine_row.addWidget(self.engine_settings_button)
        layout.addLayout(engine_row)
        self.engine_warning = _label("", "WarningText", wrap=True)
        layout.addWidget(self.engine_warning)

        layout.addStretch(1)
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.start_button = QPushButton("翻訳を開始")
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.setMinimumWidth(160)
        actions.addWidget(self.start_button)
        layout.addLayout(actions)
        self.stack.addWidget(page)
        self.ready_page = page

        self.change_file_button.clicked.connect(self.choose_pdf)
        self.style_group.buttonToggled.connect(self._style_changed)
        self.all_pages_check.toggled.connect(self._range_changed)
        self.first_page.valueChanged.connect(lambda v: self.last_page.setMinimum(v))
        self.next_to_source_check.toggled.connect(self._folder_mode_changed)
        self.output_browse.clicked.connect(self._browse_output)
        self.engine_settings_button.clicked.connect(self.open_settings_requested.emit)
        self.source_combo.currentIndexChanged.connect(self._languages_changed)
        self.target_combo.currentIndexChanged.connect(self._languages_changed)
        self.swap_button.clicked.connect(self._swap_languages)
        self.japanese_check.toggled.connect(self._validate)
        self.bilingual_check.toggled.connect(self._validate)
        self.start_button.clicked.connect(self.start)

    def _build_running(self) -> None:
        page, layout = _scroll_page()
        self.running_title = _label("翻訳しています", "PageTitle", wrap=True)
        layout.addWidget(self.running_title)
        self.running_file = _label("", "SecondaryText", wrap=True)
        layout.addWidget(self.running_file)
        layout.addSpacing(12)
        self.running_stage = _label("", "SectionTitle")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setTextVisible(False)
        self.running_detail = _label("", "SecondaryText")
        layout.addWidget(self.running_stage)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.running_detail)
        # One row per PDF when several are translated together.
        self.jobs_layout = QVBoxLayout()
        self.jobs_layout.setSpacing(6)
        layout.addLayout(self.jobs_layout)
        self.running_log = QPlainTextEdit()
        self.running_log.setReadOnly(True)
        self.running_log.setMaximumBlockCount(500)
        self.running_log.setMinimumHeight(140)
        layout.addWidget(self.running_log, 1)
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.cancel_button = QPushButton("キャンセル")
        actions.addWidget(self.cancel_button)
        layout.addLayout(actions)
        self.stack.addWidget(page)
        self.running_page = page
        self.cancel_button.clicked.connect(self.cancel)
        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.setInterval(1000)
        self.elapsed_timer.timeout.connect(self._update_elapsed)

    def _build_done(self) -> None:
        page, layout = _scroll_page()
        title_row = QHBoxLayout()
        title_row.addWidget(_label("翻訳が完了しました", "PageTitle"))
        title_row.addStretch(1)
        layout.addLayout(title_row)
        self.result_row = QWidget()
        result_row = QHBoxLayout(self.result_row)
        result_row.setContentsMargins(0, 0, 0, 0)
        result_row.setSpacing(10)
        result_row.addWidget(_label("表示するPDF", "SecondaryText"))
        self.result_combo = QComboBox()
        self.result_combo.setAccessibleName("表示するPDF")
        result_row.addWidget(self.result_combo, 1)
        layout.addWidget(self.result_row)
        self.result_row.setVisible(False)
        self.done_detail = _label("", "SecondaryText", wrap=True)
        layout.addWidget(self.done_detail)

        previews = QHBoxLayout()
        previews.setSpacing(12)
        self.before_pane = PreviewPane("翻訳前")
        self.after_pane = PreviewPane("翻訳後")
        previews.addWidget(self.before_pane, 1)
        previews.addWidget(self.after_pane, 1)
        layout.addLayout(previews)

        nav = QHBoxLayout()
        nav.addStretch(1)
        self.prev_page_button = QPushButton()
        self.prev_page_button.setIcon(_icon("move-left"))
        self.prev_page_button.setAccessibleName("前のページ")
        self.prev_page_button.setToolTip("前のページ")
        self.page_indicator = _label("", "SecondaryText")
        self.page_indicator.setAlignment(Qt.AlignCenter)
        self.page_indicator.setMinimumWidth(90)
        self.next_page_button = QPushButton()
        self.next_page_button.setIcon(_icon("move-right"))
        self.next_page_button.setAccessibleName("次のページ")
        self.next_page_button.setToolTip("次のページ")
        nav.addWidget(self.prev_page_button)
        nav.addWidget(self.page_indicator)
        nav.addWidget(self.next_page_button)
        nav.addSpacing(16)
        self.compare_button = QPushButton("拡大して比較")
        self.compare_button.setObjectName("PrimaryButton")
        self.compare_button.setIcon(_icon("slides-review"))
        self.compare_button.setToolTip("翻訳前と翻訳後を画面いっぱいに並べて比較します")
        nav.addWidget(self.compare_button)
        nav.addStretch(1)
        layout.addLayout(nav)

        section_header(layout, "保存したファイル")
        self.files_layout = QVBoxLayout()
        self.files_layout.setSpacing(8)
        layout.addLayout(self.files_layout)

        layout.addStretch(1)
        actions = QHBoxLayout()
        self.open_folder_button = QPushButton("フォルダを開く")
        self.translate_again_button = QPushButton("訳し方を変えて再翻訳")
        self.new_pdf_button = QPushButton("別のPDFを翻訳")
        self.new_pdf_button.setObjectName("PrimaryButton")
        actions.addWidget(self.open_folder_button)
        actions.addStretch(1)
        actions.addWidget(self.translate_again_button)
        actions.addWidget(self.new_pdf_button)
        layout.addLayout(actions)
        self.stack.addWidget(page)
        self.done_page = page

        self.result_combo.currentIndexChanged.connect(self._show_result)
        self.prev_page_button.clicked.connect(lambda: self._show_preview(self.preview_index - 1))
        self.next_page_button.clicked.connect(lambda: self._show_preview(self.preview_index + 1))
        self.open_folder_button.clicked.connect(self._open_output_folder)
        self.compare_button.clicked.connect(self.open_compare)
        for pane in (self.before_pane, self.after_pane):
            pane.double_clicked = self.open_compare
        self.translate_again_button.clicked.connect(lambda: self.show_state("ready"))
        self.new_pdf_button.clicked.connect(self.choose_pdf)

    def _build_error(self) -> None:
        page, layout = _scroll_page()
        self.error_title = _label("翻訳できませんでした", "PageTitle", wrap=True)
        self.error_message = _label("", "ErrorText", wrap=True)
        self.error_hint = _label("", "SecondaryText", wrap=True)
        layout.addWidget(self.error_title)
        layout.addWidget(self.error_message)
        layout.addWidget(self.error_hint)
        layout.addStretch(1)
        actions = QHBoxLayout()
        self.error_settings_button = QPushButton("翻訳エンジンの設定")
        self.error_back_button = QPushButton("設定に戻る")
        self.error_retry_button = QPushButton("再実行")
        self.error_retry_button.setObjectName("PrimaryButton")
        actions.addWidget(self.error_settings_button)
        actions.addStretch(1)
        actions.addWidget(self.error_back_button)
        actions.addWidget(self.error_retry_button)
        layout.addLayout(actions)
        self.stack.addWidget(page)
        self.error_page = page
        self.error_settings_button.clicked.connect(self.open_settings_requested.emit)
        self.error_back_button.clicked.connect(lambda: self.show_state("ready"))
        self.error_retry_button.clicked.connect(self.start)

    # state --------------------------------------------------------------------

    def show_state(self, state: str) -> None:
        page = {
            "empty": self.empty_page,
            "ready": self.ready_page,
            "running": self.running_page,
            "done": self.done_page,
            "error": self.error_page,
        }[state]
        if state == "ready":
            self.refresh_engine()
        self.stack.setCurrentWidget(page)

    @property
    def busy(self) -> bool:
        return self.worker is not None

    def _load_options(self) -> None:
        style = str(self.settings.value("translate/style", "natural"))
        self.style_buttons.get(style, self.style_buttons["natural"]).setChecked(True)
        self.japanese_check.setChecked(
            self.settings.value("translate/make_japanese", True, type=bool)
        )
        self.bilingual_check.setChecked(
            self.settings.value("translate/make_bilingual", True, type=bool)
        )
        index = self.ocr_combo.findData(str(self.settings.value("translate/ocr", "auto")))
        self.ocr_combo.setCurrentIndex(max(0, index))
        self.next_to_source_check.setChecked(
            self.settings.value("translate/next_to_source", True, type=bool)
        )
        default_folder = Path.home() / "Documents" / "Lectavo" / "translations"
        self.output_folder.setText(
            str(self.settings.value("translate/output_dir", str(default_folder)))
        )
        source = str(self.settings.value("translate/source_lang", DEFAULT_SOURCE))
        target = str(self.settings.value("translate/target_lang", DEFAULT_TARGET))
        self._set_combo(self.source_combo, source if source == AUTO or is_known(source) else AUTO)
        self._set_combo(self.target_combo, target if is_known(target) else DEFAULT_TARGET)
        self.all_pages_check.setChecked(True)
        self._folder_mode_changed()
        self._languages_changed()

    def _save_options(self) -> None:
        self.settings.setValue("translate/style", self.selected_style())
        self.settings.setValue("translate/make_japanese", self.japanese_check.isChecked())
        self.settings.setValue("translate/make_bilingual", self.bilingual_check.isChecked())
        self.settings.setValue("translate/ocr", self.ocr_combo.currentData())
        self.settings.setValue("translate/next_to_source", self.next_to_source_check.isChecked())
        self.settings.setValue("translate/output_dir", self.output_folder.text())
        self.settings.setValue("translate/source_lang", self.source_lang())
        self.settings.setValue("translate/target_lang", self.target_lang())

    @staticmethod
    def _set_combo(combo, value) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(max(0, index))

    def source_lang(self) -> str:
        return str(self.source_combo.currentData())

    def target_lang(self) -> str:
        return str(self.target_combo.currentData())

    def _swap_languages(self) -> None:
        source, target = self.source_lang(), self.target_lang()
        if source == AUTO:
            # "Auto" cannot become a target: guess the usual pair instead.
            source = "en" if target != "en" else "ja"
        self._set_combo(self.source_combo, target)
        self._set_combo(self.target_combo, source)

    def _languages_changed(self, *_args) -> None:
        target = self.target_lang()
        name = tr(language(target).label)
        for key, button in self.style_buttons.items():
            button.setText(tr(style_label(key, target)))
        if target == "ja":
            self.japanese_check.setText(tr("日本語PDF（元のレイアウトのまま日本語に）"))
            self.bilingual_check.setText(tr("日英対訳PDF（原文と訳文を左右に並べる）"))
        else:
            self.japanese_check.setText(
                tr("{language}のPDF（元のレイアウトのまま）").format(language=name)
            )
            self.bilingual_check.setText(tr("対訳PDF（原文と訳文を左右に並べる）"))
        self._style_changed()

    def selected_style(self) -> str:
        for key, button in self.style_buttons.items():
            if button.isChecked():
                return key
        return "natural"

    def refresh_engine(self) -> None:
        provider = self.engine_settings.provider
        self.engine_label.setText(self.engine_settings.summary())
        warnings = []
        if provider == "builtin" and not self.engine_settings.ready(provider):
            if not local_llm.runtime_available():
                warnings.append(
                    tr("内蔵のローカルLLMが見つかりません。Lectavoを再インストールしてください。")
                )
            else:
                warnings.append(
                    tr(
                        "翻訳に使うモデルがまだありません。「設定を変更」からダウンロードしてください（初回のみ）。"
                    )
                )
        elif not self.engine_settings.ready(provider):
            warnings.append(tr("APIキーが設定されていません。「設定を変更」から登録してください。"))
        if not supports_style(provider, self.selected_style()):
            warnings.append(
                tr(
                    "DeepLは「やさしい表現」に対応していません。ほかの訳し方かエンジンを選んでください。"
                )
            )
        if not supports_language(provider, self.source_lang(), self.target_lang()):
            warnings.append(
                tr("DeepLはこの言語に対応していません。ほかの言語かエンジンを選んでください。")
            )
        if self.source_lang() == self.target_lang():
            warnings.append(tr("翻訳元と翻訳先が同じ言語です。"))
        self.engine_warning.setText("\n".join(warnings))
        self.engine_warning.setVisible(bool(warnings))
        self._validate()

    def _validate(self) -> None:
        provider = self.engine_settings.provider
        ok = (
            bool(self.sources)
            and (self.japanese_check.isChecked() or self.bilingual_check.isChecked())
            and self.engine_settings.ready(provider)
            and supports_style(provider, self.selected_style())
            and supports_language(provider, self.source_lang(), self.target_lang())
            and self.source_lang() != self.target_lang()
        )
        self.start_button.setEnabled(ok)

    def _style_changed(self, *_args) -> None:
        self.style_hint.setText(tr(STYLE_DESCRIPTIONS[self.selected_style()]))
        self.refresh_engine()

    def _range_changed(self, all_pages: bool) -> None:
        # Hidden rather than disabled: the range only matters when it is chosen.
        for widget in (self.first_page, self.last_page, self.range_separator):
            widget.setVisible(not all_pages)

    def _folder_mode_changed(self, *_args) -> None:
        self.output_folder_row.setVisible(not self.next_to_source_check.isChecked())

    def _browse_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, tr("保存先を選択"), self.output_folder.text()
        )
        if folder:
            self.output_folder.setText(folder)

    @property
    def source(self) -> Path | None:
        return self.sources[0] if self.sources else None

    def output_dir(self, source: Path | None = None) -> Path:
        source = source or self.source
        if self.next_to_source_check.isChecked() and source is not None:
            return source.parent
        return Path(self.output_folder.text())

    # file selection -----------------------------------------------------------

    def choose_pdf(self) -> None:
        start = (
            str(self.source.parent)
            if self.source
            else str(self.settings.value("translate/last_dir", ""))
        )
        paths, _ = QFileDialog.getOpenFileNames(self, tr("PDFを選択"), start, tr("PDF (*.pdf)"))
        if paths:
            self.set_sources([Path(path) for path in paths])

    def set_source(self, path: Path) -> bool:
        return self.set_sources([path])

    def set_sources(self, paths: list[Path]) -> bool:
        """Choose the PDFs to translate; several are translated in one run."""
        if self.busy:
            return False
        paths = list(dict.fromkeys(paths))
        if not paths or any(p.suffix.lower() != ".pdf" or not p.is_file() for p in paths):
            QMessageBox.warning(self, tr("PDF翻訳"), tr("PDFファイルを選んでください。"))
            return False
        from movie_slide_notes.pdf_translate.pipeline import inspect_pdf

        infos = []
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for path in paths:
                infos.append(inspect_pdf(path))
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(
                self,
                tr("PDF翻訳"),
                tr("このPDFを開けませんでした。") + f"\n\n{path.name}\n{tr(str(exc))}",
            )
            return False
        QApplication.restoreOverrideCursor()
        self.sources = paths
        self.source_info = infos[0]
        self.settings.setValue("translate/last_dir", str(paths[0].parent))
        total = sum(int(info["pages"]) for info in infos)
        if len(paths) == 1:
            info = infos[0]
            self.file_name.setText(paths[0].name)
            detail = tr("{pages}ページ").format(pages=total)
            if info.get("scanned"):
                detail += tr(" · スキャンPDF（OCRで読み取ります）")
            elif info.get("partly_scanned"):
                detail += tr(" · 一部がスキャンページ（自動でOCR）")
        else:
            self.file_name.setText(tr("{count}件のPDF").format(count=len(paths)))
            detail = tr("合計{pages}ページ · {parallel}件ずつ並行して翻訳します").format(
                pages=total, parallel=PARALLEL_FILES
            )
            detail += "\n" + "\n".join(path.name for path in paths)
        self.file_detail.setText(detail)
        self.file_detail.setWordWrap(True)
        pages = int(infos[0]["pages"])
        for spin in (self.first_page, self.last_page):
            spin.setMaximum(max(1, pages))
        self.first_page.setValue(1)
        self.last_page.setValue(max(1, pages))
        self.all_pages_check.setChecked(True)
        self._range_changed(True)
        # A page range only makes sense for one PDF.
        single = len(paths) == 1
        self.range_row.setVisible(single)
        label = self.options_form.labelForField(self.range_row)
        if label is not None:
            label.setVisible(single)
        self.show_state("ready")
        return True

    # running ------------------------------------------------------------------

    def start(self) -> None:
        if self.busy or not self.sources:
            return
        self._save_options()
        options = PdfTranslationOptions(
            style=self.selected_style(),
            ocr_mode=str(self.ocr_combo.currentData()),
            make_japanese=self.japanese_check.isChecked(),
            make_bilingual=self.bilingual_check.isChecked(),
            first_page=1 if self.all_pages_check.isChecked() else self.first_page.value(),
            last_page=0 if self.all_pages_check.isChecked() else self.last_page.value(),
            source_lang=self.source_lang(),
            target_lang=self.target_lang(),
        )
        self.last_options = options
        jobs = [(source, self.output_dir(source)) for source in self.sources]
        try:
            for _source, output_dir in jobs:
                output_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            QMessageBox.warning(self, tr("PDF翻訳"), tr("保存先のフォルダを作成できません。"))
            return
        from movie_slide_notes.pdf_translate.pipeline import output_paths

        existing = [
            path
            for source, output_dir in jobs
            for path in output_paths(source, output_dir, options)
            if path and path.exists()
        ]
        if existing:
            names = "\n".join(p.name for p in existing)
            answer = QMessageBox.question(
                self,
                tr("PDF翻訳"),
                tr("同じ名前のファイルがあります。上書きしますか？") + f"\n\n{names}",
            )
            if answer != QMessageBox.Yes:
                return
        self.running_log.clear()
        if len(jobs) == 1:
            self.running_file.setText(self.source.name)
        else:
            self.running_file.setText(
                tr("{count}件のPDFを、{parallel}件ずつ並行して翻訳しています").format(
                    count=len(jobs), parallel=PARALLEL_FILES
                )
            )
        self._build_job_rows([source for source, _output in jobs])
        self.running_stage.setText(tr("準備しています"))
        self.running_detail.setText("")
        self.progress_bar.setValue(0)
        self.cancel_button.setEnabled(True)
        self.show_state("running")
        self.started_at = time.monotonic()
        self.elapsed_timer.start()

        self.thread = QThread(self)
        self.worker = PdfTranslateWorker(jobs, options, self.engine_settings.config())
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._progress)
        self.worker.log.connect(self.running_log.appendPlainText)
        self.worker.file_done.connect(self._file_done)
        self.worker.finished.connect(self._finished)
        self.worker.failed.connect(self._failed)
        self.worker.cancelled.connect(self._cancelled)
        self.thread.start()
        self.busy_changed.emit(True)

    def cancel(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
            self.cancel_button.setEnabled(False)
            self.running_stage.setText(tr("キャンセルしています…"))

    def _build_job_rows(self, sources: list[Path]) -> None:
        while self.jobs_layout.count():
            item = self.jobs_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.job_rows = []
        self.job_fractions = [0.0] * len(sources)
        if len(sources) < 2:
            return
        for source in sources:
            row = QFrame()
            row.setObjectName("SoftPanel")
            row_layout = QGridLayout(row)
            row_layout.setContentsMargins(12, 8, 12, 8)
            row_layout.setHorizontalSpacing(12)
            row_layout.setVerticalSpacing(4)
            name = ElidedLabel(source.name)
            name.setObjectName("FileName")
            status = _label(tr("待機中"), "SecondaryText")
            bar = QProgressBar()
            bar.setRange(0, 1000)
            bar.setTextVisible(False)
            bar.setFixedHeight(6)
            row_layout.addWidget(name, 0, 0)
            row_layout.addWidget(status, 0, 1, Qt.AlignRight)
            row_layout.addWidget(bar, 1, 0, 1, 2)
            row_layout.setColumnStretch(0, 1)
            self.jobs_layout.addWidget(row)
            self.job_rows.append((status, bar))

    def _progress(self, position: int, event) -> None:
        if position < len(self.job_fractions):
            self.job_fractions[position] = min(event.fraction, 0.99)
        if self.job_rows:
            status, bar = self.job_rows[position]
            bar.setValue(int(event.fraction * 1000))
            status.setText(tr(event.message))
        else:
            self.running_stage.setText(tr(event.message))
        self._update_overall()

    def _update_overall(self) -> None:
        fractions = self.job_fractions or [0.0]
        self.progress_bar.setValue(int(sum(fractions) / len(fractions) * 1000))
        if self.job_rows:
            done = sum(1 for fraction in fractions if fraction >= 1.0)
            self.running_stage.setText(
                tr("{done}/{count}件が完了").format(done=done, count=len(fractions))
            )

    def _file_done(self, position: int, error: str) -> None:
        if position < len(self.job_fractions):
            self.job_fractions[position] = 1.0
        if position < len(self.job_rows):
            status, bar = self.job_rows[position]
            bar.setValue(1000)
            if error:
                status.setText(tr("翻訳できませんでした"))
                status.setObjectName("ErrorText")
                status.style().unpolish(status)
                status.style().polish(status)
            else:
                status.setText(tr("完了しました"))
        self._update_overall()

    def _update_elapsed(self) -> None:
        seconds = int(time.monotonic() - self.started_at)
        minutes, secs = divmod(seconds, 60)
        self.running_detail.setText(tr("経過時間 {time}").format(time=f"{minutes:02d}:{secs:02d}"))

    def _stop_thread(self) -> None:
        self.elapsed_timer.stop()
        if self.thread is not None:
            self.thread.quit()
            self.thread.wait()
            self.thread.deleteLater()
        self.thread = None
        self.worker = None
        self.busy_changed.emit(False)
        if self.close_after_cancel is not None:
            callback, self.close_after_cancel = self.close_after_cancel, None
            callback()

    def _finished(self, outcomes: list) -> None:
        self._stop_thread()
        self.results = [result for result, _error in outcomes if result is not None]
        self.failure_text = "\n".join(
            f"{source.name}: {tr(error) if error else tr('翻訳できませんでした')}"
            for source, (result, error) in zip(self.sources, outcomes, strict=False)
            if result is None
        )
        self.result_combo.blockSignals(True)
        self.result_combo.clear()
        for result in self.results:
            self.result_combo.addItem(result.source.name)
        self.result_combo.blockSignals(False)
        self.result_row.setVisible(len(self.results) > 1)
        self._show_result(0)
        self.show_state("done")
        for result in self.results:
            self.history_entry.emit(
                {
                    "name": result.source.name,
                    "path": str(result.source),
                    "output": str((result.japanese_pdf or result.bilingual_pdf).parent),
                    "status": "翻訳完了",
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                }
            )

    def _show_result(self, position: int) -> None:
        """Show the summary, files and preview of one translated PDF."""
        if not 0 <= position < len(self.results):
            return
        result = self.results[position]
        self.result = result
        parts = [tr("{pages}ページ").format(pages=result.pages)]
        if result.ocr_pages:
            parts.append(tr("OCR {count}ページ").format(count=result.ocr_pages))
        parts.append(tr("{count}か所を翻訳").format(count=result.translated_blocks))
        options = self.last_options
        parts.append(
            f"{tr(self.source_combo.currentText())} → {tr(language(options.target_lang).label)}"
        )
        parts.append(tr(style_label(options.style, options.target_lang)))
        lines = [" · ".join(parts)]
        if len(self.sources) > 1:
            lines.insert(
                0,
                tr("{done}/{count}件のPDFを翻訳しました").format(
                    done=len(self.results), count=len(self.sources)
                ),
            )
        if self.failure_text:
            lines += [tr("翻訳できなかったPDF:"), self.failure_text]
        self.done_detail.setText("\n".join(lines))
        self._populate_files(result)
        self._show_preview(0)

    def _failed(self, message: str) -> None:
        self._stop_thread()
        self.error_message.setText(tr(message))
        self.error_hint.setText(tr(error_hint(message)))
        self.show_state("error")

    def _cancelled(self) -> None:
        self._stop_thread()
        self.show_state("ready")

    # results ------------------------------------------------------------------

    def _populate_files(self, result: PdfTranslationResult) -> None:
        while self.files_layout.count():
            item = self.files_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        japanese = self.last_options.target_lang == "ja"
        translated_kind = (
            "日本語PDF"
            if japanese
            else tr("{language}のPDF").format(
                language=tr(language(self.last_options.target_lang).label)
            )
        )
        for path, kind in (
            (result.japanese_pdf, translated_kind),
            (result.bilingual_pdf, "日英対訳PDF" if japanese else "対訳PDF"),
        ):
            if path is None:
                continue
            self.files_layout.addWidget(self._file_row(path, kind))

    def _file_row(self, path: Path, kind: str) -> QWidget:
        row = QFrame()
        row.setObjectName("SoftPanel")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)
        icon = QLabel()
        icon.setPixmap(svg_pixmap("file-pdf", 24, "#14a39b"))
        layout.addWidget(icon)
        text = QVBoxLayout()
        text.setSpacing(0)
        name = ElidedLabel(path.name)
        name.setObjectName("FileName")
        text.addWidget(_label(tr(kind), "Badge"), 0, Qt.AlignLeft)
        text.addWidget(name)
        layout.addLayout(text, 1)
        open_button = QPushButton(tr("開く"))
        open_button.setObjectName("PrimaryButton")
        save_button = QPushButton(tr("名前を付けて保存…"))
        open_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))))
        save_button.clicked.connect(lambda: self._save_copy(path))
        layout.addWidget(save_button)
        layout.addWidget(open_button)
        return row

    def _save_copy(self, path: Path) -> None:
        target, _ = QFileDialog.getSaveFileName(
            self,
            tr("名前を付けて保存"),
            str(Path.home() / "Downloads" / path.name),
            tr("PDF (*.pdf)"),
        )
        if not target:
            return
        try:
            shutil.copyfile(path, target)
        except OSError as exc:
            QMessageBox.warning(self, tr("PDF翻訳"), tr("保存できませんでした。") + f"\n\n{exc}")

    def open_compare(self) -> None:
        from movie_slide_notes.gui.compare_viewer import CompareViewer, PdfSide

        result = self.result
        if result is None:
            return
        first = self._first_page()
        before = PdfSide(result.source, first - 1)
        if result.japanese_pdf is not None:
            after = PdfSide(result.japanese_pdf)
        else:
            after = PdfSide(result.bilingual_pdf, 0, "right")
        try:
            viewer = CompareViewer(before, after, result.pages, self.preview_index, parent=self)
        except Exception as exc:
            QMessageBox.warning(self, tr("PDF翻訳"), tr("PDFを開けませんでした。") + f"\n\n{exc}")
            return
        self.compare_viewer = viewer
        viewer.showMaximized()

    def _first_page(self) -> int:
        """First translated page (1-based); a range is only offered for a single PDF."""
        if self.all_pages_check.isChecked() or len(self.sources) > 1:
            return 1
        return self.first_page.value()

    def _open_output_folder(self) -> None:
        if self.result is None:
            return
        path = self.result.japanese_pdf or self.result.bilingual_pdf
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    def _show_preview(self, index: int) -> None:
        result = self.result
        if result is None:
            return
        first = self._first_page()
        index = max(0, min(result.pages - 1, index))
        self.preview_index = index
        ratio = self.devicePixelRatioF()
        self.before_pane.set_pixmap(
            render_pdf_page(result.source, first - 1 + index, PREVIEW_HEIGHT, ratio)
        )
        if result.japanese_pdf is not None:
            self.after_pane.caption.setText(tr("翻訳後"))
            after = render_pdf_page(result.japanese_pdf, index, PREVIEW_HEIGHT, ratio)
        else:
            self.after_pane.caption.setText(tr("日英対訳"))
            after = render_pdf_page(result.bilingual_pdf, index, PREVIEW_HEIGHT, ratio)
        self.after_pane.set_pixmap(after)
        self.page_indicator.setText(f"{index + 1} / {result.pages}")
        self.prev_page_button.setEnabled(index > 0)
        self.next_page_button.setEnabled(index < result.pages - 1)

    # drag and drop ------------------------------------------------------------

    def dragEnterEvent(self, event) -> None:
        if any(url.toLocalFile().lower().endswith(".pdf") for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls()]
        pdfs = [path for path in paths if path.suffix.lower() == ".pdf"]
        if pdfs:
            self.set_sources(pdfs)
            event.acceptProposedAction()


def error_hint(message: str) -> str:
    if "APIキー" in message:
        return "「翻訳エンジンの設定」でAPIキーを確認してから再実行してください。"
    if "接続" in message:
        return "ネットワークや接続先を確認してから再実行してください。翻訳済みの部分は保存されているので、再実行で続きから翻訳します。"
    if "上限" in message or "混雑" in message:
        return "時間をおいて再実行してください。翻訳済みの部分は再利用されます。"
    if "英語の文章が見つかりません" in message:
        return "画像だけのPDFの場合は「文字の読み取り（OCR）」を「すべてのページ」にして再実行してください。"
    return "翻訳済みの部分は保存されているので、再実行すると続きから翻訳します。"


def _icon(name: str):
    from movie_slide_notes.gui.icons import icon

    return icon(name, 18)


def _feature_card(icon_name: str, heading: str, body: str) -> QFrame:
    card = QFrame()
    card.setObjectName("StepCard")
    card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    layout = QHBoxLayout(card)
    layout.setContentsMargins(14, 14, 14, 14)
    layout.setSpacing(12)
    image = QLabel()
    from movie_slide_notes.gui.icons import icon as svg_icon

    image.setPixmap(svg_icon(icon_name, 30, "#14a39b").pixmap(QSize(30, 30)))
    layout.addWidget(image, alignment=Qt.AlignTop)
    text = QVBoxLayout()
    text.setSpacing(2)
    title = QLabel(heading)
    title.setStyleSheet("font-weight: 600;")
    description = QLabel(body)
    description.setObjectName("SecondaryText")
    description.setWordWrap(True)
    description.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    text.addWidget(title)
    text.addWidget(description)
    layout.addLayout(text, 1)
    return card
