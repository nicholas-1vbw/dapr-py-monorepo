"""Calling other apps through the local Dapr sidecar with generated Connect clients.

A call reaches the target app over one of two protocols:

- `InvokeProtocol.CONNECT` (default): the Connect protocol over HTTP to the
  sidecar's service invocation API,
  `<sidecar>/v1.0/invoke/<app-id>/method/<service>/<method>`. The target serves
  its Connect services over HTTP (`mount_connect`). The target's HTTP status
  and Connect error body are passed back unchanged.
- `InvokeProtocol.GRPC`: gRPC over HTTP/2 to the sidecar's gRPC port, with the
  target app id in the `dapr-app-id` header (Dapr gRPC proxying). The target
  serves the same services over gRPC (`app_runtime.grpc_server`).

The sidecar addresses and API token come from the Dapr SDK settings
(`DAPR_HTTP_ENDPOINT` or `DAPR_RUNTIME_HOST` and `DAPR_HTTP_PORT`;
`DAPR_GRPC_ENDPOINT` or `DAPR_RUNTIME_HOST` and `DAPR_GRPC_PORT`;
`DAPR_API_TOKEN`), so Connect clients and the SDK always talk to the same
sidecar with the same credentials.
"""

import functools
import os
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import urlsplit

from connectrpc.code import Code
from connectrpc.compression import Compression
from connectrpc.errors import ConnectError
from connectrpc.method import StreamType
from connectrpc.protocol import ProtocolType
from connectrpc.request import RequestContext
from dapr.clients.http.conf import DAPR_API_TOKEN_HEADER
from dapr.clients.http.helpers import get_api_url
from dapr.conf import settings
from dapr.conf.helpers import GrpcEndpoint
from pyqwest import Client, HTTPTransport, HTTPVersion

LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")

# Headers of gRPC calls proxied by the sidecar: the target app id, and a marker
# that Dapr's documentation requires on streaming calls.
DAPR_APP_ID_HEADER = "dapr-app-id"
DAPR_STREAM_HEADER = "dapr-stream"


class InvokeProtocol(StrEnum):
    """Protocol of calls from one app to another through the sidecar."""

    CONNECT = "connect"
    GRPC = "grpc"


def bypass_proxies_for_sidecar() -> None:
    """Keep requests to the local sidecar away from configured proxies.

    HTTP clients (the Connect client, the Dapr SDK's health check) and gRPC honour
    HTTP_PROXY / HTTPS_PROXY / ALL_PROXY from the environment. Adding the
    sidecar's hosts to NO_PROXY makes every client reach the sidecar directly,
    while other traffic still uses the proxies.
    """
    sidecar_hosts = {urlsplit(get_api_url()).hostname or "", _grpc_endpoint().hostname or ""}
    for variable in ("NO_PROXY", "no_proxy"):
        current = [h.strip() for h in os.environ.get(variable, "").split(",") if h.strip()]
        wanted = [*LOOPBACK_HOSTS, *sorted(sidecar_hosts)]
        missing = [h for h in dict.fromkeys(wanted) if h and h not in current]
        if missing:
            os.environ[variable] = ",".join([*current, *missing])


def invoke_url(app_id: str) -> str:
    """Base URL of Connect calls to `app_id`: the URL the Dapr SDK uses for
    `invoke_method`, with the Connect method path appended by the client."""
    return f"{get_api_url()}/invoke/{app_id}/method"


def _grpc_endpoint() -> GrpcEndpoint:
    """The sidecar's gRPC endpoint, resolved as the Dapr SDK resolves it."""
    endpoint = (
        settings.DAPR_GRPC_ENDPOINT or f"{settings.DAPR_RUNTIME_HOST}:{settings.DAPR_GRPC_PORT}"
    )
    return GrpcEndpoint(endpoint)


