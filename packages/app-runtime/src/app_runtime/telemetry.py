"""OpenTelemetry tracing setup driven by the standard `OTEL_*` variables.

Spans are always created so that W3C trace context propagates. Where they go
is chosen with the standard variables:

- `OTEL_TRACES_EXPORTER`: comma-separated `otlp` (default), `console`, `none`.
- OTLP exports only when an endpoint is set (`OTEL_EXPORTER_OTLP_ENDPOINT` or
  `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`); without one nothing is sent.
- `OTEL_EXPORTER_OTLP_TRACES_PROTOCOL` / `OTEL_EXPORTER_OTLP_PROTOCOL`:
  `http/protobuf` (default) or `grpc`.

Sampling (`OTEL_TRACES_SAMPLER`, `OTEL_TRACES_SAMPLER_ARG`), propagators
(`OTEL_PROPAGATORS`, W3C Trace Context by default) and the service name
(`OTEL_SERVICE_NAME`) are read by the SDK itself.
"""

import os
from collections.abc import Iterator

from connectrpc_otel import ConnectInstrumentor
from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)

SUPPORTED_PROTOCOLS = ("http/protobuf", "grpc")


def _env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def otlp_exporter() -> SpanExporter | None:
    """The OTLP exporter for the configured protocol, or None without an endpoint."""
    if not _env("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT"):
        return None
    protocol = _env("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", "OTEL_EXPORTER_OTLP_PROTOCOL")
    protocol = (protocol or "http/protobuf").lower()
    if protocol == "grpc":
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter as GrpcExporter,
        )

        return GrpcExporter()
    if protocol == "http/protobuf":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter as HttpExporter,
        )

        return HttpExporter()
    raise ValueError(
        f"unsupported OTLP protocol {protocol!r}; use one of: {', '.join(SUPPORTED_PROTOCOLS)}"
    )


def span_processors() -> Iterator[SpanProcessor]:
    """Processors for the exporters selected by `OTEL_TRACES_EXPORTER`."""
    names = (_env("OTEL_TRACES_EXPORTER") or "otlp").lower().split(",")
    for name in (n.strip() for n in names):
        if name in ("", "none"):
            continue
        if name == "otlp":
            exporter = otlp_exporter()
            if exporter is not None:
                yield BatchSpanProcessor(exporter)
        elif name == "console":
            yield SimpleSpanProcessor(ConsoleSpanExporter())
        else:
            raise ValueError(
                f"unsupported OTEL_TRACES_EXPORTER {name!r}; use otlp, console or none"
            )


_provider: TracerProvider | None = None


def configure_tracing(default_service_name: str) -> TracerProvider:
    """Install the process-wide tracer provider and instrument Connect.

    OpenTelemetry keeps one global provider per process, so this configures it
    once and returns the same provider on later calls (for example when several
    apps are created in one test process). The SDK flushes and shuts it down at
    process exit.

    Call this before creating Connect applications or clients: the Connect
    instrumentation only applies to objects created afterwards.
    """
    global _provider
    if _provider is not None:
        return _provider
    attributes = {} if os.environ.get("OTEL_SERVICE_NAME") else {SERVICE_NAME: default_service_name}
    provider = TracerProvider(resource=Resource.create(attributes))
    for processor in span_processors():
        provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    instrumentor = ConnectInstrumentor()
    if not instrumentor.is_instrumented_by_opentelemetry:
        instrumentor.instrument()
    _provider = provider
    return provider
