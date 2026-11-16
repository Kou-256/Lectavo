"""Crisp, tintable SVG icons for the desktop UI."""

from __future__ import annotations

import re
from functools import cache

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from movie_slide_notes.paths import resource_path

BASE_COLOR = "#94a3a8"
ACTIVE_COLOR = "#14a39b"
_BASE_PATTERN = re.compile(re.escape(BASE_COLOR), re.IGNORECASE)


@cache
def _svg_source(name: str) -> str:
    return resource_path("assets", "icons", f"{name}.svg").read_text(encoding="utf-8")


@cache
def icon_pixmap(name: str, size: int, color: str | None = None) -> QPixmap:
    """Render ``name`` at ``size`` logical pixels, optionally recolored.

    Rendering at 2x keeps icons sharp on high-DPI displays.
    """
    source = _svg_source(name)
    if color:
        source = _BASE_PATTERN.sub(color, source)
    renderer = QSvgRenderer(QByteArray(source.encode("utf-8")))
    scale = 2
    pixmap = QPixmap(size * scale, size * scale)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size * scale, size * scale))
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return pixmap


def icon(name: str, size: int = 20, color: str | None = None) -> QIcon:
    return QIcon(icon_pixmap(name, size, color))


def toggle_icon(name: str, size: int = 20) -> QIcon:
    """Muted when unchecked, accent-colored when checked (navigation items)."""
    result = QIcon()
    normal = icon_pixmap(name, size)
    active = icon_pixmap(name, size, ACTIVE_COLOR)
    for mode in (QIcon.Normal, QIcon.Active, QIcon.Selected):
        result.addPixmap(normal, mode, QIcon.Off)
        result.addPixmap(active, mode, QIcon.On)
    result.addPixmap(icon_pixmap(name, size, "#5b6670"), QIcon.Disabled, QIcon.Off)
    return result


ICON_SIZE = QSize(20, 20)
