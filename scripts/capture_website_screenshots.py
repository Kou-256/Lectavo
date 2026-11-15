from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TEMP_ROOT = Path(tempfile.mkdtemp(prefix="lectavo-screenshots-"))
os.environ["APPDATA"] = str(TEMP_ROOT / "Roaming")
os.environ["LOCALAPPDATA"] = str(TEMP_ROOT / "Local")

from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from movie_slide_notes.gui.i18n import set_ui_language  # noqa: E402
from movie_slide_notes.gui.main_window import MainWindow  # noqa: E402
from movie_slide_notes.gui.region_selector import RegionSelector  # noqa: E402


def capture(window, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    window.repaint()
    QApplication.processEvents()
    QTest.qWait(350)
    pixmap = window.grab()
    if not pixmap.save(str(destination), "PNG"):
        raise RuntimeError(f"スクリーンショットを保存できませんでした: {destination}")


def main() -> int:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Lectavo Screenshot")
    for font_name in ("YuGothR.ttc", "meiryo.ttc"):
        QFontDatabase.addApplicationFont(str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / font_name))
    app.setFont(QFont("Yu Gothic UI", 10))
    set_ui_language("ja")

    icon_path = ROOT / "assets" / "app_icon.png"
    window = MainWindow(icon_path=icon_path)
    window.history = []
    window._refresh_recent()
    window._refresh_history()
    window.resize(1280, 800)
    window.show()
    window._select_page("home")
    window._set_workflow_state("empty")
    window.empty_page.verticalScrollBar().setValue(0)
    QTest.qWait(700)

    docs_dir = ROOT / "docs" / "images"
    site_dir = ROOT / "website" / "public" / "screenshots"
    capture(window, docs_dir / "lectavo-home.png")

    window.close()
    window.deleteLater()
    QApplication.processEvents()

    sample_paths = [
        Path(r"C:\講義動画\講義サンプル-01.mp4"),
        Path(r"C:\講義動画\講義サンプル-02.mp4"),
    ]
    ready_window = MainWindow(icon_path=icon_path)
    ready_window.history = []
    ready_window.videos = sample_paths
    ready_window.video_statuses = {path: "待機中" for path in sample_paths}
    ready_window._set_output_path(Path(r"C:\Lectavo_output"))
    ready_window._refresh_video_list()
    ready_window._update_naming_preview()
    ready_window._select_page("home")
    ready_window._set_workflow_state("ready")
    ready_window.resize(1280, 800)
    ready_window.show()
    ready_window.ready_page.verticalScrollBar().setValue(0)
    QTest.qWait(700)
    capture(ready_window, docs_dir / "lectavo-ready.png")

    ready_window.videos = [Path(r"C:\音声サンプル\インタビュー-01.mp3"), Path(r"C:\音声サンプル\インタビュー-02.m4a")]
    ready_window._refresh_video_list()
    ready_window._update_naming_preview()
    ready_window._sync_input_kind()
    capture(ready_window, docs_dir / "lectavo-audio.png")

    import cv2
    import numpy as np
    image = QImage(1280, 720, QImage.Format_RGB888)
    image.fill(QColor("#222b30"))
    painter = QPainter(image)
    painter.fillRect(25, 45, 940, 630, QColor("#ffffff"))
    painter.setPen(QColor("#153b40"))
    painter.setFont(QFont("Segoe UI", 32))
    painter.drawText(85, 140, "Research methods")
    painter.setFont(QFont("Segoe UI", 19))
    for index, text in enumerate(("01  Define a question", "02  Collect evidence", "03  Compare the results")):
        painter.drawText(85, 260 + index * 100, text)
    painter.setFont(QFont("Segoe UI", 15))
    painter.drawText(900, 645, "12")
    painter.setPen(QColor("#b6c5c9"))
    painter.drawText(1000, 350, "Speaker")
    painter.end()
    pixels = np.frombuffer(image.bits(), np.uint8).reshape(720, image.bytesPerLine())[:, :1280 * 3].reshape(720, 1280, 3)
    preview_path = TEMP_ROOT / "lecture-sample.avi"
    writer = cv2.VideoWriter(str(preview_path), cv2.VideoWriter_fourcc(*"MJPG"), 1, (1280, 720))
    for _ in range(3):
        writer.write(cv2.cvtColor(pixels, cv2.COLOR_RGB2BGR))
    writer.release()
    selector = RegionSelector([preview_path], (25 / 1280, 45 / 720, 940 / 1280, 630 / 720), (0.9, 0.9, 0.09, 0.08))
    selector.source.setItemText(0, "講義サンプル.avi")
    selector.show()
    for _ in range(100):
        QTest.qWait(50)
        if not selector.canvas.image.isNull() and not selector.loader.isRunning():
            break
    if selector.canvas.image.isNull():
        raise RuntimeError("Preview frame was not loaded")
    capture(selector, docs_dir / "lectavo-region.png")
    selector.close()

    site_dir.mkdir(parents=True, exist_ok=True)
    for name in ("lectavo-home.png", "lectavo-ready.png", "lectavo-audio.png", "lectavo-region.png"):
        shutil.copy2(docs_dir / name, site_dir / name)

    icon_destination = ROOT / "website" / "public" / "app-icon.png"
    icon_destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(icon_path, icon_destination)

    ready_window.close()
    shutil.rmtree(TEMP_ROOT, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
