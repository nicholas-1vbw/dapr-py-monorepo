"""Startup component checks against a real Dapr sidecar.

These tests belong to the shared runtime, not to the system: they use whatever
components the system declares in deploy/environment/components.yaml.
"""

import socket
from pathlib import Path

import pytest
import yaml
from app_runtime import ComponentRequirement, RuntimeSettings, create_app
from app_runtime.components import ComponentCheckError, loaded_components, wait_for_components
from dapr.aio.clients import DaprClient

from tests.integration.support import StartupFailed, serve

COMPONENTS_FILE = Path(__file__).resolve().parents[3] / "deploy/environment/components.yaml"
MISSING = ComponentRequirement("not-configured", "state.")


@pytest.fixture(scope="module")
def declared() -> ComponentRequirement:
    """A component the system declares, required by name and category."""
    components = (yaml.safe_load(COMPONENTS_FILE.read_text()) or {}).get("components") or {}
    if not components:
        pytest.skip("the system declares no Dapr components")
    name, spec = sorted(components.items())[0]
    return ComponentRequirement(name, spec["type"].split(".")[0] + ".")


async def test_required_component_is_loaded(declared: ComponentRequirement) -> None:
    async with DaprClient() as dapr:
        await wait_for_components([declared], lambda: loaded_components(dapr), timeout_seconds=30)


async def test_missing_component_is_reported() -> None:
    async with DaprClient() as dapr:
        with pytest.raises(ComponentCheckError, match="'not-configured' is not loaded"):
            await wait_for_components(
                [MISSING], lambda: loaded_components(dapr), timeout_seconds=1, interval_seconds=0.2
            )


async def test_wrong_category_is_reported(declared: ComponentRequirement) -> None:
    other = "bindings." if declared.type_prefix != "bindings." else "state."
    async with DaprClient() as dapr:
        with pytest.raises(ComponentCheckError, match=f"expected '{other}\\*'"):
            await wait_for_components(
                [ComponentRequirement(declared.name, other)],
                lambda: loaded_components(dapr),
                timeout_seconds=1,
                interval_seconds=0.2,
            )


def test_app_does_not_serve_without_required_component() -> None:
    app = create_app(
        RuntimeSettings(name="startup-check-it", startup_timeout_seconds=1),
        requirements=[MISSING],
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with pytest.raises(StartupFailed), serve(app, port):
        pass
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", port)) != 0, "port must not accept requests"
