"""Connect clients reach other apps through the sidecar configured for the Dapr SDK,
over Connect (service invocation) or gRPC (gRPC proxying)."""

import pytest
from app_runtime.client import (
    DaprInterceptor,
    InvokeProtocol,
    dapr_client,
    grpc_url,
    invoke_url,
)
from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.method import StreamType
from contracts.platform.testing.v1.probe_connect import (
    ProbeService,
    ProbeServiceASGIApplication,
    ProbeServiceClient,
)
from contracts.platform.testing.v1.probe_pb import EchoRequest, EchoResponse
from dapr.clients.http.helpers import get_api_url


@pytest.fixture(autouse=True)
def _sidecar_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("DAPR_HTTP_ENDPOINT", "DAPR_GRPC_ENDPOINT", "DAPR_RUNTIME_HOST", "DAPR_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DAPR_HTTP_PORT", "3511")
    monkeypatch.setenv("DAPR_GRPC_PORT", "50011")


def test_invoke_url_matches_the_sdk() -> None:
    assert invoke_url("probe") == f"{get_api_url()}/invoke/probe/method"
    assert invoke_url("probe") == "http://127.0.0.1:3511/v1.0/invoke/probe/method"


def test_invoke_url_follows_http_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAPR_HTTP_ENDPOINT", "https://sidecar.example:443")
    assert invoke_url("probe") == "https://sidecar.example:443/v1.0/invoke/probe/method"


class _Method:
    def __init__(self, stream_type: StreamType) -> None:
        self.stream_type = stream_type


class _Ctx:
    def __init__(self, stream_type: StreamType = StreamType.UNARY) -> None:
        self.request_headers: dict[str, str] = {}
        self.method = _Method(stream_type)


def test_grpc_url_follows_the_sdk_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    assert grpc_url() == "http://127.0.0.1:50011"
    monkeypatch.setenv("DAPR_GRPC_ENDPOINT", "sidecar.example:443?tls=true")
    assert grpc_url() == "https://sidecar.example:443"
    monkeypatch.setenv("DAPR_GRPC_ENDPOINT", "sidecar.local:50001")
    assert grpc_url() == "http://sidecar.local:50001"


async def test_token_header_only_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _Ctx()
    await DaprInterceptor().on_start(ctx)  # type: ignore[arg-type]
    assert ctx.request_headers == {}

    monkeypatch.setenv("DAPR_API_TOKEN", "secret")
    await DaprInterceptor().on_start(ctx)  # type: ignore[arg-type]
    assert ctx.request_headers == {"dapr-api-token": "secret"}


async def test_app_id_header_for_grpc_proxying() -> None:
    ctx = _Ctx()
    await DaprInterceptor("probe").on_start(ctx)  # type: ignore[arg-type]
    assert ctx.request_headers == {"dapr-app-id": "probe"}


@pytest.mark.parametrize(
    "stream_type", [StreamType.SERVER_STREAM, StreamType.CLIENT_STREAM, StreamType.BIDI_STREAM]
)
async def test_streaming_marker_for_grpc_streams(stream_type: StreamType) -> None:
    ctx = _Ctx(stream_type)
    await DaprInterceptor("probe").on_start(ctx)  # type: ignore[arg-type]
    assert ctx.request_headers == {"dapr-app-id": "probe", "dapr-stream": "true"}


class _Echo(ProbeService):
    def __init__(self) -> None:
        self.seen: dict[str, str] = {}

    async def echo(self, request: EchoRequest, ctx, /) -> EchoResponse:  # type: ignore[override]
        self.seen = dict(ctx.request_headers.items())
        return EchoResponse(text=request.text)


async def test_client_sends_token_and_uses_invoke_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end over HTTP, without Dapr: a stand-in sidecar serves the invoke path."""
    import socket
    import threading

    import uvicorn
    from starlette.applications import Starlette
    from starlette.routing import Mount

    echo = _Echo()
    app = Starlette(routes=[Mount("/v1.0/invoke/probe/method", ProbeServiceASGIApplication(echo))])
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, port=port, log_config=None, lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        thread.join(0.02)
    try:
        monkeypatch.setenv("DAPR_HTTP_PORT", str(port))
        monkeypatch.setenv("DAPR_API_TOKEN", "secret")
        async with dapr_client(ProbeServiceClient, "probe", timeout_ms=5000) as client:
            response = await client.echo(EchoRequest(text="a"))
    finally:
        server.should_exit = True
        thread.join(5)
    assert response.text == "a"
    assert echo.seen.get("dapr-api-token") == "secret"


async def test_grpc_client_reaches_the_sidecar_grpc_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """Over gRPC, without Dapr: a stand-in sidecar serves the gRPC method."""
    import grpc
    from app_runtime.grpc_server import grpc_service

    echo = _Echo()
    server = grpc.aio.server()
    server.add_generic_rpc_handlers((grpc_service(ProbeService, echo),))
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        monkeypatch.setenv("DAPR_GRPC_PORT", str(port))
        monkeypatch.setenv("DAPR_API_TOKEN", "secret")
        client = dapr_client(
            ProbeServiceClient, "probe", timeout_ms=5000, protocol=InvokeProtocol.GRPC
        )
        async with client:
            response = await client.echo(EchoRequest(text="a"))
    finally:
        await server.stop(0)
    assert response.text == "a"
    assert echo.seen.get("dapr-app-id") == "probe"
    assert echo.seen.get("dapr-api-token") == "secret"
    # Requests are not compressed (see dapr_client).
    assert "grpc-encoding" not in echo.seen


def test_unknown_with_other_message_is_not_unavailable() -> None:
    from app_runtime.client import is_unavailable

    assert not is_unavailable(ConnectError(Code.UNKNOWN, "boom"))
    assert is_unavailable(ConnectError(Code.UNKNOWN, "failed to invoke, id: probe, err: x"))
    assert is_unavailable(
        ConnectError(Code.UNKNOWN, "failed to resolve address for 'probe-dapr.default.svc'")
    )


def test_sidecar_bypasses_configured_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    from app_runtime.client import bypass_proxies_for_sidecar

    monkeypatch.setenv("NO_PROXY", "internal.example")
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setenv("DAPR_HTTP_ENDPOINT", "http://sidecar.local:3500")
    bypass_proxies_for_sidecar()
    bypass_proxies_for_sidecar()  # idempotent
    import os

    assert os.environ["NO_PROXY"] == "internal.example,localhost,127.0.0.1,::1,sidecar.local"
    assert os.environ["no_proxy"] == "localhost,127.0.0.1,::1,sidecar.local"
