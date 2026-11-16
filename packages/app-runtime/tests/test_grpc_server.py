"""Connect service implementations served over gRPC by grpcio.

Calls use the generated Connect client over gRPC, as other apps do through the
sidecar, directly against the server (no Dapr).
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

import grpc
import pytest
from app_runtime import Runtime, RuntimeSettings
from app_runtime import grpc_server as grpc_server_module
from app_runtime.grpc_server import grpc_service, serve_grpc
from app_runtime.interceptors import server_interceptors
from connectrpc.client import ResponseMetadata
from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.protocol import ProtocolType
from connectrpc_otel import OpenTelemetryInterceptor
from contracts.platform.testing.v1.probe_connect import ProbeService, ProbeServiceClient
from contracts.platform.testing.v1.probe_pb import (
    ChatRequest,
    ChatResponse,
    EchoRequest,
    EchoResponse,
    JoinRequest,
    JoinResponse,
    SplitRequest,
    SplitResponse,
)
from pyqwest import Client, HTTPTransport, HTTPVersion


class Probe(ProbeService):
    def __init__(self, calls: list[str] | None = None) -> None:
        self.calls = calls if calls is not None else []
        self.headers: dict[str, str] = {}
        self.timeout_ms: float | None = None

    async def echo(self, request: EchoRequest, ctx: Any, /) -> EchoResponse:
        self.calls.append("echo")
        self.headers = dict(ctx.request_headers.items())
        self.timeout_ms = ctx.timeout_ms
        if request.text == "boom":
            raise RuntimeError("secret detail")
        if request.text == "busy":
            raise ConnectError(Code.ABORTED, "try again")
        if request.text == "slow":
            await asyncio.sleep(5)
        ctx.response_headers["x-probe-header"] = "h"
        ctx.response_trailers["x-probe-trailer"] = "t"
        return EchoResponse(text=request.text)

    async def split(self, request: SplitRequest, ctx: Any, /) -> AsyncIterator[SplitResponse]:
        self.calls.append("split")
        for character in request.text:
            if character == "!":
                raise RuntimeError("secret detail")
            yield SplitResponse(text=character)

    async def join(self, request: AsyncIterator[JoinRequest], ctx: Any, /) -> JoinResponse:
        return JoinResponse(text="".join([message.text async for message in request]))

    async def chat(
        self, request: AsyncIterator[ChatRequest], ctx: Any, /
    ) -> AsyncIterator[ChatResponse]:
        async for message in request:
            yield ChatResponse(text=message.text.upper())


async def drain(stream: AsyncIterator[Any], into: list[str]) -> None:
    async for message in stream:
        into.append(message.text)


@asynccontextmanager
async def served(handler: grpc.GenericRpcHandler) -> AsyncIterator[ProbeServiceClient]:
    server = grpc.aio.server()
    server.add_generic_rpc_handlers((handler,))
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        async with ProbeServiceClient(
            f"http://127.0.0.1:{port}",
            protocol=ProtocolType.GRPC,
            http_client=Client(HTTPTransport(http_version=HTTPVersion.HTTP2)),
            timeout_ms=5000,
        ) as client:
            yield client
    finally:
        await server.stop(0)


async def test_unary_call_with_metadata_and_deadline() -> None:
    probe = Probe()
    async with served(grpc_service(ProbeService, probe)) as client:
        with ResponseMetadata() as metadata:
            response = await client.echo(EchoRequest(text="hi"), headers={"x-caller": "c"})
    assert response.text == "hi"
    assert probe.headers["x-caller"] == "c"
    assert probe.timeout_ms is not None
    assert 0 < probe.timeout_ms <= 5000
    assert metadata.headers.get("x-probe-header") == "h"
    assert metadata.trailers.get("x-probe-trailer") == "t"


async def test_validation_and_error_mapping_match_connect() -> None:
    async with served(
        grpc_service(ProbeService, Probe(), interceptors=server_interceptors())
    ) as client:
        with pytest.raises(ConnectError) as invalid:
            await client.echo(EchoRequest(text=""))
        with pytest.raises(ConnectError) as busy:
            await client.echo(EchoRequest(text="busy"))
        with pytest.raises(ConnectError) as boom:
            await client.echo(EchoRequest(text="boom"))
    assert invalid.value.code == Code.INVALID_ARGUMENT
    assert "text" in invalid.value.message
    assert (busy.value.code, busy.value.message) == (Code.ABORTED, "try again")
    assert boom.value.code == Code.INTERNAL
    assert "secret" not in boom.value.message


async def test_server_stream_and_its_errors() -> None:
    async with served(grpc_service(ProbeService, Probe())) as client:
        letters = [m.text async for m in client.split(SplitRequest(text="abc"))]
        received: list[str] = []
        with pytest.raises(ConnectError) as failed:
            await drain(client.split(SplitRequest(text="ab!c")), received)
    assert letters == ["a", "b", "c"]
    assert received == ["a", "b"]
    assert failed.value.code == Code.INTERNAL
    assert "secret" not in failed.value.message


async def test_client_stream() -> None:
    async def texts() -> AsyncIterator[JoinRequest]:
        for text in ("a", "b", "c"):
            yield JoinRequest(text=text)

    async with served(grpc_service(ProbeService, Probe())) as client:
        response = await client.join(texts())
    assert response.text == "abc"


async def test_bidi_stream_is_full_duplex() -> None:
    """Each reply arrives before the next request is sent."""
    outgoing: asyncio.Queue[ChatRequest | None] = asyncio.Queue()

    async def requests() -> AsyncIterator[ChatRequest]:
        while (message := await outgoing.get()) is not None:
            yield message

    async with served(grpc_service(ProbeService, Probe())) as client:
        replies = client.chat(requests()).__aiter__()
        answers = []
        for text in ("a", "b", "c"):
            await outgoing.put(ChatRequest(text=text))
            answers.append((await asyncio.wait_for(replies.__anext__(), 5)).text)
        await outgoing.put(None)
        assert [m async for m in replies] == []
    assert answers == ["A", "B", "C"]


class Recorder:
    """A metadata interceptor and a unary interceptor, recording their calls."""

    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.errors: list[Exception | None] = []

    async def on_start(self, ctx: Any) -> str:
        self.calls.append("metadata.start")
        return "token"

    async def on_end(self, token: str, ctx: Any, error: Exception | None) -> None:
        self.errors.append(error)
        self.calls.append(f"metadata.end:{type(error).__name__ if error else 'ok'}")


class UnaryRecorder:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def intercept_unary(self, call_next: Any, request: Any, ctx: Any) -> Any:
        self.calls.append("unary.before")
        response = await call_next(request, ctx)
        self.calls.append("unary.after")
        return response


async def test_interceptors_run_in_order_for_the_kinds_they_implement() -> None:
    calls: list[str] = []
    handler = grpc_service(
        ProbeService, Probe(calls), interceptors=[Recorder(calls), UnaryRecorder(calls)]
    )
    async with served(handler) as client:
        await client.echo(EchoRequest(text="hi"))
        unary_calls = list(calls)
        calls.clear()
        with pytest.raises(ConnectError):
            await drain(client.split(SplitRequest(text="a!")), [])
    assert unary_calls == [
        "metadata.start",
        "unary.before",
        "echo",
        "unary.after",
        "metadata.end:ok",
    ]
    # The unary interceptor does not apply to streams; the metadata one does.
    assert calls == ["metadata.start", "split", "metadata.end:RuntimeError"]


async def test_binary_metadata_is_not_passed_as_headers() -> None:
    probe = Probe()
    server = grpc.aio.server()
    server.add_generic_rpc_handlers((grpc_service(ProbeService, probe),))
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            echo = channel.unary_unary(
                "/platform.testing.v1.ProbeService/Echo",
                request_serializer=EchoRequest.to_binary,
                response_deserializer=EchoResponse.from_binary,
            )
            response = await echo(
                EchoRequest(text="hi"),
                metadata=(("grpc-trace-bin", b"\x00\x01"), ("x-text", "v")),
            )
    finally:
        await server.stop(0)
    assert response.text == "hi"
    assert probe.headers["x-text"] == "v"
    assert "grpc-trace-bin" not in probe.headers


def test_telemetry_interceptor_comes_first_when_connect_is_instrumented(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = UnaryRecorder([])
    monkeypatch.setattr(
        grpc_server_module.ConnectInstrumentor, "is_instrumented_by_opentelemetry", True
    )
    chain = grpc_server_module._with_telemetry([other])
    assert isinstance(chain[0], OpenTelemetryInterceptor)
    assert chain[1:] == [other]
    assert grpc_server_module._with_telemetry(chain) == chain

    monkeypatch.setattr(
        grpc_server_module.ConnectInstrumentor, "is_instrumented_by_opentelemetry", False
    )
    assert grpc_server_module._with_telemetry([other]) == [other]


async def _port_is_open(port: int) -> bool:
    try:
        _, writer = await asyncio.open_connection("127.0.0.1", port)
    except OSError:
        return False
    writer.close()
    return True


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def test_port_opens_only_after_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = RuntimeSettings(host="127.0.0.1", port=_free_port())

    async def failing_start(*args: Any) -> Runtime:
        assert not await _port_is_open(settings.port)
        raise RuntimeError("component missing")

    monkeypatch.setattr(grpc_server_module, "start_runtime", failing_start)
    with pytest.raises(RuntimeError, match="component missing"):
        async with serve_grpc(settings, lambda runtime: []):
            pass
    assert not await _port_is_open(settings.port)

    async def start(settings: RuntimeSettings, _req: Any, _hook: Any, stack: AsyncExitStack):
        return Runtime(settings=settings, dapr=None, exit_stack=stack)  # type: ignore[arg-type]

    monkeypatch.setattr(grpc_server_module, "start_runtime", start)
    async with serve_grpc(
        settings, lambda runtime: [grpc_service(ProbeService, Probe())], grace_seconds=0
    ):
        assert await _port_is_open(settings.port)


async def test_cancelled_call_still_ends_the_metadata_interceptors() -> None:
    """A call cancelled by its deadline still ends the telemetry span (as CANCELED)."""
    calls: list[str] = []
    recorder = Recorder(calls)
    async with served(grpc_service(ProbeService, Probe(calls), interceptors=[recorder])) as client:
        with pytest.raises(ConnectError) as raised:
            await client.echo(EchoRequest(text="slow"), timeout_ms=200)
        for _ in range(50):
            if recorder.errors:
                break
            await asyncio.sleep(0.05)
    assert raised.value.code == Code.DEADLINE_EXCEEDED
    (error,) = recorder.errors
    assert isinstance(error, ConnectError)
    assert error.code == Code.CANCELED
