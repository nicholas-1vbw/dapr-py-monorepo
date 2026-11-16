"""Helpers for integration tests."""

import threading
from collections.abc import Iterator
from contextlib import contextmanager

import uvicorn
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
