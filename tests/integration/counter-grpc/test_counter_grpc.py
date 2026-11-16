"""The counter service over gRPC against a real Dapr sidecar and Redis.

The sidecar talks gRPC to this app (the counter-grpc app declares
`appProtocol: grpc`). The tests call the service by this sidecar's own app id
through Dapr gRPC proxying, so every call crosses Dapr.
"""

import asyncio
from collections.abc import Iterator

import pytest
from app_runtime import ComponentRequirement, RuntimeSettings
from app_runtime.client import InvokeProtocol, dapr_client
from app_runtime.grpc_server import grpc_service
from app_runtime.interceptors import server_interceptors
from app_runtime.telemetry import configure_tracing
from connectrpc.code import Code
from connectrpc.errors import ConnectError
from contracts.example.counter.v1.counter_connect import CounterService, CounterServiceClient
from contracts.example.counter.v1.counter_pb import IncrementRequest
from dapr.clients import DaprClient
from greeting.counter import CounterServiceImpl, DaprStateStore
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from tests.integration.conftest import IntegrationEnv
from tests.integration.support import serve_grpc_app

STATESTORE = "statestore"
INCREMENT = "example.counter.v1.CounterService/Increment"


@pytest.fixture(scope="module")
def spans(it_env: IntegrationEnv) -> Iterator[InMemorySpanExporter]:
    # As in the app: tracing (and the Connect instrumentation) before the services.
    provider = configure_tracing("counter-grpc-it")
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    settings = RuntimeSettings(
        name="counter-grpc-it",
        host="127.0.0.1",
        port=it_env.app_port,
        startup_timeout_seconds=30,
    )

    def services(runtime):
        store = DaprStateStore(runtime.dapr, STATESTORE)
        return [
            grpc_service(
                CounterService, CounterServiceImpl(store), interceptors=server_interceptors()
            )
        ]

    requirements = [ComponentRequirement(STATESTORE, "state.", frozenset({"ETAG"}))]
    with serve_grpc_app(settings, services, requirements):
        yield exporter


@pytest.fixture
def cleanup_keys() -> Iterator[list[str]]:
    keys: list[str] = []
    yield keys
    with DaprClient() as dapr:
        for key in keys:
            dapr.delete_state(STATESTORE, key)


def counter(it_env: IntegrationEnv) -> CounterServiceClient:
    return dapr_client(
        CounterServiceClient, it_env.app_id, timeout_ms=30_000, protocol=InvokeProtocol.GRPC
    )


async def test_concurrent_increments_lose_no_update(
    spans: InMemorySpanExporter,
    it_env: IntegrationEnv,
    unique_name: str,
    cleanup_keys: list[str],
) -> None:
    cleanup_keys.append(unique_name)
    async with counter(it_env) as client:
        responses = await asyncio.gather(
            *(client.increment(IncrementRequest(key=unique_name)) for _ in range(30))
        )
    assert sorted(response.value for response in responses) == list(range(1, 31))


async def test_invalid_request_is_rejected_through_dapr(
    spans: InMemorySpanExporter, it_env: IntegrationEnv
) -> None:
    async with counter(it_env) as client:
        with pytest.raises(ConnectError) as raised:
            await client.increment(IncrementRequest(key="not/valid"))
    assert raised.value.code == Code.INVALID_ARGUMENT
    assert "key" in raised.value.message


async def test_trace_context_crosses_dapr(
    spans: InMemorySpanExporter, it_env: IntegrationEnv, unique_name: str, cleanup_keys: list[str]
) -> None:
    cleanup_keys.append(unique_name)
    spans.clear()
    async with counter(it_env) as client:
        await client.increment(IncrementRequest(key=unique_name))

    finished = spans.get_finished_spans()
    (caller,) = [s for s in finished if s.name == INCREMENT and s.kind == SpanKind.CLIENT]
    (server,) = [s for s in finished if s.name == INCREMENT and s.kind == SpanKind.SERVER]
    assert caller.context is not None
    assert server.context is not None
    assert server.context.trace_id == caller.context.trace_id
    # The server span continues a context received over the network (via Dapr).
    assert server.parent is not None
    assert server.parent.is_remote
