"""Calling other apps through the local Dapr sidecar with Connect.

Requests use the Connect protocol over HTTP and go to the sidecar's service
invocation API, `<sidecar>/v1.0/invoke/<app-id>/method/<service>/<method>`,
which forwards them to the target app. The target's HTTP status and Connect
error body are passed back unchanged.

The sidecar address and API token come from the Dapr SDK settings
(`DAPR_HTTP_ENDPOINT`, or `DAPR_RUNTIME_HOST` and `DAPR_HTTP_PORT`, and
`DAPR_API_TOKEN`), so Connect clients and the SDK always talk to the same
sidecar with the same credentials.
"""

import os
from typing import Any, Protocol
from urllib.parse import urlsplit

from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.request import RequestContext
from dapr.clients.http.conf import DAPR_API_TOKEN_HEADER
from dapr.clients.http.helpers import get_api_url
from dapr.conf import settings

LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")


def bypass_proxies_for_sidecar() -> None:
    """Keep requests to the local sidecar away from configured proxies.

    HTTP clients (the Connect client, the Dapr SDK's health check) and gRPC honour
    HTTP_PROXY / HTTPS_PROXY / ALL_PROXY from the environment. Adding the
    sidecar's host to NO_PROXY makes every client reach the sidecar directly,
    while other traffic still uses the proxies.
    """
    sidecar_host = urlsplit(get_api_url()).hostname or ""
    for variable in ("NO_PROXY", "no_proxy"):
        current = [h.strip() for h in os.environ.get(variable, "").split(",") if h.strip()]
        missing = [h for h in (*LOOPBACK_HOSTS, sidecar_host) if h and h not in current]
        if missing:
            os.environ[variable] = ",".join([*current, *missing])


def invoke_url(app_id: str) -> str:
    """Base URL of Connect calls to `app_id`: the URL the Dapr SDK uses for
    `invoke_method`, with the Connect method path appended by the client."""
    return f"{get_api_url()}/invoke/{app_id}/method"


class DaprApiTokenInterceptor:
    """Adds the Dapr API token to outgoing calls when one is configured, as the
    Dapr SDK does for its own requests."""

    async def on_start(self, ctx: RequestContext) -> None:
        token = settings.DAPR_API_TOKEN
        if token:
            ctx.request_headers[DAPR_API_TOKEN_HEADER] = token

    async def on_end(self, token: None, ctx: RequestContext, error: Exception | None, /) -> None:
        return


class ConnectClientType[C](Protocol):
    def __call__(self, address: str, *, timeout_ms: int | None, interceptors: Any) -> C: ...


def dapr_client[C](client_type: ConnectClientType[C], app_id: str, *, timeout_ms: int) -> C:
    """A generated Connect client whose calls reach `app_id` through Dapr.

    Create it after tracing is configured (in the app's startup hook) so that it
    is instrumented, and close it on shutdown.
    """
    return client_type(
        invoke_url(app_id), timeout_ms=timeout_ms, interceptors=[DaprApiTokenInterceptor()]
    )


# Errors that mean the target could not be reached or did not answer in time,
# as opposed to errors returned by the target itself.
UNAVAILABLE_CODES = frozenset({Code.UNAVAILABLE, Code.DEADLINE_EXCEEDED})

# When Dapr cannot deliver an invocation (unknown or unreachable app id), the
# sidecar answers HTTP 500 with its own JSON error
# ({"errorCode": "ERR_DIRECT_INVOKE", "message": "failed to invoke, id: ..."}),
# which the Connect client reports as UNKNOWN with that message.
DAPR_INVOKE_FAILURE_PREFIX = "failed to invoke, id: "


def is_unavailable(error: ConnectError) -> bool:
    """True when the target app could not be reached through Dapr."""
    return error.code in UNAVAILABLE_CODES or (
        error.code == Code.UNKNOWN and error.message.startswith(DAPR_INVOKE_FAILURE_PREFIX)
    )
