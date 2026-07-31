"""Centralized logging configuration.

Provides a single ``get_logger`` factory so every module emits consistent,
timestamped, colour-free logs that work both in notebooks and on servers.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_DEFAULT_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-22s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_CONFIGURED = False


def _configure_root(level: int = logging.INFO) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    root = logging.getLogger()
    root.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_DEFAULT_FORMAT, datefmt=_DATE_FORMAT))
    root.addHandler(handler)
    _CONFIGURED = True


def set_log_level(level: int | str = logging.INFO) -> None:
    """Set the log level for the root logger."""
    _configure_root()
    logging.getLogger().setLevel(level)


def get_logger(name: str, level: int | str | None = None) -> logging.Logger:
    """Return a configured logger.

    Args:
        name: Usually ``__name__`` of the calling module.
        level: Optional override for this logger only.
    """
    _configure_root()
    logger = logging.getLogger(name)
    if level is not None:
        logger.setLevel(level)
    return logger


def add_file_handler(path: str | Path, level: int = logging.INFO) -> None:
    """Attach a file handler so logs are persisted to disk (useful for live runs)."""
    _configure_root()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setFormatter(logging.Formatter(_DEFAULT_FORMAT, datefmt=_DATE_FORMAT))
    fh.setLevel(level)
    logging.getLogger().addHandler(fh)
