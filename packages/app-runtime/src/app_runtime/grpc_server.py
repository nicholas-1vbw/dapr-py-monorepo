"""Serving generated Connect services over gRPC with grpcio.

An app normally serves its Connect services through FastAPI (`mount_connect`)
and is reached through Dapr service invocation. An app can instead serve the
same service implementations over gRPC and be reached through Dapr gRPC
proxying: callers use `dapr_client(..., protocol=InvokeProtocol.GRPC)` and the
app runs with `appProtocol: grpc`. What stays the same:

- The contract: methods, message classes and stream kinds come from the
  generated service's public descriptor (`<Service>.desc()`), and messages are
  the generated protobuf-py classes.
- The implementation: the object that implements the generated service.
- The interceptors: the same Connect interceptors run around every call, in
  order, with the OpenTelemetry interceptor first when Connect is instrumented,
  as `ConnectASGIApplication` does. An interceptor applies to the stream kinds
  it implements (`intercept_unary`, `intercept_server_stream`, ...); metadata
  interceptors (`on_start`/`on_end`) apply to all.
- The startup sequence of `create_app`: the port opens only after the sidecar
  and the required components are ready, so an open port means the app is
  ready.

Calls get a `RequestContext` with the request metadata as headers (text
metadata only; binary `-bin` metadata is not passed) and the remaining deadline
as `timeout_ms`. Response headers and trailers set on the context are sent as
gRPC metadata. An error becomes the gRPC status with the same code and message;
error details are not sent.
"""

import asyncio
import logging
import re
import signal
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from functools import partial
from typing import Any

import grpc
from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.method import IdempotencyLevel, MethodInfo, StreamType
from connectrpc.request import Headers, RequestContext
from connectrpc_otel import ConnectInstrumentor, OpenTelemetryInterceptor

from app_runtime.app import Runtime, start_runtime
from app_runtime.client import bypass_proxies_for_sidecar
from app_runtime.components import ComponentRequirement
from app_runtime.logging import configure_logging
from app_runtime.settings import RuntimeSettings
from app_runtime.telemetry import configure_tracing

logger = logging.getLogger(__name__)

STATUS_CODES = {
    code: grpc.StatusCode["CANCELLED" if code is Code.CANCELED else code.name] for code in Code
}
STREAM_TYPES = {
    "unary": StreamType.UNARY,
    "server_streaming": StreamType.SERVER_STREAM,
    "client_streaming": StreamType.CLIENT_STREAM,
    "bidi_streaming": StreamType.BIDI_STREAM,
}
INTERCEPT_METHODS = {
    StreamType.UNARY: "intercept_unary",
    StreamType.SERVER_STREAM: "intercept_server_stream",
    StreamType.CLIENT_STREAM: "intercept_client_stream",
    StreamType.BIDI_STREAM: "intercept_bidi_stream",
}
# Streams whose response is a single message (awaited) rather than a stream.
SINGLE_RESPONSE = frozenset({StreamType.UNARY, StreamType.CLIENT_STREAM})

type Call = Callable[[Any, RequestContext], Any]


def grpc_service(
    service: Any, implementation: Any, *, interceptors: Iterable[Any] = ()
) -> grpc.GenericRpcHandler:
    """A grpcio handler that serves `implementation` of the generated Connect
    service protocol `service` (for example `CounterService`)."""
    desc = service.desc()
    chain = _with_telemetry(list(interceptors))
    handlers: dict[str, grpc.RpcMethodHandler] = {}
    for method_desc in desc.methods:
        method = MethodInfo(
            name=method_desc.name,
            service_name=desc.type_name,
            input=method_desc.input.type,
            output=method_desc.output.type,
            idempotency_level=IdempotencyLevel(method_desc.idempotency + 1),
            stream_type=STREAM_TYPES[method_desc.method_kind],
        )
        call: Call = getattr(implementation, _python_name(method_desc.name))
        for interceptor in reversed(chain):
            call = _intercept(method.stream_type, interceptor, call)
        handlers[method.name] = _method_handler(method, call)
    return grpc.method_handlers_generic_handler(desc.type_name, handlers)


