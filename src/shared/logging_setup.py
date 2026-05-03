"""Structured JSON logging for B2 services.

Both the processor and the API call ``configure_logging(service_name)`` once at
startup. Library logs (kafka-python, sqlalchemy, uvicorn) flow through the same
JSON formatter so B4 / ELK can parse every line uniformly.
"""

from __future__ import annotations

import logging
import sys

from pythonjsonlogger import jsonlogger


def configure_logging(service_name: str, level: int = logging.INFO) -> None:
    """Configure root logger with a JSON formatter. Idempotent."""
    root = logging.getLogger()
    if getattr(root, "_b2_json_configured", False):
        return

    handler = logging.StreamHandler(stream=sys.stdout)
    formatter = jsonlogger.JsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={"asctime": "time", "levelname": "level", "name": "logger"},
    )
    handler.setFormatter(formatter)

    for existing in list(root.handlers):
        root.removeHandler(existing)

    root.addHandler(handler)
    root.setLevel(level)

    logging.LoggerAdapter(root, {"service": service_name})
    root._b2_json_configured = True  # type: ignore[attr-defined]

    logging.getLogger(service_name).info("logging_configured", extra={"service": service_name})
