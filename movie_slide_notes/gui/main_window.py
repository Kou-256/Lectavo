from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (
    QItemSelectionModel,
    QProcess,
    QSettings,
    QSize,
    Qt,
    QThread,
    QTimer,
    QUrl,
)
from PySide6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QBoxLayout,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from movie_slide_notes.core.output_naming import (
    DEFAULT_DATE_STYLE,
    DEFAULT_NAMING_MODE,
    OutputNamingConfig,
    output_stem,
)
from movie_slide_notes.core.processor import (
    BatchResult,
    OperationOptions,
    ProcessingOptions,
    ProgressEvent,
    discover_videos,
    natural_video_sort_key,
)
from movie_slide_notes.gui.controls import ElidedLabel
from movie_slide_notes.gui.controls import NoWheelComboBox as QComboBox
from movie_slide_notes.gui.design_tokens import LAYOUT, SPACING, build_stylesheet
from movie_slide_notes.gui.i18n import (
    DEFAULT_UI_LANGUAGE,
    current_ui_language,
    set_ui_language,
    tr,
    translate_widget_tree,
)
from movie_slide_notes.gui.icons import icon as svg_icon
from movie_slide_notes.gui.icons import icon_pixmap as svg_pixmap
from movie_slide_notes.gui.icons import toggle_icon
from movie_slide_notes.gui.pdf_translate_page import (
    EngineSettings,
    EngineSettingsWidget,
    PdfTranslatePage,
)
from movie_slide_notes.gui.settings_widget import (
    ProcessingSettingsWidget,
    align_form_labels,
    form_layout,
    section_header,
    separator,
)
from movie_slide_notes.gui.splash import system_prefers_dark
from movie_slide_notes.gui.worker import ProcessingWorker
from movie_slide_notes.media import AUDIO_EXTENSIONS, MEDIA_EXTENSIONS, VIDEO_EXTENSIONS
from movie_slide_notes.paths import (
    resource_path,
    user_cache_dir,
    user_config_dir,
    user_log_dir,
)
from movie_slide_notes.version import APP_DESCRIPTION, APP_NAME, APP_PUBLISHER, __version__


