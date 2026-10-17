from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QDoubleSpinBox, QSpinBox

from movie_slide_notes.gui.main_window import MainWindow


class ScrollSafeSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_all_settings_ignore_wheel_even_with_keyboard_focus(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "movie_slide_notes.gui.main_window.user_config_dir",
                return_value=Path(directory),
            ):
                window = MainWindow()
                try:
                    controls = [
                        control
                        for widget_type in (QComboBox, QSpinBox, QDoubleSpinBox)
                        for control in window.findChildren(widget_type)
                    ]
                    self.assertGreater(len(controls), 15)
                    for control in controls:
                        control.setFocus()
                        read = (
                            control.currentIndex
                            if isinstance(control, QComboBox)
                            else control.value
                        )
                        before = read()
                        for direction in (-120, 120):
                            event = QWheelEvent(
                                QPointF(10, 10),
                                QPointF(control.mapToGlobal(QPoint(10, 10))),
                                QPoint(),
                                QPoint(0, direction),
                                Qt.NoButton,
                                Qt.NoModifier,
                                Qt.ScrollUpdate,
                                False,
                            )
                            QApplication.sendEvent(control, event)
                            self.assertEqual(read(), before, type(control).__name__)
                            self.assertFalse(event.isAccepted())

                    combo = window.quality_combo
                    combo.setCurrentIndex(0)
                    QTest.keyClick(combo, Qt.Key_Down)
                    self.assertEqual(combo.currentIndex(), 1)
                finally:
                    window.close()


if __name__ == "__main__":
    unittest.main()
