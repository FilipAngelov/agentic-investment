"""Centralised logging configuration."""

import logging
import logging.handlers
from pathlib import Path

_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
_LOG_FILE = _LOG_DIR / "trading.log"

_CONSOLE_FMT = "%(asctime)s %(levelname)-5s [%(module)s] %(message)s"
_FILE_FMT = "%(asctime)s %(levelname)-5s [%(name)s:%(lineno)d] %(message)s"
_DATEFMT = "%H:%M:%S"

_NOISY_LOGGERS = ("ib_async", "asyncio", "urllib3", "httpcore", "httpx", "uvicorn.access")


def setup_logging() -> None:
    """Configure root logger with console (INFO) and rotating file (DEBUG) handlers."""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Console handler — INFO
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(_CONSOLE_FMT, datefmt=_DATEFMT))
    root.addHandler(console)

    # Rotating file handler — DEBUG, 10 MB × 5 backups
    file_h = logging.handlers.RotatingFileHandler(
        _LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5,
    )
    file_h.setLevel(logging.DEBUG)
    file_h.setFormatter(logging.Formatter(_FILE_FMT, datefmt=_DATEFMT))
    root.addHandler(file_h)

    # Quiet noisy libraries
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
