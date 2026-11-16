"""`VisitCounter` backed by `CounterService`, called through the Dapr sidecar over
Connect (service invocation) or gRPC (gRPC proxying)."""

import logging

from app_runtime.client import InvokeProtocol, dapr_client, is_unavailable
from connectrpc.code import Code
from connectrpc.errors import ConnectError
from contracts.example.counter.v1.counter_connect import CounterServiceClient
from contracts.example.counter.v1.counter_pb import IncrementRequest

from greeting.gateway.domain import CounterBusy, CounterFailed, CounterUnavailable, InvalidName

logger = logging.getLogger(__name__)


class ConnectVisitCounter:
    """Calls the counter app (by Dapr app id) through the local sidecar.

    Create it after tracing is configured so the client is instrumented, and
    close it on shutdown.
    """

    def __init__(
        self,
        counter_app_id: str,
        *,
        timeout_ms: int,
        protocol: InvokeProtocol = InvokeProtocol.CONNECT,
    ) -> None:
        self._client = dapr_client(
            CounterServiceClient, counter_app_id, timeout_ms=timeout_ms, protocol=protocol
        )

    async def increment(self, name: str) -> int:
        try:
            response = await self._client.increment(IncrementRequest(key=name))
        except ConnectError as error:
            raise _translate(error) from error
        return response.value

    async def close(self) -> None:
        await self._client.close()


def _translate(error: ConnectError) -> Exception:
    if error.code == Code.INVALID_ARGUMENT:
        return InvalidName(error.message)
    if is_unavailable(error):
        logger.warning("Counter unavailable: %s %s", error.code.value, error.message)
        return CounterUnavailable()
    if error.code == Code.ABORTED:
        return CounterBusy()
    logger.error("Counter call failed: %s %s", error.code.value, error.message)
    return CounterFailed()
