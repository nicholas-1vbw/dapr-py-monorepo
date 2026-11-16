"""Behaviour of generated contract types that the shared runtime relies on,
checked with the platform's test contract."""

import protovalidate
import pytest
from contracts.platform.testing.v1.probe_pb import EchoRequest, EchoResponse
from pydantic import BaseModel


def test_valid_request_passes_validation() -> None:
    protovalidate.validate(EchoRequest(text="hello"))


@pytest.mark.parametrize("text", ["", "x" * 65])
def test_invalid_request_violates_rules(text: str) -> None:
    with pytest.raises(protovalidate.ValidationError):
        protovalidate.validate(EchoRequest(text=text))


def test_construction_does_not_validate() -> None:
    # Validation is explicit; building an invalid message is allowed.
    assert EchoRequest(text="").text == ""


class Envelope(BaseModel):
    request: EchoRequest
    note: str


def test_generated_message_is_a_pydantic_field() -> None:
    envelope = Envelope(request=EchoRequest(text="hello"), note="n")
    assert isinstance(envelope.model_dump()["request"], EchoRequest)
    assert envelope.model_dump(mode="json") == {"request": {"text": "hello"}, "note": "n"}
    parsed = Envelope.model_validate_json('{"request": {"text": "hi"}, "note": "n"}')
    assert parsed.request.text == "hi"


def test_binary_round_trip() -> None:
    response = EchoResponse(text="hello")
    assert EchoResponse.from_binary(response.to_binary()) == response
