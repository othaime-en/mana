import json
import logging
from io import StringIO

import pytest

from src.logging_config import (
    AUDIT_LOGGER_NAME,
    JsonFormatter,
    configure_logging,
    normalize_level,
)
from src.utils import AuditAction, AuditLogger


@pytest.fixture
def captured():
    """Route logging to a buffer and restore the global logging state after"""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    audit = logging.getLogger(AUDIT_LOGGER_NAME)
    saved_disabled = audit.disabled
    stream = StringIO()

    def lines():
        return [json.loads(line) for line in stream.getvalue().splitlines()]

    yield stream, lines

    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)
    audit.disabled = saved_disabled


def make_record(**extra):
    record = logging.LogRecord("svc", logging.WARNING, __file__, 1, "hello %s", ("world",), None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


class TestJsonFormatter:
    def test_emits_one_json_object_with_core_fields(self):
        line = JsonFormatter().format(make_record())

        payload = json.loads(line)

        assert "\n" not in line
        assert payload["level"] == "WARNING"
        assert payload["logger"] == "svc"
        assert payload["message"] == "hello world"
        assert payload["timestamp"].endswith("Z")

    def test_includes_extra_fields(self):
        payload = json.loads(JsonFormatter().format(make_record(max_retries=3)))

        assert payload["max_retries"] == 3

    def test_merges_audit_event_into_the_line(self):
        event = {"action": "rollback_completed", "success": True, "namespace": "production"}

        payload = json.loads(JsonFormatter().format(make_record(audit_event=event)))

        assert payload["action"] == "rollback_completed"
        assert payload["success"] is True
        assert payload["namespace"] == "production"
        assert "audit_event" not in payload

    def test_includes_exception_text(self):
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            import sys
            record = make_record()
            record.exc_info = sys.exc_info()

        payload = json.loads(JsonFormatter().format(record))

        assert "RuntimeError: boom" in payload["exception"]

    def test_drops_the_uvicorn_colour_duplicate_of_the_message(self):
        payload = json.loads(JsonFormatter().format(make_record(color_message="\x1b[32mhi")))

        assert "color_message" not in payload

    def test_unserialisable_extra_does_not_raise(self):
        payload = json.loads(JsonFormatter().format(make_record(thing=object())))

        assert "object object" in payload["thing"]


class TestConfigureLogging:
    def test_audit_events_reach_the_log_with_their_payload(self, captured):
        stream, lines = captured
        configure_logging("INFO", True, stream)

        AuditLogger().log_event(
            action=AuditAction.ROLLBACK_COMPLETED,
            deployment_id="deploy-1",
            namespace="production",
            version="1.0.0",
        )

        event = lines()[-1]
        assert event["message"] == "AUDIT: rollback_completed"
        assert event["action"] == "rollback_completed"
        assert event["deployment_id"] == "deploy-1"
        assert event["namespace"] == "production"
        assert event["success"] is True

    def test_log_level_filters_application_logs(self, captured):
        stream, lines = captured
        configure_logging("WARNING", True, stream)
        app_logger = logging.getLogger("src.example")

        app_logger.info("quiet")
        app_logger.warning("loud")

        assert [line["message"] for line in lines()] == ["loud"]

    def test_debug_level_emits_debug_logs(self, captured):
        stream, lines = captured
        configure_logging("DEBUG", True, stream)

        logging.getLogger("src.example").debug("detail")

        assert [line["message"] for line in lines()] == ["detail"]

    def test_audit_trail_is_independent_of_log_level(self, captured):
        stream, lines = captured
        configure_logging("ERROR", True, stream)

        AuditLogger().log_event(action=AuditAction.STATE_SAVED)

        assert [line["action"] for line in lines()] == ["state_saved"]

    def test_audit_logging_can_be_disabled(self, captured):
        stream, lines = captured
        configure_logging("INFO", False, stream)

        AuditLogger().log_event(action=AuditAction.STATE_SAVED)
        logging.getLogger("src.example").info("still here")

        assert [line["message"] for line in lines()] == ["still here"]

    def test_is_idempotent(self, captured):
        stream, lines = captured
        configure_logging("INFO", True, stream)
        configure_logging("INFO", True, stream)

        logging.getLogger("src.example").info("once")

        assert len(lines()) == 1

    def test_uvicorn_loggers_use_the_json_handler(self, captured):
        stream, lines = captured
        uvicorn_logger = logging.getLogger("uvicorn.access")
        uvicorn_logger.addHandler(logging.NullHandler())
        uvicorn_logger.propagate = False
        configure_logging("INFO", True, stream)

        uvicorn_logger.info("GET /health 200")

        assert lines()[-1]["logger"] == "uvicorn.access"


class TestNormalizeLevel:
    def test_accepts_any_case(self):
        assert normalize_level(" debug ") == "DEBUG"

    def test_rejects_unknown_level(self):
        with pytest.raises(ValueError):
            normalize_level("LOUD")
