"""Startup check: the Dapr components an app requires must be loaded.

Requirements come from the app's own code and settings (for example "the state
store named by APP_STATESTORE_NAME, with ETag support"). They are never derived
from the components a deployment happens to provide; otherwise a missing
component would silently drop out of the check.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ComponentRequirement:
    """A Dapr component the app needs before it can serve requests."""

    name: str
    """Component name, as configured for the app."""
    type_prefix: str
    """Component category, matched as a prefix of the type (`state.` matches `state.redis`)."""
    capabilities: frozenset[str] = frozenset()
    """Capabilities reported by the Metadata API, such as `ETAG` or `TRANSACTIONAL`."""


@dataclass(frozen=True)
class LoadedComponent:
    name: str
    type: str
    capabilities: frozenset[str]


class ComponentCheckError(RuntimeError):
    """Raised when required components are still missing after the timeout."""


class MetadataSource(Protocol):
    async def get_metadata(self) -> object: ...


def find_problems(
    requirements: Sequence[ComponentRequirement], loaded: Sequence[LoadedComponent]
) -> list[str]:
    """Return a human-readable problem for each unmet requirement."""
    by_name = {component.name: component for component in loaded}
    problems: list[str] = []
    for requirement in requirements:
        component = by_name.get(requirement.name)
        if component is None:
            problems.append(f"component '{requirement.name}' is not loaded")
            continue
        if not component.type.startswith(requirement.type_prefix):
            problems.append(
                f"component '{requirement.name}' has type '{component.type}', "
                f"expected '{requirement.type_prefix}*'"
            )
        missing = sorted(requirement.capabilities - component.capabilities)
        if missing:
            problems.append(
                f"component '{requirement.name}' lacks capabilities: {', '.join(missing)}"
            )
    return problems


async def loaded_components(source: MetadataSource) -> list[LoadedComponent]:
    """Read the components loaded by the sidecar through the Metadata API."""
    metadata = await source.get_metadata()
    return [
        LoadedComponent(
            name=component.name,
            type=component.type,
            capabilities=frozenset(component.capabilities),
        )
        for component in metadata.registered_components  # type: ignore[attr-defined]
    ]


async def wait_for_components(
    requirements: Sequence[ComponentRequirement],
    load: Callable[[], Awaitable[Sequence[LoadedComponent]]],
    *,
    timeout_seconds: float,
    interval_seconds: float = 1.0,
) -> None:
    """Poll until every requirement is met, or raise `ComponentCheckError`.

    Polling covers sidecars that are still loading components when the app
    starts. A failed or slow metadata call is retried like an unmet requirement;
    the whole check, including each call, ends at the deadline. With no
    requirements the check passes immediately.
    """
    if not requirements:
        return
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            async with asyncio.timeout(max(deadline - time.monotonic(), 0.1)):
                problems = find_problems(requirements, await load())
        except TimeoutError:
            problems = ["the Dapr Metadata API did not answer in time"]
        except Exception as error:  # e.g. the sidecar is briefly unavailable
            problems = [f"reading Dapr metadata failed: {type(error).__name__}: {error}"]
        if not problems:
            logger.info("Required Dapr components are loaded: %s", [r.name for r in requirements])
            return
        if time.monotonic() >= deadline:
            raise ComponentCheckError("; ".join(problems))
        logger.info("Waiting for Dapr components: %s", "; ".join(problems))
        await asyncio.sleep(min(interval_seconds, max(deadline - time.monotonic(), 0)))
