"""Shared fixtures for integration tests.

These tests run inside `dapr run` (one sidecar per package), started by
`uv run poe integration`, which provides:

    IT_RUN_ID      id of the integration run (use it in test data keys)
    IT_APP_ID      Dapr app id of this package's sidecar
    IT_APP_PORT    port the sidecar forwards service invocations to
    DAPR_HTTP_PORT, DAPR_GRPC_PORT   set by `dapr run`
"""

import os
import uuid
from dataclasses import dataclass

import pytest


@dataclass(frozen=True)
class IntegrationEnv:
    run_id: str
    app_id: str
    app_port: int


@pytest.fixture(scope="session")
def it_env() -> IntegrationEnv:
    required = ("IT_RUN_ID", "IT_APP_ID", "IT_APP_PORT", "DAPR_HTTP_PORT")
    missing = [name for name in required if name not in os.environ]
    if missing:
        pytest.fail(
            f"missing {', '.join(missing)}: run integration tests with `uv run poe integration`"
        )
    return IntegrationEnv(
        run_id=os.environ["IT_RUN_ID"],
        app_id=os.environ["IT_APP_ID"],
        app_port=int(os.environ["IT_APP_PORT"]),
    )


@pytest.fixture
def unique_name(it_env: IntegrationEnv) -> str:
    """A test-data identifier unique to this run and test."""
    return f"{it_env.run_id[-4:]}-{uuid.uuid4().hex[:10]}"
