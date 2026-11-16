# Services and RPC

Contracts are written in Protobuf, generated with Buf, and served and called with Connect for
Python. Apps call each other through Dapr service invocation.

## Where things are

| What | Where |
| --- | --- |
| Contracts | `proto/<namespace>/<service>/v1/*.proto` |
| Generation config | `buf.yaml` (module, deps, lint), `buf.gen.yaml` (plugins, output) |
| Generated code | `gen/python/src/contracts/` (package `contracts`, not committed) |
| Implementations | a domain package, e.g. `packages/greeting/src/greeting/counter/` |
| Serving | the app, with `app_runtime.mount_connect()` |
| Calling | a typed client from `*_connect.py`, sent to the local sidecar |

## Add a method

1. Add the RPC and messages to the `.proto` file, with validation rules on the request fields:

   ```proto
   service CounterService {
     rpc Increment(IncrementRequest) returns (IncrementResponse);
     rpc Get(GetRequest) returns (GetResponse);
   }

   message GetRequest {
     string key = 1 [(buf.validate.field).string = {
       min_len: 1
       max_len: 64
     }];
   }

   message GetResponse {
     int64 value = 1;
   }
   ```

2. Format, lint and regenerate:

   ```bash
   buf format -w
   buf lint
   uv run poe proto
   ```

3. Implement the method in the service class. The generated `CounterService` protocol lists the
   methods and their signatures. A method you do not implement keeps the generated default, which
   answers `UNIMPLEMENTED`; type checking does not report it, so cover every method with a test.

   ```python
   async def get(
       self, request: GetRequest, ctx: RequestContext[GetRequest, GetResponse], /
   ) -> GetResponse:
       current = await self._store.get(request.key)
       return GetResponse(value=int(current.data or b"0"))
   ```

4. Add unit tests for the method with a fake store, and an integration test if it talks to Dapr
   (see [Testing](testing.md)).

The request reaches the method only after it passed validation: the interceptors from
`app_runtime.interceptors.server_interceptors()` validate it and turn rule violations into
`INVALID_ARGUMENT`. Unexpected exceptions are logged and returned as a generic `INTERNAL` error.
Raise `ConnectError` with a caller-facing code (`INVALID_ARGUMENT`, `NOT_FOUND`, `ABORTED`,
`FAILED_PRECONDITION`, ...) for errors the caller should see; their messages are returned as they
are. Server-side codes (`INTERNAL`, `UNKNOWN`, `UNAVAILABLE`, `DEADLINE_EXCEEDED`, `DATA_LOSS`) keep
their code but get a generic message, because they may carry details of a failed downstream call.

## Add a service

1. Create `proto/<namespace>/<service>/v1/<service>.proto` with `package <namespace>.<service>.v1;`.
2. Run `uv run poe proto`; the code appears under
   `gen/python/src/contracts/<namespace>/<service>/v1/`.
3. Implement the generated protocol (subclass it) in a domain package.
4. Serve it from an app:

   ```python
   from app_runtime import mount_connect
   from app_runtime.interceptors import server_interceptors
   from contracts.example.orders.v1.orders_connect import OrdersServiceASGIApplication

   mount_connect(
       app, OrdersServiceASGIApplication(OrdersServiceImpl(...), interceptors=server_interceptors())
   )
   ```

   An app can mount several services. If the service lives in a new app, see [Apps](apps.md).

## Call another app

Create clients with `app_runtime.client.dapr_client()` after `create_app()` (tracing must be
configured first), typically in the app's `on_startup` hook, and close them on shutdown:

```python
from app_runtime.client import dapr_client
from contracts.example.counter.v1.counter_connect import CounterServiceClient

client = dapr_client(CounterServiceClient, "counter", timeout_ms=5000)
runtime.exit_stack.push_async_callback(client.close)

response = await client.increment(IncrementRequest(key="Alice"))
```

- The client is the generated Connect client, pointed at the sidecar's service invocation API:
  `<sidecar>/v1.0/invoke/counter/method/example.counter.v1.CounterService/Increment`. Dapr
  forwards the request to the `counter` app.
- The sidecar address and the API token come from the Dapr SDK settings (`DAPR_HTTP_ENDPOINT`, or
  `DAPR_RUNTIME_HOST` and `DAPR_HTTP_PORT`, and `DAPR_API_TOKEN`), the same values the SDK's own
  `DaprClient` uses. `dapr run` and the sidecar injector set them.
