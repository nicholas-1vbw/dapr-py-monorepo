import pytest
from app_runtime.telemetry import otlp_exporter, span_processors
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
    OTLPSpanExporter as GrpcExporter,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter as HttpExporter,
)
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

OTEL_VARIABLES = (
    "OTEL_TRACES_EXPORTER",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    "OTEL_EXPORTER_OTLP_PROTOCOL",
    "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in OTEL_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def test_no_endpoint_exports_nothing() -> None:
    assert otlp_exporter() is None
    assert list(span_processors()) == []


def test_http_protobuf_is_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    assert isinstance(otlp_exporter(), HttpExporter)


def test_grpc_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4317")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
    assert isinstance(otlp_exporter(), GrpcExporter)


def test_traces_specific_protocol_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://collector:4317")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", "grpc")
    assert isinstance(otlp_exporter(), GrpcExporter)


def test_unsupported_protocol_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/json")
    with pytest.raises(ValueError, match="unsupported OTLP protocol"):
        otlp_exporter()


def test_exporter_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "console,otlp")
    kinds = [type(p) for p in span_processors()]
    assert kinds == [SimpleSpanProcessor, BatchSpanProcessor]

    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "none")
    assert list(span_processors()) == []

    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "zipkin")
    with pytest.raises(ValueError, match="unsupported OTEL_TRACES_EXPORTER"):
        list(span_processors())


def test_tracing_is_configured_once_per_process() -> None:
    from app_runtime.telemetry import configure_tracing

    assert configure_tracing("first") is configure_tracing("second")
