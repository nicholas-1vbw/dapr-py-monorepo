"""Connect clients reach other apps through the sidecar configured for the Dapr SDK."""

import pytest
from app_runtime.client import DaprApiTokenInterceptor, dapr_client, invoke_url
from connectrpc.code import Code
from connectrpc.errors import ConnectError
from contracts.platform.testing.v1.probe_connect import (
    ProbeService,
    ProbeServiceASGIApplication,
    ProbeServiceClient,
)
from contracts.platform.testing.v1.probe_pb import EchoRequest, EchoResponse
from dapr.clients.http.helpers import get_api_url


@pytest.fixture(autouse=True)
def _sidecar_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("DAPR_HTTP_ENDPOINT", "DAPR_RUNTIME_HOST", "DAPR_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DAPR_HTTP_PORT", "3511")


def test_invoke_url_matches_the_sdk() -> None:
    assert invoke_url("probe") == f"{get_api_url()}/invoke/probe/method"
    assert invoke_url("probe") == "http://127.0.0.1:3511/v1.0/invoke/probe/method"


def test_invoke_url_follows_http_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAPR_HTTP_ENDPOINT", "https://sidecar.example:443")
    assert invoke_url("probe") == "https://sidecar.example:443/v1.0/invoke/probe/method"


class _Ctx:
    def __init__(self) -> None:
        self.request_headers: dict[str, str] = {}


async def test_token_header_only_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _Ctx()
    await DaprApiTokenInterceptor().on_start(ctx)  # type: ignore[arg-type]
    assert ctx.request_headers == {}

    monkeypatch.setenv("DAPR_API_TOKEN", "secret")
    await DaprApiTokenInterceptor().on_start(ctx)  # type: ignore[arg-type]
    assert ctx.request_headers == {"dapr-api-token": "secret"}


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


def test_unknown_with_other_message_is_not_unavailable() -> None:
    from app_runtime.client import is_unavailable

    assert not is_unavailable(ConnectError(Code.UNKNOWN, "boom"))
    assert is_unavailable(ConnectError(Code.UNKNOWN, "failed to invoke, id: probe, err: x"))


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
