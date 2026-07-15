from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .paths import user_log_dir


def setup_file_logging() -> logging.Logger:
    logger = logging.getLogger("lectavo")
    logger.setLevel(logging.INFO)
    if any(isinstance(handler, RotatingFileHandler) for handler in logger.handlers):
        return logger

    log_path = user_log_dir() / "lectavo.log"
    handler = RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    return logger
