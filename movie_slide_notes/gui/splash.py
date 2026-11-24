"""A small startup splash shown while the main window is being built.

This module must stay light: it is imported before the heavy GUI modules so the
splash can appear almost immediately after the user launches the app.
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import QApplication, QLabel

SPLASH_WIDTH = 460
SPLASH_HEIGHT = 270


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


def theme_is_dark(theme: str) -> bool:
    return theme == "dark" or (theme == "system" and system_prefers_dark())


class StartupSplash(QLabel):
    """Frameless window showing the splash picture.

    QSplashScreen is avoided on purpose: showing one takes about a second on
    Windows, far longer than an ordinary frameless window.
    """

    def __init__(self, pixmap: QPixmap):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setPixmap(pixmap)
        self.setFixedSize(SPLASH_WIDTH, SPLASH_HEIGHT)
        screen = QApplication.primaryScreen()
        if screen is not None:
            self.move(screen.availableGeometry().center() - self.rect().center())


TAGLINE = "動画もPDFも、読める資料に。"


def _palette(dark: bool) -> dict[str, str]:
    if dark:
        return {
            "top": "#0b1016",
            "bottom": "#0d2224",
            "border": "#1f3a3c",
            "glow": "#2dd4bf",
            "title": "#f1f5f9",
            "muted": "#8ea3b0",
            "track": "#1b2a30",
            "accent_a": "#2dd4bf",
            "accent_b": "#60a5fa",
        }
    return {
        "top": "#ffffff",
        "bottom": "#e9f6f4",
        "border": "#cfe3e0",
        "glow": "#14b8a6",
        "title": "#0f172a",
        "muted": "#5b6b78",
        "track": "#dbe9e7",
        "accent_a": "#0f8f8a",
        "accent_b": "#3b82f6",
    }


def show_startup_splash(
    app: QApplication,
    icon_path: Path | None,
    dark: bool,
    message: str,
    version: str = "",
    tagline: str = "",
) -> StartupSplash:
    """Paint and show the splash, returning it so the caller can close() it."""
    screen = app.primaryScreen()
    ratio = screen.devicePixelRatio() if screen is not None else 1.0
    pixmap = QPixmap(int(SPLASH_WIDTH * ratio), int(SPLASH_HEIGHT * ratio))
    pixmap.setDevicePixelRatio(ratio)
    colors = _palette(dark)
    pixmap.fill(QColor(colors["top"]))

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    painter.setRenderHint(QPainter.TextAntialiasing)

    background = QLinearGradient(0, 0, SPLASH_WIDTH * 0.35, SPLASH_HEIGHT)
    background.setColorAt(0.0, QColor(colors["top"]))
    background.setColorAt(1.0, QColor(colors["bottom"]))
    painter.fillRect(QRectF(0, 0, SPLASH_WIDTH, SPLASH_HEIGHT), background)

    # Soft glow behind the icon.
    icon_size = 72
    icon_center = QPointF(SPLASH_WIDTH / 2, 82)
    glow = QRadialGradient(icon_center, 110)
    glow_color = QColor(colors["glow"])
    glow_color.setAlpha(70 if dark else 46)
    glow.setColorAt(0.0, glow_color)
    glow_color.setAlpha(0)
    glow.setColorAt(1.0, glow_color)
    painter.fillRect(QRectF(0, 0, SPLASH_WIDTH, SPLASH_HEIGHT), glow)

    painter.setPen(QPen(QColor(colors["border"]), 1))
    painter.setBrush(Qt.NoBrush)
    painter.drawRect(QRectF(0.5, 0.5, SPLASH_WIDTH - 1, SPLASH_HEIGHT - 1))

    if icon_path is not None and icon_path.exists():
        icon = QPixmap(str(icon_path)).scaled(
            int(icon_size * ratio),
            int(icon_size * ratio),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        icon.setDevicePixelRatio(ratio)
        painter.drawPixmap(
            int(icon_center.x() - icon_size / 2), int(icon_center.y() - icon_size / 2), icon
        )

    heading = QFont("Segoe UI Variable Display")
    heading.setPixelSize(32)
    heading.setWeight(QFont.DemiBold)
    heading.setLetterSpacing(QFont.PercentageSpacing, 103)
    painter.setFont(heading)
    painter.setPen(QColor(colors["title"]))
    painter.drawText(QRectF(0, 130, SPLASH_WIDTH, 42), Qt.AlignHCenter | Qt.AlignVCenter, "Lectavo")

    tagline_font = QFont("Yu Gothic UI")
    tagline_font.setPixelSize(13)
    painter.setFont(tagline_font)
    painter.setPen(QColor(colors["muted"]))
    painter.drawText(
        QRectF(0, 172, SPLASH_WIDTH, 22), Qt.AlignHCenter | Qt.AlignVCenter, tagline or TAGLINE
    )

    # Thin progress track with an accent segment.
    track = QRectF(40, SPLASH_HEIGHT - 46, SPLASH_WIDTH - 80, 3)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(colors["track"]))
    painter.drawRoundedRect(track, 1.5, 1.5)
    segment = QRectF(track.left(), track.top(), track.width() * 0.42, track.height())
    accent = QLinearGradient(segment.topLeft(), segment.topRight())
    accent.setColorAt(0.0, QColor(colors["accent_a"]))
    accent.setColorAt(1.0, QColor(colors["accent_b"]))
    painter.setBrush(accent)
    painter.drawRoundedRect(segment, 1.5, 1.5)

    caption = QFont("Yu Gothic UI")
    caption.setPixelSize(11)
    painter.setFont(caption)
    painter.setPen(QColor(colors["muted"]))
    footer = QRectF(40, SPLASH_HEIGHT - 36, SPLASH_WIDTH - 80, 22)
    painter.drawText(footer, Qt.AlignLeft | Qt.AlignVCenter, message)
    if version:
        painter.drawText(footer, Qt.AlignRight | Qt.AlignVCenter, f"v{version}")
    painter.end()

    splash = StartupSplash(pixmap)
    splash.show()
    app.processEvents()
    return splash
