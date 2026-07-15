from __future__ import annotations

from dataclasses import dataclass

from movie_slide_notes.paths import resource_path


@dataclass(frozen=True)
class Radius:
    small: int = 6
    control: int = 8
    panel: int = 12
    popup: int = 10


@dataclass(frozen=True)
class Spacing:
    xs: int = 4
    sm: int = 8
    md: int = 12
    lg: int = 16
    xl: int = 24
    xxl: int = 32


@dataclass(frozen=True)
class Control:
    compact: int = 32
    default: int = 36
    large: int = 40


@dataclass(frozen=True)
class Layout:
    sidebar_wide: int = 200
    sidebar_compact: int = 72
    inspector_width: int = 300


@dataclass(frozen=True)
class Colors:
    app_background: str
    surface: str
    elevated_surface: str
    primary_text: str
    secondary_text: str
    muted_text: str
    separator: str
    control_border: str
    control_hover: str
    accent: str
    accent_hover: str
    accent_soft: str
    success: str
    warning: str
    error: str
    progress_track: str


RADIUS = Radius()
SPACING = Spacing()
CONTROL = Control()
LAYOUT = Layout()


LIGHT = Colors(
    app_background="#f7f8fa",
    surface="#ffffff",
    elevated_surface="#fbfcfd",
    primary_text="#111827",
    secondary_text="#4b5563",
    muted_text="#6b7280",
    separator="#e5e7eb",
    control_border="#d1d7de",
    control_hover="#eef7f7",
    accent="#0f8f8a",
    accent_hover="#0b7f7a",
    accent_soft="#e7f5f4",
    success="#0f766e",
    warning="#b45309",
    error="#b42318",
    progress_track="#eef2f6",
)

DARK = Colors(
    app_background="#0f141c",
    surface="#151b24",
    elevated_surface="#1b2330",
    primary_text="#eef2f7",
    secondary_text="#cbd5e1",
    muted_text="#94a3b8",
    separator="#2b3646",
    control_border="#3b4758",
    control_hover="#22303f",
    accent="#2dd4bf",
    accent_hover="#14b8a6",
    accent_soft="#123d3a",
    success="#5eead4",
    warning="#fbbf24",
    error="#f87171",
    progress_track="#111827",
)


