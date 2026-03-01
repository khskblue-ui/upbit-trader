"""Logging configuration with rotating file handlers and structured output."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path


_DEFAULT_LOG_DIR = Path("logs")
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB per file
_BACKUP_COUNT = 7               # keep 7 rotated files


def setup_logging(
    level: str = "INFO",
    log_dir: Path | str | None = None,
    enable_console: bool = True,
) -> None:
    """Configure root-level logging for the upbit-trader application.

    Sets up:
    - Console handler (stdout) with colourised level name.
    - Rotating ``trading_YYYYMMDD.log`` handler for trade-related logs.
    - Rotating ``system.log`` handler for all application logs.
    - Separate ``error.log`` handler for WARNING and above.

    Args:
        level: Root log level string, e.g. "DEBUG", "INFO", "WARNING".
        log_dir: Directory where log files are written.
                 Defaults to ``logs/`` in the current working directory.
        enable_console: When ``False`` the console handler is omitted
                        (useful in production / systemd deployments).
    """
    log_dir = Path(log_dir) if log_dir else _DEFAULT_LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)

    numeric_level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(numeric_level)

    # Remove any existing handlers to avoid duplicates on re-initialisation
    root.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ------------------------------------------------------------------ #
    # Console handler                                                      #
    # ------------------------------------------------------------------ #
    if enable_console:
        console = logging.StreamHandler(sys.stdout)
        console.setLevel(numeric_level)
        console.setFormatter(fmt)
        root.addHandler(console)

    # ------------------------------------------------------------------ #
    # System log — all messages, daily rotation                           #
    # ------------------------------------------------------------------ #
    system_handler = logging.handlers.RotatingFileHandler(
        log_dir / "system.log",
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    system_handler.setLevel(numeric_level)
    system_handler.setFormatter(fmt)
    root.addHandler(system_handler)

    # ------------------------------------------------------------------ #
    # Trading log — only trading-related loggers                          #
    # ------------------------------------------------------------------ #
    trading_handler = logging.handlers.RotatingFileHandler(
        log_dir / "trading.log",
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    trading_handler.setLevel(logging.INFO)
    trading_handler.setFormatter(fmt)

    _TRADING_LOGGERS = (
        "src.core.trading_engine",
        "src.execution",
        "src.strategy",
        "src.risk",
    )
    for name in _TRADING_LOGGERS:
        lg = logging.getLogger(name)
        lg.addHandler(trading_handler)

    # ------------------------------------------------------------------ #
    # Error log — WARNING and above only                                   #
    # ------------------------------------------------------------------ #
    error_handler = logging.handlers.RotatingFileHandler(
        log_dir / "error.log",
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.WARNING)
    error_handler.setFormatter(fmt)
    root.addHandler(error_handler)

    logging.getLogger(__name__).info(
        "Logging initialised: level=%s log_dir=%s", level, log_dir.resolve()
    )


def get_logger(name: str) -> logging.Logger:
    """Return a named logger (thin wrapper around :func:`logging.getLogger`).

    Args:
        name: Logger name, typically ``__name__`` from the calling module.

    Returns:
        :class:`logging.Logger` instance.
    """
    return logging.getLogger(name)
