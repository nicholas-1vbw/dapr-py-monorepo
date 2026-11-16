"""The greeting domain against a real Dapr sidecar and Redis.

One app serves both the gateway API and CounterService. The gateway part calls
the counter by this sidecar's own app id, so every call goes through Dapr
service invocation and back into the same process.
"""

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass

import httpx2
import pytest
from app_runtime import ComponentRequirement, Runtime, RuntimeSettings, create_app, mount_connect
from app_runtime.client import dapr_client
from app_runtime.interceptors import server_interceptors
from connectrpc.code import Code
from connectrpc.errors import ConnectError
from contracts.example.counter.v1.counter_connect import (
    CounterServiceASGIApplication,
    CounterServiceClient,
)
from contracts.example.counter.v1.counter_pb import IncrementRequest
from dapr.clients import DaprClient
from greeting.counter import CounterServiceImpl, DaprStateStore, LazyStateStore
from greeting.gateway import ConnectVisitCounter, create_router
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from tests.integration.conftest import IntegrationEnv
from tests.integration.support import serve

STATESTORE = "statestore"
INCREMENT = "example.counter.v1.CounterService/Increment"

# Endpoints are local: ignore proxies configured in the shell.
LOCAL = httpx2.Client(trust_env=False)


@dataclass
class GreetingApp:
    url: str
    spans: InMemorySpanExporter


@pytest.fixture(scope="module")
def greeting_app(it_env: IntegrationEnv) -> Iterator[GreetingApp]:
    async def on_startup(runtime: Runtime) -> None:
        runtime.state["store"] = DaprStateStore(runtime.dapr, STATESTORE)
        counter = ConnectVisitCounter(it_env.app_id, timeout_ms=10_000)
        runtime.exit_stack.push_async_callback(counter.close)
        runtime.state["counter"] = counter

    app = create_app(
        RuntimeSettings(name="greeting-it", startup_timeout_seconds=30),
        requirements=[ComponentRequirement(STATESTORE, "state.", frozenset({"ETAG"}))],
        on_startup=on_startup,
    )
    service = CounterServiceImpl(LazyStateStore(lambda: app.state.runtime.state["store"]))
    mount_connect(app, CounterServiceASGIApplication(service, interceptors=server_interceptors()))
    app.include_router(create_router(lambda: app.state.runtime.state["counter"]))

    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    spans = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(spans))

    with serve(app, it_env.app_port) as url:
        yield GreetingApp(url, spans)


@pytest.fixture
def cleanup_keys() -> Iterator[list[str]]:
    keys: list[str] = []
    yield keys
    with DaprClient() as dapr:
        for key in keys:
            dapr.delete_state(STATESTORE, key)


def test_greeting_counts_visits(
    greeting_app: GreetingApp, unique_name: str, cleanup_keys: list[str]
) -> None:
    cleanup_keys.append(unique_name)
    first = LOCAL.post(f"{greeting_app.url}/greet", json={"name": unique_name}, timeout=30)
    second = LOCAL.post(f"{greeting_app.url}/greet", json={"name": unique_name}, timeout=30)
    assert first.status_code == 200, first.text
    assert first.json() == {
        "message": f"Hello, {unique_name}! You are visitor #1.",
        "visits": 1,
    }
    assert second.json()["visits"] == 2


def test_validation_error_crosses_dapr(greeting_app: GreetingApp) -> None:
    response = LOCAL.post(f"{greeting_app.url}/greet", json={"name": ""}, timeout=30)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_argument"
    assert "key" in response.json()["detail"]


async def test_concurrent_increments_lose_no_update(
    greeting_app: GreetingApp,
    it_env: IntegrationEnv,
    unique_name: str,
    cleanup_keys: list[str],
) -> None:
    cleanup_keys.append(unique_name)
    async with dapr_client(CounterServiceClient, it_env.app_id, timeout_ms=30_000) as client:
        responses = await asyncio.gather(
            *(client.increment(IncrementRequest(key=unique_name)) for _ in range(30))
        )
    assert sorted(response.value for response in responses) == list(range(1, 31))


async def test_invalid_request_is_rejected_by_counter(
    greeting_app: GreetingApp, it_env: IntegrationEnv
) -> None:
    async with dapr_client(CounterServiceClient, it_env.app_id, timeout_ms=10_000) as client:
        with pytest.raises(ConnectError) as raised:
            await client.increment(IncrementRequest(key="not/valid"))
    assert raised.value.code == Code.INVALID_ARGUMENT


def test_trace_context_crosses_dapr(
    greeting_app: GreetingApp, unique_name: str, cleanup_keys: list[str]
) -> None:
    cleanup_keys.append(unique_name)
    greeting_app.spans.clear()
    response = LOCAL.post(f"{greeting_app.url}/greet", json={"name": unique_name}, timeout=30)
    assert response.status_code == 200

    spans = greeting_app.spans.get_finished_spans()
    (client,) = [s for s in spans if s.name == INCREMENT and s.kind == SpanKind.CLIENT]
    (server,) = [s for s in spans if s.name == INCREMENT and s.kind == SpanKind.SERVER]
    assert client.context is not None
    assert server.context is not None
    assert server.context.trace_id == client.context.trace_id
    # The server span continues a context received over the network (via Dapr).
    assert server.parent is not None
    assert server.parent.is_remote
