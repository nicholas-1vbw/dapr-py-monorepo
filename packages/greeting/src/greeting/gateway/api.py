"""HTTP routes of the public greeting API."""

from collections.abc import Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from greeting.gateway.domain import (
    CounterBusy,
    CounterFailed,
    CounterUnavailable,
    InvalidName,
    VisitCounter,
    greet,
)


class GreetRequest(BaseModel):
    # Only the shape is checked here (422 on mismatch). The rules for a valid
    # name live in the counter contract and are enforced by the counter (400).
    name: str


class GreetResponse(BaseModel):
    message: str
    visits: int


class ErrorResponse(BaseModel):
    error: str
    detail: str


def _error(status: int, error: str, detail: str) -> JSONResponse:
    return JSONResponse(ErrorResponse(error=error, detail=detail).model_dump(), status_code=status)


def create_router(counter: Callable[[], VisitCounter]) -> APIRouter:
    """Routes that obtain the visit counter from `counter()` on each request."""
    router = APIRouter()

    @router.post(
        "/greet",
        response_model=GreetResponse,
        responses={
            400: {"model": ErrorResponse},
            502: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    async def greet_visitor(body: GreetRequest) -> GreetResponse | JSONResponse:
        try:
            message, visits = await greet(counter(), body.name)
        except InvalidName as error:
            return _error(400, "invalid_argument", error.detail)
        except CounterUnavailable, CounterBusy:
            return _error(503, "unavailable", "the counter service is unavailable, please retry")
        except CounterFailed:
            return _error(502, "bad_gateway", "the counter service failed")
        return GreetResponse(message=message, visits=visits)

    return router
