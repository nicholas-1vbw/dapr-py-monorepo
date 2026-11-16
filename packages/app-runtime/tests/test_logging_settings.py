import json
import logging

import pytest
from app_runtime.logging import JsonFormatter
from app_runtime.settings import RuntimeSettings
from opentelemetry.sdk.trace import TracerProvider


def test_log_record_includes_trace_ids() -> None:
    tracer = TracerProvider().get_tracer("test")
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    with tracer.start_as_current_span("span") as span:
        payload = json.loads(JsonFormatter().format(record))
    assert payload["message"] == "hello world"
    assert payload["trace_id"] == format(span.get_span_context().trace_id, "032x")


def test_log_record_without_span_has_no_trace_ids() -> None:
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "plain", (), None)
    assert "trace_id" not in json.loads(JsonFormatter().format(record))


def test_settings_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PORT", "9000")
    monkeypatch.setenv("APP_RELOAD", "true")
    settings = RuntimeSettings()
    assert (settings.port, settings.reload) == (9000, True)
