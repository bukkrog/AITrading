"""Logging setup. Logging is enabled from the very first line of code so that
every signal, decision and trade leaves a trace (complemented by the durable
audit log in the database)."""
from __future__ import annotations

import logging
import sys

from app.config import settings

_CONFIGURED = False


def configure_logging(level: str | None = None) -> None:
    """Configure root logging once, idempotently."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_level = (level or settings.log_level or "INFO").upper()

    # Ensure UTF-8 output so non-ASCII characters (e.g. currency/emoji) don't
    # crash on Windows' default cp1252 console.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):  # pragma: no cover - stream not reconfigurable
                pass

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.setLevel(log_level)
    root.handlers = [handler]
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a module logger, ensuring logging is configured."""
    configure_logging()
    return logging.getLogger(name)
