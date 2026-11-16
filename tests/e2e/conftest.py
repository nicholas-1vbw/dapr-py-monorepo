"""Endpoints of the environment under test.

`uv run poe e2e` passes the exposed ports of its cluster in `E2E_ENDPOINTS`
(`{"<deployment or instance>.<port>": "http://127.0.0.1:<port>"}`). Without it,
the scenarios run against the host ports declared under `expose`, for example
on an environment created with `uv run poe kind up`:

    uv run pytest tests/e2e
"""

import json
import os
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def _declared_endpoints() -> dict[str, str]:
    endpoints: dict[str, str] = {}
    for path in sorted((ROOT / "deploy" / "apps").glob("*.yaml")):
        for port, host_port in (yaml.safe_load(path.read_text()).get("expose") or {}).items():
            endpoints[f"{path.stem}.{port}"] = f"http://127.0.0.1:{host_port}"
    infra = yaml.safe_load((ROOT / "deploy" / "environment" / "infra.yaml").read_text()) or {}
    for name, spec in infra.items():
        for port, host_port in (spec.get("expose") or {}).items():
            endpoints[f"{name}.{port}"] = f"http://127.0.0.1:{host_port}"
    return endpoints


@pytest.fixture(scope="session")
def endpoints() -> dict[str, str]:
    given = os.environ.get("E2E_ENDPOINTS")
    return json.loads(given) if given else _declared_endpoints()


@pytest.fixture(scope="session")
def api_url(endpoints: dict[str, str]) -> str:
    return endpoints["gateway.http"]


@pytest.fixture(scope="session")
def jaeger_url(endpoints: dict[str, str]) -> str:
    return endpoints["jaeger.ui"]
