"""Gateway app: the public greeting HTTP API.

Settings (environment):
    APP_COUNTER_APP_ID      Dapr app id of the counter app (default: counter)
    APP_COUNTER_TIMEOUT_MS  timeout for each counter call (default: 5000)
    APP_PORT, APP_HOST, APP_LOG_LEVEL, APP_STARTUP_TIMEOUT_SECONDS, APP_RELOAD
"""

from app_runtime import Runtime, RuntimeSettings, create_app, run
from fastapi import FastAPI
from greeting.gateway import ConnectVisitCounter, VisitCounter, create_router


class GatewaySettings(RuntimeSettings):
    name: str = "gateway"
    counter_app_id: str = "counter"
    counter_timeout_ms: int = 5000


def build_app() -> FastAPI:
    settings = GatewaySettings()

    async def on_startup(runtime: Runtime) -> None:
        # Created after tracing is configured, so calls carry trace context.
        counter = ConnectVisitCounter(
            settings.counter_app_id, timeout_ms=settings.counter_timeout_ms
        )
        runtime.exit_stack.push_async_callback(counter.close)
        runtime.state["counter"] = counter

    # The gateway requires no Dapr components; the counter is a service
    # dependency and is not checked at startup.
    app = create_app(settings, on_startup=on_startup)

    def counter() -> VisitCounter:
        return app.state.runtime.state["counter"]

    app.include_router(create_router(counter))
    return app


def main() -> None:
    run("gateway_app.main:build_app", GatewaySettings())
