"""Process-wide logging setup: one JSON object per line on stdout."""

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional, TextIO

AUDIT_LOGGER_NAME = "self-healing-orchestrator.audit"
UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")
VALID_LEVELS = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG")

_HANDLER_MARKER = "_mana_json_handler"
_STANDARD_ATTRIBUTES = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime", "audit_event", "color_message"}


def _utc_timestamp(epoch: float) -> str:
    moment = datetime.fromtimestamp(epoch, timezone.utc)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class JsonFormatter(logging.Formatter):
    """Render a record as a single JSON line, merging audit events into it"""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": _utc_timestamp(record.created),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRIBUTES:
                payload[key] = value

        audit_event = getattr(record, "audit_event", None)
        if isinstance(audit_event, dict):
            payload.update(audit_event)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def normalize_level(level: str) -> str:
    """Return the upper-case level name or raise ValueError"""
    name = level.strip().upper()
    if name not in VALID_LEVELS:
        raise ValueError(f"log_level must be one of {', '.join(VALID_LEVELS)}")
    return name


def configure_logging(
    level: str = "INFO",
    audit_enabled: bool = True,
    stream: Optional[TextIO] = None,
) -> None:
    """
    Send every log record to one JSON handler on the root logger

    The level applies to application and uvicorn loggers. Audit events are
    always INFO (set by AuditLogger) and independent of the level, so a quiet LOG_LEVEL does not
    thin out the audit trail; ENABLE_AUDIT_LOGGING is the only switch for them.
    """
    root = logging.getLogger()
    for existing in [h for h in root.handlers if getattr(h, _HANDLER_MARKER, False)]:
        root.removeHandler(existing)

    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())
    setattr(handler, _HANDLER_MARKER, True)
    root.addHandler(handler)
    root.setLevel(normalize_level(level))

    for name in UVICORN_LOGGERS:
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    audit_logger = logging.getLogger(AUDIT_LOGGER_NAME)
    audit_logger.disabled = not audit_enabled
