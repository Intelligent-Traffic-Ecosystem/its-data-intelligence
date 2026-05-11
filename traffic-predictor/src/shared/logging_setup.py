"""
Logging configuration — mirrors the style used by the upstream processor/api services.

Supports two output formats:
* ``"json"`` — structured JSON via ``python-json-logger`` (production default).
* ``"text"`` — human-readable format for local development.
"""

from __future__ import annotations

import logging
import sys
from typing import Literal

LogFormat = Literal["json", "text"]


def configure_logging(
    level: str = "INFO",
    fmt: LogFormat = "json",
) -> None:
    """
    Set up the root logger.

    Parameters
    ----------
    level:
        Logging level string (e.g. ``"INFO"``, ``"DEBUG"``).
    fmt:
        ``"json"`` for structured logging, ``"text"`` for readable output.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    if fmt == "json":
        try:
            from pythonjsonlogger import jsonlogger

            handler = logging.StreamHandler(sys.stdout)
            formatter = jsonlogger.JsonFormatter(
                "%(asctime)s %(name)s %(levelname)s %(message)s"
            )
            handler.setFormatter(formatter)
        except ImportError:
            handler = _text_handler()
    else:
        handler = _text_handler()

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(numeric_level)

    # Silence overly verbose third-party loggers
    for noisy in ("kafka", "urllib3", "torch_geometric"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _text_handler() -> logging.StreamHandler:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    return handler
