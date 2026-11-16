"""Step definitions for features/greeting.feature."""

import secrets
import time
import uuid
from typing import Any

import httpx2
from pytest_bdd import given, parsers, scenarios, then, when

scenarios("features/greeting.feature")

# Endpoints are local: ignore proxies configured in the shell.
LOCAL = httpx2.Client(trust_env=False)

TIMEOUT = 30


def greet(api_url: str, name: str, headers: dict[str, str] | None = None) -> httpx2.Response:
    return LOCAL.post(f"{api_url}/greet", json={"name": name}, headers=headers, timeout=TIMEOUT)


@given("a new visitor", target_fixture="visitor")
def new_visitor() -> str:
    return f"visitor-{uuid.uuid4().hex[:10]}"


@when("the visitor is greeted twice", target_fixture="responses")
def greet_twice(api_url: str, visitor: str) -> list[httpx2.Response]:
    return [greet(api_url, visitor), greet(api_url, visitor)]


@then("the second greeting reports one more visit than the first")
def one_more_visit(responses: list[httpx2.Response], visitor: str) -> None:
    first, second = (response.json() for response in responses)
    assert all(response.status_code == 200 for response in responses)
    assert second["visits"] == first["visits"] + 1
    assert second["message"] == f"Hello, {visitor}! You are visitor #{second['visits']}."


@when("a visitor with an empty name is greeted", target_fixture="response")
def greet_empty(api_url: str) -> httpx2.Response:
    return greet(api_url, "")


@then(parsers.parse("the response status is {status:d}"))
def response_status(response: httpx2.Response, status: int) -> None:
    assert response.status_code == status, response.text


@then(parsers.parse('the error is "{error}"'))
def response_error(response: httpx2.Response, error: str) -> None:
    assert response.json()["error"] == error


@when("the visitor is greeted with a new trace id", target_fixture="trace_id")
def greet_traced(api_url: str, visitor: str) -> str:
    trace_id = secrets.token_hex(16)
    traceparent = f"00-{trace_id}-{secrets.token_hex(8)}-01"
    assert greet(api_url, visitor, {"traceparent": traceparent}).status_code == 200
    return trace_id


DAPR_SCOPE = "dapr-diagnostics"


def _span_sources(trace: dict[str, Any]) -> set[tuple[str, str]]:
    """(service name, "app" or "sidecar") of every span. Apps and their sidecars
    report the same service name (the app id); sidecar spans come from Dapr's
    instrumentation scope."""
    sources = set()
    for span in trace["spans"]:
        service = trace["processes"][span["processID"]]["serviceName"]
        tags = {tag["key"]: tag["value"] for tag in span.get("tags", [])}
        sources.add((service, "sidecar" if tags.get("otel.scope.name") == DAPR_SCOPE else "app"))
    return sources


@then("the trace contains spans of the gateway and counter apps and of both Dapr sidecars")
def trace_through_everything(jaeger_url: str, trace_id: str) -> None:
    expected = {
        ("gateway", "app"),
        ("gateway", "sidecar"),
        ("counter", "sidecar"),
        ("counter", "app"),
    }
    deadline = time.monotonic() + 60
    sources: set[tuple[str, str]] = set()
    while time.monotonic() < deadline:
        response = LOCAL.get(f"{jaeger_url}/api/traces/{trace_id}", timeout=TIMEOUT)
        if response.status_code == 200 and response.json().get("data"):
            sources = _span_sources(response.json()["data"][0])
            if expected <= sources:
                return
        time.sleep(2)
    raise AssertionError(f"trace {trace_id}: expected span sources {expected}, found {sources}")
