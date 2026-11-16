"""Helpers for integration tests."""

import asyncio
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

import uvicorn
from app_runtime import ComponentRequirement, RuntimeSettings
from app_runtime.grpc_server import Services, serve_grpc
from fastapi import FastAPI


class StartupFailed(RuntimeError):
    """The app's lifespan startup failed, so it never served requests."""


@contextmanager
def serve(app: FastAPI, port: int) -> Iterator[str]:
    """Serve `app` with uvicorn in a background thread; yield its base URL."""
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, lifespan="on")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        if not thread.is_alive():
            raise StartupFailed(f"app on port {port} failed to start")
        thread.join(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=30)


@contextmanager
def serve_grpc_app(
    settings: RuntimeSettings,
    services: Services,
    requirements: Sequence[ComponentRequirement] = (),
) -> Iterator[None]:
    """Run `app_runtime.grpc_server.serve_grpc` in a background thread with its
    own event loop, as `run_grpc` does in an app process."""
    started = threading.Event()
    running: list[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = []
    failure: list[BaseException] = []

    async def main() -> None:
        stopped = asyncio.Event()
        running.append((asyncio.get_running_loop(), stopped))
        async with serve_grpc(settings, services, requirements=requirements, grace_seconds=5):
            started.set()
            await stopped.wait()

    def target() -> None:
        try:
            asyncio.run(main())
        except BaseException as error:
            failure.append(error)
        finally:
            started.set()

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    started.wait()
    if failure:
        raise StartupFailed(f"gRPC app on port {settings.port} failed to start") from failure[0]
    try:
        yield
    finally:
        loop, stopped = running[0]
        loop.call_soon_threadsafe(stopped.set)
        thread.join(timeout=30)
