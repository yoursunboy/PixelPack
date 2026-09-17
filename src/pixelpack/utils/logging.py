"""Application logging setup.

Logging must never be able to crash the application, so every file-system
operation in here is defensive.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "pixelpack"
_CONFIGURED = False
_LOG_FORMAT = "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s"


def default_log_dir() -> Path:
    """Return the per-user directory used for log files."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_STATE_HOME")
    if base:
        return Path(base) / "PixelPack" / "logs"
    return Path.home() / ".pixelpack" / "logs"


def setup_logging(level: int = logging.INFO, log_dir: Path | None = None) -> logging.Logger:
    """Configure the package logger once and return it."""
    global _CONFIGURED

    logger = logging.getLogger(LOGGER_NAME)
    if _CONFIGURED:
        return logger

    logger.setLevel(level)
    logger.propagate = False
    formatter = logging.Formatter(_LOG_FORMAT)

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    logger.addHandler(stream)

    try:
        directory = Path(log_dir) if log_dir is not None else default_log_dir()
        directory.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            directory / "pixelpack.log",
            maxBytes=2_000_000,
            backupCount=2,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError:
        # A read-only or missing profile directory must not stop the app.
        logger.debug("无法创建日志文件，仅使用控制台输出。", exc_info=True)

    _CONFIGURED = True
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a namespaced child of the package logger."""
    if not name:
        return logging.getLogger(LOGGER_NAME)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