class MainWindow(QMainWindow):
    def __init__(self, icon_path: Path | None = None):
        super().__init__()
        self.icon_path = icon_path
        self.settings = QSettings(str(user_config_dir() / "settings.ini"), QSettings.IniFormat)
        self.videos: list[Path] = []
        self.video_statuses: dict[Path, str] = {}
        self.worker: ProcessingWorker | None = None
        self.thread: QThread | None = None
        self.last_batch: BatchResult | None = None
        self.workflow_state = "empty"
        self.inspector_requested = False
        self.log_messages: list[str] = []
        self.history = self._load_history()
        self.run_started_at: datetime | None = None
        self._syncing = False
        self._close_after_cancel = False
        self._cancel_requested = False
        self._naming_moment: datetime | None = None
        self._preflight_dialog = None
        self.pending_job = self._load_pending_job()

        self.setWindowTitle(APP_NAME)
        self.resize(1100, 720)
        self.setMinimumSize(600, 440)
        self.setAcceptDrops(True)
        if icon_path and icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.setInterval(1000)
        self.elapsed_timer.timeout.connect(self._update_elapsed)

        self._build_ui()
        self._load_settings()
        self._connect_signals()
        self._apply_theme()
        self._apply_ui_language(initial=True)
        self._set_workflow_state("empty")
        self._update_responsive()
        from .update_manager import UpdateManager

        self.updater = UpdateManager(self)
        self._manual_update_check = False
        self.update_progress_dialog = None
        self.updater.available.connect(self._update_available)
        self.updater.checked.connect(self._update_checked)
        self.updater.failed.connect(self._update_failed)
        self.updater.progress.connect(self._update_progress)
        self.updater.downloaded.connect(self._update_downloaded)
        self.updater.cancelled.connect(self._update_cancelled)
        self.check_updates_button.clicked.connect(lambda: self.check_updates(True))
        self.auto_update_check.toggled.connect(
            lambda value: self.settings.setValue("check_updates_on_start", value)
        )

    def _build_ui(self) -> None:
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(central)

        self.sidebar = QFrame()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(LAYOUT.sidebar_wide)
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(14, 14, 14, 14)
        sidebar_layout.setSpacing(8)

        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(6, 4, 0, 0)
        brand_row.setSpacing(10)
        self.brand_icon = QLabel()
        app_icon = resource_path("assets", "app_icon.png")
        if app_icon.exists():
            self.brand_icon.setPixmap(QIcon(str(app_icon)).pixmap(QSize(26, 26)))
        self.brand_label = QLabel(APP_NAME)
        self.brand_label.setObjectName("AppName")
        brand_row.addWidget(self.brand_icon)
        brand_row.addWidget(self.brand_label, 1)
        sidebar_layout.addLayout(brand_row)
        sidebar_layout.addSpacing(22)

        self.nav_home = nav_button("ホーム")
        self.nav_process = nav_button("処理")
        self.nav_translate = nav_button("PDF翻訳")
        self.nav_history = nav_button("履歴")
        sidebar_layout.addWidget(self.nav_home)
        sidebar_layout.addWidget(self.nav_process)
        sidebar_layout.addWidget(self.nav_translate)
        sidebar_layout.addWidget(self.nav_history)
        sidebar_layout.addStretch(1)
        sidebar_layout.addWidget(separator())
        self.nav_settings = nav_button("設定")
        self.nav_help = nav_button("ヘルプ")
        for button, icon_name in [
            (self.nav_home, "home"),
            (self.nav_process, "processing"),
            (self.nav_translate, "translate"),
            (self.nav_history, "history"),
            (self.nav_settings, "settings"),
            (self.nav_help, "help"),
        ]:
            button.setIcon(toggle_icon(icon_name, 20))
            button.setIconSize(QSize(20, 20))
        sidebar_layout.addWidget(self.nav_settings)
        sidebar_layout.addWidget(self.nav_help)
        root.addWidget(self.sidebar)

        main_shell = QWidget()
        main_layout = QVBoxLayout(main_shell)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        root.addWidget(main_shell, 1)

        self.page_stack = QStackedWidget()
        main_layout.addWidget(self.page_stack, 1)

        self.settings_widget = ProcessingSettingsWidget()
        self.settings_widget.region_requested.connect(self.edit_region)
        self.engine_settings = EngineSettings(self.settings)
        self._build_home_page()
        self.translate_page = PdfTranslatePage(self.settings, self.engine_settings)
        self.page_stack.addWidget(self.translate_page)
        self._build_history_page()
        self._build_settings_page()
        self._build_help_page()
        self._build_inspector()
        root.addWidget(self.inspector)

    def _build_home_page(self) -> None:
        self.home_page = QWidget()
        layout = QVBoxLayout(self.home_page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.page_stack.addWidget(self.home_page)
        self.processing_page = QSplitter(Qt.Horizontal)
        self.processing_page.setChildrenCollapsible(True)
        self.page_stack.addWidget(self.processing_page)
        self.file_panel = QWidget()
        self.file_panel.setMinimumWidth(0)
        file_layout = QVBoxLayout(self.file_panel)
        file_layout.setContentsMargins(12, 20, 12, 16)
        file_layout.addWidget(section_title("ファイル"))
        self.file_tabs = QListWidget()
        self.file_tabs.setObjectName("FileTabs")
        self.file_tabs.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.file_tabs.setTextElideMode(Qt.ElideMiddle)
        self.file_tabs.setUniformItemSizes(True)
        self.file_tabs.setSpacing(2)
        self.file_tabs.setAccessibleName(tr("入力ファイル"))
        self.file_tabs.setToolTip(
            tr("Ctrlで複数選択、Shiftで範囲選択できます。設定は全ファイルに適用されます。")
        )
        self.file_tabs.currentItemChanged.connect(self._file_tab_changed)
        file_layout.addWidget(self.file_tabs, 1)
        self.add_media_button = QPushButton("ファイルを追加")
        self.add_media_button.clicked.connect(self.add_files)
        file_layout.addWidget(self.add_media_button)
        self.remove_media_button = QPushButton("選択を削除")
        self.remove_media_button.clicked.connect(self.remove_selected_files)
        file_layout.addWidget(self.remove_media_button)
        self.processing_page.addWidget(self.file_panel)
        self.home_stack = QStackedWidget()
        self.home_stack.setMinimumWidth(0)
        self.processing_page.addWidget(self.home_stack)
        self.processing_page.setSizes([190, 710])
        self.processing_page.setStretchFactor(1, 1)

        self.empty_page = scroll_page()
        empty_content = self.empty_page.widget()
        empty_layout = empty_content.layout()
        empty_layout.setSpacing(24)
        empty_layout.addWidget(page_title("新しい処理"))
        empty_layout.addWidget(subtitle("動画や音声から資料と文字起こしを作成します。"))
        task_row = QHBoxLayout()
        task_row.setSpacing(12)
        self.task_video_button = task_card("file-video", "動画・音声から資料を作る")
        self.task_translate_button = task_card("translate", "PDFを翻訳（17言語）")
        task_row.addWidget(self.task_video_button, 1)
        task_row.addWidget(self.task_translate_button, 1)
        empty_layout.addLayout(task_row)

        self.resume_banner = QFrame()
        self.resume_banner.setObjectName("ResumeBanner")
        banner_layout = QHBoxLayout(self.resume_banner)
        banner_layout.setContentsMargins(16, 14, 16, 14)
        banner_layout.setSpacing(14)
        banner_icon = QLabel()
        banner_icon.setPixmap(icon_pixmap("resume", 28))
        banner_layout.addWidget(banner_icon, alignment=Qt.AlignTop)
        banner_text = QVBoxLayout()
        banner_title = QLabel("前回の処理が完了していません")
        banner_title.setObjectName("SectionTitle")
        self.resume_detail = subtitle("")
        banner_text.addWidget(banner_title)
        banner_text.addWidget(self.resume_detail)
        banner_layout.addLayout(banner_text, 1)
        self.discard_resume_button = QPushButton("破棄")
        self.discard_resume_button.setObjectName("LinkButton")
        self.resume_button = QPushButton("続きから再開")
        self.resume_button.setObjectName("PrimaryButton")
        banner_layout.addWidget(self.discard_resume_button)
        banner_layout.addWidget(self.resume_button)
        self.resume_banner.setVisible(False)
        empty_layout.addWidget(self.resume_banner)

        drop_zone = QFrame()
        drop_zone.setObjectName("DropZone")
        drop_zone.setMinimumHeight(310)
        drop_layout = QVBoxLayout(drop_zone)
        drop_layout.setContentsMargins(32, 30, 32, 30)
        drop_layout.setSpacing(14)
        drop_layout.setAlignment(Qt.AlignCenter)
        drop_hero = QLabel()
        drop_hero.setPixmap(icon_pixmap("drop-hero", 72))
        drop_hero.setAlignment(Qt.AlignCenter)
        drop_layout.addWidget(drop_hero)
        drop_title = QLabel("動画・音声をここにドロップ")
        drop_title.setObjectName("SectionTitle")
        drop_title.setAlignment(Qt.AlignCenter)
        drop_hint = subtitle("MP4・MOV・MKVなど / MP3・M4A・WAV")
        drop_hint.setAlignment(Qt.AlignCenter)
        drop_hint.setWordWrap(False)
        self.select_video_button = QPushButton("動画・音声を選択")
        self.select_video_button.setObjectName("PrimaryButton")
        self.select_video_button.setAccessibleName("動画・音声を選択")
        self.select_folder_link = QPushButton("フォルダを選択")
        self.select_folder_link.setObjectName("LinkButton")
        drop_layout.addWidget(drop_title)
        drop_layout.addWidget(self.select_video_button, alignment=Qt.AlignCenter)
        drop_layout.addWidget(drop_hint)
        drop_layout.addWidget(self.select_folder_link, alignment=Qt.AlignCenter)
        empty_layout.addWidget(drop_zone, 1)
        empty_layout.addWidget(section_title("使い方"))
        self.steps_layout = QGridLayout()
        self.steps_layout.setHorizontalSpacing(12)
        self.steps_layout.setVerticalSpacing(12)
        self.step_cards = [
            step_card("step-add", "1", "ファイルを追加", "動画・音声をドロップするか選択します。"),
            step_card(
                "step-choose",
                "2",
                "出力を選ぶ",
                "スライドPDFや文字起こしなど必要なものを選びます。",
            ),
            step_card(
                "step-output",
                "3",
                "チェックして開始",
                "開始前に空き容量・モデル・音声を自動で確認します。",
            ),
            step_card(
                "step-review",
                "4",
                "確認・修正",
                "抽出スライドを並べ替え・削除・追加してPDFを確定します。",
            ),
        ]
        empty_layout.addLayout(self.steps_layout)
        empty_layout.addWidget(section_title("最近の処理"))
        self.recent_layout = QVBoxLayout()
        self.recent_layout.setSpacing(0)
        empty_layout.addLayout(self.recent_layout)
        empty_layout.addStretch(1)
        layout.addWidget(self.empty_page)

        self.ready_page = scroll_page()
        ready_content = self.ready_page.widget()
        ready_layout = ready_content.layout()
        ready_layout.addWidget(page_title("新しい処理"))
        ready_layout.addWidget(subtitle("出力内容を選んでから処理を開始します。"))
        ready_layout.addSpacing(8)

        ready_layout.addWidget(section_title("入力ファイル"))
        ready_layout.addWidget(separator())
        self.video_list_layout = QVBoxLayout()
        self.video_list_layout.setSpacing(0)
        ready_layout.addLayout(self.video_list_layout)

        self.clear_button = QPushButton("すべて削除")
        self.clear_button.setObjectName("LinkButton")
        clear_row = QHBoxLayout()
        clear_row.addStretch(1)
        clear_row.addWidget(self.clear_button)
        ready_layout.addLayout(clear_row)

        ready_layout.addSpacing(12)
        ready_layout.addWidget(section_title("出力方法"))
        ready_layout.addWidget(separator())
        self.output_mode_combo = QComboBox()
        self.output_mode_combo.addItem("まとめて出力（1フォルダ）", "combined")
        self.output_mode_combo.addItem("動画ごとに出力", "per_video")
        self.output_mode_combo.setAccessibleName("出力方法")
        self.output_mode_combo.setToolTip(
            "まとめて出力では、全動画の重複スライドを除去してPDF、文字起こし、講義ノートを1つにまとめます。"
        )
        self.output_mode_hint = QLabel()
        self.output_mode_hint.setObjectName("Hint")
        self.output_mode_hint.setWordWrap(True)
        ready_layout.addWidget(self.output_mode_combo)
        ready_layout.addWidget(self.output_mode_hint)

        ready_layout.addSpacing(12)
        ready_layout.addWidget(section_title("出力内容"))
        ready_layout.addWidget(separator())
        outputs = QGridLayout()
        self.outputs_grid = outputs
        outputs.setHorizontalSpacing(28)
        outputs.setVerticalSpacing(10)
        self.output_pdf = QCheckBox("スライドPDF")
        self.output_lecture_notes = QCheckBox("文字起こし＋講義ノート")
        self.output_images = QCheckBox("画像ファイル")
        for checkbox in [
            self.output_pdf,
            self.output_lecture_notes,
            self.output_images,
        ]:
            checkbox.setChecked(True)
        outputs.addWidget(self.output_pdf, 0, 0)
        outputs.addWidget(self.output_lecture_notes, 0, 1)
        outputs.addWidget(self.output_images, 1, 0)
        ready_layout.addLayout(outputs)
        self.dependency_label = QLabel("")
        self.dependency_label.setObjectName("Hint")
        ready_layout.addWidget(self.dependency_label)

        ready_layout.addSpacing(12)
        ready_layout.addWidget(section_title("保存先"))
        ready_layout.addWidget(separator())
        self.next_to_input_check = QCheckBox("動画と同じフォルダに保存する")
        self.next_to_input_check.setToolTip(
            "オンにすると、入力した動画・音声があるフォルダに結果を保存します。"
        )
        ready_layout.addWidget(self.next_to_input_check)
        self.output_row_widget = QWidget()
        output_row = QHBoxLayout(self.output_row_widget)
        output_row.setContentsMargins(0, 0, 0, 0)
        self.output_path = QLineEdit()
        self.output_path.setReadOnly(True)
        self.output_path.setAccessibleName("出力先")
        self.output_browse = QPushButton("変更")
        output_row.addWidget(self.output_path, 1)
        output_row.addWidget(self.output_browse)
        ready_layout.addWidget(self.output_row_widget)
        self.output_location_hint = QLabel()
        self.output_location_hint.setObjectName("Hint")
        self.output_location_hint.setWordWrap(True)
        self.output_location_hint.setTextFormat(Qt.PlainText)
        ready_layout.addWidget(self.output_location_hint)

        ready_layout.addSpacing(12)
        ready_layout.addWidget(section_title("ファイル名"))
        ready_layout.addWidget(separator())
        naming_grid = QGridLayout()
        naming_grid.setHorizontalSpacing(12)
        naming_grid.setVerticalSpacing(10)
        self.naming_mode = QComboBox()
        self.naming_mode.addItem("従来の名前", "original")
        self.naming_mode.addItem("動画名の共通部分", "common")
        self.naming_mode.addItem("自由に入力", "custom")
        self.naming_mode.setAccessibleName("名前の付け方")
        self.custom_output_name = QLineEdit()
        self.custom_output_name.setPlaceholderText("例: 第3回 講義資料")
        self.custom_output_name.setAccessibleName("出力名")
        self.naming_date = QComboBox()
        self.naming_date.addItem("付けない", "none")
        self.naming_date.addItem("日付を付ける", "date")
        self.naming_date.addItem("日時を付ける", "datetime")
        self.naming_date.setAccessibleName("日付・日時")
        naming_grid.addWidget(grid_label("名前の付け方"), 0, 0)
        naming_grid.addWidget(self.naming_mode, 0, 1)
        naming_grid.addWidget(self.custom_output_name, 1, 1)
        naming_grid.addWidget(grid_label("日付・日時"), 2, 0)
        naming_grid.addWidget(self.naming_date, 2, 1)
        naming_grid.setColumnStretch(1, 1)
        ready_layout.addLayout(naming_grid)
        self.naming_preview = QLabel()
        self.naming_preview.setObjectName("Hint")
        self.naming_preview.setWordWrap(True)
        self.naming_preview.setMinimumWidth(0)
        self.naming_preview.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.naming_preview.setTextFormat(Qt.PlainText)
        ready_layout.addWidget(self.naming_preview)

        ready_layout.addSpacing(12)
        ready_layout.addWidget(section_title("処理品質"))
        ready_layout.addWidget(separator())
        quality_grid = QGridLayout()
        quality_grid.setHorizontalSpacing(12)
        quality_grid.setVerticalSpacing(10)
        self.quality_combo = QComboBox()
        self.quality_combo.addItem("高速", "conservative")
        self.quality_combo.addItem("標準", "standard")
        self.quality_combo.addItem("高精度", "sensitive")
        self.device_combo = QComboBox()
        self.device_combo.addItem("CPU", "cpu")
        self.device_combo.addItem("GPU（CUDA）", "cuda")
        self.device_combo.setToolTip(
            "文字起こしに使用する処理デバイスです。GPUが利用できない場合は理由を表示してCPUへ自動で切り替わります。"
        )
        self.details_button = QPushButton("詳細設定")
        self.details_button.setObjectName("LinkButton")
        quality_grid.addWidget(grid_label("品質"), 0, 0)
        quality_grid.addWidget(self.quality_combo, 0, 1)
        quality_grid.addWidget(grid_label("文字起こし処理"), 1, 0)
        quality_grid.addWidget(self.device_combo, 1, 1)
        self.region_button = QPushButton("画面で検出範囲を指定")
        self.region_button.setIcon(svg_icon("crop", 18))
        self.region_button.setIconSize(QSize(18, 18))
        self.region_button.clicked.connect(self.edit_region)
        region_row = QHBoxLayout()
        region_row.setSpacing(12)
        region_row.addWidget(self.region_button)
        region_row.addStretch(1)
        region_row.addWidget(self.details_button)
        quality_grid.addWidget(grid_label("検出範囲"), 2, 0)
        quality_grid.addLayout(region_row, 2, 1)
        self.region_hint = QLabel()
        self.region_hint.setWordWrap(True)
        self.region_hint.setObjectName("Hint")
        quality_grid.addWidget(self.region_hint, 3, 1)
        quality_grid.setColumnStretch(1, 1)
        ready_layout.addLayout(quality_grid)

        ready_layout.addSpacing(20)
        action_row = QHBoxLayout()
        self.preflight_button = QPushButton("処理前チェック")
        self.preflight_button.setObjectName("LinkButton")
        self.preflight_button.setIcon(QIcon(icon_path("preflight")))
        self.preflight_button.setToolTip(
            "モデル、GPU、出力先の空き容量、音声トラックを確認します。開始時にも自動で確認します。"
        )
        action_row.addWidget(self.preflight_button)
        action_row.addStretch(1)
        self.start_button = QPushButton("処理を開始")
        self.start_button.setObjectName("PrimaryButton")
        action_row.addWidget(self.start_button)
        ready_layout.addLayout(action_row)
        ready_layout.addStretch(1)
        self.home_stack.addWidget(self.ready_page)

        self.running_page = scroll_page()
        running_layout = self.running_page.widget().layout()
        running_layout.setSpacing(16)
        self.running_title = page_title("処理しています")
        self.running_stage = QLabel("準備中")
        self.running_stage.setObjectName("SectionTitle")
        self.running_detail = subtitle("")
        self.overall_progress = QProgressBar()
        self.overall_progress.setRange(0, 100)
        self.elapsed_label = subtitle("経過時間 00:00")
        self.running_settings_title = section_title("現在の処理設定")
        self.running_settings = subtitle("")
        self.running_settings.setWordWrap(True)
        self.cancel_button = QPushButton("キャンセル")
        self.log_toggle_button = QPushButton("詳細ログを表示")
        self.log_toggle_button.setObjectName("LinkButton")
        self.running_log_text = QPlainTextEdit()
        self.running_log_text.setReadOnly(True)
        self.running_log_text.setVisible(False)
        running_layout.addWidget(self.running_title)
        running_layout.addSpacing(8)
        running_layout.addWidget(self.running_stage)
        running_layout.addWidget(self.overall_progress)
        running_layout.addWidget(self.running_detail)
        running_layout.addWidget(self.elapsed_label)
        running_layout.addSpacing(8)
        running_layout.addWidget(self.running_settings_title)
        running_layout.addWidget(separator())
        running_layout.addWidget(self.running_settings)
        running_layout.addStretch(1)
        running_layout.addWidget(self.log_toggle_button, alignment=Qt.AlignLeft)
        running_layout.addWidget(self.running_log_text, 1)
        running_actions = QHBoxLayout()
        running_actions.addStretch(1)
        running_actions.addWidget(self.cancel_button)
        running_layout.addLayout(running_actions)
        self.home_stack.addWidget(self.running_page)

        self.complete_page = scroll_page()
        complete_layout = self.complete_page.widget().layout()
        complete_layout.setSpacing(16)
        complete_layout.addWidget(page_title("処理が完了しました"))
        self.complete_summary_layout = QVBoxLayout()
        self.complete_summary_layout.setSpacing(8)
        complete_layout.addLayout(self.complete_summary_layout)
        complete_layout.addSpacing(8)
        self.review_panel = QFrame()
        self.review_panel.setObjectName("SoftPanel")
        review_layout = QHBoxLayout(self.review_panel)
        review_layout.setContentsMargins(16, 14, 16, 14)
        review_layout.setSpacing(14)
        review_icon = QLabel()
        review_icon.setPixmap(icon_pixmap("step-review", 40))
        review_layout.addWidget(review_icon, alignment=Qt.AlignTop)
        review_text = QVBoxLayout()
        review_text.addWidget(section_title("スライドを確認しましょう"))
        review_text.addWidget(
            subtitle(
                "不要なページの削除、順序の変更、取り逃した場面の追加をしてからPDFを確定できます。"
            )
        )
        review_layout.addLayout(review_text, 1)
        self.review_slides_button = QPushButton("スライドを確認・修正")
        self.review_slides_button.setObjectName("PrimaryButton")
        self.review_slides_button.setIcon(QIcon(icon_path("slides-review")))
        review_layout.addWidget(self.review_slides_button, alignment=Qt.AlignVCenter)
        complete_layout.addWidget(self.review_panel)
        complete_layout.addSpacing(8)
        complete_actions = QHBoxLayout()
        self.complete_actions = complete_actions
        self.open_output_button = QPushButton("出力フォルダを開く")
        self.open_pdf_button = QPushButton("PDFを開く")
        self.open_transcript_button = QPushButton("文字起こしを開く")
        complete_actions.addWidget(self.open_output_button)
        complete_actions.addWidget(self.open_pdf_button)
        complete_actions.addWidget(self.open_transcript_button)
        complete_actions.addStretch(1)
        complete_layout.addLayout(complete_actions)
        complete_layout.addStretch(1)
        new_row = QHBoxLayout()
        new_row.addStretch(1)
        self.new_process_button = QPushButton("新しい処理")
        self.new_process_button.setObjectName("PrimaryButton")
        new_row.addWidget(self.new_process_button)
        complete_layout.addLayout(new_row)
        self.home_stack.addWidget(self.complete_page)

        self.error_page = scroll_page()
        error_layout = self.error_page.widget().layout()
        error_layout.setSpacing(16)
        self.error_title = page_title("処理に問題がありました")
        self.error_message = QLabel("")
        self.error_message.setObjectName("ErrorText")
        self.error_message.setWordWrap(True)
        self.error_message.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.error_hint = subtitle(
            "「続きから再実行」では、完了した部分を再利用して失敗した処理だけをやり直します。"
        )
        self.error_log_toggle = QPushButton("詳細を表示")
        self.error_log_toggle.setObjectName("LinkButton")
        self.error_log_text = QPlainTextEdit()
        self.error_log_text.setReadOnly(True)
        self.error_log_text.setVisible(False)
        error_layout.addWidget(self.error_title)
        error_layout.addWidget(self.error_message)
        error_layout.addWidget(self.error_hint)
        error_layout.addStretch(1)
        error_layout.addWidget(self.error_log_toggle, alignment=Qt.AlignLeft)
        error_layout.addWidget(self.error_log_text, 1)
        error_actions = QHBoxLayout()
        self.error_actions = error_actions
        self.retry_button = QPushButton("続きから再実行")
        self.retry_button.setObjectName("PrimaryButton")
        self.retry_button.setIcon(QIcon(icon_path("resume")))
        self.retry_button.setToolTip(
            "完了したスライド解析と文字起こしは再利用し、失敗した処理だけをやり直します。"
        )
        self.change_settings_button = QPushButton("設定を変更")
        self.copy_diagnostics_button = QPushButton("診断情報をコピー")
        self.open_logs_button = QPushButton("ログフォルダを開く")
        error_actions.addWidget(self.open_logs_button)
        error_actions.addWidget(self.copy_diagnostics_button)
        error_actions.addStretch(1)
        error_actions.addWidget(self.change_settings_button)
        error_actions.addWidget(self.retry_button)
        error_layout.addLayout(error_actions)
        self.home_stack.addWidget(self.error_page)

    def _build_history_page(self) -> None:
        self.history_page = scroll_page()
        content = self.history_page.widget()
        layout = content.layout()
        history_header = QHBoxLayout()
        history_header.addWidget(page_title("履歴"))
        history_header.addStretch(1)
        self.clear_history_button = QPushButton("履歴をすべて削除")
        self.clear_history_button.setObjectName("LinkButton")
        self.clear_history_button.setAccessibleName("履歴をすべて削除")
        history_header.addWidget(self.clear_history_button)
        layout.addLayout(history_header)
        layout.addWidget(subtitle("最近処理した動画と出力先です。"))
        layout.addSpacing(16)
        self.history_layout = QVBoxLayout()
        self.history_layout.setSpacing(0)
        layout.addLayout(self.history_layout)
        layout.addStretch(1)
        self.page_stack.addWidget(self.history_page)

    def _build_settings_page(self) -> None:
        self.settings_page = scroll_page()
        content = self.settings_page.widget()
        layout = content.layout()
        layout.addWidget(page_title("設定"))
        layout.addWidget(
            subtitle(
                "通常は標準設定のままで利用できます。専門的な設定は必要なときだけ変更してください。"
            )
        )
        layout.addSpacing(8)
        section_header(layout, "一般")
        general = form_layout()
        self.settings_next_to_input_check = QCheckBox("動画と同じフォルダに保存する")
        general.addRow("保存先", self.settings_next_to_input_check)
        default_output_row = QHBoxLayout()
        default_output_row.setSpacing(8)
        self.settings_output_path = QLineEdit()
        self.settings_output_path.setReadOnly(True)
        self.settings_output_browse = QPushButton("変更")
        default_output_row.addWidget(self.settings_output_path, 1)
        default_output_row.addWidget(self.settings_output_browse)
        general.addRow("ほかのフォルダ", default_output_row)
        layout.addLayout(general)

        layout.addWidget(self.settings_widget)

        section_header(layout, "PDF翻訳")
        self.engine_settings_widget = EngineSettingsWidget(self.engine_settings)
        layout.addWidget(self.engine_settings_widget)

        section_header(layout, "途中保存と再開")
        resume = form_layout()
        self.resume_enabled_check = QCheckBox("完了した処理を保存し、中断後に続きから再開する")
        self.resume_enabled_check.setChecked(self.settings.value("resume_enabled", True, type=bool))
        resume.addRow("途中保存", self.resume_enabled_check)
        checkpoint_row = QHBoxLayout()
        checkpoint_row.setSpacing(12)
        self.checkpoint_size_label = QLabel()
        self.checkpoint_size_label.setObjectName("Hint")
        self.checkpoint_size_label.setWordWrap(True)
        self.clear_checkpoints_button = QPushButton("保存データを削除")
        checkpoint_row.addWidget(self.checkpoint_size_label, 1)
        checkpoint_row.addWidget(self.clear_checkpoints_button)
        resume.addRow("保存データ", checkpoint_row)
        layout.addLayout(resume)

        section_header(layout, "アプリの更新")
        updates = form_layout()
        self.auto_update_check = QCheckBox("起動時に更新を確認する")
        self.auto_update_check.setChecked(
            self.settings.value("check_updates_on_start", True, type=bool)
        )
        updates.addRow("自動確認", self.auto_update_check)
        update_row = QHBoxLayout()
        update_row.setSpacing(12)
        self.check_updates_button = QPushButton("更新を確認")
        self.update_status = QLabel("ダウンロードとインストールの前に確認します。")
        self.update_status.setObjectName("Hint")
        self.update_status.setWordWrap(True)
        update_row.addWidget(self.check_updates_button)
        update_row.addWidget(self.update_status, 1)
        updates.addRow("手動確認", update_row)
        layout.addLayout(updates)
        layout.addStretch(1)
        align_form_labels(content)
        self.page_stack.addWidget(self.settings_page)

    def _build_help_page(self) -> None:
        self.help_page = scroll_page()
        content = self.help_page.widget()
        layout = content.layout()
        layout.addWidget(page_title("ヘルプ"))
        layout.addWidget(subtitle("Lectavoの基本操作と、困ったときの確認先です。"))
        layout.addSpacing(16)

        for title, lines in [
            (
                "はじめに",
                [
                    "1. 動画を追加",
                    "2. 出力内容を選択",
                    "3. 処理を開始（開始前に自動でチェックします）",
                    "4. 完了後に「スライドを確認・修正」でPDFを仕上げる",
                ],
            ),
            (
                "PDF翻訳",
                [
                    "1. 設定ページの「PDF翻訳」で翻訳エンジンとAPIキーを登録（ローカルLLMはキー不要）",
                    "2. サイドバーの「PDF翻訳」でPDFをドロップし、意訳・直訳・やさしい日本語から選ぶ",
                    "3. 日本語PDF（レイアウト保持）と日英対訳PDFが、元のPDFと同じフォルダに保存されます",
                    "スキャンしたPDFは自動でOCRにかけます。図表・数式・画像はそのまま残ります。",
                ],
            ),
            (
                "途中で止まったとき",
                [
                    "アプリを閉じたりエラーが起きたりしても、完了したスライド解析と文字起こしは保存されています。",
                    "ホームの「続きから再開」、またはエラー画面の「続きから再実行」で、残りの処理だけを行います。",
                ],
            ),
            (
                "よくある問題",
                [
                    "スライドが多く抽出される: 処理品質を高速にするか、詳細設定で最小差分領域を上げます。",
                    "スライドが抜ける: 処理品質を高精度にするか、フレーム確認間隔を短くします。",
                    "同じスライドが残る: 重複判定距離や重複SSIMしきい値を調整します。",
                    "文字起こしが開始されない: モデル準備のログを確認します。",
                    "処理が途中で停止する: 出力先の空き容量と動画ファイルの状態を確認します。",
                ],
            ),
        ]:
            layout.addWidget(section_title(title))
            layout.addWidget(separator())
            for line in lines:
                label = QLabel(line)
                label.setWordWrap(True)
                layout.addWidget(label)
            layout.addSpacing(18)

        layout.addWidget(section_title("トラブルシューティング"))
        layout.addWidget(separator())
        help_actions = QHBoxLayout()
        self.help_open_logs = QPushButton("ログフォルダを開く")
        self.help_copy_diagnostics = QPushButton("診断情報をコピー")
        help_actions.addWidget(self.help_open_logs)
        help_actions.addWidget(self.help_copy_diagnostics)
        help_actions.addStretch(1)
        layout.addLayout(help_actions)
        layout.addSpacing(18)

        layout.addWidget(section_title("Lectavoについて"))
        layout.addWidget(separator())
        self.about_text = QLabel(
            f"{APP_NAME}\nVersion {__version__}\n発行元: {APP_PUBLISHER}\n\n{APP_DESCRIPTION}"
        )
        self.about_text.setWordWrap(True)
        layout.addWidget(self.about_text)
        about_actions = QHBoxLayout()
        self.open_notices_button = QPushButton("第三者ライセンス")
        about_actions.addWidget(self.open_notices_button)
        about_actions.addStretch(1)
        layout.addLayout(about_actions)
        layout.addStretch(1)
        self.page_stack.addWidget(self.help_page)

    def _build_inspector(self) -> None:
        self.inspector = QFrame()
        self.inspector.setObjectName("Inspector")
        self.inspector.setFixedWidth(LAYOUT.inspector_width)
        self.inspector.setVisible(False)
        layout = QVBoxLayout(self.inspector)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)
        header = QHBoxLayout()
        header.addWidget(section_title("詳細設定"))
        header.addStretch(1)
        self.close_inspector_button = QPushButton("閉じる")
        self.close_inspector_button.setObjectName("LinkButton")
        header.addWidget(self.close_inspector_button)
        layout.addLayout(header)
        layout.addWidget(separator())

        layout.addWidget(QLabel("処理品質"))
        self.inspector_preset = QComboBox()
        self.inspector_preset.addItem("高速", "conservative")
        self.inspector_preset.addItem("標準", "standard")
        self.inspector_preset.addItem("高精度", "sensitive")
        layout.addWidget(self.inspector_preset)

        layout.addWidget(QLabel("文字起こしの言語"))
        self.inspector_language = QComboBox()
        self.inspector_language.addItem("自然に検出", "auto")
        self.inspector_language.addItem("日本語", "ja")
        self.inspector_language.addItem("英語", "en")
        layout.addWidget(self.inspector_language)

        layout.addWidget(QLabel("文字起こし品質"))
        self.inspector_model = QComboBox()
        self.inspector_model.addItem("高速", "tiny")
        self.inspector_model.addItem("軽量", "base")
        self.inspector_model.addItem("標準", "small")
        self.inspector_model.addItem("高精度", "medium")
        self.inspector_model.addItem("最高精度", "large-v3")
        layout.addWidget(self.inspector_model)
        hint = subtitle(
            "さらに細かい検出しきい値、CUDA、計算精度は設定ページの開発者向けから変更できます。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch(1)

    def _connect_signals(self) -> None:
        for button, page in [
            (self.nav_home, "home"),
            (self.nav_process, "processing"),
            (self.nav_translate, "translate"),
            (self.nav_history, "history"),
            (self.nav_settings, "settings"),
            (self.nav_help, "help"),
        ]:
            button.clicked.connect(lambda _checked=False, p=page: self._select_page(p))
        self.task_video_button.clicked.connect(self.add_files)
        self.task_translate_button.clicked.connect(lambda: self._select_page("translate"))
        self.translate_page.open_settings_requested.connect(self._open_translation_settings)
        self.translate_page.history_entry.connect(self._add_history_entry)
        self.engine_settings_widget.changed.connect(self.translate_page.refresh_engine)

        self.select_video_button.clicked.connect(self.add_files)
        self.select_folder_link.clicked.connect(self.add_folder)
        self.clear_button.clicked.connect(self.clear_videos)
        self.clear_history_button.clicked.connect(self.clear_history)
        self.output_browse.clicked.connect(self.browse_output)
        self.settings_output_browse.clicked.connect(self.browse_output)
        self.next_to_input_check.toggled.connect(self._next_to_input_changed)
        self.settings_next_to_input_check.toggled.connect(self._next_to_input_changed)
        self.start_button.clicked.connect(self.start_processing)
        self.cancel_button.clicked.connect(self.cancel_processing)
        self.retry_button.clicked.connect(self._retry_processing)
        self.change_settings_button.clicked.connect(self._change_settings_after_error)
        self.preflight_button.clicked.connect(self.run_preflight_only)
        self.review_slides_button.clicked.connect(self.open_slide_review)
        self.resume_button.clicked.connect(self.resume_pending_job)
        self.discard_resume_button.clicked.connect(self.discard_pending_job)
        self.resume_enabled_check.toggled.connect(
            lambda value: self.settings.setValue("resume_enabled", value)
        )
        self.clear_checkpoints_button.clicked.connect(self.clear_checkpoints)
        self.new_process_button.clicked.connect(self.new_process)
        self.open_output_button.clicked.connect(self.open_output_folder)
        self.open_pdf_button.clicked.connect(self.open_first_pdf)
        self.open_transcript_button.clicked.connect(self.open_first_transcript)
        self.open_logs_button.clicked.connect(self.open_log_folder)
        self.help_open_logs.clicked.connect(self.open_log_folder)
        self.open_notices_button.clicked.connect(self.open_third_party_notices)
        self.copy_diagnostics_button.clicked.connect(self.copy_diagnostics)
        self.help_copy_diagnostics.clicked.connect(self.copy_diagnostics)
        self.details_button.clicked.connect(self.open_details)
        self.close_inspector_button.clicked.connect(self.close_inspector)
        self.log_toggle_button.clicked.connect(
            lambda: self._toggle_log(self.running_log_text, self.log_toggle_button)
        )
        self.error_log_toggle.clicked.connect(
            lambda: self._toggle_log(self.error_log_text, self.error_log_toggle)
        )

        for checkbox in [
            self.output_pdf,
            self.output_lecture_notes,
            self.output_images,
        ]:
            checkbox.toggled.connect(self._operation_changed)

        self.output_mode_combo.currentIndexChanged.connect(self._output_mode_changed)
        self.naming_mode.currentIndexChanged.connect(self._naming_changed)
        self.naming_date.currentIndexChanged.connect(self._naming_changed)
        self.custom_output_name.textChanged.connect(self._naming_changed)
        self.quality_combo.currentIndexChanged.connect(self._compact_preset_changed)
        self.device_combo.currentIndexChanged.connect(self._compact_device_changed)
        self.inspector_preset.currentIndexChanged.connect(self._compact_preset_changed)
        self.inspector_language.currentIndexChanged.connect(self._compact_language_changed)
        self.inspector_model.currentIndexChanged.connect(self._compact_model_changed)
        self.settings_widget.theme_combo.currentIndexChanged.connect(self._apply_theme)
        self.settings_widget.settings_changed.connect(self._settings_widget_changed)

    def _load_settings(self) -> None:
        self.settings_widget.load_settings(self.settings)
        output = str(self.settings.value("output_path", ""))
        self.output_path.setText(output)
        self.settings_output_path.setText(output)
        self._set_next_to_input(self.settings.value("output_next_to_input", True, type=bool))
        self.output_pdf.setChecked(self.settings.value("generate_pdf", True, type=bool))
        lecture_notes = self.settings.value(
            "transcribe_audio", True, type=bool
        ) or self.settings.value("generate_markdown", True, type=bool)
        self.output_lecture_notes.setChecked(lecture_notes)
        self.output_images.setChecked(self.settings.value("extract_slides", True, type=bool))
        output_mode = str(self.settings.value("output_mode", "combined"))
        if output_mode not in {"combined", "per_video"}:
            output_mode = "combined"
        set_combo_data(self.output_mode_combo, output_mode)
        naming_mode = str(self.settings.value("naming_mode", DEFAULT_NAMING_MODE))
        naming_date = str(self.settings.value("naming_date", DEFAULT_DATE_STYLE))
        if not self.settings.value("naming_defaults_v2", False, type=bool):
            if naming_mode == "original" and naming_date == "none":
                naming_mode, naming_date = DEFAULT_NAMING_MODE, DEFAULT_DATE_STYLE
            self.settings.setValue("naming_defaults_v2", True)
        set_combo_data(self.naming_mode, naming_mode)
        set_combo_data(self.naming_date, naming_date)
        self.custom_output_name.setText(str(self.settings.value("custom_output_name", "")))
        self._update_naming_preview()
        self._output_mode_changed()
        self._sync_compact_settings_from_settings_widget()
        self._refresh_recent()
        self._refresh_history()
        geometry = self.settings.value("window_geometry")
        if geometry:
            self.restoreGeometry(geometry)
        self._operation_changed()
        self._refresh_region_hint()
        self._refresh_resume_banner()

    def _save_settings(self) -> None:
        self.settings.setValue("output_path", self.output_path.text())
        self.settings.setValue("output_next_to_input", self.next_to_input_check.isChecked())
        self.settings.setValue("naming_mode", self.naming_mode.currentData())
        self.settings.setValue("naming_date", self.naming_date.currentData())
        self.settings.setValue("custom_output_name", self.custom_output_name.text())
        self.settings.setValue("extract_slides", self._operation_options().extract_slides)
        self.settings.setValue("generate_pdf", self.output_pdf.isChecked())
        self.settings.setValue("transcribe_audio", self.output_lecture_notes.isChecked())
        self.settings.setValue("generate_markdown", self.output_lecture_notes.isChecked())
        self.settings.setValue("output_mode", self.output_mode_combo.currentData())
        self.settings.setValue(
            "generate_combined_pdf", self.output_mode_combo.currentData() == "combined"
        )
        self.settings_widget.save_settings(self.settings)
        self.settings.setValue("history", json.dumps(self.history, ensure_ascii=False))

    def _settings_widget_changed(self) -> None:
        self._refresh_region_hint()
        requested_language = str(self.settings_widget.ui_language_combo.currentData())
        language_changed = requested_language != current_ui_language()
        if language_changed:
            set_ui_language(requested_language)
        self._sync_compact_settings_from_settings_widget()
        if language_changed:
            self._apply_ui_language()
        self._save_settings()

    def _operation_changed(self) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            notes: list[str] = []
            if self.output_lecture_notes.isChecked() and not self.output_images.isChecked():
                self.output_images.setChecked(True)
                notes.append(
                    "文字起こし＋講義ノートにはスライド画像が必要なため、自動で有効にしました。"
                )
            if self.output_pdf.isChecked() and not self.output_images.isChecked():
                self.output_images.setChecked(True)
                notes.append("スライドPDFにはスライド画像の抽出が必要です。")
            if not any(
                checkbox.isChecked()
                for checkbox in [
                    self.output_pdf,
                    self.output_lecture_notes,
                    self.output_images,
                ]
            ):
                self.output_pdf.setChecked(True)
                notes.append("少なくとも1つの出力内容が必要です。")
            self.dependency_label.setText(" ".join(tr(note) for note in dict.fromkeys(notes)))
        finally:
            self._syncing = False
        self._save_settings()

    def _output_mode_changed(self) -> None:
        self._update_naming_preview()
        combined = self.output_mode_combo.currentData() == "combined"
        if combined:
            self.output_mode_hint.setText(
                tr("入力ファイルを1フォルダにまとめ、動画をまたぐ重複スライドも除去します。")
            )
        else:
            self.output_mode_hint.setText(tr("入力ファイルごとのフォルダに成果物を保存します。"))
        if not self._syncing:
            self._save_settings()

    def _operation_options(self) -> OperationOptions:
        if self.videos and all(path.suffix.lower() in AUDIO_EXTENSIONS for path in self.videos):
            return OperationOptions(False, False, True, True, False)
        return OperationOptions(
            extract_slides=(
                self.output_images.isChecked()
                or self.output_pdf.isChecked()
                or self.output_lecture_notes.isChecked()
            ),
            generate_pdf=self.output_pdf.isChecked(),
            transcribe_audio=self.output_lecture_notes.isChecked(),
            generate_markdown=self.output_lecture_notes.isChecked(),
            generate_combined_pdf=(
                self.output_mode_combo.currentData() == "combined" and self.output_pdf.isChecked()
            ),
        ).normalized()

    def add_files(self) -> None:
        filters = (
            tr("動画・音声ファイル")
            + " ("
            + " ".join(f"*{ext}" for ext in sorted(MEDIA_EXTENSIONS))
            + ");;"
            + tr("すべて")
            + " (*.*)"
        )
        paths, _ = QFileDialog.getOpenFileNames(self, tr("動画・音声を選択"), "", filters)
        self.add_paths([Path(path) for path in paths])

    def add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("動画フォルダを選択"))
        if folder:
            self.add_paths(discover_videos(Path(folder)))

    def add_paths(self, paths: list[Path]) -> None:
        if self.worker is not None:
            return
        added: list[Path] = []
        for path in paths:
            if path.is_dir():
                added.extend(discover_videos(path))
            elif path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS:
                added.append(path)
        for path in added:
            resolved = path.resolve()
            if resolved not in self.videos:
                self.videos.append(resolved)
                self.video_statuses[resolved] = "待機中"
        self.videos.sort(key=natural_video_sort_key)
        if self.videos and not self.output_path.text().strip():
            self._set_output_path(self.videos[0].parent / "Lectavo_output")
        self._refresh_video_list()
        self._update_naming_preview()
        self._sync_input_kind()
        self._refresh_output_controls()
        if self.videos:
            self._set_workflow_state("ready")
            self._select_page("processing")
        self._save_settings()

    def remove_video(self, path: Path) -> None:
        if self.worker is not None:
            return
        resolved = Path(path)
        if resolved in self.videos:
            self.videos.remove(resolved)
            self.video_statuses.pop(resolved, None)
        self._refresh_video_list()
        self._update_naming_preview()
        self._sync_input_kind()
        self._set_workflow_state("ready" if self.videos else "empty")

    def clear_videos(self) -> None:
        if self.worker is not None:
            return
        self.videos.clear()
        self._sync_input_kind()
        self.video_statuses.clear()
        self._refresh_video_list()
        self._set_workflow_state("empty")

    def browse_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("出力先を選択"), self.output_path.text())
        if folder:
            self._set_output_path(Path(folder))
            self._save_settings()

    def start_processing(self) -> None:
        if not self.videos:
            self._set_workflow_state("empty")
            return
        output = self._resolved_output()
        if output is None:
            QMessageBox.information(
                self,
                tr("出力先がありません"),
                tr("出力先フォルダを選択してください。"),
            )
            return
        try:
            output_stem(self.videos, self._naming_config(), combined=True)
        except ValueError as exc:
            QMessageBox.information(self, tr("出力名"), tr(str(exc)))
            return
        self._save_settings()
        moment = self._naming_moment or datetime.now()
        self._naming_moment = None
        self._open_preflight(
            Path(output),
            self._processing_options(moment),
            lambda: self._launch(Path(output), moment),
        )

    def _processing_options(self, moment: datetime) -> ProcessingOptions:
        return ProcessingOptions(
            operations=self._operation_options(),
            slide_config=self.settings_widget.slide_config(),
            duplicate_config=self.settings_widget.duplicate_config(),
            transcription_config=self.settings_widget.transcription_config(),
            per_video_dirs=True,
            output_mode=str(self.output_mode_combo.currentData()),
            show_progress=False,
            output_naming=self._naming_config(moment),
            checkpoint_dir=self._checkpoint_dir(),
            output_next_to_input=self.next_to_input_check.isChecked(),
            region_schedules=self.settings_widget.region_schedule_map(),
        )

    def _checkpoint_dir(self) -> Path | None:
        if not self.resume_enabled_check.isChecked():
            return None
        return user_cache_dir() / "checkpoints"

    def _open_preflight(self, output: Path, options: ProcessingOptions, on_start=None) -> None:
        from .preflight_dialog import PreflightDialog

        dialog = PreflightDialog(self, start_mode=on_start is not None)
        self._preflight_dialog = dialog
        if on_start is not None:
            dialog.accepted.connect(on_start)
        dialog.finished.connect(lambda _result: setattr(self, "_preflight_dialog", None))
        dialog.finished.connect(dialog.deleteLater)
        dialog.open()
        dialog.run(self.videos.copy(), output, options)

    def run_preflight_only(self) -> None:
        output = self._resolved_output()
        if not self.videos or output is None:
            return
        self._open_preflight(output, self._processing_options(datetime.now()))

    def _resolved_output(self) -> Path | None:
        """Folder that receives the outputs, or None when none is chosen."""
        if self.next_to_input_check.isChecked() and self.videos:
            return self.videos[0].parent
        text = self.output_path.text().strip()
        return Path(text) if text else None

    def _set_next_to_input(self, enabled: bool) -> None:
        for check in (self.next_to_input_check, self.settings_next_to_input_check):
            check.blockSignals(True)
            check.setChecked(enabled)
            check.blockSignals(False)
        self._refresh_output_controls()

    def _next_to_input_changed(self, enabled: bool) -> None:
        self._set_next_to_input(enabled)
        self._save_settings()

    def _refresh_output_controls(self) -> None:
        next_to_input = self.next_to_input_check.isChecked()
        running = self.worker is not None
        for field in (self.output_path, self.settings_output_path):
            field.setEnabled(not next_to_input)
        for button in (self.output_browse, self.settings_output_browse):
            button.setEnabled(not next_to_input and not running)
        if next_to_input:
            folders = list(dict.fromkeys(str(path.parent) for path in self.videos))
            if not folders:
                hint = tr("動画・音声と同じフォルダに保存します。")
            elif len(folders) == 1:
                hint = tr("保存先") + f": {folders[0]}"
            else:
                hint = tr("保存先") + f": {folders[0]} " + tr(f"ほか{len(folders) - 1}フォルダ")
        else:
            hint = ""
        self.output_location_hint.setText(hint)
        self.output_location_hint.setVisible(bool(hint))
        self.output_row_widget.setVisible(not next_to_input)

    def _launch(self, output: Path, moment: datetime) -> None:
        if self.worker is not None or not self.videos:
            return
        self.log_messages.clear()
        self._set_log_texts()
        self.last_batch = None
        self._cancel_requested = False
        self.overall_progress.setValue(0)
        self.run_started_at = datetime.now()
        self._last_moment = moment
        self._update_naming_preview(moment)
        self._update_elapsed()
        self.elapsed_timer.start()
        for path in self.videos:
            self.video_statuses[path] = "待機中"

        options = self._processing_options(moment)
        self._save_pending_job(output, moment)
        self._running_device_text = self.device_combo.currentText()
        self._refresh_running_settings()

        self._set_running(True)
        self._set_workflow_state("running")
        self.thread = QThread(self)
        self.worker = ProcessingWorker(self.videos.copy(), output, options)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.handle_progress)
        self.worker.log.connect(self.append_log)
        self.worker.finished.connect(self.handle_finished)
        self.worker.failed.connect(self.handle_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self._thread_finished)
        self.thread.start()

    def cancel_processing(self, _checked: bool = False, *, confirm: bool = True) -> None:
        if self.worker is None:
            return
        if confirm:
            answer = QMessageBox.question(
                self,
                tr("処理をキャンセル"),
                tr(
                    "本当にキャンセルしていいですか？\n\n実行中の処理を中断してホーム画面に戻ります。"
                ),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        self._cancel_requested = True
        self.worker.cancel()
        self.cancel_button.setEnabled(False)
        self.running_stage.setText(tr("キャンセル中"))
        self.running_detail.setText(tr("実行中の処理を停止しています。"))

    def handle_progress(self, event: ProgressEvent) -> None:
        self.overall_progress.setValue(event.overall_percent)
        if event.message.startswith("CUDAが使えないためCPUで続行しました"):
            self._running_device_text = tr("CPU（GPUから自動切替）")
            self._refresh_running_settings()
        if event.video_path:
            self.video_statuses[Path(event.video_path)] = stage_label(event.stage)
            for index in range(self.file_tabs.count()):
                item = self.file_tabs.item(index)
                row = self.file_tabs.itemWidget(item)
                if item.data(Qt.UserRole) == Path(event.video_path) and row is not None:
                    row.set_status(stage_label(event.stage))
            self.running_title.setText(tr(f"{event.video_path.name} を処理しています"))
        self.running_stage.setText(stage_label(event.stage))
        self.running_detail.setText(tr(event.message))

    def handle_finished(self, batch: BatchResult) -> None:
        self.last_batch = batch
        self.elapsed_timer.stop()
        self.overall_progress.setValue(100)
        for result in batch.results:
            if result.cancelled:
                status = "キャンセル"
            elif result.error:
                status = "エラー"
            elif result.transcription and result.transcription.status == "silence":
                status = "完了（無音）"
            elif result.transcription and result.transcription.status == "no_speech":
                status = "完了（発話未検出）"
            elif result.warnings:
                status = "完了（警告あり）"
            else:
                status = "完了"
            self.video_statuses[result.video_path] = status
        self._refresh_video_list()
        if self._cancel_requested or batch.cancelled_count:
            self._finish_cancelled_workflow()
            self._save_settings()
            return
        self._add_history(batch)
        self._refresh_recent()
        self._refresh_history()
        if not batch.failed_count:
            self._clear_pending_job()
        if batch.failed_count:
            self.error_message.setText(
                tr(
                    f"{len(batch.results)}本中 {batch.success_count}本成功、"
                    f"{batch.failed_count}本失敗しました。"
                )
                + "\n\n"
                + "\n\n".join(
                    result.video_path.name + ": " + tr(result.error)
                    for result in batch.results
                    if result.error
                )
            )
            self._set_workflow_state("error")
        else:
            self._populate_complete_summary(batch)
            self._set_workflow_state("complete")
        self._save_settings()

    def handle_failed(self, message: str) -> None:
        if self._cancel_requested:
            self._finish_cancelled_workflow()
            self._save_settings()
            return
        self.append_log(tr(f"エラー: {message}"))
        self.elapsed_timer.stop()
        self.error_message.setText(message)
        self._set_workflow_state("error")

    def _thread_finished(self) -> None:
        self._set_running(False)
        self.worker = None
        pending = getattr(self, "_pending_installer", None)
        if pending and not self._close_after_cancel:
            self._pending_installer = None
            QTimer.singleShot(0, lambda: self._update_downloaded(pending))
        self.thread = None
        self._refresh_video_list()
        self._refresh_checkpoint_size()
        self._cancel_requested = False
        if self._close_after_cancel:
            self._close_after_cancel = False
            QTimer.singleShot(0, self.close)

    def _finish_cancelled_workflow(self) -> None:
        self.elapsed_timer.stop()
        self.videos.clear()
        self.video_statuses.clear()
        self.last_batch = None
        self._refresh_video_list()
        self._refresh_resume_banner()
        self._set_workflow_state("empty")
        self._select_page("home")

    def append_log(self, message: str) -> None:
        self.log_messages.append(message)
        self._set_log_texts()

    def _set_running(self, running: bool) -> None:
        self.add_media_button.setEnabled(not running)
        self.remove_media_button.setEnabled(not running)
        self.select_video_button.setEnabled(not running)
        self.select_folder_link.setEnabled(not running)
        self.clear_button.setEnabled(not running)
        self.next_to_input_check.setEnabled(not running)
        self.settings_next_to_input_check.setEnabled(not running)
        self.output_browse.setEnabled(not running and not self.next_to_input_check.isChecked())
        self.settings_output_browse.setEnabled(
            not running and not self.next_to_input_check.isChecked()
        )
        self.start_button.setEnabled(not running)
        self.preflight_button.setEnabled(not running)
        self.resume_button.setEnabled(not running)
        self.resume_enabled_check.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        for checkbox in [
            self.output_pdf,
            self.output_lecture_notes,
            self.output_images,
        ]:
            checkbox.setEnabled(not running)
        self.output_mode_combo.setEnabled(not running)
        for control in (self.naming_mode, self.naming_date, self.custom_output_name):
            control.setEnabled(not running)
        self.quality_combo.setEnabled(not running)
        self.device_combo.setEnabled(not running)
        self.settings_widget.setEnabled(not running)
        self.region_button.setEnabled(
            not running and any(path.suffix.lower() in VIDEO_EXTENSIONS for path in self.videos)
        )
        self.check_updates_button.setEnabled(not running)
        if not running:
            self._sync_input_kind()

    def _set_workflow_state(self, state: str) -> None:
        self.workflow_state = state
        self.nav_process.setEnabled(state != "empty")
        if state == "empty":
            self._select_page("home")
            return
        index = {
            "empty": self.home_stack.indexOf(self.empty_page),
            "ready": self.home_stack.indexOf(self.ready_page),
            "running": self.home_stack.indexOf(self.running_page),
            "complete": self.home_stack.indexOf(self.complete_page),
            "error": self.home_stack.indexOf(self.error_page),
        }[state]
        self.home_stack.setCurrentIndex(index)
        self._select_page("processing")

    def _select_page(self, page: str) -> None:
        mapping = {
            "home": self.home_page,
            "processing": self.processing_page,
            "translate": self.translate_page,
            "history": self.history_page,
            "settings": self.settings_page,
            "help": self.help_page,
        }
        self.page_stack.setCurrentWidget(mapping[page])
        all_buttons = {
            "home": [self.nav_home],
            "processing": [self.nav_process],
            "translate": [self.nav_translate],
            "history": [self.nav_history],
            "settings": [self.nav_settings],
            "help": [self.nav_help],
        }
        for name, buttons in all_buttons.items():
            for button in buttons:
                button.setChecked(name == page)
        if page == "history":
            self._refresh_history()
        elif page == "settings":
            # Scanning the checkpoint folder is only needed when it is shown.
            self._refresh_checkpoint_size()

    def _refresh_video_list(self) -> None:
        selected = {item.data(Qt.UserRole) for item in self.file_tabs.selectedItems()}
        current = self.file_tabs.currentItem()
        current_path = current.data(Qt.UserRole) if current else None
        self.file_tabs.blockSignals(True)
        self.file_tabs.clear()
        for path in self.videos:
            status = self.video_statuses.get(path, "待機中")
            item = QListWidgetItem()
            item.setData(Qt.UserRole, path)
            item.setData(Qt.AccessibleTextRole, f"{path.name} {tr(status)}")
            item.setToolTip(str(path))
            item.setSizeHint(QSize(0, FILE_ROW_HEIGHT))
            self.file_tabs.addItem(item)
            self.file_tabs.setItemWidget(item, FileTabRow(path, status))
            if path in selected:
                item.setSelected(True)
            if path == current_path:
                self.file_tabs.setCurrentItem(item, QItemSelectionModel.NoUpdate)
        if self.file_tabs.currentRow() < 0 and self.videos:
            self.file_tabs.setCurrentRow(0)
        self.file_tabs.blockSignals(False)
        self._refresh_selected_file()

    def _refresh_selected_file(self):
        clear_layout(self.video_list_layout)
        if not self.videos:
            self.video_list_layout.addWidget(subtitle(tr("動画はまだ追加されていません。")))
            return
        current = self.file_tabs.currentItem()
        active_path = current.data(Qt.UserRole) if current else self.videos[0]
        for path in [active_path]:
            row = QFrame()
            row.setObjectName("ListRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 12, 0, 12)
            text_layout = QVBoxLayout()
            name = QLabel(path.name)
            name.setObjectName("SectionTitle")
            name.setWordWrap(True)
            name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            parent = subtitle(str(path.parent))
            parent.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            status = QLabel(tr(self.video_statuses.get(path, "待機中")))
            status.setObjectName("MutedText")
            text_layout.addWidget(name)
            text_layout.addWidget(parent)
            row_layout.addLayout(text_layout, 1)
            row_layout.addWidget(status)
            if self.worker is None:
                remove = QPushButton(tr("削除"))
                remove.setObjectName("LinkButton")
                remove.clicked.connect(lambda _checked=False, p=path: self.remove_video(p))
                row_layout.addWidget(remove)
            self.video_list_layout.addWidget(row)
        self.video_list_layout.addWidget(
            subtitle(tr("設定は全ファイルに適用されます。") + f" ({len(self.videos)})")
        )

    def _file_tab_changed(self, current, previous=None):
        if current is not None:
            self._refresh_selected_file()

    def remove_selected_files(self):
        if self.worker is not None:
            return
        paths = [item.data(Qt.UserRole) for item in self.file_tabs.selectedItems()]
        for path in paths:
            if path in self.videos:
                self.videos.remove(path)
                self.video_statuses.pop(path, None)
        self._refresh_video_list()
        self._update_naming_preview()
        self._sync_input_kind()
        self._set_workflow_state("ready" if self.videos else "empty")

    def _refresh_recent(self) -> None:
        clear_layout(self.recent_layout)
        if not self.history:
            self.recent_layout.addWidget(subtitle(tr("まだ履歴はありません。")))
            return
        for entry in self.history[:3]:
            self.recent_layout.addWidget(history_row(entry, self._delete_history_entry))

    def _refresh_history(self) -> None:
        clear_layout(self.history_layout)
        self.clear_history_button.setVisible(bool(self.history))
        if not self.history:
            self.history_layout.addWidget(subtitle(tr("まだ履歴はありません。")))
            return
        for entry in self.history:
            self.history_layout.addWidget(history_row(entry, self._delete_history_entry))

    def _delete_history_entry(self, path: str) -> None:
        self.history = [entry for entry in self.history if entry.get("path") != path]
        self._refresh_recent()
        self._refresh_history()
        self._save_settings()

    def clear_history(self) -> None:
        if not self.history:
            return
        answer = QMessageBox.question(
            self,
            tr("履歴をすべて削除"),
            tr(
                "すべての処理履歴を削除しますか？\n\n"
                "生成したPDFや文字起こし、入力動画は削除されません。"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._manual_update_check = True
        self.history.clear()
        self._refresh_recent()
        self._refresh_history()
        self._save_settings()

    def _add_history(self, batch: BatchResult) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        for result in batch.results:
            status = "キャンセル" if result.cancelled else "エラー" if result.error else "完了"
            entry = {
                "name": result.video_path.name,
                "path": str(result.video_path),
                "output": str(result.output_dir),
                "status": status,
                "time": now,
            }
            self.history = [item for item in self.history if item.get("path") != entry["path"]]
            self.history.insert(0, entry)
        self.history = self.history[:20]

    def _add_history_entry(self, entry: dict) -> None:
        self.history = [item for item in self.history if item.get("path") != entry.get("path")]
        self.history.insert(0, entry)
        self.history = self.history[:20]
        self._refresh_recent()
        self._refresh_history()
        self._save_settings()

    def _open_translation_settings(self) -> None:
        self._select_page("settings")
        self.engine_settings_widget.load()
        QTimer.singleShot(
            0, lambda: self.settings_page.ensureWidgetVisible(self.engine_settings_widget, 0, 80)
        )

    def _load_history(self) -> list[dict[str, str]]:
        raw = str(self.settings.value("history", "[]"))
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(loaded, list):
            return []
        return [item for item in loaded if isinstance(item, dict)]

    def _populate_complete_summary(self, batch: BatchResult) -> None:
        clear_layout(self.complete_summary_layout)
        if batch.output_mode == "combined":
            slide_count = batch.combined_slide_count
            pdf_count = int(batch.combined_pdf_path is not None)
            transcript_count = int(batch.combined_transcript_path is not None)
            markdown_count = int(batch.combined_markdown_path is not None)
        else:
            slide_count = sum(len(result.slides) for result in batch.results)
            pdf_count = sum(1 for result in batch.results if result.pdf_path)
            transcript_count = sum(1 for result in batch.results if result.transcription)
            markdown_count = sum(1 for result in batch.results if result.markdown_path)
        for text in [
            f"スライドPDF: {pdf_count} 件",
            f"抽出スライド: {slide_count} 枚",
            f"文字起こし: {transcript_count} 件",
            f"講義ノート: {markdown_count} 件",
        ]:
            label = QLabel(tr(text))
            label.setObjectName("SuccessText")
            self.complete_summary_layout.addWidget(label)
        for result in batch.results:
            if result.transcription and result.transcription.warning:
                label = QLabel(result.video_path.name + ": " + tr(result.transcription.warning))
                label.setWordWrap(True)
                self.complete_summary_layout.addWidget(label)
        if batch.reused_count:
            reused_label = QLabel(tr(f"保存済みの結果を再利用: {batch.reused_count} 件"))
            reused_label.setObjectName("MutedText")
            self.complete_summary_layout.addWidget(reused_label)
        if batch.output_mode == "combined" and batch.combined_duplicate_count:
            duplicate_label = QLabel(tr(f"動画間の重複除去: {batch.combined_duplicate_count} 枚"))
            duplicate_label.setObjectName("SuccessText")
            self.complete_summary_layout.addWidget(duplicate_label)
        self.open_pdf_button.setVisible(
            batch.combined_pdf_path is not None or any(result.pdf_path for result in batch.results)
        )
        self.open_transcript_button.setVisible(
            batch.combined_transcript_path is not None
            or any(result.transcription for result in batch.results)
        )
        has_slides = bool(batch.combined_slides) or (
            batch.output_mode != "combined" and any(result.slides for result in batch.results)
        )
        self.review_panel.setVisible(has_slides)

    def _retry_processing(self) -> None:
        if not self.videos:
            return
        self._naming_moment = getattr(self, "_last_moment", None)
        self.start_processing()

    def _change_settings_after_error(self) -> None:
        if self.videos:
            self._set_workflow_state("ready")
            self._select_page("processing")

    # Resume -------------------------------------------------------------------------

    def _load_pending_job(self) -> dict | None:
        raw = str(self.settings.value("pending_job", ""))
        if not raw:
            return None
        try:
            job = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(job, dict) or not isinstance(job.get("videos"), list):
            return None
        return job

    def _save_pending_job(self, output: Path, moment: datetime) -> None:
        if self._checkpoint_dir() is None:
            self._clear_pending_job()
            return
        self.pending_job = {
            "videos": [str(path) for path in self.videos],
            "output": str(output),
            "moment": moment.isoformat(timespec="seconds"),
            "next_to_input": self.next_to_input_check.isChecked(),
            "started": moment.strftime("%Y-%m-%d %H:%M"),
        }
        self.settings.setValue("pending_job", json.dumps(self.pending_job, ensure_ascii=False))
        self.settings.sync()

    def _clear_pending_job(self) -> None:
        self.pending_job = None
        self.settings.remove("pending_job")
        self._refresh_resume_banner()

    def _refresh_resume_banner(self) -> None:
        job = self.pending_job
        if not job:
            self.resume_banner.setVisible(False)
            return
        videos = [Path(path) for path in job.get("videos", [])]
        names = ", ".join(path.name for path in videos[:2])
        if len(videos) > 2:
            names += tr(f" ほか{len(videos) - 2}件")
        self.resume_detail.setText(
            tr(f"{job.get('started', '')} に開始: {names}")
            + "\n"
            + tr("完了したスライド解析と文字起こしを再利用して、残りの処理だけを行います。")
        )
        self.resume_banner.setVisible(True)

    def resume_pending_job(self) -> None:
        job = self.pending_job
        if not job or self.worker is not None:
            return
        videos = [Path(path) for path in job.get("videos", []) if Path(path).is_file()]
        if not videos:
            QMessageBox.information(
                self,
                tr("続きから再開"),
                tr("入力ファイルが見つからないため再開できません。"),
            )
            self._clear_pending_job()
            return
        self.videos.clear()
        self.video_statuses.clear()
        self.add_paths(videos)
        if job.get("output"):
            self._set_output_path(Path(job["output"]))
        if "next_to_input" in job:
            self._set_next_to_input(bool(job["next_to_input"]))
        try:
            self._naming_moment = datetime.fromisoformat(str(job.get("moment")))
        except ValueError:
            self._naming_moment = None
        self.start_processing()

    def discard_pending_job(self) -> None:
        self._clear_pending_job()

    def _refresh_checkpoint_size(self) -> None:
        from movie_slide_notes.core.checkpoint import CheckpointStore
        from movie_slide_notes.core.preflight import format_mb

        size = CheckpointStore(user_cache_dir() / "checkpoints").size_bytes()
        self.checkpoint_size_label.setText(
            tr(f"保存データ: {format_mb(size / 1024 / 1024)}")
            + "  "
            + tr("（14日以上使われないデータは自動で削除されます）")
        )
        self.clear_checkpoints_button.setEnabled(size > 0 and self.worker is None)

    def clear_checkpoints(self) -> None:
        if self.worker is not None:
            return
        answer = QMessageBox.question(
            self,
            tr("保存データを削除"),
            tr("途中保存したデータを削除しますか？\n\n出力済みのPDFや文字起こしは削除されません。"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        from movie_slide_notes.core.checkpoint import CheckpointStore

        CheckpointStore(user_cache_dir() / "checkpoints").clear()
        self._refresh_checkpoint_size()

    # Slide review -------------------------------------------------------------------

    def open_slide_review(self) -> None:
        if self.last_batch is None or self.worker is not None:
            return
        from .slide_review_dialog import SlideReviewDialog

        dialog = SlideReviewDialog(self.last_batch, self.last_batch.slide_region, self)
        translate_widget_tree(dialog)
        if dialog.exec() == QDialog.Accepted and dialog.outcomes:
            self._populate_complete_summary(self.last_batch)
            removed = sum(outcome.removed for outcome in dialog.outcomes)
            added = sum(outcome.added for outcome in dialog.outcomes)
            label = QLabel(
                tr(f"スライドを修正し、PDFを更新しました（削除 {removed} 枚 / 追加 {added} 枚）")
            )
            label.setObjectName("SuccessText")
            label.setWordWrap(True)
            self.complete_summary_layout.addWidget(label)
            self.append_log(label.text())

    def new_process(self) -> None:
        if self.worker is not None:
            return
        self.videos.clear()
        self.video_statuses.clear()
        self.log_messages.clear()
        self.last_batch = None
        self._refresh_video_list()
        self._set_log_texts()
        self._set_workflow_state("empty")
        self._select_page("home")

    def open_details(self) -> None:
        if self.width() < 1180:
            self._show_details_dialog()
            return
        self.inspector_requested = not self.inspector_requested
        self._update_responsive()

    def close_inspector(self) -> None:
        self.inspector_requested = False
        self._update_responsive()

    def _show_details_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("詳細設定"))
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        preset = cloned_combo(self.inspector_preset)
        language = cloned_combo(self.inspector_language)
        model = cloned_combo(self.inspector_model)
        set_combo_data(preset, self.settings_widget.preset_combo.currentData())
        set_combo_data(language, self.settings_widget.language_combo.currentData())
        set_combo_data(model, self.settings_widget.model_combo.currentData())
        for label, combo in [
            ("処理品質", preset),
            ("文字起こしの言語", language),
            ("文字起こし品質", model),
        ]:
            layout.addWidget(QLabel(tr(label)))
            layout.addWidget(combo)
        actions = QHBoxLayout()
        cancel = QPushButton(tr("キャンセル"))
        ok = QPushButton(tr("適用"))
        ok.setObjectName("PrimaryButton")
        actions.addStretch(1)
        actions.addWidget(cancel)
        actions.addWidget(ok)
        layout.addLayout(actions)
        cancel.clicked.connect(dialog.reject)
        ok.clicked.connect(dialog.accept)
        if dialog.exec() == QDialog.Accepted:
            set_combo_data(self.settings_widget.preset_combo, preset.currentData())
            set_combo_data(self.settings_widget.language_combo, language.currentData())
            set_combo_data(self.settings_widget.model_combo, model.currentData())
            self._settings_widget_changed()

    def _compact_preset_changed(self) -> None:
        if self._syncing:
            return
        source = self.sender()
        if isinstance(source, QComboBox):
            set_combo_data(self.settings_widget.preset_combo, source.currentData())
            self._settings_widget_changed()

    def _compact_language_changed(self) -> None:
        if self._syncing:
            return
        set_combo_data(self.settings_widget.language_combo, self.inspector_language.currentData())
        self._settings_widget_changed()

    def _compact_model_changed(self) -> None:
        if self._syncing:
            return
        set_combo_data(self.settings_widget.model_combo, self.inspector_model.currentData())
        self._settings_widget_changed()

    def _compact_device_changed(self) -> None:
        if self._syncing:
            return
        set_combo_data(self.settings_widget.device_combo, self.device_combo.currentData())
        self._settings_widget_changed()

    def _sync_compact_settings_from_settings_widget(self) -> None:
        self._syncing = True
        try:
            for combo in [self.quality_combo, self.inspector_preset]:
                set_combo_data(combo, self.settings_widget.preset_combo.currentData())
            set_combo_data(
                self.inspector_language, self.settings_widget.language_combo.currentData()
            )
            set_combo_data(self.inspector_model, self.settings_widget.model_combo.currentData())
            set_combo_data(self.device_combo, self.settings_widget.device_combo.currentData())
        finally:
            self._syncing = False

    def _refresh_running_settings(self) -> None:
        outputs: list[str] = []
        if self.output_pdf.isChecked():
            outputs.append(tr("スライドPDF"))
        if self.output_lecture_notes.isChecked():
            outputs.append(tr("文字起こし＋講義ノート"))
        if self.output_images.isChecked():
            outputs.append(tr("画像ファイル"))
        device = getattr(self, "_running_device_text", self.device_combo.currentText())
        output_separator = ", " if current_ui_language() == "en" else "、"
        self.running_settings.setText(
            "\n".join(
                [
                    tr(f"出力方法: {self.output_mode_combo.currentText()}"),
                    self.naming_preview.text(),
                    tr(f"出力: {output_separator.join(outputs)}"),
                    tr(f"処理品質: {self.quality_combo.currentText()}"),
                    tr(f"言語: {self.settings_widget.language_combo.currentText()}"),
                    tr(f"文字起こし品質: {self.settings_widget.model_combo.currentText()}"),
                    tr(f"処理デバイス: {device}"),
                    self.region_hint.text(),
                ]
            )
        )

    def _naming_config(self, moment: datetime | None = None) -> OutputNamingConfig:
        return OutputNamingConfig(
            mode=str(self.naming_mode.currentData()),
            custom_name=self.custom_output_name.text(),
            date_style=str(self.naming_date.currentData()),
            timestamp=moment,
        )

    def _sync_input_kind(self):
        audio_only = bool(self.videos) and all(
            path.suffix.lower() in AUDIO_EXTENSIONS for path in self.videos
        )
        was_audio = getattr(self, "_audio_only_ui", False)
        for checkbox in (self.output_pdf, self.output_images):
            checkbox.blockSignals(True)
            if audio_only and not was_audio:
                checkbox.setProperty("video_choice", checkbox.isChecked())
                checkbox.setChecked(False)
            elif was_audio and not audio_only:
                checkbox.setChecked(bool(checkbox.property("video_choice")))
            checkbox.setEnabled(not audio_only)
            checkbox.blockSignals(False)
        has_audio = any(path.suffix.lower() in AUDIO_EXTENSIONS for path in self.videos)
        if has_audio:
            self.output_lecture_notes.setChecked(True)
            self.dependency_label.setText(tr("音声ファイルは文字起こしとノートを出力します。"))
        self.output_lecture_notes.setEnabled(not has_audio)
        self.region_button.setEnabled(
            any(path.suffix.lower() in VIDEO_EXTENSIONS for path in self.videos)
        )
        self._audio_only_ui = audio_only

    def _refresh_region_hint(self):
        region = getattr(self.settings_widget, "slide_region", None)
        parts = [
            tr("検出範囲: 自動")
            if region is None
            else tr("検出範囲: 手動") + "  " + ", ".join(f"{value:.3f}" for value in region)
        ]
        names = [
            Path(key).name
            for key in self.settings_widget.region_schedules
            if any(Path(key) == path for path in self.videos) or not self.videos
        ]
        if names:
            parts.append(tr("時間ごとの範囲") + ": " + ", ".join(names))
        if self.settings_widget.no_page_number_check.isChecked():
            parts.append(tr("ページ番号なし"))
        self.region_hint.setText(" ・ ".join(parts))

    def edit_region(self):
        videos = [path for path in self.videos if path.suffix.lower() in VIDEO_EXTENSIONS]
        if not videos:
            paths, _ = QFileDialog.getOpenFileNames(
                self,
                tr("プレビュー用の動画を選択"),
                "",
                "Video (*.mp4 *.mov *.mkv *.avi *.webm *.m4v)",
            )
            videos = [Path(path) for path in paths]
        if not videos:
            return
        from .region_selector import RegionSelector

        config = self.settings_widget.slide_config()
        dialog = RegionSelector(
            videos,
            config.slide_region,
            config.page_region,
            self,
            schedules=self.settings_widget.region_schedules,
            use_page_region=config.use_page_region,
        )
        if dialog.exec() == QDialog.Accepted:
            self.settings_widget.slide_region = dialog.common_region
            self.settings_widget.region_schedules = dialog.result_schedules()
            self.settings_widget.no_page_number_check.setChecked(not dialog.use_page_region)
            for field, value in zip(
                (
                    self.settings_widget.region_x,
                    self.settings_widget.region_y,
                    self.settings_widget.region_w,
                    self.settings_widget.region_h,
                ),
                dialog.canvas.page,
            ):
                field.setValue(value)
            self.settings_widget.region_w.setValue(
                min(
                    self.settings_widget.region_w.value(), 1 - self.settings_widget.region_x.value()
                )
            )
            self.settings_widget.region_h.setValue(
                min(
                    self.settings_widget.region_h.value(), 1 - self.settings_widget.region_y.value()
                )
            )
            self._refresh_region_hint()
            self._save_settings()

    def check_updates(self, manual=False):
        if self.updater.busy:
            return
        self._manual_update_check = manual
        self.update_status.setText(tr("更新を確認しています…"))
        self.updater.check()

    def _update_checked(self):
        self.update_status.setText(tr("最新バージョンです。"))

    def _update_available(self, info):
        self.update_status.setText(tr("新しいバージョンがあります。") + f" {info.version}")
        if self.worker is not None:
            return
        answer = QMessageBox.question(
            self,
            tr("Lectavoの更新"),
            tr("新しいバージョンをダウンロードしますか？")
            + f"\n{__version__} → {info.version} ({info.size / 1024 / 1024:.0f} MB)",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.update_progress_dialog = QProgressDialog(
            tr("更新をダウンロードしています…"), tr("キャンセル"), 0, 100, self
        )
        self.update_progress_dialog.setAutoClose(False)
        self.update_progress_dialog.canceled.connect(self.updater.cancel)
        self.update_progress_dialog.show()
        self.updater.download(info)

    def _update_progress(self, current, total):
        if self.update_progress_dialog:
            self.update_progress_dialog.setValue(int(current / max(1, total) * 100))

    def _close_update_progress(self):
        if self.update_progress_dialog:
            self.update_progress_dialog.close()
            self.update_progress_dialog.deleteLater()
            self.update_progress_dialog = None

    def _update_cancelled(self):
        self._close_update_progress()
        self.update_status.setText(tr("更新のダウンロードをキャンセルしました。"))

    def _update_failed(self, message):
        self._close_update_progress()
        self.update_status.setText(tr(message))
        if self._manual_update_check:
            QMessageBox.warning(self, tr("アプリの更新"), tr(message))

    def _update_downloaded(self, path):
        self._close_update_progress()
        self.update_status.setText(tr("更新ファイルの検証が完了しました。"))
        if self.worker is not None:
            self._pending_installer = path
            QMessageBox.information(
                self, tr("アプリの更新"), tr("処理完了後に更新を確認してください。")
            )
            return
        answer = QMessageBox.question(
            self,
            tr("Lectavoの更新"),
            tr("アプリを閉じてインストーラーを起動しますか？"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            started, _ = QProcess.startDetached(path, [])
            if started:
                self._save_settings()
                self.close()
            else:
                QMessageBox.warning(
                    self, tr("アプリの更新"), tr("インストーラーを起動できませんでした。")
                )

    def _naming_changed(self) -> None:
        self._update_naming_preview()
        self._save_settings()

    def _update_naming_preview(self, moment: datetime | None = None) -> None:
        self.custom_output_name.setVisible(self.naming_mode.currentData() == "custom")
        combined = self.output_mode_combo.currentData() == "combined"
        paths = self.videos if combined else self.videos[:1]
        try:
            stem = output_stem(paths, self._naming_config(moment), combined=combined)
            self.naming_preview.setText(
                tr("出力名の例") + f": {stem}_slides.pdf / {stem}_transcript.txt"
            )
        except ValueError as exc:
            self.naming_preview.setText(tr(str(exc)))

    def _set_output_path(self, path: Path) -> None:
        text = str(path)
        self.output_path.setText(text)
        self.settings_output_path.setText(text)

    def _toggle_log(self, widget: QPlainTextEdit, button: QPushButton) -> None:
        visible = not widget.isVisible()
        widget.setVisible(visible)
        button.setText(tr("詳細ログを閉じる" if visible else "詳細ログを表示"))

    def _set_log_texts(self) -> None:
        text = "\n".join(self.log_messages)
        self.running_log_text.setPlainText(text)
        self.error_log_text.setPlainText(text)

    def _update_elapsed(self) -> None:
        if self.run_started_at is None:
            self.elapsed_label.setText(tr("経過時間 00:00"))
            return
        elapsed = int((datetime.now() - self.run_started_at).total_seconds())
        self.elapsed_label.setText(tr(f"経過時間 {format_duration(elapsed)}"))

    def open_output_folder(self) -> None:
        path = self._resolved_output() or Path(self.output_path.text())
        if self.last_batch is not None and self.last_batch.output_dir is not None:
            path = self.last_batch.output_dir
        if path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def open_first_pdf(self) -> None:
        if self.last_batch and self.last_batch.combined_pdf_path:
            path = self.last_batch.combined_pdf_path
            if path.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
                return
        for result in self.last_batch.results if self.last_batch else []:
            if result.pdf_path and result.pdf_path.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(result.pdf_path)))
                return

    def open_first_transcript(self) -> None:
        if self.last_batch and self.last_batch.combined_transcript_path:
            path = self.last_batch.combined_transcript_path
            if path.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
                return
        for result in self.last_batch.results if self.last_batch else []:
            if result.transcription and result.transcription.text_path.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(result.transcription.text_path)))
                return

    def open_log_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(user_log_dir())))

    def open_third_party_notices(self) -> None:
        notices = resource_path("THIRD_PARTY_NOTICES.txt")
        if notices.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(notices)))

    def copy_diagnostics(self) -> None:
        QApplication.clipboard().setText("\n".join(self.log_messages))

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        pdfs = [path for path in paths if path.suffix.lower() == ".pdf"]
        if pdfs:
            # PDFs go to translation; media in the same drop still goes to processing.
            self._select_page("translate")
            self.translate_page.set_sources(pdfs)
            paths = [path for path in paths if path.suffix.lower() != ".pdf"]
            if not paths:
                return
        self.add_paths(paths)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_responsive()

    def closeEvent(self, event) -> None:
        if self.translate_page.busy:
            answer = QMessageBox.question(
                self,
                tr("翻訳中です"),
                tr("翻訳をキャンセルして終了しますか？翻訳済みの部分は次回再利用されます。"),
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self.translate_page.close_after_cancel = self.close
            self.translate_page.cancel()
            event.ignore()
            return
        if self.worker is not None:
            answer = QMessageBox.question(
                self,
                tr("処理中です"),
                tr("処理をキャンセルして終了しますか？"),
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self._close_after_cancel = True
            self.cancel_processing(confirm=False)
            event.ignore()
            return
        self._save_settings()
        self.settings.setValue("window_geometry", self.saveGeometry())
        self.engine_settings_widget.local_models.shutdown()
        from movie_slide_notes.pdf_translate.local_llm import stop_server

        stop_server()
        if hasattr(self, "updater") and self.updater.busy:
            self._manual_update_check = False
            self.updater.cancel()
        event.accept()

    def _update_responsive(self) -> None:
        compact = self.width() < 960
        self.sidebar.setVisible(True)
        self.brand_label.setVisible(not compact)
        self.sidebar.setFixedWidth(LAYOUT.sidebar_compact if compact else LAYOUT.sidebar_wide)
        for button, label in [
            (self.nav_home, "ホーム"),
            (self.nav_process, "処理"),
            (self.nav_translate, "PDF翻訳"),
            (self.nav_history, "履歴"),
            (self.nav_settings, "設定"),
            (self.nav_help, "ヘルプ"),
        ]:
            button.setText("" if compact else tr(label))
            button.setToolTip(tr(label))
            if button.property("compact") != compact:
                button.setProperty("compact", compact)
                button.style().unpolish(button)
                button.style().polish(button)
        if hasattr(self, "outputs_grid"):
            for checkbox in (self.output_pdf, self.output_lecture_notes, self.output_images):
                self.outputs_grid.removeWidget(checkbox)
            for index, checkbox in enumerate(
                (self.output_pdf, self.output_lecture_notes, self.output_images)
            ):
                self.outputs_grid.addWidget(
                    checkbox, index if compact else index // 2, 0 if compact else index % 2
                )
            columns = 1 if self.width() < 760 else 2 if self.width() < 1280 else 4
            for card in self.step_cards:
                self.steps_layout.removeWidget(card)
            for index, card in enumerate(self.step_cards):
                self.steps_layout.addWidget(card, index // columns, index % columns)
            direction = QBoxLayout.TopToBottom if compact else QBoxLayout.LeftToRight
            self.complete_actions.setDirection(direction)
            self.error_actions.setDirection(direction)
        if getattr(self, "_compact_layout", None) != compact:
            self._compact_layout = compact
            self.processing_page.setSizes([150 if compact else 190, max(1, self.width() - 400)])
        inspector_inline = self.width() >= 1180
        self.inspector.setVisible(inspector_inline and self.inspector_requested)
        if not inspector_inline:
            self.inspector_requested = False

    def _apply_theme(self) -> None:
        theme = (
            self.settings_widget.theme_combo.currentData()
            if hasattr(self, "settings_widget")
            else "system"
        )
        app = QApplication.instance()
        if app is None:
            return
        dark = theme == "dark" or (theme == "system" and system_prefers_dark())
        app.setStyleSheet(build_stylesheet(dark))

    def _apply_ui_language(self, initial: bool = False) -> None:
        set_ui_language(str(self.settings_widget.ui_language_combo.currentData()))
        # Widgets are created in Japanese, so at startup there is nothing to
        # translate; a later language change still walks the whole tree.
        if not (initial and current_ui_language() == DEFAULT_UI_LANGUAGE):
            translate_widget_tree(self)
        self.about_text.setText(
            f"{APP_NAME}\nVersion {__version__}\n{tr('発行元')}: {APP_PUBLISHER}"
            f"\n\n{tr(APP_DESCRIPTION)}"
        )
        self._output_mode_changed()
        self._refresh_video_list()
        self._refresh_recent()
        self._refresh_history()
        self._refresh_running_settings()
        self.settings.setValue("ui_language", current_ui_language())
        self._update_responsive()


def stage_label(stage: str) -> str:
    return tr(
        {
            "start": "処理開始",
            "slide_detection": "スライド解析中",
            "pdf": "PDF生成中",
            "audio_extract": "音声抽出中",
            "model_prepare": "モデル準備中",
            "transcribe": "文字起こし中",
            "markdown": "Markdown生成中",
            "combined_pdf": "統合PDF生成中",
            "reused": "保存済みの結果を再利用",
            "done": "完了",
            "error": "エラー",
        }.get(stage, stage)
    )


def page_container() -> QWidget:
    page = QWidget()
    page.setObjectName("PageContent")
    page.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    return page


def scroll_page() -> QScrollArea:
    scroll = QScrollArea()
    scroll.setObjectName("PageScroll")
    scroll.setWidgetResizable(True)
    content = QWidget()
    content.setObjectName("PageContent")
    layout = QVBoxLayout(content)
    layout.setContentsMargins(28, 32, 28, 32)
    layout.setSpacing(SPACING.md)
    scroll.setWidget(content)
    return scroll


def page_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("PageTitle")
    label.setWordWrap(True)
    return label


def section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("SectionTitle")
    return label


def subtitle(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("SecondaryText")
    label.setWordWrap(True)
    return label


def nav_button(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("NavButton")
    button.setCheckable(True)
    button.setAccessibleName(text)
    return button


def clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        child_layout = item.layout()
        if widget is not None:
            widget.hide()  # never shown again while the deferred delete is pending
            widget.deleteLater()
        elif child_layout is not None:
            clear_layout(child_layout)


def history_row(entry: dict[str, str], delete_callback=None) -> QWidget:
    row = QFrame()
    row.setObjectName("ListRow")
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 12, 0, 12)
    text_layout = QVBoxLayout()
    name = QLabel(entry.get("name", ""))
    name.setObjectName("SectionTitle")
    name.setWordWrap(True)
    name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    detail = subtitle(f"{entry.get('time', '')}  {entry.get('output', '')}")
    detail.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    text_layout.addWidget(name)
    text_layout.addWidget(detail)
    status = QLabel(tr(entry.get("status", "")))
    status.setObjectName("MutedText")
    layout.addLayout(text_layout, 1)
    layout.addWidget(status)
    if delete_callback is not None:
        delete_button = QPushButton(tr("削除"))
        delete_button.setObjectName("LinkButton")
        delete_button.setAccessibleName(tr(f"{entry.get('name', '')} の履歴を削除"))
        delete_button.setToolTip(tr("この履歴だけを削除"))
        delete_button.clicked.connect(
            lambda _checked=False, path=entry.get("path", ""): delete_callback(path)
        )
        layout.addWidget(delete_button)
    return row


FILE_ROW_HEIGHT = 56


def file_status_state(status: str) -> str:
    lowered = status.lower()
    if "エラー" in status or "error" in lowered:
        return "error"
    if status in {"待機中", "キャンセル", "Waiting", "Cancelled"}:
        return "idle"
    if status.startswith("完了") or lowered.startswith("completed"):
        return "warning" if ("（" in status or "(" in status) else "done"
    return "active"


class FileTabRow(QWidget):
    """One input file in the left rail: type icon, name, and colored status."""

    def __init__(self, path: Path, status: str):
        super().__init__()
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(10)
        kind = "file-audio" if path.suffix.lower() in AUDIO_EXTENSIONS else "file-video"
        self.kind_icon = QLabel()
        self.kind_icon.setPixmap(svg_pixmap(kind, 20))
        self.kind_icon.setFixedSize(22, 22)
        layout.addWidget(self.kind_icon, 0, Qt.AlignVCenter)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(1)
        self.name_label = ElidedLabel(path.name)
        self.name_label.setObjectName("FileName")
        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(6)
        self.status_dot = QFrame()
        self.status_dot.setObjectName("StatusDot")
        self.status_dot.setFixedSize(8, 8)
        self.status_label = QLabel()
        self.status_label.setObjectName("FileStatus")
        status_row.addWidget(self.status_dot, 0, Qt.AlignVCenter)
        status_row.addWidget(self.status_label, 1)
        text.addWidget(self.name_label)
        text.addLayout(status_row)
        layout.addLayout(text, 1)
        self.set_status(status)

    def set_status(self, status: str) -> None:
        state = file_status_state(status)
        self.status_label.setText(tr(status))
        for widget in (self.status_label, self.status_dot):
            widget.setProperty("state", state)
            widget.style().unpolish(widget)
            widget.style().polish(widget)


READY_LABEL_WIDTH = 130


def grid_label(text: str) -> QLabel:
    """Fixed-width label so grids on the process page share one label column."""
    label = QLabel(text)
    label.setFixedWidth(READY_LABEL_WIDTH)
    label.setMinimumHeight(36)
    label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    return label


def icon_path(name: str) -> str:
    return str(resource_path("assets", "icons", f"{name}.svg"))


def icon_pixmap(name: str, size: int):
    return QIcon(icon_path(name)).pixmap(QSize(size, size))


def task_card(icon_name: str, text: str) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("TaskCard")
    button.setIcon(svg_icon(icon_name, 22, "#14a39b"))
    button.setIconSize(QSize(22, 22))
    button.setCursor(Qt.PointingHandCursor)
    button.setAccessibleName(text)
    button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    button.setFixedHeight(64)
    return button


def step_card(icon_name: str, number: str, title: str, body: str) -> QFrame:
    card = QFrame()
    card.setObjectName("StepCard")
    card.setMinimumWidth(0)
    card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    layout = QHBoxLayout(card)
    layout.setContentsMargins(14, 14, 14, 14)
    layout.setSpacing(12)
    image = QLabel()
    image.setPixmap(icon_pixmap(icon_name, 40))
    layout.addWidget(image, alignment=Qt.AlignTop)
    text = QVBoxLayout()
    text.setSpacing(2)
    heading = QLabel(f"{number}. {title}")
    heading.setStyleSheet("font-weight: 600;")
    description = QLabel(body)
    description.setObjectName("SecondaryText")
    description.setWordWrap(True)
    description.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    text.addWidget(heading)
    text.addWidget(description)
    layout.addLayout(text, 1)
    return card


def set_combo_data(combo: QComboBox, data) -> None:
    index = combo.findData(data)
    if index >= 0:
        combo.setCurrentIndex(index)


def cloned_combo(source: QComboBox) -> QComboBox:
    combo = QComboBox()
    for index in range(source.count()):
        combo.addItem(source.itemText(index), source.itemData(index))
    return combo


def format_duration(seconds: int) -> str:
    minutes, secs = divmod(max(0, seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"