def _python_name(rpc_name: str) -> str:
    """`ServerStream` -> `server_stream`, as in the generated service protocol."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", rpc_name).lower()


def _with_telemetry(interceptors: list[Any]) -> list[Any]:
    """Put the OpenTelemetry interceptor first when Connect is instrumented,
    as the instrumentation does for Connect applications."""
    instrumented = ConnectInstrumentor().is_instrumented_by_opentelemetry
    if not instrumented or any(isinstance(i, OpenTelemetryInterceptor) for i in interceptors):
        return interceptors
    return [OpenTelemetryInterceptor(), *interceptors]


def _intercept(stream_type: StreamType, interceptor: Any, call: Call) -> Call:
    intercept = getattr(interceptor, INTERCEPT_METHODS[stream_type], None)
    if intercept is not None:
        return partial(intercept, call)
    if hasattr(interceptor, "on_start"):
        if stream_type in SINGLE_RESPONSE:
            return partial(_metadata_single, interceptor, call)
        return partial(_metadata_stream, interceptor, call)
    return call


async def _metadata_single(interceptor: Any, call: Call, request: Any, ctx: RequestContext) -> Any:
    # As connectrpc runs metadata interceptors: on_end always runs, with a
    # CANCELED error when the call is cancelled (deadline passed, client gone).
    token = await interceptor.on_start(ctx)
    error: Exception | None = None
    try:
        return await call(request, ctx)
    except asyncio.CancelledError:
        error = ConnectError(Code.CANCELED, "Request was cancelled")
        raise
    except Exception as raised:
        error = raised
        raise
    finally:
        await interceptor.on_end(token, ctx, error)


async def _metadata_stream(
    interceptor: Any, call: Call, request: Any, ctx: RequestContext
) -> AsyncIterator[Any]:
    token = await interceptor.on_start(ctx)
    error: Exception | None = None
    try:
        responses = call(request, ctx)
        try:
            async for message in responses:
                yield message
        finally:
            close = getattr(responses, "aclose", None)
            if close is not None:
                await close()
    except asyncio.CancelledError:
        error = ConnectError(Code.CANCELED, "Request was cancelled")
        raise
    except Exception as raised:
        error = raised
        raise
    finally:
        await interceptor.on_end(token, ctx, error)


def _method_handler(method: MethodInfo, call: Call) -> grpc.RpcMethodHandler:
    codec: dict[str, Any] = {
        "request_deserializer": method.input.from_binary,
        "response_serializer": lambda message: message.to_binary(),
    }
    match method.stream_type:
        case StreamType.UNARY:
            return grpc.unary_unary_rpc_method_handler(_single(method, call), **codec)
        case StreamType.CLIENT_STREAM:
            return grpc.stream_unary_rpc_method_handler(_single(method, call), **codec)
        case StreamType.SERVER_STREAM:
            return grpc.unary_stream_rpc_method_handler(_stream(method, call), **codec)
        case StreamType.BIDI_STREAM:
            return grpc.stream_stream_rpc_method_handler(_stream(method, call), **codec)


def _single(
    method: MethodInfo, call: Call
) -> Callable[[Any, grpc.aio.ServicerContext], Awaitable[Any]]:
    async def handle(request: Any, context: grpc.aio.ServicerContext) -> Any:
        ctx = _request_context(method, context)
        try:
            response = await call(request, ctx)
        except Exception as error:
            await _abort(context, ctx, error)
            raise  # not reached: abort raises
        await _send_headers(context, ctx)
        _set_trailers(context, ctx)
        return response

    return handle


def _stream(
    method: MethodInfo, call: Call
) -> Callable[[Any, grpc.aio.ServicerContext], AsyncIterator[Any]]:
    async def handle(request: Any, context: grpc.aio.ServicerContext) -> AsyncIterator[Any]:
        ctx = _request_context(method, context)
        headers_sent = False
        try:
            async for message in call(request, ctx):
                if not headers_sent:
                    await _send_headers(context, ctx)
                    headers_sent = True
                yield message
        except Exception as error:
            await _abort(context, ctx, error)
        if not headers_sent:
            await _send_headers(context, ctx)
        _set_trailers(context, ctx)

    return handle


def _request_context(method: MethodInfo, context: grpc.aio.ServicerContext) -> RequestContext:
    metadata = context.invocation_metadata() or ()
    headers = Headers([(key, value) for key, value in metadata if isinstance(value, str)])
    remaining = context.time_remaining()
    return RequestContext(
        method=method,
        http_method="POST",
        request_headers=headers,
        timeout_ms=None if remaining is None else int(remaining * 1000),
        client_address=re.sub(r"^ipv[46]:", "", context.peer() or "") or None,
    )


async def _send_headers(context: grpc.aio.ServicerContext, ctx: RequestContext) -> None:
    if ctx.response_headers:
        await context.send_initial_metadata(tuple(ctx.response_headers.allitems()))


def _set_trailers(context: grpc.aio.ServicerContext, ctx: RequestContext) -> None:
    if ctx.response_trailers:
        context.set_trailing_metadata(tuple(ctx.response_trailers.allitems()))


async def _abort(context: grpc.aio.ServicerContext, ctx: RequestContext, error: Exception) -> None:
    if not isinstance(error, ConnectError):
        # Interceptors such as ErrorInterceptor normally translate errors; this
        # covers anything they let through (and stream kinds they skip).
        logger.error(
            "%s/%s failed",
            ctx.method.service_name,
            ctx.method.name,
            exc_info=(type(error), error, error.__traceback__),
        )
        error = ConnectError(Code.INTERNAL, "internal error")
    _set_trailers(context, ctx)
    await context.abort(STATUS_CODES[error.code], error.message)


type Services = Callable[[Runtime], Iterable[grpc.GenericRpcHandler]]


@asynccontextmanager
async def serve_grpc(
    settings: RuntimeSettings,
    services: Services,
    *,
    requirements: Sequence[ComponentRequirement] = (),
    grace_seconds: float = 10.0,
) -> AsyncIterator[Runtime]:
    """Run the startup sequence of `create_app`, then serve `services(runtime)`
    on `settings.host:settings.port` until the block exits.

    On exit, calls in progress get `grace_seconds` to finish before they are
    cancelled; then the app's resources and the Dapr client are closed.
    """
    async with AsyncExitStack() as stack:
        runtime = await start_runtime(settings, requirements, None, stack)
        server = grpc.aio.server()
        server.add_generic_rpc_handlers(tuple(services(runtime)))
        server.add_insecure_port(f"{settings.host}:{settings.port}")
        await server.start()
        logger.info("%s is ready", settings.name)
        try:
            yield runtime
        finally:
            logger.info("%s is shutting down", settings.name)
            await server.stop(grace_seconds)


def run_grpc(
    settings: RuntimeSettings,
    services: Services,
    *,
    requirements: Sequence[ComponentRequirement] = (),
) -> None:
    """Serve over gRPC until SIGTERM or SIGINT; the counterpart of `run`.

    The process exits with a non-zero code if startup fails. There is no reload
    on code changes (`APP_RELOAD`): restart the app instead.
    """
    configure_logging(settings.log_level)
    bypass_proxies_for_sidecar()
    tracer_provider = configure_tracing(settings.name)
    if settings.reload:
        logger.warning("APP_RELOAD is not supported for gRPC apps; restart to apply changes")

    async def main() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        async with serve_grpc(settings, services, requirements=requirements):
            await stop.wait()

    try:
        asyncio.run(main())
    finally:
        # The provider is shared by the process and shut down at exit.
        tracer_provider.force_flush()
