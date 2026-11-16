"""`CounterService` implementation."""

import asyncio
import logging
import random

from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.request import RequestContext
from contracts.example.counter.v1.counter_connect import CounterService
from contracts.example.counter.v1.counter_pb import IncrementRequest, IncrementResponse

from greeting.counter.store import ConflictError, StateStore

logger = logging.getLogger(__name__)


class CounterServiceImpl(CounterService):
    """Increments counters with read-modify-write and ETag concurrency control.

    Concurrent increments of the same key never lose an update: a write based
    on a stale read fails and is retried with jittered exponential backoff.

    Retries stop early when the caller's deadline (sent by Connect clients)
    would not leave time for another attempt, so the caller receives `ABORTED`
    instead of timing out while a write may still be in flight.
    """

    def __init__(
        self,
        store: StateStore,
        *,
        max_attempts: int = 10,
        base_backoff_seconds: float = 0.01,
        attempt_budget_seconds: float = 0.5,
    ) -> None:
        self._store = store
        self._max_attempts = max_attempts
        self._base_backoff_seconds = base_backoff_seconds
        self._attempt_budget_seconds = attempt_budget_seconds
        """Time kept for one more read-and-write attempt and the response."""

    async def increment(
        self,
        request: IncrementRequest,
        ctx: RequestContext[IncrementRequest, IncrementResponse],
        /,
    ) -> IncrementResponse:
        attempts = 0
        for attempt in range(self._max_attempts):
            attempts = attempt + 1
            current = await self._store.get(request.key)
            value = int(current.data or b"0") + 1
            try:
                await self._store.save(request.key, str(value).encode(), current.etag)
            except ConflictError as conflict:
                delay = random.uniform(0, self._base_backoff_seconds * 2**attempt)
                if not self._time_left(ctx, delay):
                    # Out of time: the caller may retry (ABORTED), whatever the kind
                    # of conflict.
                    break
                if attempt + 1 == self._max_attempts:
                    if not conflict.confirmed:
                        # Still failing after every retry: probably not a conflict,
                        # so surface the store error.
                        raise
                    break
                await asyncio.sleep(delay)
                continue
            return IncrementResponse(value=value)
        logger.warning("Gave up incrementing %r after %d conflicts", request.key, attempts)
        raise ConnectError(Code.ABORTED, "too many concurrent updates, please retry")

    def _time_left(self, ctx: RequestContext | None, delay: float) -> bool:
        """Whether the caller's deadline leaves room for `delay` and one more attempt."""
        remaining_ms = ctx.timeout_ms if ctx is not None else None
        if remaining_ms is None:
            return True
        return remaining_ms / 1000 > delay + self._attempt_budget_seconds