def build_stylesheet(dark: bool) -> str:
    colors = DARK if dark else LIGHT
    check_icon = resource_path("assets", "icons", "check.svg").as_posix()
    chevron_icon = resource_path("assets", "icons", "chevron-down.svg").as_posix()
    return f"""
QWidget {{
    color: {colors.primary_text};
    font-family: "Yu Gothic UI", "Meiryo", "Segoe UI", sans-serif;
    font-size: 13px;
    background: transparent;
}}
QMainWindow {{
    background: {colors.app_background};
}}
QWidget#PageContent, QScrollArea#PageScroll {{
    background: {colors.app_background};
}}
QScrollArea#PageScroll > QWidget > QWidget {{
    background: {colors.app_background};
}}
QFrame#Sidebar {{
    background: {colors.surface};
    border-right: 1px solid {colors.separator};
}}
QFrame#Inspector {{
    background: {colors.surface};
    border-left: 1px solid {colors.separator};
}}
QFrame#DropZone {{
    background: {colors.surface};
    border: 1px dashed {colors.muted_text};
    border-radius: {RADIUS.panel}px;
}}
QFrame#ListRow {{
    background: transparent;
    border-bottom: 1px solid {colors.separator};
}}
QFrame#SoftPanel {{
    background: {colors.elevated_surface};
    border: 1px solid {colors.separator};
    border-radius: {RADIUS.panel}px;
}}
QLabel#AppName {{
    font-size: 15px;
    font-weight: 600;
}}
QLabel#PageTitle {{
    font-size: 25px;
    font-weight: 650;
}}
QLabel#SectionTitle {{
    font-size: 16px;
    font-weight: 600;
}}
QLabel#Subtitle, QLabel#SecondaryText, QLabel#Hint {{
    color: {colors.secondary_text};
}}
QLabel#MutedText {{
    color: {colors.muted_text};
}}
QLabel#SuccessText {{
    color: {colors.success};
    font-weight: 600;
}}
QLabel#ErrorText {{
    color: {colors.error};
    font-weight: 600;
}}
QPushButton {{
    min-height: {CONTROL.default}px;
    border: 1px solid {colors.control_border};
    border-radius: {RADIUS.control}px;
    padding: 0 14px;
    background: {colors.surface};
    color: {colors.primary_text};
}}
QPushButton:hover {{
    background: {colors.control_hover};
}}
QPushButton:pressed {{
    background: {colors.accent_soft};
}}
QPushButton:disabled {{
    color: {colors.muted_text};
    background: {colors.elevated_surface};
}}
QPushButton#PrimaryButton {{
    background: {colors.accent};
    color: white;
    border-color: {colors.accent};
    font-weight: 600;
}}
QPushButton#PrimaryButton:hover {{
    background: {colors.accent_hover};
    border-color: {colors.accent_hover};
}}
QPushButton#LinkButton {{
    border: none;
    background: transparent;
    color: {colors.accent};
    padding: 0 4px;
    min-height: {CONTROL.compact}px;
}}
QPushButton#NavButton {{
    border: none;
    border-radius: {RADIUS.control}px;
    background: transparent;
    text-align: left;
    padding: 0 14px;
    min-height: {CONTROL.large}px;
}}
QPushButton#NavButton:hover {{
    background: {colors.control_hover};
}}
QPushButton#NavButton:checked {{
    background: {colors.accent_soft};
    color: {colors.accent};
    font-weight: 600;
}}
QToolButton {{
    min-height: {CONTROL.default}px;
    border: 1px solid {colors.control_border};
    border-radius: {RADIUS.control}px;
    padding: 0 12px;
    background: {colors.surface};
}}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    min-height: {CONTROL.default}px;
    border: 1px solid {colors.control_border};
    border-radius: {RADIUS.control}px;
    padding: 0 10px;
    background: {colors.surface};
    selection-background-color: {colors.accent};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {colors.accent};
}}
QComboBox {{
    padding-right: 32px;
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 30px;
    border: none;
}}
QComboBox::down-arrow {{
    image: url("{chevron_icon}");
    width: 16px;
    height: 16px;
    margin-right: 8px;
}}
QComboBox:disabled, QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    color: {colors.muted_text};
    background: {colors.elevated_surface};
}}
QComboBox QAbstractItemView {{
    background: {colors.surface};
    border: 1px solid {colors.control_border};
    border-radius: {RADIUS.popup}px;
    padding: 4px;
    outline: 0;
    selection-background-color: {colors.accent_soft};
    selection-color: {colors.primary_text};
}}
QComboBox QAbstractItemView::item {{
    min-height: 32px;
    padding: 6px 10px;
    border-radius: {RADIUS.small}px;
}}
QComboBox QAbstractItemView::item:hover {{
    background: {colors.control_hover};
}}
QCheckBox {{
    spacing: 10px;
    min-height: 30px;
}}
QCheckBox::indicator {{
    width: 19px;
    height: 19px;
    border: 1.5px solid {colors.control_border};
    border-radius: 6px;
    background: {colors.surface};
}}
QCheckBox::indicator:hover {{
    border-color: {colors.accent};
}}
QCheckBox::indicator:checked {{
    background: {colors.surface};
    border-color: {colors.control_border};
    image: url("{check_icon}");
}}
QCheckBox::indicator:disabled {{
    background: {colors.elevated_surface};
    border-color: {colors.separator};
}}
QProgressBar {{
    border: none;
    border-radius: {RADIUS.small}px;
    text-align: center;
    height: 12px;
    background: {colors.progress_track};
}}
QProgressBar::chunk {{
    background: {colors.accent};
    border-radius: {RADIUS.small}px;
}}
QPlainTextEdit, QTextEdit {{
    border: 1px solid {colors.separator};
    border-radius: {RADIUS.control}px;
    background: {colors.surface};
    color: {colors.primary_text};
    padding: 8px;
}}
QScrollArea {{
    border: none;
    background: {colors.app_background};
}}
QMenuBar {{
    background: {colors.app_background};
}}
QMenu {{
    background: {colors.surface};
    border: 1px solid {colors.separator};
    border-radius: {RADIUS.popup}px;
}}
QMenu::item:selected {{
    background: {colors.accent_soft};
}}
"""
