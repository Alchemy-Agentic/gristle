"""Structured logging configuration for Gristle.

Provides JSON-formatted logging for production (streamable-http transport) and
human-readable coloured output for local development (stdio transport).

Usage in any module::

    import logging
    logger = logging.getLogger(__name__)

Then call ``configure_logging()`` once at startup (done automatically via
``main()`` in ``mcp/server.py``).

Environment variables
---------------------
GRISTLE_LOG_LEVEL : str
    Root log level.  Default ``INFO``.
GRISTLE_LOG_FORMAT : str
    ``json`` for structured output, ``text`` for human-readable.
    Auto-detected from transport when not set.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any


class JSONFormatter(logging.Formatter):
    """Emit one JSON object per log line — easy to parse in production."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            entry["exception"] = self.formatException(record.exc_info)
        # Carry extra fields set via ``logger.info("...", extra={...})``
        for key in ("repo_id", "files", "nodes", "rels", "duration_ms", "event"):
            val = getattr(record, key, None)
            if val is not None:
                entry[key] = val
        return json.dumps(entry, default=str)


class TextFormatter(logging.Formatter):
    """Coloured, human-readable formatter for local development."""

    COLOURS = {
        "DEBUG": "\033[90m",  # grey
        "INFO": "\033[36m",  # cyan
        "WARNING": "\033[33m",  # yellow
        "ERROR": "\033[31m",  # red
        "CRITICAL": "\033[1;31m",  # bold red
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        colour = self.COLOURS.get(record.levelname, "")
        ts = self.formatTime(record, "%H:%M:%S")
        base = f"{colour}{ts} {record.levelname:<8}{self.RESET} {record.name}: {record.getMessage()}"
        if record.exc_info and record.exc_info[0] is not None:
            base += "\n" + self.formatException(record.exc_info)
        return base


def configure_logging(transport: str = "stdio") -> None:
    """Set up root logging based on transport mode and env overrides.

    Called once at server startup.
    """
    level_name = os.environ.get("GRISTLE_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    fmt_override = os.environ.get("GRISTLE_LOG_FORMAT", "").lower()
    if fmt_override == "json":
        use_json = True
    elif fmt_override == "text":
        use_json = False
    else:
        # Auto: JSON for HTTP transport (production), text for stdio (dev)
        use_json = transport == "streamable-http"

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JSONFormatter() if use_json else TextFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Quiet noisy third-party loggers
    for name in ("httpx", "httpcore", "uvicorn.access", "watchfiles"):
        logging.getLogger(name).setLevel(logging.WARNING)


class Timer:
    """Simple context-manager timer for measuring operation duration.

    Usage::

        with Timer() as t:
            do_work()
        logger.info("done", extra={"duration_ms": t.ms})
    """

    __slots__ = ("_start", "ms")

    def __enter__(self) -> Timer:
        self._start = time.perf_counter()
        self.ms = 0.0
        return self

    def __exit__(self, *exc: object) -> None:
        self.ms = round((time.perf_counter() - self._start) * 1000, 1)
