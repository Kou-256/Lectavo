"""A small startup splash shown while the main window is being built.

This module must stay light: it is imported before the heavy GUI modules so the
splash can appear almost immediately after the user launches the app.
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QLabel

SPLASH_WIDTH = 360
SPLASH_HEIGHT = 210


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


def show_startup_splash(
    app: QApplication, icon_path: Path | None, dark: bool, message: str
) -> StartupSplash:
    """Paint and show the splash, returning it so the caller can close() it."""
    screen = app.primaryScreen()
    ratio = screen.devicePixelRatio() if screen is not None else 1.0
    pixmap = QPixmap(int(SPLASH_WIDTH * ratio), int(SPLASH_HEIGHT * ratio))
    pixmap.setDevicePixelRatio(ratio)
    background, border, title, muted = (
        ("#0f141c", "#2b3646", "#eef2f7", "#94a3b8")
        if dark
        else ("#f7f8fa", "#d1d7de", "#111827", "#6b7280")
    )
    pixmap.fill(QColor(background))

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    painter.setPen(QPen(QColor(border), 1))
    painter.setBrush(Qt.NoBrush)
    painter.drawRect(QRectF(0.5, 0.5, SPLASH_WIDTH - 1, SPLASH_HEIGHT - 1))

    icon_size = 84
    if icon_path is not None and icon_path.exists():
        icon = QPixmap(str(icon_path)).scaled(
            int(icon_size * ratio),
            int(icon_size * ratio),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        icon.setDevicePixelRatio(ratio)
        painter.drawPixmap((SPLASH_WIDTH - icon_size) // 2, 30, icon)

    heading = QFont("Yu Gothic UI")
    heading.setPixelSize(24)
    heading.setBold(True)
    painter.setFont(heading)
    painter.setPen(QColor(title))
    painter.drawText(QRectF(0, 124, SPLASH_WIDTH, 36), Qt.AlignHCenter | Qt.AlignVCenter, "Lectavo")

    caption = QFont("Yu Gothic UI")
    caption.setPixelSize(13)
    painter.setFont(caption)
    painter.setPen(QColor(muted))
    painter.drawText(QRectF(0, 162, SPLASH_WIDTH, 24), Qt.AlignHCenter | Qt.AlignVCenter, message)
    painter.end()

    splash = StartupSplash(pixmap)
    splash.show()
    app.processEvents()
    return splash
