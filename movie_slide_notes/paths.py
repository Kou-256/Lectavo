from __future__ import annotations

import os
import sys
from pathlib import Path

from .version import APP_NAME


def get_app_base_dir() -> Path:
    """Return the runtime base directory for development and PyInstaller builds."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def get_resource_base_dirs() -> list[Path]:
    """Return candidate directories for read-only bundled resources."""
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent)
        pyinstaller_internal = getattr(sys, "_MEIPASS", None)
        if pyinstaller_internal:
            candidates.append(Path(pyinstaller_internal).resolve())
    else:
        candidates.append(get_app_base_dir())

    seen: set[Path] = set()
    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in seen:
            unique.append(candidate)
            seen.add(candidate)
    return unique


def resource_path(*parts: str) -> Path:
    bases = get_resource_base_dirs()
    for base in bases:
        candidate = base.joinpath(*parts)
        if candidate.exists():
            return candidate
    return bases[0].joinpath(*parts)


def user_config_dir() -> Path:
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Roaming"
    path = root / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_cache_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    path = root / APP_NAME / "Cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_log_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    path = root / APP_NAME / "Logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def app_icon_path() -> Path | None:
    for candidate in [
        resource_path("assets", "app_icon.png"),
        resource_path("assets", "app_icon.ico"),
    ]:
        if candidate.exists():
            return candidate
    return None
