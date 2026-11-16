"""Checks of the deployment inputs (part of `poe lint`).

- Every chart is linted with the inputs it is used with.
- The environment description is consistent: components refer to resources
  provided in every local environment, scopes name existing app ids, exposed
  host ports are unique, and the generated Compose projects are valid.
- Rendered components keep their scopes unless integration-test mode removes them.
"""

import shutil
import tempfile
from pathlib import Path

import yaml

from scripts.framework.description import (
    COMPONENTS_FILE,
    ENVIRONMENTS,
    addresses,
    exposures,
    load_components,
    load_deployments,
    load_instances,
)
from scripts.framework.docker import compose_model
from scripts.framework.kubernetes import INFRA_CHART, chart_values
from scripts.framework.repo import PLATFORM_DIR
from scripts.framework.runs import entrypoint
from scripts.framework.shell import fail, log, require_tools, run

APP_CHART = PLATFORM_DIR / "charts" / "app"
RESOURCES_CHART = PLATFORM_DIR / "charts" / "dapr-resources"
EXAMPLE_VALUES = PLATFORM_DIR / "resources-values.example.yaml"
IMAGE = ["--set", "image.repository=example/app", "--set", "image.tag=check"]


def check_references() -> list[str]:
    """Problems in the links between apps, components and instances."""
    problems: list[str] = []
    deployments = load_deployments()
    app_ids = {d.app_id for d in deployments}
    components = load_components()
    for environment in ENVIRONMENTS:
        provided = {
            i.resource["name"] for i in load_instances(environment) if i.resource is not None
        }
        for name, component in components.items():
            if component.get("resource") not in provided:
                problems.append(
                    f"component '{name}' uses resource '{component.get('resource')}', which no "
                    f"instance enabled for '{environment}' provides"
                )
    for name, component in components.items():
        unknown = sorted(set(component.get("scopes") or []) - app_ids)
        if unknown:
            problems.append(
                f"component '{name}' is scoped to unknown app ids: {', '.join(unknown)}"
            )
    host_ports: dict[int, str] = {}
    for exposure in exposures(deployments, load_instances()):
        other = host_ports.setdefault(exposure.host_port, exposure.name)
        if other != exposure.name:
            problems.append(
                f"host port {exposure.host_port} is exposed by both {other} and {exposure.name}"
            )
    return problems


def described_resources(directory: Path) -> Path:
    """resources-values.yaml of the kind target, derived from infra.yaml, so the
    system's components render against the resources the system describes."""
    instances = load_instances("kind")
    found = addresses(instances, {i.name: (f"{i.name}.check.svc", i.ports) for i in instances})
    path = directory / "resources-values.yaml"
    path.write_text(yaml.safe_dump({"resources": found.resources}))
    return path


def render_components(resources_values: Path, *extra: str) -> list[dict]:
    args = ["helm", "template", "r", str(RESOURCES_CHART), "--values", str(COMPONENTS_FILE)]
    args += ["--values", str(resources_values)]
    output = run([*args, *extra], capture=True, quiet=True).stdout
    return [d for d in yaml.safe_load_all(output) if d and d["kind"] == "Component"]


def main() -> None:
    require_tools("helm")
    problems = check_references()
    if problems:
        fail("inconsistent environment description:\n  " + "\n  ".join(problems))
    log("environment references: ok")

    for deployment in load_deployments():
        run(["helm", "lint", "--strict", str(APP_CHART), "--values", str(deployment.path), *IMAGE])
    # The documented production format, on its own.
    run(["helm", "lint", "--strict", str(RESOURCES_CHART), "--values", str(EXAMPLE_VALUES)])

    kind_instances = load_instances("kind")
    with tempfile.TemporaryDirectory() as directory:
        resources_values = described_resources(Path(directory))
        run([
            "helm", "lint", "--strict", str(RESOURCES_CHART),
            "--values", str(COMPONENTS_FILE), "--values", str(resources_values),
        ])  # fmt: skip
        if not all(c.get("scopes") for c in render_components(resources_values)):
            fail("every rendered component must declare scopes")
        if any(
            "scopes" in c for c in render_components(resources_values, "--set", "omitScopes=true")
        ):
            fail("omitScopes=true must remove scopes")
        log("component scopes: ok")

        values = Path(directory) / "infra.yaml"
        exposed = exposures(load_deployments(), kind_instances)
        values.write_text(yaml.safe_dump(chart_values(kind_instances, exposed)))
        run(["helm", "lint", "--strict", str(INFRA_CHART), "--values", str(values)])

        if shutil.which("docker"):
            for environment in ("dev", "integration"):
                instances = load_instances(environment)
                if not instances:
                    continue
                compose = Path(directory) / f"compose-{environment}.yaml"
                model = compose_model(instances, expose=environment == "dev")
                compose.write_text(yaml.safe_dump(model))
                run(["docker", "compose", "--file", str(compose), "config", "--quiet"])
        else:
            log("docker not found: skipping the Compose checks")


if __name__ == "__main__":
    entrypoint(main)
