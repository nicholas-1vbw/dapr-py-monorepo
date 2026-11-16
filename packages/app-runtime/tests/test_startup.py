import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from app_runtime.app import SidecarNotReady, wait_for_sidecar


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def sidecar(monkeypatch: pytest.MonkeyPatch):
    """A stand-in sidecar answering /v1.0/healthz/outbound with `status`."""
    state = {"status": 500, "token": None}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            state["token"] = self.headers.get("dapr-api-token")
            self.send_response(state["status"])
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.delenv("DAPR_HTTP_ENDPOINT", raising=False)
    monkeypatch.setenv("DAPR_HTTP_PORT", str(server.server_address[1]))
    yield state
    server.shutdown()


async def test_returns_once_outbound_is_ready(sidecar, monkeypatch: pytest.MonkeyPatch) -> None:
    sidecar["status"] = 204
    monkeypatch.setenv("DAPR_API_TOKEN", "secret")
    await wait_for_sidecar(time.monotonic() + 5)
    assert sidecar["token"] == "secret"


async def test_not_ready_sidecar_fails_at_the_deadline(sidecar) -> None:
    started = time.monotonic()
    with pytest.raises(SidecarNotReady, match="HTTP 500"):
        await wait_for_sidecar(time.monotonic() + 0.5, interval_seconds=0.1)
    assert time.monotonic() - started < 3


async def test_missing_sidecar_fails_at_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DAPR_HTTP_ENDPOINT", raising=False)
    monkeypatch.setenv("DAPR_HTTP_PORT", str(_free_port()))
    with pytest.raises(SidecarNotReady, match="not ready"):
        await wait_for_sidecar(time.monotonic() + 0.5, interval_seconds=0.1)
