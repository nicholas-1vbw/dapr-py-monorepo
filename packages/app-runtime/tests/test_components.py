from types import SimpleNamespace

import pytest
from app_runtime.components import (
    ComponentCheckError,
    ComponentRequirement,
    LoadedComponent,
    find_problems,
    loaded_components,
    wait_for_components,
)

STATE = ComponentRequirement("statestore", "state.", frozenset({"ETAG"}))


def component(name: str, type_: str, *caps: str) -> LoadedComponent:
    return LoadedComponent(name=name, type=type_, capabilities=frozenset(caps))


def test_requirement_met() -> None:
    assert find_problems([STATE], [component("statestore", "state.redis", "ETAG", "TTL")]) == []


def test_missing_component() -> None:
    assert find_problems([STATE], []) == ["component 'statestore' is not loaded"]


def test_wrong_type() -> None:
    problems = find_problems([STATE], [component("statestore", "pubsub.redis", "ETAG")])
    assert problems == ["component 'statestore' has type 'pubsub.redis', expected 'state.*'"]


def test_missing_capability() -> None:
    problems = find_problems([STATE], [component("statestore", "state.redis")])
    assert problems == ["component 'statestore' lacks capabilities: ETAG"]


async def test_loaded_components_reads_metadata() -> None:
    metadata = SimpleNamespace(
        registered_components=[
            SimpleNamespace(name="statestore", type="state.redis", capabilities=["ETAG"])
        ]
    )

    class Source:
        async def get_metadata(self) -> object:
            return metadata

    assert await loaded_components(Source()) == [component("statestore", "state.redis", "ETAG")]


async def test_no_requirements_pass_without_metadata() -> None:
    async def load() -> list[LoadedComponent]:
        raise AssertionError("metadata must not be read")

    await wait_for_components([], load, timeout_seconds=0)


async def test_waits_until_component_is_loaded() -> None:
    responses = [[], [component("statestore", "state.redis", "ETAG")]]

    async def load() -> list[LoadedComponent]:
        return responses.pop(0)

    await wait_for_components([STATE], load, timeout_seconds=5, interval_seconds=0)
    assert responses == []


async def test_times_out_with_reason() -> None:
    async def load() -> list[LoadedComponent]:
        return []

    with pytest.raises(ComponentCheckError, match="'statestore' is not loaded"):
        await wait_for_components([STATE], load, timeout_seconds=0, interval_seconds=0)


async def test_transient_metadata_errors_are_retried() -> None:
    calls = 0

    async def load() -> list[LoadedComponent]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("sidecar unavailable")
        return [component("statestore", "state.redis", "ETAG")]

    await wait_for_components([STATE], load, timeout_seconds=5, interval_seconds=0)
    assert calls == 2


async def test_persistent_metadata_errors_fail_with_reason() -> None:
    async def load() -> list[LoadedComponent]:
        raise ConnectionError("sidecar unavailable")

    with pytest.raises(ComponentCheckError, match="reading Dapr metadata failed"):
        await wait_for_components([STATE], load, timeout_seconds=0.2, interval_seconds=0.05)


async def test_hanging_metadata_call_is_bounded_by_the_timeout() -> None:
    import asyncio
    import time

    async def load() -> list[LoadedComponent]:
        await asyncio.sleep(60)
        return []

    started = time.monotonic()
    with pytest.raises(ComponentCheckError, match="did not answer in time"):
        await wait_for_components([STATE], load, timeout_seconds=0.3)
    assert time.monotonic() - started < 2
