"""Logging to ``<data root>/logs/imagetrace.log`` (rotating).

Never log evidence content, case titles or notes, OCR text or other sensitive values. Log
IDs, event types, file names and error messages only.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_NAME = "imagetrace.log"
_handler: logging.Handler | None = None


def setup_logging(data_root: Path | None) -> Path | None:
    global _handler
    logger = logging.getLogger("imagetrace")
    logger.setLevel(logging.INFO)
    if _handler is not None:
        logger.removeHandler(_handler)
        _handler.close()
        _handler = None
    if data_root is None or not data_root.is_dir():
        return None
    logs = data_root / "logs"
    logs.mkdir(exist_ok=True)
    path = logs / LOG_NAME
    _handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(_handler)
    return path
