from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QThread, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
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

from movie_slide_notes.core.output_naming import OutputNamingConfig, output_stem
from movie_slide_notes.core.processor import (
    BatchResult,
    OperationOptions,
    ProcessingOptions,
    ProgressEvent,
    discover_videos,
    natural_video_sort_key,
)
from movie_slide_notes.gui.controls import NoWheelComboBox as QComboBox
from movie_slide_notes.gui.design_tokens import LAYOUT, SPACING, build_stylesheet
from movie_slide_notes.gui.i18n import (
    current_ui_language,
    set_ui_language,
    tr,
    translate_widget_tree,
)
from movie_slide_notes.gui.settings_widget import ProcessingSettingsWidget
from movie_slide_notes.gui.worker import ProcessingWorker
from movie_slide_notes.paths import resource_path, user_config_dir, user_log_dir
from movie_slide_notes.slide_detector import VIDEO_EXTENSIONS
from movie_slide_notes.transcriber import inspect_cuda_runtime
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
        self._apply_ui_language()
        self._set_workflow_state("empty")
        self._update_responsive()

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

        self.brand_label = QLabel(APP_NAME)
        self.brand_label.setObjectName("AppName")
        sidebar_layout.addWidget(self.brand_label)
        sidebar_layout.addSpacing(18)

        self.nav_home = nav_button("ホーム")
        self.nav_history = nav_button("履歴")
        sidebar_layout.addWidget(self.nav_home)
        sidebar_layout.addWidget(self.nav_history)
        sidebar_layout.addStretch(1)
        sidebar_layout.addWidget(separator())
        self.nav_settings = nav_button("設定")
        self.nav_help = nav_button("ヘルプ")
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
        self._build_home_page()
        self._build_history_page()
        self._build_settings_page()
        self._build_help_page()
        self._build_inspector()
        root.addWidget(self.inspector)

    def _build_home_page(self) -> None:
        self.home_page = QWidget()
        layout = QVBoxLayout(self.home_page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.home_stack = QStackedWidget()
        layout.addWidget(self.home_stack)
        self.page_stack.addWidget(self.home_page)

        self.empty_page = scroll_page()
        empty_content = self.empty_page.widget()
        empty_layout = empty_content.layout()
        empty_layout.setSpacing(24)
        empty_layout.addWidget(page_title("新しい処理"))
        empty_layout.addWidget(subtitle("講義動画からスライドPDFと文字起こしを作成します。"))

        drop_zone = QFrame()
        drop_zone.setObjectName("DropZone")
        drop_zone.setMinimumHeight(230)
        drop_layout = QVBoxLayout(drop_zone)
        drop_layout.setContentsMargins(32, 30, 32, 30)
        drop_layout.setSpacing(14)
        drop_layout.setAlignment(Qt.AlignCenter)
        drop_title = QLabel("動画をここにドロップ")
        drop_title.setObjectName("SectionTitle")
        drop_title.setAlignment(Qt.AlignCenter)
        drop_hint = subtitle("MP4、MOV、MKV、AVI、WEBM、M4V に対応")
        drop_hint.setAlignment(Qt.AlignCenter)
        drop_hint.setWordWrap(False)
        self.select_video_button = QPushButton("動画を選択")
        self.select_video_button.setObjectName("PrimaryButton")
        self.select_video_button.setAccessibleName("動画を選択")
        self.select_folder_link = QPushButton("フォルダを選択")
        self.select_folder_link.setObjectName("LinkButton")
        drop_layout.addWidget(drop_title)
        drop_layout.addWidget(self.select_video_button, alignment=Qt.AlignCenter)
        drop_layout.addWidget(drop_hint)
        drop_layout.addWidget(self.select_folder_link, alignment=Qt.AlignCenter)
        empty_layout.addWidget(drop_zone, 1)
        empty_layout.addWidget(section_title("最近の処理"))
        self.recent_layout = QVBoxLayout()
        self.recent_layout.setSpacing(0)
        empty_layout.addLayout(self.recent_layout)
        empty_layout.addStretch(1)
        self.home_stack.addWidget(self.empty_page)

        self.ready_page = scroll_page()
        ready_content = self.ready_page.widget()
        ready_layout = ready_content.layout()
        ready_layout.addWidget(page_title("新しい処理"))
        ready_layout.addWidget(subtitle("出力内容を選んでから処理を開始します。"))
        ready_layout.addSpacing(8)

        ready_layout.addWidget(section_title("動画"))
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
        output_row = QHBoxLayout()
        self.output_path = QLineEdit()
        self.output_path.setReadOnly(True)
        self.output_path.setAccessibleName("出力先")
        self.output_browse = QPushButton("変更")
        output_row.addWidget(self.output_path, 1)
        output_row.addWidget(self.output_browse)
        ready_layout.addLayout(output_row)

        ready_layout.addWidget(section_title("ファイル名"))
        naming_grid = QGridLayout()
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
        naming_grid.addWidget(QLabel("名前の付け方"), 0, 0)
        naming_grid.addWidget(self.naming_mode, 0, 1)
        naming_grid.addWidget(self.custom_output_name, 1, 1)
        naming_grid.addWidget(QLabel("日付・日時"), 2, 0)
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
        cuda_status = inspect_cuda_runtime()
        if not cuda_status.available:
            self.device_combo.setItemText(1, "GPU（現在利用不可）")
            self.device_combo.setItemData(1, cuda_status.message, Qt.ToolTipRole)
        self.device_combo.setToolTip(
            "文字起こしに使用する処理デバイスです。GPUが利用できない場合は理由を表示してCPUへ自動で切り替わります。"
        )
        self.details_button = QPushButton("詳細設定")
        self.details_button.setObjectName("LinkButton")
        quality_grid.addWidget(QLabel("品質"), 0, 0)
        quality_grid.addWidget(self.quality_combo, 0, 1)
        quality_grid.addWidget(QLabel("文字起こし処理"), 1, 0)
        quality_grid.addWidget(self.device_combo, 1, 1)
        quality_grid.addWidget(self.details_button, 2, 0, 1, 2, alignment=Qt.AlignRight)
        quality_grid.setColumnStretch(1, 1)
        ready_layout.addLayout(quality_grid)

        ready_layout.addSpacing(20)
        action_row = QHBoxLayout()
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
        complete_layout.addSpacing(16)
        complete_actions = QHBoxLayout()
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
        self.error_hint = subtitle("再試行するか、詳細ログを確認してください。")
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
        self.retry_button = QPushButton("再試行")
        self.copy_diagnostics_button = QPushButton("診断情報をコピー")
        self.open_logs_button = QPushButton("ログフォルダを開く")
        error_actions.addWidget(self.open_logs_button)
        error_actions.addWidget(self.copy_diagnostics_button)
        error_actions.addStretch(1)
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
        layout.addSpacing(16)
        layout.addWidget(section_title("一般"))
        layout.addWidget(separator())
        default_output_row = QHBoxLayout()
        self.settings_output_path = QLineEdit()
        self.settings_output_path.setReadOnly(True)
        self.settings_output_browse = QPushButton("変更")
        default_output_row.addWidget(QLabel("デフォルト出力先"))
        default_output_row.addWidget(self.settings_output_path, 1)
        default_output_row.addWidget(self.settings_output_browse)
        layout.addLayout(default_output_row)
        layout.addWidget(separator())
        layout.addWidget(self.settings_widget)
        layout.addStretch(1)
        self.page_stack.addWidget(self.settings_page)

    def _build_help_page(self) -> None:
        self.help_page = scroll_page()
        content = self.help_page.widget()
        layout = content.layout()
        layout.addWidget(page_title("ヘルプ"))
        layout.addWidget(subtitle("Lectavoの基本操作と、困ったときの確認先です。"))
        layout.addSpacing(16)

        for title, lines in [
            ("はじめに", ["1. 動画を追加", "2. 出力内容を選択", "3. 処理を開始"]),
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
            (self.nav_history, "history"),
            (self.nav_settings, "settings"),
            (self.nav_help, "help"),
        ]:
            button.clicked.connect(lambda _checked=False, p=page: self._select_page(p))

        self.select_video_button.clicked.connect(self.add_files)
        self.select_folder_link.clicked.connect(self.add_folder)
        self.clear_button.clicked.connect(self.clear_videos)
        self.clear_history_button.clicked.connect(self.clear_history)
        self.output_browse.clicked.connect(self.browse_output)
        self.settings_output_browse.clicked.connect(self.browse_output)
        self.start_button.clicked.connect(self.start_processing)
        self.cancel_button.clicked.connect(self.cancel_processing)
        self.retry_button.clicked.connect(self._retry_processing)
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
        set_combo_data(self.naming_mode, self.settings.value("naming_mode", "original"))
        set_combo_data(self.naming_date, self.settings.value("naming_date", "none"))
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

    def _save_settings(self) -> None:
        self.settings.setValue("output_path", self.output_path.text())
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
                tr("すべての動画を1フォルダにまとめ、動画をまたぐ重複スライドも除去します。")
            )
        else:
            self.output_mode_hint.setText(
                tr("動画ごとのフォルダにPDF、文字起こし、講義ノートを保存します。")
            )
        if not self._syncing:
            self._save_settings()

    def _operation_options(self) -> OperationOptions:
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
            tr("動画ファイル")
            + " ("
            + " ".join(f"*{ext}" for ext in sorted(VIDEO_EXTENSIONS))
            + ");;"
            + tr("すべて")
            + " (*.*)"
        )
        paths, _ = QFileDialog.getOpenFileNames(self, tr("動画を選択"), "", filters)
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
            elif path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
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
        if self.videos:
            self._set_workflow_state("ready")
            self._select_page("home")
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
        self._set_workflow_state("ready" if self.videos else "empty")

    def clear_videos(self) -> None:
        if self.worker is not None:
            return
        self.videos.clear()
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
        output = self.output_path.text().strip()
        if not output:
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
        self.log_messages.clear()
        self._set_log_texts()
        self.last_batch = None
        self._cancel_requested = False
        self.overall_progress.setValue(0)
        self.run_started_at = datetime.now()
        self._update_naming_preview(self.run_started_at)
        self._update_elapsed()
        self.elapsed_timer.start()
        for path in self.videos:
            self.video_statuses[path] = "待機中"

        options = ProcessingOptions(
            operations=self._operation_options(),
            slide_config=self.settings_widget.slide_config(),
            duplicate_config=self.settings_widget.duplicate_config(),
            transcription_config=self.settings_widget.transcription_config(),
            per_video_dirs=True,
            output_mode=str(self.output_mode_combo.currentData()),
            show_progress=False,
            output_naming=self._naming_config(self.run_started_at),
        )
        self._running_device_text = self.device_combo.currentText()
        self._refresh_running_settings()

        self._set_running(True)
        self._set_workflow_state("running")
        self.thread = QThread(self)
        self.worker = ProcessingWorker(self.videos.copy(), Path(output), options)
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
        if batch.failed_count:
            self.error_message.setText(
                tr(
                    f"{len(batch.results)}本中 {batch.success_count}本成功、"
                    f"{batch.failed_count}本失敗しました。"
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
        self.thread = None
        self._refresh_video_list()
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
        self._set_workflow_state("empty")
        self._select_page("home")

    def append_log(self, message: str) -> None:
        self.log_messages.append(message)
        self._set_log_texts()

    def _set_running(self, running: bool) -> None:
        self.select_video_button.setEnabled(not running)
        self.select_folder_link.setEnabled(not running)
        self.clear_button.setEnabled(not running)
        self.output_browse.setEnabled(not running)
        self.settings_output_browse.setEnabled(not running)
        self.start_button.setEnabled(not running)
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

    def _set_workflow_state(self, state: str) -> None:
        self.workflow_state = state
        index = {
            "empty": self.home_stack.indexOf(self.empty_page),
            "ready": self.home_stack.indexOf(self.ready_page),
            "running": self.home_stack.indexOf(self.running_page),
            "complete": self.home_stack.indexOf(self.complete_page),
            "error": self.home_stack.indexOf(self.error_page),
        }[state]
        self.home_stack.setCurrentIndex(index)

    def _select_page(self, page: str) -> None:
        mapping = {
            "home": self.home_page,
            "history": self.history_page,
            "settings": self.settings_page,
            "help": self.help_page,
        }
        self.page_stack.setCurrentWidget(mapping[page])
        all_buttons = {
            "home": [self.nav_home],
            "history": [self.nav_history],
            "settings": [self.nav_settings],
            "help": [self.nav_help],
        }
        for name, buttons in all_buttons.items():
            for button in buttons:
                button.setChecked(name == page)
        if page == "history":
            self._refresh_history()

    def _refresh_video_list(self) -> None:
        clear_layout(self.video_list_layout)
        if not self.videos:
            self.video_list_layout.addWidget(subtitle(tr("動画はまだ追加されていません。")))
            return
        for path in self.videos:
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

    def _retry_processing(self) -> None:
        if self.videos:
            self._set_workflow_state("ready")
            self._select_page("home")

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

    def _naming_changed(self) -> None:
        self._update_naming_preview()
        self._save_settings()

    def _update_naming_preview(self, moment: datetime | None = None) -> None:
        self.custom_output_name.setVisible(self.naming_mode.currentData() == "custom")
        combined = self.output_mode_combo.currentData() == "combined"
        paths = self.videos if combined else self.videos[:1]
        try:
            stem = output_stem(paths, self._naming_config(moment), combined=combined)
            self.naming_preview.setText(tr("出力名の例") + f": {stem}_slides.pdf / {stem}_transcript.txt")
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
        path = Path(self.output_path.text())
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
        self.add_paths(paths)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_responsive()

    def closeEvent(self, event) -> None:
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
        event.accept()

    def _update_responsive(self) -> None:
        self.sidebar.setVisible(True)
        self.brand_label.setVisible(True)
        self.sidebar.setFixedWidth(LAYOUT.sidebar_wide)
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

    def _apply_ui_language(self) -> None:
        set_ui_language(str(self.settings_widget.ui_language_combo.currentData()))
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


def system_prefers_dark() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return int(value) == 0
    except OSError:
        return False


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


def separator() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setFrameShadow(QFrame.Plain)
    return line


def clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        child_layout = item.layout()
        if widget is not None:
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
