import logging
import os
import sys
from logging.handlers import RotatingFileHandler

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
LOG_FILE = os.path.join(LOG_DIR, "trading_bot.log")

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def _ensure_log_dir() -> None:
    """Create the logs directory if it does not yet exist."""
    os.makedirs(LOG_DIR, exist_ok=True)


def get_logger(name: str, level: int = logging.DEBUG) -> logging.Logger:
    """Return a named logger configured with both console and rotating file handlers.

    Args:
        name:  Logger name, typically ``__name__`` of the calling module.
        level: Minimum log level (default: DEBUG).

    Returns:
        logging.Logger: Configured logger instance.
    """
    logger = logging.getLogger(name)

    # Avoid adding duplicate handlers when the same logger is retrieved multiple times
    if logger.handlers:
        return logger

    logger.setLevel(level)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Rotating file handler (max 5 MB per file, keep last 3 files)
    _ensure_log_dir()
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger
