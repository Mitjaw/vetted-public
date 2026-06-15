"""Centralized logging setup for Vetted owner + consumer apps.

Reads VETTED_LOG_FORMAT from env:
  - "json"  → structured JSON (default in production)
  - "text"  → human-readable single-line (dev default if unset)

VETTED_LOG_LEVEL controls level (default INFO).

Call ``configure()`` once at app startup. Idempotent.
"""

from __future__ import annotations

import logging
import os
import sys

from pythonjsonlogger import jsonlogger


_TEXT_FMT = "%(asctime)s %(levelname)-7s %(name)s:%(lineno)d  %(message)s"
_JSON_FIELDS = "%(asctime)s %(levelname)s %(name)s %(lineno)d %(message)s"

_configured = False


def configure() -> None:
    global _configured
    if _configured:
        return
    _configured = True

    level_name = os.getenv("VETTED_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    fmt = os.getenv("VETTED_LOG_FORMAT", "text").lower()
    handler = logging.StreamHandler(sys.stdout)

    if fmt == "json":
        handler.setFormatter(jsonlogger.JsonFormatter(_JSON_FIELDS, rename_fields={"asctime": "ts", "levelname": "level"}))
    else:
        handler.setFormatter(logging.Formatter(_TEXT_FMT))

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # Quiet third-party noise.
    logging.getLogger("apscheduler.scheduler").setLevel(logging.WARNING)
    logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
    logging.getLogger("yfinance").setLevel(logging.WARNING)
