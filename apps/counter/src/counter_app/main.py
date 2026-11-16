"""Counter app: serves CounterService over Connect, backed by a Dapr state store.

Settings (environment):
    APP_STATESTORE_NAME  name of the Dapr state store component (default: statestore)
    APP_PORT, APP_HOST, APP_LOG_LEVEL, APP_STARTUP_TIMEOUT_SECONDS, APP_RELOAD
"""

from app_runtime import (
    ComponentRequirement,
    Runtime,
    RuntimeSettings,
    create_app,
    mount_connect,
    run,
)
from app_runtime.interceptors import server_interceptors
from contracts.example.counter.v1.counter_connect import CounterServiceASGIApplication
from fastapi import FastAPI
from greeting.counter import CounterServiceImpl, DaprStateStore, LazyStateStore


class CounterSettings(RuntimeSettings):
    name: str = "counter"
    statestore_name: str = "statestore"


def requirements(settings: CounterSettings) -> list[ComponentRequirement]:
    """The counter needs the configured state store, with ETag support."""
    return [
        ComponentRequirement(
            name=settings.statestore_name,
            type_prefix="state.",
            capabilities=frozenset({"ETAG"}),
        )
    ]


def build_app() -> FastAPI:
    settings = CounterSettings()

    async def on_startup(runtime: Runtime) -> None:
        runtime.state["store"] = DaprStateStore(runtime.dapr, settings.statestore_name)

    app = create_app(settings, requirements=requirements(settings), on_startup=on_startup)
    service = CounterServiceImpl(LazyStateStore(lambda: app.state.runtime.state["store"]))
    mount_connect(app, CounterServiceASGIApplication(service, interceptors=server_interceptors()))
    return app


def main() -> None:
    run("counter_app.main:build_app", CounterSettings())
