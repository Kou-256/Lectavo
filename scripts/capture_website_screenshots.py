from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "windows"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TEMP_ROOT = Path(tempfile.mkdtemp(prefix="lectavo-screenshots-"))
os.environ["APPDATA"] = str(TEMP_ROOT / "Roaming")
os.environ["LOCALAPPDATA"] = str(TEMP_ROOT / "Local")

from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from movie_slide_notes.gui.i18n import set_ui_language  # noqa: E402
from movie_slide_notes.gui.main_window import MainWindow  # noqa: E402


def capture(window: MainWindow, destination: Path) -> None:
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
        Path(r"C:\講義動画\サービス情報学-01.mp4"),
        Path(r"C:\講義動画\サービス情報学-02.mp4"),
    ]
    ready_window = MainWindow(icon_path=icon_path)
    ready_window.history = []
    ready_window.videos = sample_paths
    ready_window.video_statuses = {path: "待機中" for path in sample_paths}
    ready_window._set_output_path(Path(r"C:\Lectavo_output"))
    ready_window._refresh_video_list()
    ready_window._select_page("home")
    ready_window._set_workflow_state("ready")
    ready_window.resize(1280, 800)
    ready_window.show()
    ready_window.ready_page.verticalScrollBar().setValue(0)
    QTest.qWait(700)
    capture(ready_window, docs_dir / "lectavo-ready.png")

    site_dir.mkdir(parents=True, exist_ok=True)
    for name in ("lectavo-home.png", "lectavo-ready.png"):
        shutil.copy2(docs_dir / name, site_dir / name)

    icon_destination = ROOT / "website" / "public" / "app-icon.png"
    icon_destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(icon_path, icon_destination)

    ready_window.close()
    shutil.rmtree(TEMP_ROOT, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
