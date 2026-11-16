"""Counter app over gRPC: serves CounterService with grpcio, backed by a Dapr state store.

The service implementation, its interceptors and its state store are the same
as in the counter app; only the protocol differs. Callers reach it through
Dapr gRPC proxying (`dapr_client(..., protocol=InvokeProtocol.GRPC)`).

Settings (environment):
    APP_STATESTORE_NAME  name of the Dapr state store component (default: statestore)
    APP_PORT, APP_HOST, APP_LOG_LEVEL, APP_STARTUP_TIMEOUT_SECONDS
"""

from collections.abc import Iterable

import grpc
from app_runtime import ComponentRequirement, Runtime, RuntimeSettings
from app_runtime.grpc_server import grpc_service, run_grpc
from app_runtime.interceptors import server_interceptors
from contracts.example.counter.v1.counter_connect import CounterService
from greeting.counter import CounterServiceImpl, DaprStateStore


class CounterGrpcSettings(RuntimeSettings):
    name: str = "counter-grpc"
    statestore_name: str = "statestore"


def requirements(settings: CounterGrpcSettings) -> list[ComponentRequirement]:
    """The counter needs the configured state store, with ETag support."""
    return [
        ComponentRequirement(
            name=settings.statestore_name,
            type_prefix="state.",
            capabilities=frozenset({"ETAG"}),
        )
    ]


def services(settings: CounterGrpcSettings, runtime: Runtime) -> Iterable[grpc.GenericRpcHandler]:
    store = DaprStateStore(runtime.dapr, settings.statestore_name)
    return [
        grpc_service(CounterService, CounterServiceImpl(store), interceptors=server_interceptors())
    ]


def main() -> None:
    settings = CounterGrpcSettings()
    run_grpc(
        settings,
        lambda runtime: services(settings, runtime),
        requirements=requirements(settings),
    )
