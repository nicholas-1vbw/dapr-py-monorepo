"""Greeting logic, independent of HTTP and RPC details."""

from typing import Protocol


class InvalidName(Exception):
    """The name was rejected by the counter's validation rules."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class CounterUnavailable(Exception):
    """The counter could not be reached or did not answer in time."""


class CounterBusy(Exception):
    """The counter gave up because of too many concurrent updates."""


class CounterFailed(Exception):
    """The counter returned an unexpected error."""


class VisitCounter(Protocol):
    async def increment(self, name: str) -> int:
        """Record one visit for `name` and return the visit number."""
        ...


def greeting_message(name: str, visits: int) -> str:
    return f"Hello, {name}! You are visitor #{visits}."


async def greet(counter: VisitCounter, name: str) -> tuple[str, int]:
    """Count a visit for `name` and build the greeting."""
    visits = await counter.increment(name)
    return greeting_message(name, visits), visits
