"""Server-side Connect interceptors.

Inbound order is: OpenTelemetry (added by the Connect instrumentation) →
`ErrorInterceptor` → `ValidationInterceptor` → method implementation.
"""

import logging
from collections.abc import Awaitable, Callable

import protovalidate
from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.request import RequestContext

logger = logging.getLogger(__name__)


class ValidationInterceptor:
    """Validates unary requests against their Protovalidate rules.

    A rule violation becomes `INVALID_ARGUMENT` and the method is not called.
    Any other failure (for example a rule that does not compile) propagates and
    is reported as a server error.
    """

    async def intercept_unary[REQ, RES](
        self,
        call_next: Callable[[REQ, RequestContext], Awaitable[RES]],
        request: REQ,
        ctx: RequestContext,
        /,
    ) -> RES:
        try:
            protovalidate.validate(request)  # type: ignore[arg-type]
        except protovalidate.ValidationError as error:
            raise ConnectError(Code.INVALID_ARGUMENT, str(error)) from None
        return await call_next(request, ctx)


# Codes that describe the caller's request or a condition the caller can act
# on; their messages are meant for the caller.
CALLER_FACING_CODES = frozenset({
    Code.CANCELED,
    Code.INVALID_ARGUMENT,
    Code.NOT_FOUND,
    Code.ALREADY_EXISTS,
    Code.PERMISSION_DENIED,
    Code.RESOURCE_EXHAUSTED,
    Code.FAILED_PRECONDITION,
    Code.ABORTED,
    Code.OUT_OF_RANGE,
    Code.UNIMPLEMENTED,
    Code.UNAUTHENTICATED,
})  # fmt: skip

GENERIC_MESSAGES = {
    Code.UNAVAILABLE: "service unavailable",
    Code.DEADLINE_EXCEEDED: "deadline exceeded",
}


class ErrorInterceptor:
    """Keeps internal details out of error responses.

    - Caller-facing codes (`INVALID_ARGUMENT`, `ABORTED`, ...) pass through with
      their message.
    - Server-side codes (`INTERNAL`, `UNKNOWN`, `UNAVAILABLE`,
      `DEADLINE_EXCEEDED`, `DATA_LOSS`) keep their code but get a generic
      message: they may come from a failed downstream call and carry addresses
      or internal error text.
    - Any other exception becomes a generic `INTERNAL` error.

    Everything is logged with its full details. Errors of downstream calls should
    still be translated into the service's own errors where they are made.
    """

    async def intercept_unary[REQ, RES](
        self,
        call_next: Callable[[REQ, RequestContext], Awaitable[RES]],
        request: REQ,
        ctx: RequestContext,
        /,
    ) -> RES:
        method = ctx.method
        try:
            return await call_next(request, ctx)
        except ConnectError as error:
            if error.code in CALLER_FACING_CODES:
                raise
            logger.error(
                "%s/%s failed: %s %s",
                method.service_name,
                method.name,
                error.code.value,
                error.message,
            )
            message = GENERIC_MESSAGES.get(error.code, "internal error")
            raise ConnectError(error.code, message) from None
        except Exception:
            logger.exception("Unhandled error in %s/%s", method.service_name, method.name)
            raise ConnectError(Code.INTERNAL, "internal error") from None


def server_interceptors() -> list[ErrorInterceptor | ValidationInterceptor]:
    """Interceptors every Connect service in this repository uses."""
    return [ErrorInterceptor(), ValidationInterceptor()]
