"""Native Dapr sidecars (dev, integration): `dapr run` commands and the Dapr
resources rendered to files for them."""

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from scripts.framework.description import dapr_resources_inputs
from scripts.framework.repo import PLATFORM_DIR
from scripts.framework.runs import RunRecord
from scripts.framework.shell import fail, free_port, run

RESOURCES_CHART = PLATFORM_DIR / "charts" / "dapr-resources"


@dataclass(frozen=True)
class SidecarPorts:
    app: int
    http: int
    grpc: int
    internal_grpc: int
    metrics: int

    @classmethod
    def allocate(cls, app_port: int | None = None) -> SidecarPorts:
        return cls(
            app=app_port or free_port(),
            http=free_port(),
            grpc=free_port(),
            internal_grpc=free_port(),
            metrics=free_port(),
        )


def dapr_run_command(
    *,
    app_id: str,
    ports: SidecarPorts,
    app_protocol: str,
    resources_dir: Path,
    config_file: Path,
    command: Sequence[str],
) -> list[str]:
    """`dapr run` for one app (or test process) with explicit resources.

    - The Dapr HTTP and gRPC APIs listen on 127.0.0.1 only: they have no
      authentication. (`dapr run` cannot restrict the internal gRPC and metrics
      listeners; they stay on all interfaces.)
    - Placement and Scheduler are disabled: the slim runtime installs but does
      not start them, and without them the sidecar would never report healthy.
    """
    args = [
        "dapr", "run",
        "--app-id", app_id,
        "--app-port", str(ports.app),
        "--app-protocol", app_protocol,
        "--dapr-http-port", str(ports.http),
        "--dapr-grpc-port", str(ports.grpc),
        "--dapr-internal-grpc-port", str(ports.internal_grpc),
        "--metrics-port", str(ports.metrics),
        "--dapr-listen-addresses", "127.0.0.1",
        "--resources-path", str(resources_dir),
        "--config", str(config_file),
        "--placement-host-address", "",
        "--scheduler-host-address", "",
    ]  # fmt: skip
    return [*args, "--", *command]


LOOPBACK = ("localhost", "127.0.0.1", "::1")


def sidecar_env() -> dict[str, str]:
    """Environment of `dapr run` (sidecar and app or test process).

    - DAPR_HOST_IP: announce loopback addresses for service discovery (otherwise
      mDNS may advertise a VPN or virtual interface address).
    - NO_PROXY: processes, sidecars and infrastructure all talk over loopback;
      proxies configured in the shell must not intercept that traffic.
    """
    env = {"DAPR_HOST_IP": "127.0.0.1"}
    for variable in ("NO_PROXY", "no_proxy"):
        current = [h for h in os.environ.get(variable, "").split(",") if h.strip()]
        env[variable] = ",".join([*current, *(h for h in LOOPBACK if h not in current)])
    return env


EMPTY_CONFIGURATION = {
    "apiVersion": "dapr.io/v1alpha1",
    "kind": "Configuration",
    "metadata": {"name": "default"},
    "spec": {},
}


@dataclass(frozen=True)
class NativeResources:
    """Rendered resources of a run: one components directory shared by all
    sidecars, and one file per Dapr Configuration."""

    resources_dir: Path
    configurations: dict[str, Path]

    def config_file(self, name: str) -> Path:
        """File of the named Configuration; "" selects an empty Configuration.

        Native sidecars always get an explicit file, so they never pick up the
        machine's default Dapr configuration.
        """
        if name not in self.configurations:
            known = ", ".join(sorted(n for n in self.configurations if n)) or "none"
            fail(
                f"Dapr Configuration '{name}' is not defined in "
                f"deploy/platform/charts/dapr-resources (defined: {known})"
            )
        return self.configurations[name]


def render_native(
    record: RunRecord, resources_values: Path, *, omit_scopes: bool
) -> NativeResources:
    """Render for `dapr run --resources-path/--config`.

    `omit_scopes` is for integration tests only: their sidecars use generated
    app ids, so scoped components would not load.
    """
    args = [
        "helm",
        "template",
        "dapr-resources",
        str(RESOURCES_CHART),
        *dapr_resources_inputs(resources_values),
    ]
    if omit_scopes:
        args += ["--set", "omitScopes=true"]
    documents = [d for d in yaml.safe_load_all(run(args, capture=True).stdout) if d]

    resources_dir = record.directory / "resources"
    resources_dir.mkdir(parents=True, exist_ok=True)
    components = [d for d in documents if d["kind"] != "Configuration"]
    (resources_dir / "components.yaml").write_text(yaml.safe_dump_all(components, sort_keys=False))

    config_dir = record.directory / "configurations"
    config_dir.mkdir(parents=True, exist_ok=True)
    configurations: dict[str, Path] = {}
    for document in [*(d for d in documents if d["kind"] == "Configuration"), None]:
        name = document["metadata"]["name"] if document else ""
        # ".empty" is not a valid resource name, so it cannot clash with a real one.
        path = config_dir / f"{name or '.empty'}.yaml"
        path.write_text(yaml.safe_dump(document or EMPTY_CONFIGURATION, sort_keys=False))
        configurations[name] = path
    return NativeResources(resources_dir, configurations)
