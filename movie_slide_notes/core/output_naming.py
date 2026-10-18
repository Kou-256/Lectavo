from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class OutputNamingConfig:
    mode: str = "original"
    custom_name: str = ""
    date_style: str = "none"
    timestamp: datetime | None = None


def safe_output_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" ._")[:100].rstrip(" .")
    if not name:
        return "Lectavo"
    if name.split(".")[0].upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        name = "_" + name
    return name


def common_video_name(paths: Sequence[Path]) -> str:
    stems = [Path(path).stem for path in paths]
    if not stems:
        return "Lectavo"
    if len(set(stems)) == 1:
        return safe_output_name(stems[0])
    prefix = os.path.commonprefix(stems)
    # A common prefix can stop halfway through a sequence number (e.g. 10 and 11).
    if prefix and any(len(stem) > len(prefix) and stem[len(prefix)].isdigit() for stem in stems):
        prefix = re.sub(r"\d+$", "", prefix)
    prefix = prefix.rstrip(" _-–—.()[]（）")
    return safe_output_name(prefix) if prefix else "Lectavo"


def output_stem(paths: Sequence[Path], config: OutputNamingConfig, *, combined: bool) -> str:
    if config.mode not in {"original", "common", "custom"}:
        raise ValueError("Unsupported output naming mode")
    if config.date_style not in {"none", "date", "datetime"}:
        raise ValueError("Unsupported output date style")
    if config.mode == "custom":
        if not config.custom_name.strip():
            raise ValueError("出力名を入力してください。")
        base = safe_output_name(config.custom_name)
        if not combined and paths:
            base += "_" + safe_output_name(Path(paths[0]).stem)
    elif config.mode == "common":
        base = common_video_name(paths)
    else:
        base = "all" if combined or not paths else safe_output_name(Path(paths[0]).stem)
    if config.date_style != "none":
        moment = config.timestamp or datetime.now()
        pattern = "%Y-%m-%d" if config.date_style == "date" else "%Y-%m-%d_%H-%M-%S"
        base += "_" + moment.strftime(pattern)
    return base
