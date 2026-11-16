from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QWheelEvent
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QLabel, QSizePolicy, QSpinBox


class ElidedLabel(QLabel):
    """Single-line label that shows an ellipsis instead of growing or wrapping."""

    def __init__(self, text: str = "", parent=None, mode=Qt.ElideMiddle):
        super().__init__(text, parent)
        self._mode = mode
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setMinimumWidth(0)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.setFont(self.font())
        rect = self.contentsRect()
        text = self.fontMetrics().elidedText(self.text(), self._mode, rect.width())
        painter.drawText(rect, int(self.alignment()) | Qt.TextSingleLine, text)


class NoWheelComboBox(QComboBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, event: QWheelEvent) -> None:
        # Let the enclosing scroll area handle scrolling without changing settings.
        event.ignore()


class NoWheelSpinBox(QSpinBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, event: QWheelEvent) -> None:
        event.ignore()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, event: QWheelEvent) -> None:
        event.ignore()
