import pytest
from connectrpc.code import Code
from connectrpc.errors import ConnectError
from greeting.gateway.counter_client import _translate
from greeting.gateway.domain import CounterBusy, CounterFailed, CounterUnavailable, InvalidName


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (Code.INVALID_ARGUMENT, InvalidName),
        (Code.UNAVAILABLE, CounterUnavailable),
        (Code.DEADLINE_EXCEEDED, CounterUnavailable),
        (Code.ABORTED, CounterBusy),
        (Code.UNKNOWN, CounterFailed),
        (Code.INTERNAL, CounterFailed),
    ],
)
def test_translate(code: Code, expected: type[Exception]) -> None:
    assert isinstance(_translate(ConnectError(code, "m")), expected)


def test_failure_does_not_leak_message() -> None:
    error = _translate(ConnectError(Code.UNKNOWN, "dial tcp 10.0.0.7:50002: refused"))
    assert "10.0.0.7" not in str(error)


def test_dapr_invocation_failure_is_unavailable() -> None:
    # Message the Connect client reports from the sidecar's own HTTP 500 when the
    # target app id cannot be reached.
    message = "failed to invoke, id: counter, err: couldn't find service: counter"
    assert isinstance(_translate(ConnectError(Code.UNKNOWN, message)), CounterUnavailable)
