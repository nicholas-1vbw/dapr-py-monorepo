"""FastAPI application factory with the shared startup sequence.

Startup order, executed in the FastAPI lifespan before uvicorn accepts any
request (including probes):

1. Configure logging and tracing (before any Connect app or client exists).
2. Wait for the sidecar's outbound readiness (`/v1.0/healthz/outbound`), then
   create the Dapr client.
3. Check the app's required components through the Metadata API.
4. Let the app attach its own resources (`on_startup`), then serve.

Steps 2 and 3 share one deadline, `APP_STARTUP_TIMEOUT_SECONDS`; step 4 is the
app's own code and has no deadline. If a step fails, startup fails and the
process exits with a non-zero code, with `APP_RELOAD` too. On shutdown the app's
resources and the Dapr client are closed in reverse order and pending spans are
flushed.
"""

import asyncio
import logging
import multiprocessing
import os
import signal
import time
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import uvicorn
from connectrpc.server import ConnectASGIApplication
from dapr.aio.clients import DaprClient
from dapr.clients.http.conf import DAPR_API_TOKEN_HEADER
from dapr.clients.http.helpers import get_api_url
from dapr.conf import settings as settings_dapr
from fastapi import FastAPI

from app_runtime.client import bypass_proxies_for_sidecar
from app_runtime.components import (
    ComponentRequirement,
    loaded_components,
    wait_for_components,
)
from app_runtime.logging import configure_logging
from app_runtime.settings import RuntimeSettings
from app_runtime.telemetry import configure_tracing

logger = logging.getLogger(__name__)

PROBE_PATHS = frozenset({"/healthz", "/readyz"})


@dataclass
class Runtime:
    """Resources available to the app once startup succeeded."""

    settings: RuntimeSettings
    dapr: DaprClient
    exit_stack: AsyncExitStack
    state: dict[str, Any] = field(default_factory=dict)


StartupHook = Callable[[Runtime], Awaitable[None]]


def create_app(
    settings: RuntimeSettings,
    *,
    requirements: Sequence[ComponentRequirement] = (),
    on_startup: StartupHook | None = None,
    title: str | None = None,
) -> FastAPI:
    """Create a FastAPI app that serves only after the startup checks pass."""
    configure_logging(settings.log_level)
    bypass_proxies_for_sidecar()
    tracer_provider = configure_tracing(settings.name)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            try:
                runtime = await start_runtime(settings, requirements, on_startup, stack)
            except BaseException:
                _stop_reloader(settings)
                raise
            app.state.runtime = runtime
            logger.info("%s is ready", settings.name)
            try:
                yield
            finally:
                logger.info("%s is shutting down", settings.name)
        # The provider is shared by the process and shut down at exit.
        tracer_provider.force_flush()

    # Paths without FastAPI HTTP spans: probes (frequent, no useful trace data)
    # and mounted Connect services (traced by the Connect instrumentation).
    untraced_prefixes: list[str] = []
    app = FastAPI(
        title=title or settings.name,
        lifespan=lifespan,
        telemetry={"exclude": lambda scope: _untraced(scope, untraced_prefixes)},
    )
    app.state.untraced_prefixes = untraced_prefixes

    # The lifespan completes before uvicorn accepts connections, so a probe can
    # only be answered once the startup checks have passed.
    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz() -> dict[str, str]:
        return {"status": "ready"}

    return app


async def start_runtime(
    settings: RuntimeSettings,
    requirements: Sequence[ComponentRequirement],
    on_startup: StartupHook | None,
    stack: AsyncExitStack,
) -> Runtime:
    """Steps 2-4 of the startup sequence; steps 2 and 3 share the startup
    timeout. Used by `create_app` and by `app_runtime.grpc_server`."""
    deadline = time.monotonic() + settings.startup_timeout_seconds
    await wait_for_sidecar(deadline)
    # The SDK checks the sidecar again here; it is ready, so this returns at once.
    dapr = await stack.enter_async_context(DaprClient())
    await wait_for_components(
        requirements,
        lambda: loaded_components(dapr),
        timeout_seconds=max(deadline - time.monotonic(), 0),
    )
    runtime = Runtime(settings=settings, dapr=dapr, exit_stack=stack)
    if on_startup is not None:
        await on_startup(runtime)
    return runtime


class SidecarNotReady(RuntimeError):
    """The Dapr sidecar did not become ready before the startup timeout."""


async def wait_for_sidecar(deadline: float, *, interval_seconds: float = 0.5) -> None:
    """Wait for the sidecar's outbound readiness (`/v1.0/healthz/outbound`).

    Outbound readiness means the sidecar can serve the app's API calls; it does
    not wait for the app itself, which is not listening yet.
    """
    url = f"{get_api_url()}/healthz/outbound"
    last_error = "no answer"
    while True:
        remaining = deadline - time.monotonic()
        try:
            status = await asyncio.to_thread(_get_status, url, max(min(remaining, 5.0), 0.1))
            if 200 <= status < 300:
                return
            last_error = f"HTTP {status}"
        except OSError as error:
            last_error = str(error)
        if time.monotonic() >= deadline:
            raise SidecarNotReady(f"Dapr sidecar at {url} is not ready: {last_error}")
        await asyncio.sleep(interval_seconds)


def _get_status(url: str, timeout: float) -> int:
    request = urllib.request.Request(url)
    if settings_dapr.DAPR_API_TOKEN:
        request.add_header(DAPR_API_TOKEN_HEADER, settings_dapr.DAPR_API_TOKEN)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def _stop_reloader(settings: RuntimeSettings) -> None:
    """With `APP_RELOAD`, end the reloader too when startup fails.

    The reloader would otherwise keep running and wait for a code change, so a
    failed app would look alive; stopping it makes the process exit as it does
    without reload.
    """
    if settings.reload and multiprocessing.parent_process() is not None:
        logger.error("startup failed; stopping the reloader")
        os.kill(os.getppid(), signal.SIGTERM)


def mount_connect(app: FastAPI, connect_app: ConnectASGIApplication[Any]) -> None:
    """Serve a generated Connect ASGI application under its service path."""
    app.state.untraced_prefixes.append(connect_app.path)
    app.mount(connect_app.path, connect_app)


def _untraced(scope: MutableMapping[str, Any], prefixes: Sequence[str]) -> bool:
    path = scope.get("path", "")
    return path in PROBE_PATHS or any(path.startswith(prefix) for prefix in prefixes)


def run(factory: str, settings: RuntimeSettings) -> None:
    """Serve the app built by `factory` ("module:function") with uvicorn.

    The factory is imported by uvicorn, which allows `APP_RELOAD=true` for local
    development. Logging is configured as JSON by the factory.
    """
    uvicorn.run(
        factory,
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        reload_dirs=settings.reload_dirs or None,
        log_config=None,
    )