- Always set a timeout. Translate `ConnectError` codes into your own domain errors at the client
  boundary, as `greeting.gateway.counter_client` does, and do not forward internal error messages
  to external callers. `app_runtime.client.is_unavailable()` recognizes calls that did not reach
  the target, including Dapr's own invocation errors.

The target app's sidecar must run with `app-protocol: http` (the default in `deploy/apps`).

### Over gRPC

Pass `protocol=InvokeProtocol.GRPC` to call an app that serves the service over gRPC (see
[Serve a service over gRPC](#serve-a-service-over-grpc)):

```python
from app_runtime.client import InvokeProtocol, dapr_client

client = dapr_client(
    CounterServiceClient, "counter-grpc", timeout_ms=5000, protocol=InvokeProtocol.GRPC
)
```

The same generated client then sends gRPC over HTTP/2 to the sidecar's gRPC port
(`DAPR_GRPC_ENDPOINT`, or `DAPR_RUNTIME_HOST` and `DAPR_GRPC_PORT`) with the target app id in the
`dapr-app-id` header, and Dapr proxies the call to the target. Calls, errors and timeouts look
the same to the caller. Two differences: Dapr's documentation states that its resiliency policies
(retries) are not supported for gRPC proxying, and in standalone mode a call to an unknown app id
fails only at its deadline.

Use the Dapr SDK (`runtime.dapr`) for the other building blocks: state, pub/sub, metadata and so
on. Its generic `invoke_method` is not needed for Connect services: it posts bytes to the same
invocation API but does not know the contract, so typed messages, Connect error codes, deadlines
and trace propagation would have to be rebuilt around it.

## Compatibility of contracts

`buf lint` runs in `poe check`. Changing a released contract also needs a breaking-change check
against the version in use, for example:

```bash
buf breaking --against '.git#branch=main'
```

Add it to CI once `main` holds the contracts your consumers rely on. Tightening a validation rule
does not break the wire format but can reject requests that used to pass; treat it as a behaviour
change.

## Serve a service over gRPC

Any generated service can also be served over gRPC, by an app of its own that runs the same
implementation with grpcio. Use it when a caller needs gRPC: full-duplex bidirectional streaming
(see [Streaming](#streaming)) or clients that only speak gRPC. `apps/counter-grpc` is the example:

```python
from app_runtime.grpc_server import grpc_service, run_grpc
from app_runtime.interceptors import server_interceptors


def main() -> None:
    settings = CounterGrpcSettings()

    def services(runtime):
        store = DaprStateStore(runtime.dapr, settings.statestore_name)
        return [
            grpc_service(
                CounterService, CounterServiceImpl(store), interceptors=server_interceptors()
            )
        ]

    run_grpc(settings, services, requirements=requirements(settings))
```

- `grpc_service()` takes the generated service protocol (`CounterService`), its implementation
  and the same interceptors as `mount_connect`. Methods and message types come from the generated
  code; the OpenTelemetry interceptor is added first, as for Connect applications, so traces have
  the same spans.
- `run_grpc()` runs the startup sequence of `create_app()` (sidecar, required components) and
  opens the port only afterwards; on SIGTERM, calls in progress get time to finish.
- The app is a separate Dapr app (`appProtocol: grpc` in `deploy/apps`), because a Dapr app has
  one protocol. If it shares state with the Connect app, the state store needs
  `keyPrefix: name` (see [Components](components.md)).
- Request metadata reaches the implementation as `ctx.request_headers` (text metadata only), and
  the remaining deadline as `ctx.timeout_ms`. Response headers and trailers set on the context are
  sent; error details are not.

## Streaming

The example service is unary. Generated services can declare streaming methods; what works
through Dapr depends on the protocol:

| Stream type | Connect (service invocation) | gRPC (gRPC proxying) |
| --- | --- | --- |
| server streaming | yes | yes |
| client streaming | yes | yes |
| bidirectional, half-duplex (all requests, then responses) | yes | yes |
| bidirectional, full-duplex | no: the sidecar answers only after the request stream ends | yes |

`ValidationInterceptor` validates unary requests only; validate stream messages in the
implementation. For gRPC streaming calls, `dapr_client` adds the `dapr-stream: true` header that
Dapr's gRPC proxying expects.
