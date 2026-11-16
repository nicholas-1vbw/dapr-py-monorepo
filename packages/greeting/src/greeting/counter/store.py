"""Optimistic-concurrency access to a Dapr state store."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import grpc
from dapr.aio.clients import DaprClient
from dapr.clients.exceptions import DaprInternalError
from dapr.clients.grpc._state import Concurrency, StateOptions


@dataclass(frozen=True)
class StoredValue:
    data: bytes
    etag: str | None
    """ETag of the stored item, or None when the key does not exist."""


class ConflictError(Exception):
    """Another writer changed the item; read it again and retry.

    `confirmed` is False when the store reported a failed insert that may be a
    concurrent insert or a real failure; the caller should give up on such an
    error once its retries are exhausted.
    """

    def __init__(self, key: str, *, confirmed: bool) -> None:
        super().__init__(key)
        self.confirmed = confirmed


class StateStore(Protocol):
    async def get(self, key: str) -> StoredValue: ...

    async def save(self, key: str, data: bytes, etag: str | None) -> None:
        """Write with first-write-wins semantics; raise `ConflictError` on conflict."""
        ...


class DaprStateStore:
    """`StateStore` on top of a Dapr state component with ETag support."""

    def __init__(self, dapr: DaprClient, store_name: str) -> None:
        self._dapr = dapr
        self._store_name = store_name
        self._options = StateOptions(concurrency=Concurrency.first_write)

    async def get(self, key: str) -> StoredValue:
        item = await self._dapr.get_state(self._store_name, key)
        return StoredValue(data=item.data, etag=item.etag or None)

    async def save(self, key: str, data: bytes, etag: str | None) -> None:
        try:
            await self._dapr.save_state(
                self._store_name, key, data, etag=etag, options=self._options
            )
        except DaprInternalError as error:
            code = _grpc_code(error)
            if code == grpc.StatusCode.ABORTED:
                # A write based on a stale ETag.
                raise ConflictError(key, confirmed=True) from error
            if etag is None and code == grpc.StatusCode.INTERNAL:
                # Concurrent first writes of one key can be reported as a generic
                # save failure instead of ABORTED, depending on the state store.
                raise ConflictError(key, confirmed=False) from error
            raise


def _grpc_code(error: DaprInternalError) -> grpc.StatusCode | None:
    cause = error.__cause__
    if isinstance(cause, grpc.RpcError):
        return cause.code()  # type: ignore[attr-defined]
    return None


class LazyStateStore:
    """A `StateStore` resolved on each call, for stores created after wiring.

    Services are wired when the app is built, but the Dapr client only exists
    once startup has run; `resolve` returns the store at request time.
    """

    def __init__(self, resolve: Callable[[], StateStore]) -> None:
        self._resolve = resolve

    async def get(self, key: str) -> StoredValue:
        return await self._resolve().get(key)

    async def save(self, key: str, data: bytes, etag: str | None) -> None:
        await self._resolve().save(key, data, etag)
