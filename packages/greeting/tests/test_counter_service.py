import asyncio
from typing import Any

import pytest
from connectrpc.code import Code
from connectrpc.errors import ConnectError
from contracts.example.counter.v1.counter_pb import IncrementRequest
from greeting.counter import ConflictError, CounterServiceImpl, StoredValue


class MemoryStore:
    """In-memory StateStore with first-write-wins ETags."""

    def __init__(self) -> None:
        self.items: dict[str, tuple[bytes, int]] = {}

    async def get(self, key: str) -> StoredValue:
        if key not in self.items:
            return StoredValue(data=b"", etag=None)
        data, version = self.items[key]
        return StoredValue(data=data, etag=str(version))

    async def save(self, key: str, data: bytes, etag: str | None) -> None:
        await asyncio.sleep(0)  # let concurrent writers interleave
        current = self.items.get(key)
        if (current is None) != (etag is None) or (current and str(current[1]) != etag):
            raise ConflictError(key, confirmed=True)
        self.items[key] = (data, (current[1] if current else 0) + 1)


CTX: Any = None


async def test_increments_from_zero() -> None:
    service = CounterServiceImpl(MemoryStore())
    assert (await service.increment(IncrementRequest(key="a"), CTX)).value == 1
    assert (await service.increment(IncrementRequest(key="a"), CTX)).value == 2
    assert (await service.increment(IncrementRequest(key="b"), CTX)).value == 1


async def test_concurrent_increments_lose_no_update() -> None:
    service = CounterServiceImpl(MemoryStore(), max_attempts=50, base_backoff_seconds=0.001)
    results = await asyncio.gather(
        *(service.increment(IncrementRequest(key="k"), CTX) for _ in range(20))
    )
    assert sorted(r.value for r in results) == list(range(1, 21))


class AlwaysConflict(MemoryStore):
    def __init__(self, *, confirmed: bool) -> None:
        super().__init__()
        self.confirmed = confirmed

    async def save(self, key: str, data: bytes, etag: str | None) -> None:
        raise ConflictError(key, confirmed=self.confirmed) from RuntimeError("store error")


async def test_gives_up_with_aborted_after_confirmed_conflicts() -> None:
    service = CounterServiceImpl(
        AlwaysConflict(confirmed=True), max_attempts=3, base_backoff_seconds=0
    )
    with pytest.raises(ConnectError) as raised:
        await service.increment(IncrementRequest(key="k"), CTX)
    assert raised.value.code == Code.ABORTED


async def test_unconfirmed_conflict_surfaces_as_failure() -> None:
    service = CounterServiceImpl(
        AlwaysConflict(confirmed=False), max_attempts=3, base_backoff_seconds=0
    )
    with pytest.raises(ConflictError):
        await service.increment(IncrementRequest(key="k"), CTX)


class _Deadline:
    """Request context with a fixed remaining time."""

    def __init__(self, remaining_ms: float) -> None:
        self.timeout_ms = remaining_ms


async def test_stops_retrying_before_the_callers_deadline() -> None:
    store = AlwaysConflict(confirmed=True)
    saves = 0
    original = store.save

    async def counting_save(key: str, data: bytes, etag: str | None) -> None:
        nonlocal saves
        saves += 1
        await original(key, data, etag)

    store.save = counting_save  # type: ignore[method-assign]
    service = CounterServiceImpl(store, max_attempts=10, attempt_budget_seconds=0.5)
    with pytest.raises(ConnectError) as raised:
        await service.increment(IncrementRequest(key="k"), _Deadline(remaining_ms=300))  # type: ignore[arg-type]
    assert raised.value.code == Code.ABORTED
    assert saves == 1  # no retry: 300 ms left is less than the attempt budget


async def test_retries_while_the_deadline_allows() -> None:
    service = CounterServiceImpl(
        AlwaysConflict(confirmed=True), max_attempts=4, base_backoff_seconds=0
    )
    with pytest.raises(ConnectError):
        await service.increment(IncrementRequest(key="k"), _Deadline(remaining_ms=60_000))  # type: ignore[arg-type]


async def test_insert_race_at_the_deadline_is_aborted_not_internal() -> None:
    service = CounterServiceImpl(AlwaysConflict(confirmed=False), max_attempts=10)
    with pytest.raises(ConnectError) as raised:
        await service.increment(IncrementRequest(key="k"), _Deadline(remaining_ms=100))  # type: ignore[arg-type]
    assert raised.value.code == Code.ABORTED
