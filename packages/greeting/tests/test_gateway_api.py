from collections.abc import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from greeting.gateway import (
    CounterBusy,
    CounterFailed,
    CounterUnavailable,
    InvalidName,
    create_router,
)


class FakeCounter:
    def __init__(self, error: Exception | None = None) -> None:
        self.visits: dict[str, int] = {}
        self.error = error

    async def increment(self, name: str) -> int:
        if self.error:
            raise self.error
        self.visits[name] = self.visits.get(name, 0) + 1
        return self.visits[name]


def client_for(counter: FakeCounter) -> TestClient:
    app = FastAPI()
    get_counter: Callable[[], FakeCounter] = lambda: counter  # noqa: E731
    app.include_router(create_router(get_counter))
    return TestClient(app)


def test_greets_and_counts() -> None:
    client = client_for(FakeCounter())
    first = client.post("/greet", json={"name": "Alice"})
    second = client.post("/greet", json={"name": "Alice"})
    assert first.status_code == 200
    assert first.json() == {"message": "Hello, Alice! You are visitor #1.", "visits": 1}
    assert second.json()["visits"] == 2


def test_missing_field_is_422() -> None:
    assert client_for(FakeCounter()).post("/greet", json={}).status_code == 422


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (InvalidName("key: value length must be at least 1 characters"), 400, "invalid_argument"),
        (CounterUnavailable(), 503, "unavailable"),
        (CounterBusy(), 503, "unavailable"),
        (CounterFailed(), 502, "bad_gateway"),
    ],
)
def test_error_mapping(error: Exception, status: int, code: str) -> None:
    response = client_for(FakeCounter(error)).post("/greet", json={"name": "x"})
    assert response.status_code == status
    assert response.json()["error"] == code


def test_invalid_name_detail_is_forwarded() -> None:
    detail = "key: value length must be at least 1 characters"
    response = client_for(FakeCounter(InvalidName(detail))).post("/greet", json={"name": ""})
    assert response.json()["detail"] == detail
