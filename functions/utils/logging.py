"""Structured JSON-line logging.

All modules obtain a logger via ``logging.getLogger(__name__)`` and call the
usual ``info``/``warning``/``error``/``debug`` methods. Scripts (and test
fixtures) call :func:`configure_logging` exactly once at startup to install
handlers.

Rationale for JSON-per-line:
- Matches the JSONL cache style used elsewhere in the project, so every log
  line is greppable and machine-parseable.
- Each record carries a ``stage`` hint (``search``, ``select``, ``scrape``,
  ``validate``, ``summarize``) when emitted from pipeline code, making it
  trivial to slice traces by stage.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

# Fields populated by the LogRecord constructor we should *not* leak into the
# JSON output as spurious "extras".
_STD_LOGRECORD_ATTRS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName", "message", "asctime",
}


class JsonLineFormatter(logging.Formatter):
    """Render each ``LogRecord`` as a single-line JSON object.

    The output always contains ``ts``, ``level``, ``logger``, ``msg``. Any
    attribute attached via ``logger.info(..., extra={...})`` surfaces as a
    top-level key. Exceptions serialize under the ``exc`` key.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _STD_LOGRECORD_ATTRS or key.startswith("_"):
                continue
            try:
                json.dumps(value)  # probe serializability
                payload[key] = value
            except TypeError:
                payload[key] = repr(value)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _resolve_level(level: str | int) -> int:
    if isinstance(level, int):
        return level
    upper = level.upper()
    resolved = logging.getLevelNamesMapping().get(upper)
    if resolved is None:
        raise ValueError(f"unknown log level: {level!r}")
    return resolved


def configure_logging(
    *,
    level: str | int = "INFO",
    log_file: Path,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 3,
) -> None:
    """Install a rotating file handler + stderr handler on the root logger.

    Idempotent: repeated calls replace handlers rather than stacking duplicates.
    """
    numeric_level = _resolve_level(level)
    log_file = Path(log_file)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    # Remove only handlers we previously installed (tagged via an attribute).
    for h in list(root.handlers):
        if getattr(h, "_a2a_managed", False):
            root.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass

    formatter = JsonLineFormatter()

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler._a2a_managed = True  # type: ignore[attr-defined]

    stream_handler = logging.StreamHandler(stream=sys.stderr)
    stream_handler.setFormatter(formatter)
    stream_handler._a2a_managed = True  # type: ignore[attr-defined]

    root.addHandler(file_handler)
    root.addHandler(stream_handler)
    root.setLevel(numeric_level)

    # Silence third-party INFO chatter.
    # - httpx / httpcore / urllib3: log full request URLs at INFO, which
    #   leaks API keys passed in query strings (e.g. SerpAPI).
    # - google_genai: emits verbose per-request INFO lines that clutter
    #   the log without adding debugging value.
    for noisy in ("httpx", "httpcore", "urllib3", "google_genai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
