# Observability

## Traces

One request produces one trace across the two apps it involves and their sidecars:

```text
gateway  POST /greet                                              app: FastAPI server span
gateway  └─ example.counter.v1.CounterService/Increment            app: Connect client span
gateway     └─ POST                                                app: HTTP client span
gateway        └─ CallLocal/counter/example.counter.v1...          sidecar: service invocation
counter           └─ CallLocal/counter/example.counter.v1...       sidecar: service invocation
counter              └─ example.counter.v1.CounterService/Increment   app: Connect server span
```

W3C Trace Context (`traceparent`) carries the trace through Dapr service invocation. Apps and
sidecars export spans independently, so the backend joins them by trace id.

Over gRPC (`?protocol=grpc`, the `counter-grpc` app) the trace has the same spans; the sidecar
spans are named after the method (`/example.counter.v1.CounterService/Increment`), and the app's
server span is a child of the gateway sidecar's span, because the target sidecar passes on the
`traceparent` it received instead of its own:

```text
gateway  └─ /example.counter.v1.CounterService/Increment   sidecar: gRPC proxying
counter-grpc ├─ /example.counter.v1...                     sidecar: gRPC proxying
counter-grpc └─ example.counter.v1.CounterService/Increment   app: Connect server span (grpcio)
```

| Producer | Configured by | Settings |
| --- | --- | --- |
| Apps | `app_runtime.telemetry.configure_tracing()` | standard OpenTelemetry variables: `OTEL_EXPORTER_OTLP_ENDPOINT` or `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` (enables OTLP export), `OTEL_EXPORTER_OTLP_PROTOCOL` / `OTEL_EXPORTER_OTLP_TRACES_PROTOCOL` (`http/protobuf`, the default, or `grpc`), `OTEL_TRACES_EXPORTER` (`otlp`, `console`, `none`), `OTEL_SERVICE_NAME`, `OTEL_TRACES_SAMPLER`, `OTEL_TRACES_SAMPLER_ARG`, `OTEL_PROPAGATORS` |
| Sidecars | Dapr Configuration `app-config` from `deploy/platform/charts/dapr-resources` | `configuration.tracing.samplingRate`, and the `tracing` resource (kind `otlp`) in `resources-values.yaml`; without it sidecars only propagate context |

Apps report the service name of their settings (`gateway`, `counter`, `counter-grpc`) and sidecars their app id,
so both appear under the same service in Jaeger. Sidecar spans carry the instrumentation scope
`otel.scope.name=dapr-diagnostics`, which tells them apart.

- Without an OTLP endpoint, apps still create spans and propagate context but export nothing.
  An unsupported protocol or exporter name fails at startup instead of silently dropping spans.
- The local environments export over gRPC to Jaeger's port 4317 and set
  `OTEL_EXPORTER_OTLP_PROTOCOL=grpc` accordingly.
- Connect calls are instrumented by `connectrpc-otel`, also when a service is served over gRPC
  (`grpc_service()` adds its interceptor); FastAPI's built-in telemetry covers HTTP
  routes. Paths of mounted Connect services and the probes are excluded from FastAPI's spans, so
  each RPC appears once.
- Add business spans with `opentelemetry.trace.get_tracer(__name__)` where they help.

### Local backends

The example's `deploy/environment/infra.yaml` declares a Jaeger instance for `dev` and `kind`. It
provides the `tracing` resource (sidecar export) and, through `appEnv`, the
`OTEL_EXPORTER_OTLP_*` variables of every app. Integration tests do not run it; they assert trace
propagation with an in-memory exporter.

| Environment | Jaeger | App export | Sidecar export |
| --- | --- | --- | --- |
| `poe dev` | Docker, UI on `localhost:16686` | yes | yes |
| `poe kind up` | in-cluster, UI on `localhost:16686` | yes | yes |
| `poe integration` | none | in-memory exporter in tests | no |

To use another backend locally, replace the instance in `infra.yaml`; nothing else changes as long
as it provides a `tracing` resource of kind `otlp` and the `appEnv` variables.

### Production

Point both producers at the platform's collector:

- apps: set `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_PROTOCOL` matching the collector port
  (`http/protobuf` for 4318, `grpc` for 4317) and the sampling variables in the app's Helm values
  `env`;
- sidecars: provide a `tracing` service in the environment's `resources-values.yaml`
  (`kind: otlp`, `endpoint`, `insecure`) and choose `configuration.tracing.samplingRate`.

Dapr's default sampling rate is very low (0.0001); the chart sets `"1"` for local environments.

## Logs

Apps log one JSON object per line to stderr, including uvicorn's logs:

```json
{"time": "2026-01-01T12:00:00.000+00:00", "level": "INFO", "logger": "app_runtime.app", "message": "counter is ready", "trace_id": "4bf9...", "span_id": "00f0..."}
```

`trace_id` and `span_id` are present when a span is active, which links log lines to traces. Set
the level with `APP_LOG_LEVEL`. Sidecar logs are separate (`daprd` container in Kubernetes, the
`dapr run` output locally).

## Health

| Endpoint | Meaning |
| --- | --- |
| `/healthz` | the process is up and its startup checks passed |
| `/readyz` | the app accepts requests |

Neither endpoint calls Dapr or other services on each request: dependencies are checked once at
startup, and failures of other services surface as errors of the requests that need them.

gRPC apps have no HTTP endpoints: their port opens only after the startup checks passed, and the
probes check that it accepts connections.
