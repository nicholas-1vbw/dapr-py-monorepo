from typing import Any

import pytest
from app_runtime.interceptors import ErrorInterceptor, ValidationInterceptor
from connectrpc.code import Code
from connectrpc.errors import ConnectError
from contracts.platform.testing.v1.probe_pb import EchoRequest, EchoResponse


class Ctx:
    method = type("Method", (), {"service_name": "svc", "name": "M"})()


async def reached(_request: Any, _ctx: Any) -> EchoResponse:
    return EchoResponse(text="ok")


async def test_valid_request_reaches_method() -> None:
    response = await ValidationInterceptor().intercept_unary(
        reached,
        EchoRequest(text="hello"),
        Ctx(),  # type: ignore[arg-type]
    )
    assert response.text == "ok"


async def test_invalid_request_never_reaches_method() -> None:
    async def must_not_run(_request: Any, _ctx: Any) -> EchoResponse:
        raise AssertionError("method called with an invalid request")

    with pytest.raises(ConnectError) as raised:
        await ValidationInterceptor().intercept_unary(
            must_not_run,
            EchoRequest(text=""),
            Ctx(),  # type: ignore[arg-type]
        )
    assert raised.value.code == Code.INVALID_ARGUMENT
    assert "text" in raised.value.message


async def test_unexpected_error_becomes_generic_internal() -> None:
    async def boom(_request: Any, _ctx: Any) -> EchoResponse:
        raise RuntimeError("redis at 10.0.0.7:6379 refused")

    with pytest.raises(ConnectError) as raised:
        await ErrorInterceptor().intercept_unary(boom, EchoRequest(text="a"), Ctx())  # type: ignore[arg-type]
    assert raised.value.code == Code.INTERNAL
    assert "10.0.0.7" not in raised.value.message


async def test_connect_errors_pass_through() -> None:
    async def aborted(_request: Any, _ctx: Any) -> EchoResponse:
        raise ConnectError(Code.ABORTED, "busy")

    with pytest.raises(ConnectError) as raised:
        await ErrorInterceptor().intercept_unary(aborted, EchoRequest(text="a"), Ctx())  # type: ignore[arg-type]
    assert raised.value.code == Code.ABORTED


@pytest.mark.parametrize(
    ("code", "message"),
    [
        (Code.INTERNAL, "internal error"),
        (Code.UNKNOWN, "internal error"),
        (Code.UNAVAILABLE, "service unavailable"),
        (Code.DEADLINE_EXCEEDED, "deadline exceeded"),
    ],
)
async def test_server_side_errors_keep_code_but_not_details(code: Code, message: str) -> None:
    async def downstream(_request: Any, _ctx: Any) -> EchoResponse:
        raise ConnectError(code, "dial tcp 10.0.0.7:50002: connection refused")

    with pytest.raises(ConnectError) as raised:
        await ErrorInterceptor().intercept_unary(downstream, EchoRequest(text="a"), Ctx())  # type: ignore[arg-type]
    assert raised.value.code == code
    assert raised.value.message == message


async def test_caller_facing_errors_keep_their_message() -> None:
    async def invalid(_request: Any, _ctx: Any) -> EchoResponse:
        raise ConnectError(Code.INVALID_ARGUMENT, "text: value is empty")

    with pytest.raises(ConnectError) as raised:
        await ErrorInterceptor().intercept_unary(invalid, EchoRequest(text="a"), Ctx())  # type: ignore[arg-type]
    assert raised.value.message == "text: value is empty"