def grpc_url() -> str:
    """Base URL of gRPC calls proxied by the sidecar."""
    endpoint = _grpc_endpoint()
    return f"{'https' if endpoint.tls else 'http'}://{endpoint.hostname}:{endpoint.port}"


@functools.cache
def _sidecar_http2_client() -> Client:
    """One HTTP/2 connection pool to the sidecar's gRPC port per process.

    gRPC needs HTTP/2; without TLS it has to be requested explicitly. The pool
    is shared by all gRPC clients of the process and lives as long as it.
    """
    return Client(HTTPTransport(http_version=HTTPVersion.HTTP2))


class DaprInterceptor:
    """Adds what the sidecar expects on outgoing calls: the Dapr API token when
    one is configured (as the Dapr SDK does for its own requests) and, for gRPC
    proxying, the target app id and the streaming marker."""

    def __init__(self, app_id: str | None = None) -> None:
        self._app_id = app_id

    async def on_start(self, ctx: RequestContext) -> None:
        token = settings.DAPR_API_TOKEN
        if token:
            ctx.request_headers[DAPR_API_TOKEN_HEADER] = token
        if self._app_id is not None:
            ctx.request_headers[DAPR_APP_ID_HEADER] = self._app_id
            if ctx.method.stream_type is not StreamType.UNARY:
                ctx.request_headers[DAPR_STREAM_HEADER] = "true"

    async def on_end(self, token: None, ctx: RequestContext, error: Exception | None, /) -> None:
        return


class ConnectClientType[C](Protocol):
    def __call__(
        self,
        address: str,
        *,
        protocol: ProtocolType = ...,
        send_compression: Compression | None = ...,
        timeout_ms: int | None = ...,
        interceptors: Any = ...,
        http_client: Client | None = ...,
    ) -> C: ...


def dapr_client[C](
    client_type: ConnectClientType[C],
    app_id: str,
    *,
    timeout_ms: int,
    protocol: InvokeProtocol = InvokeProtocol.CONNECT,
) -> C:
    """A generated Connect client whose calls reach `app_id` through Dapr.

    Create it after tracing is configured (in the app's startup hook) so that it
    is instrumented, and close it on shutdown.
    """
    if protocol is InvokeProtocol.GRPC:
        return client_type(
            grpc_url(),
            protocol=ProtocolType.GRPC,
            # The sidecar's gRPC proxy decompresses requests but forwards their
            # original content-length, which the target then rejects; the
            # Connect client sends content-length, so requests stay uncompressed.
            send_compression=None,
            timeout_ms=timeout_ms,
            interceptors=[DaprInterceptor(app_id)],
            http_client=_sidecar_http2_client(),
        )
    return client_type(
        invoke_url(app_id),
        timeout_ms=timeout_ms,
        interceptors=[DaprInterceptor()],
    )


# Errors that mean the target could not be reached or did not answer in time,
# as opposed to errors returned by the target itself.
UNAVAILABLE_CODES = frozenset({Code.UNAVAILABLE, Code.DEADLINE_EXCEEDED})

# When Dapr cannot deliver a call (unknown or unreachable app id), the sidecar
# answers with its own error, which the Connect client reports as UNKNOWN:
# HTTP 500 {"errorCode": "ERR_DIRECT_INVOKE", "message": "failed to invoke, id: ..."}
# for service invocation, "failed to resolve address for '<app>-dapr...'" for
# gRPC proxying in Kubernetes. (Standalone, gRPC proxying to an unknown app id
# waits for the deadline instead: DEADLINE_EXCEEDED.)
DAPR_DELIVERY_FAILURE_PREFIXES = ("failed to invoke, id: ", "failed to resolve address for ")


def is_unavailable(error: ConnectError) -> bool:
    """True when the target app could not be reached through Dapr."""
    return error.code in UNAVAILABLE_CODES or (
        error.code == Code.UNKNOWN and error.message.startswith(DAPR_DELIVERY_FAILURE_PREFIXES)
    )
