from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QGridLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from movie_slide_notes.core.processor import PRESETS
from movie_slide_notes.duplicate_filter import DuplicateConfig
from movie_slide_notes.gui.controls import NoWheelComboBox as QComboBox
from movie_slide_notes.gui.controls import NoWheelDoubleSpinBox as QDoubleSpinBox
from movie_slide_notes.gui.controls import NoWheelSpinBox as QSpinBox
from movie_slide_notes.gui.i18n import tr
from movie_slide_notes.slide_detector import SlideDetectionConfig
from movie_slide_notes.transcriber import TranscriptionConfig, inspect_cuda_runtime


class ProcessingSettingsWidget(QWidget):
    settings_changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._loading = False
        self._build_ui()
        self._connect_signals()
        self.apply_preset("standard")

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(20)

        root.addWidget(section_title("処理"))
        processing_form = QFormLayout()
        processing_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        processing_form.setLabelAlignment(Qt.AlignRight)

        self.preset_combo = QComboBox()
        self.preset_combo.addItem("高速", "conservative")
        self.preset_combo.addItem("標準", "standard")
        self.preset_combo.addItem("高精度", "sensitive")
        self.preset_combo.setToolTip(
            "通常は標準で十分です。高精度は短時間だけ表示されたスライドを拾いやすくなります。"
        )
        processing_form.addRow("処理品質", self.preset_combo)
        root.addLayout(processing_form)

        self.slide_detail_button = disclosure_button("スライド検出の詳細")
        self.slide_detail_panel = QWidget()
        detail_layout = QFormLayout(self.slide_detail_panel)
        detail_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        detail_layout.setLabelAlignment(Qt.AlignRight)

        self.interval = self._double_spin(0.1, 60.0, 1.0, 1)
        self.interval.setToolTip(
            "値を小さくすると短時間だけ表示されたスライドを拾いやすくなりますが、処理時間が増加します。"
        )
        detail_layout.addRow("フレーム確認間隔", self.interval)

        self.min_gap = self._double_spin(0.0, 300.0, 1.0, 1)
        self.min_gap.setToolTip(
            "スライド保存後に次の保存を抑制する秒数です。アニメーション途中の連続保存を減らします。"
        )
        detail_layout.addRow("最小保存間隔", self.min_gap)

        self.compare_width = self._int_spin(120, 1280, 320)
        self.compare_width.setToolTip(
            "画像差分のために縮小する幅です。大きいほど精密ですが処理時間が増えます。"
        )
        detail_layout.addRow("比較画像幅", self.compare_width)

        self.diff_threshold = self._int_spin(1, 255, 30)
        self.diff_threshold.setToolTip(
            "ピクセル単位で変化とみなす明るさ差です。上げると小さな色変化を無視します。"
        )
        detail_layout.addRow("ピクセル差分しきい値", self.diff_threshold)

        self.min_component_area = self._int_spin(1, 20000, 800)
        self.min_component_area.setToolTip(
            "小さい差分領域を無視する面積です。上げるとポインターやカーソルを誤検出しにくくなります。"
        )
        detail_layout.addRow("最小差分領域", self.min_component_area)

        self.diff_ratio_threshold = self._double_spin(0.001, 1.0, 0.025, 3)
        self.diff_ratio_threshold.setToolTip(
            "画面全体のうち、どれだけ変化したら新しいスライドとみなすかです。"
        )
        detail_layout.addRow("全体差分率しきい値", self.diff_ratio_threshold)

        self.ssim_threshold = self._double_spin(0.1, 1.0, 0.92, 3)
        self.ssim_threshold.setToolTip(
            "画像構造の類似度です。上げると似たスライドも検出しやすくなります。"
        )
        detail_layout.addRow("SSIMしきい値", self.ssim_threshold)

        region_box = QWidget()
        region_layout = QGridLayout(region_box)
        region_layout.setContentsMargins(0, 0, 0, 0)
        region_layout.setHorizontalSpacing(8)
        self.region_x = self._double_spin(0.0, 1.0, 0.75, 2)
        self.region_y = self._double_spin(0.0, 1.0, 0.80, 2)
        self.region_w = self._double_spin(0.01, 1.0, 0.25, 2)
        self.region_h = self._double_spin(0.01, 1.0, 0.20, 2)
        for label, widget, col in [
            ("X", self.region_x, 0),
            ("Y", self.region_y, 1),
            ("幅", self.region_w, 2),
            ("高さ", self.region_h, 3),
        ]:
            region_layout.addWidget(QLabel(label), 0, col)
            region_layout.addWidget(widget, 1, col)
        region_box.setToolTip(
            "画面内のスライド領域を自動検出し、その内側でページ番号などがある領域を比率指定します。"
            "初期値は 0.75, 0.80, 0.25, 0.20 です。"
        )
        detail_layout.addRow("ページ番号領域", region_box)

        self.page_diff_ratio_threshold = self._double_spin(0.001, 1.0, 0.012, 3)
        self.page_diff_ratio_threshold.setToolTip(
            "ページ番号領域だけを見た差分率です。下げるとページ番号変化を拾いやすくなります。"
        )
        detail_layout.addRow("ページ領域差分率", self.page_diff_ratio_threshold)

        self.page_min_component_area = self._int_spin(1, 2000, 8)
        self.page_min_component_area.setToolTip("ページ番号領域内で無視しない差分の最小面積です。")
        detail_layout.addRow("ページ領域最小成分面積", self.page_min_component_area)

        self.phash_threshold = self._int_spin(0, 64, 8)
        self.phash_threshold.setToolTip(
            "小さいほど重複判定が厳しくなります。pHashだけで除外せず、画像構造も似ている場合だけ重複とみなします。"
        )
        detail_layout.addRow("pHash重複判定距離", self.phash_threshold)

        self.duplicate_ssim_threshold = self._double_spin(0.1, 1.0, 0.97, 3)
        self.duplicate_ssim_threshold.setToolTip(
            "大きいほど、ほぼ完全一致に近い画像だけを重複とみなします。"
        )
        detail_layout.addRow("重複SSIMしきい値", self.duplicate_ssim_threshold)
        self.slide_detail_panel.setVisible(False)
        root.addWidget(self.slide_detail_button)
        root.addWidget(self.slide_detail_panel)

        root.addWidget(separator())
        root.addWidget(section_title("文字起こし"))
        transcript_form = QFormLayout()
        transcript_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        transcript_form.setLabelAlignment(Qt.AlignRight)

        self.language_combo = QComboBox()
        self.language_combo.addItem("自然に検出", "auto")
        self.language_combo.addItem("日本語", "ja")
        self.language_combo.addItem("英語", "en")
        transcript_form.addRow("言語", self.language_combo)

        self.model_combo = QComboBox()
        self.model_combo.addItem("高速", "tiny")
        self.model_combo.addItem("軽量", "base")
        self.model_combo.addItem("標準", "small")
        self.model_combo.addItem("高精度", "medium")
        self.model_combo.addItem("最高精度", "large-v3")
        transcript_form.addRow("品質", self.model_combo)
        root.addLayout(transcript_form)

        root.addWidget(separator())
        root.addWidget(section_title("外観"))
        appearance_form = QFormLayout()
        appearance_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        appearance_form.setLabelAlignment(Qt.AlignRight)
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("システム設定に追従", "system")
        self.theme_combo.addItem("ライト", "light")
        self.theme_combo.addItem("ダーク", "dark")
        self.ui_language_combo = QComboBox()
        self.ui_language_combo.addItem("日本語", "ja")
        self.ui_language_combo.addItem("英語", "en")
        appearance_form.addRow("表示言語", self.ui_language_combo)
        appearance_form.addRow("テーマ", self.theme_combo)
        root.addLayout(appearance_form)

        root.addWidget(separator())
        self.developer_button = disclosure_button("開発者向け")
        self.developer_panel = QWidget()
        developer_layout = QFormLayout(self.developer_panel)
        developer_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        developer_layout.setLabelAlignment(Qt.AlignRight)

        self.device_combo = QComboBox()
        self.device_combo.addItem("CPU", "cpu")
        self.device_combo.addItem("GPU（CUDA）", "cuda")
        cuda_status = inspect_cuda_runtime()
        if not cuda_status.available:
            self.device_combo.setItemText(1, "GPU（現在利用不可）")
            self.device_combo.setItemData(1, cuda_status.message, Qt.ToolTipRole)
        developer_layout.addRow("処理デバイス", self.device_combo)

        self.compute_type_combo = QComboBox()
        for compute_type in ["int8", "float16", "float32"]:
            self.compute_type_combo.addItem(compute_type, compute_type)
        developer_layout.addRow("計算精度", self.compute_type_combo)

        self.developer_panel.setVisible(False)
        root.addWidget(self.developer_button)
        root.addWidget(self.developer_panel)
        root.addStretch(1)

    def _connect_signals(self) -> None:
        self.preset_combo.currentIndexChanged.connect(self._preset_changed)
        self.slide_detail_button.toggled.connect(self._slide_detail_toggled)
        self.developer_button.toggled.connect(self._developer_toggled)
        for widget in self.findChildren(QSpinBox):
            widget.valueChanged.connect(self._emit_changed)
        for widget in self.findChildren(QDoubleSpinBox):
            widget.valueChanged.connect(self._emit_changed)
        for widget in self.findChildren(QComboBox):
            widget.currentIndexChanged.connect(self._emit_changed)

    def _emit_changed(self) -> None:
        if not self._loading:
            self.settings_changed.emit()

    def _preset_changed(self) -> None:
        self.apply_preset(str(self.preset_combo.currentData()))
        self._emit_changed()

    def _slide_detail_toggled(self, visible: bool) -> None:
        self.slide_detail_panel.setVisible(visible)
        self.slide_detail_button.setText(("v " if visible else "> ") + tr("スライド検出の詳細"))

    def _developer_toggled(self, visible: bool) -> None:
        self.developer_panel.setVisible(visible)
        self.developer_button.setText(("v " if visible else "> ") + tr("開発者向け"))

    def apply_preset(self, preset_name: str) -> None:
        base = SlideDetectionConfig()
        values = PRESETS.get(preset_name, {})
        config = replace(base, **values)
        self._set_slide_config(config)
        self.phash_threshold.setValue(8)
        self.duplicate_ssim_threshold.setValue(0.97)

    def slide_config(self) -> SlideDetectionConfig:
        return SlideDetectionConfig(
            interval=self.interval.value(),
            min_gap=self.min_gap.value(),
            compare_width=self.compare_width.value(),
            diff_threshold=self.diff_threshold.value(),
            min_component_area=self.min_component_area.value(),
            diff_ratio_threshold=self.diff_ratio_threshold.value(),
            ssim_threshold=self.ssim_threshold.value(),
            page_region=(
                self.region_x.value(),
                self.region_y.value(),
                self.region_w.value(),
                self.region_h.value(),
            ),
            page_diff_ratio_threshold=self.page_diff_ratio_threshold.value(),
            page_min_component_area=self.page_min_component_area.value(),
        )

    def duplicate_config(self) -> DuplicateConfig:
        return DuplicateConfig(
            phash_threshold=self.phash_threshold.value(),
            ssim_threshold=self.duplicate_ssim_threshold.value(),
            compare_width=self.compare_width.value(),
        )

    def transcription_config(self) -> TranscriptionConfig:
        return TranscriptionConfig(
            model_size=self.model_combo.currentData(),
            language=self.language_combo.currentData(),
            device=self.device_combo.currentData(),
            compute_type=self.compute_type_combo.currentData(),
        )

    def load_settings(self, settings: QSettings) -> None:
        self._loading = True
        try:
            slide_defaults_migration_key = "slide_detection_defaults_v2_migrated"
            if not settings.value(slide_defaults_migration_key, False, type=bool):
                stored_preset = str(settings.value("preset", "standard"))
                stored_min_gap = float(settings.value("min_gap", 5.0))
                if stored_preset in {"standard", "sensitive"} and stored_min_gap == 5.0:
                    settings.setValue("min_gap", 1.0)
                settings.setValue(slide_defaults_migration_key, True)

            quality_migration_key = "slide_detection_quality_v3_migrated"
            if not settings.value(quality_migration_key, False, type=bool):
                legacy_fast_values = {
                    "interval": 1.0,
                    "min_gap": 7.0,
                    "compare_width": 320,
                    "diff_threshold": 30,
                    "min_component_area": 1500,
                    "diff_ratio_threshold": 0.04,
                    "ssim_threshold": 0.92,
                    "region_x": 0.75,
                    "region_y": 0.80,
                    "region_w": 0.25,
                    "region_h": 0.20,
                    "page_diff_ratio_threshold": 0.025,
                    "page_min_component_area": 8,
                    "phash_threshold": 8,
                    "duplicate_ssim_threshold": 0.97,
                }
                legacy_fast_unchanged = str(settings.value("preset", "standard")) == "conservative"
                for key, expected in legacy_fast_values.items():
                    actual = float(settings.value(key, expected))
                    if abs(actual - float(expected)) > 1e-9:
                        legacy_fast_unchanged = False
                        break
                if legacy_fast_unchanged:
                    standard = SlideDetectionConfig()
                    settings.setValue("preset", "standard")
                    settings.setValue("interval", standard.interval)
                    settings.setValue("min_gap", standard.min_gap)
                    settings.setValue("compare_width", standard.compare_width)
                    settings.setValue("diff_threshold", standard.diff_threshold)
                    settings.setValue("min_component_area", standard.min_component_area)
                    settings.setValue("diff_ratio_threshold", standard.diff_ratio_threshold)
                    settings.setValue("ssim_threshold", standard.ssim_threshold)
                    settings.setValue("region_x", standard.page_region[0])
                    settings.setValue("region_y", standard.page_region[1])
                    settings.setValue("region_w", standard.page_region[2])
                    settings.setValue("region_h", standard.page_region[3])
                    settings.setValue(
                        "page_diff_ratio_threshold",
                        standard.page_diff_ratio_threshold,
                    )
                    settings.setValue("page_min_component_area", standard.page_min_component_area)
                    settings.setValue("phash_threshold", 8)
                    settings.setValue("duplicate_ssim_threshold", 0.97)
                settings.setValue(quality_migration_key, True)
            preset = str(settings.value("preset", "standard"))
            self._set_combo(self.preset_combo, preset)
            self.apply_preset(preset)
            self.interval.setValue(float(settings.value("interval", self.interval.value())))
            self.min_gap.setValue(float(settings.value("min_gap", self.min_gap.value())))
            self.compare_width.setValue(
                int(settings.value("compare_width", self.compare_width.value()))
            )
            self.diff_threshold.setValue(
                int(settings.value("diff_threshold", self.diff_threshold.value()))
            )
            self.min_component_area.setValue(
                int(settings.value("min_component_area", self.min_component_area.value()))
            )
            self.diff_ratio_threshold.setValue(
                float(settings.value("diff_ratio_threshold", self.diff_ratio_threshold.value()))
            )
            self.ssim_threshold.setValue(
                float(settings.value("ssim_threshold", self.ssim_threshold.value()))
            )
            self.region_x.setValue(float(settings.value("region_x", self.region_x.value())))
            self.region_y.setValue(float(settings.value("region_y", self.region_y.value())))
            self.region_w.setValue(float(settings.value("region_w", self.region_w.value())))
            self.region_h.setValue(float(settings.value("region_h", self.region_h.value())))
            self.page_diff_ratio_threshold.setValue(
                float(
                    settings.value(
                        "page_diff_ratio_threshold", self.page_diff_ratio_threshold.value()
                    )
                )
            )
            self.page_min_component_area.setValue(
                int(settings.value("page_min_component_area", self.page_min_component_area.value()))
            )
            self.phash_threshold.setValue(
                int(settings.value("phash_threshold", self.phash_threshold.value()))
            )
            self.duplicate_ssim_threshold.setValue(
                float(
                    settings.value(
                        "duplicate_ssim_threshold", self.duplicate_ssim_threshold.value()
                    )
                )
            )
            language_migration_key = "natural_language_default_migrated"
            if not settings.value(language_migration_key, False, type=bool):
                settings.setValue("language", "auto")
                settings.setValue(language_migration_key, True)
            self._set_combo(self.language_combo, str(settings.value("language", "auto")))
            self._set_combo(self.model_combo, str(settings.value("model", "small")))
            self._set_combo(self.device_combo, str(settings.value("device", "cpu")))
            self._set_combo(self.compute_type_combo, str(settings.value("compute_type", "int8")))
            self._set_combo(self.theme_combo, str(settings.value("theme", "system")))
            self._set_combo(self.ui_language_combo, str(settings.value("ui_language", "ja")))
        finally:
            self._loading = False

    def save_settings(self, settings: QSettings) -> None:
        settings.setValue("preset", self.preset_combo.currentData())
        config = self.slide_config()
        settings.setValue("interval", config.interval)
        settings.setValue("min_gap", config.min_gap)
        settings.setValue("compare_width", config.compare_width)
        settings.setValue("diff_threshold", config.diff_threshold)
        settings.setValue("min_component_area", config.min_component_area)
        settings.setValue("diff_ratio_threshold", config.diff_ratio_threshold)
        settings.setValue("ssim_threshold", config.ssim_threshold)
        settings.setValue("region_x", config.page_region[0])
        settings.setValue("region_y", config.page_region[1])
        settings.setValue("region_w", config.page_region[2])
        settings.setValue("region_h", config.page_region[3])
        settings.setValue("page_diff_ratio_threshold", config.page_diff_ratio_threshold)
        settings.setValue("page_min_component_area", config.page_min_component_area)
        settings.setValue("phash_threshold", self.phash_threshold.value())
        settings.setValue("duplicate_ssim_threshold", self.duplicate_ssim_threshold.value())
        settings.setValue("language", self.language_combo.currentData())
        settings.setValue("model", self.model_combo.currentData())
        settings.setValue("device", self.device_combo.currentData())
        settings.setValue("compute_type", self.compute_type_combo.currentData())
        settings.setValue("theme", self.theme_combo.currentData())
        settings.setValue("ui_language", self.ui_language_combo.currentData())

    def _set_slide_config(self, config: SlideDetectionConfig) -> None:
        self.interval.setValue(config.interval)
        self.min_gap.setValue(config.min_gap)
        self.compare_width.setValue(config.compare_width)
        self.diff_threshold.setValue(config.diff_threshold)
        self.min_component_area.setValue(config.min_component_area)
        self.diff_ratio_threshold.setValue(config.diff_ratio_threshold)
        self.ssim_threshold.setValue(config.ssim_threshold)
        self.region_x.setValue(config.page_region[0])
        self.region_y.setValue(config.page_region[1])
        self.region_w.setValue(config.page_region[2])
        self.region_h.setValue(config.page_region[3])
        self.page_diff_ratio_threshold.setValue(config.page_diff_ratio_threshold)
        self.page_min_component_area.setValue(config.page_min_component_area)

    @staticmethod
    def _set_combo(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    @staticmethod
    def _double_spin(minimum: float, maximum: float, value: float, decimals: int) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setSingleStep(10**-decimals)
        spin.setValue(value)
        spin.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        return spin

    @staticmethod
    def _int_spin(minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        return spin


def section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("SectionTitle")
    return label


def separator() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setFrameShadow(QFrame.Plain)
    return line


def disclosure_button(text: str) -> QToolButton:
    button = QToolButton()
    button.setCheckable(True)
    button.setToolButtonStyle(Qt.ToolButtonTextOnly)
    button.setText("> " + text)
    return button
