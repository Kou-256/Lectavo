from __future__ import annotations

import os
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from movie_slide_notes.gui.i18n import DEFAULT_UI_LANGUAGE, set_ui_language
from movie_slide_notes.gui.main_window import MainWindow
from movie_slide_notes.logging_config import setup_file_logging
from movie_slide_notes.paths import app_icon_path, user_cache_dir, user_config_dir
from movie_slide_notes.version import APP_NAME


def main() -> int:
    if "--input" in sys.argv:
        from movie_slide_notes.main import main as cli_main

        return cli_main()

    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
    os.environ.setdefault("HF_HUB_VERBOSITY", "error")
    os.environ.setdefault("HF_HOME", str(user_cache_dir() / "huggingface"))
    setup_file_logging()
    settings = QSettings(str(user_config_dir() / "settings.ini"), QSettings.IniFormat)
    set_ui_language(str(settings.value("ui_language", DEFAULT_UI_LANGUAGE)))
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setFont(QFont("Yu Gothic UI", 9))
    icon_path = app_icon_path()
    if icon_path and icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    window = MainWindow(icon_path=icon_path)
    window.show()
    if "--smoke" in sys.argv:
        QTimer.singleShot(0, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
