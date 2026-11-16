"""The environment description of the system, read as data.

    deploy/environment/infra.yaml       infrastructure instances (Redis, Jaeger, ...)
    deploy/environment/components.yaml  Dapr components and their scopes
    deploy/environment/files/           files mounted into instances
    deploy/apps/<deployment>.yaml       app instances

The framework turns it into running environments. It treats every name, image
and port in these files as data and never refers to a particular one.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from scripts.framework.repo import ROOT, load_yaml
from scripts.framework.runs import RunRecord
from scripts.framework.shell import fail

ENVIRONMENT_DIR = ROOT / "deploy" / "environment"


INFRA_FILE = ENVIRONMENT_DIR / "infra.yaml"


COMPONENTS_FILE = ENVIRONMENT_DIR / "components.yaml"


FILES_DIR = ENVIRONMENT_DIR / "files"


ENVIRONMENTS = ("dev", "integration", "kind")
"""Local environments: native development, integration tests, Kubernetes on kind."""


_NAME = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")


_INSTANCE_KEYS = {
    "image", "args", "env", "ports", "files", "ready",
    "environments", "expose", "resource", "appEnv",
}  # fmt: skip


_PLACEHOLDER = re.compile(r"\{(host|port:[a-z0-9-]+)\}")


# NodePorts given to exposed ports in kind, in a fixed order.
NODE_PORT_BASE = 30000


APPS_DIR = ROOT / "deploy" / "apps"


@dataclass(frozen=True)
class Deployment:
    name: str
    path: Path
    app_id: str
    member: str
    bake_target: str
    command: str
    app_port: int
    app_protocol: str
    dapr_config: str
    env: dict[str, str] = field(default_factory=dict)
    expose: dict[str, int] = field(default_factory=dict)
    """Host ports in dev and kind, by port name (`http` is the app port)."""

    @classmethod
    def from_file(cls, path: Path) -> Deployment:
        data: dict[str, Any] = load_yaml(path)
        return cls(
            name=path.stem,
            path=path,
            app_id=data["appId"],
            member=data["member"],
            bake_target=data["bakeTarget"],
            command=data["command"],
            app_port=int(data["appPort"]),
            app_protocol=data.get("appProtocol", "http"),
            dapr_config=data.get("daprConfig", ""),
            env={k: str(v) for k, v in (data.get("env") or {}).items()},
            expose={str(k): int(v) for k, v in (data.get("expose") or {}).items()},
        )


def load_deployments(names: list[str] | None = None) -> list[Deployment]:
    """Load the named deployments, or all of them."""
    available = {path.stem: path for path in sorted(APPS_DIR.glob("*.yaml"))}
    if names:
        unknown = sorted(set(names) - set(available))
        if unknown:
            fail(f"unknown deployments: {', '.join(unknown)} (available: {', '.join(available)})")
        return [Deployment.from_file(available[name]) for name in names]
    return [Deployment.from_file(path) for path in available.values()]


@dataclass(frozen=True)
class Instance:
    """One infrastructure instance of deploy/environment/infra.yaml."""

    name: str
    image: str
    ports: dict[str, int]
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)
    """File name in deploy/environment/files/ -> path in the container."""
    ready: list[str] | None = None
    environments: tuple[str, ...] = ENVIRONMENTS
    expose: dict[str, int] = field(default_factory=dict)
    resource: dict[str, Any] | None = None
    app_env: dict[str, str] = field(default_factory=dict)


def _invalid(name: str, problem: str) -> None:
    fail(f"{INFRA_FILE.relative_to(ROOT)}: instance '{name}': {problem}")


def _instance(name: str, spec: Any) -> Instance:
    if not _NAME.match(name):
        _invalid(name, "the name must be a lowercase DNS label")
    if not isinstance(spec, dict):
        _invalid(name, "must be a mapping")
    unknown = sorted(set(spec) - _INSTANCE_KEYS)
    if unknown:
        _invalid(name, f"unknown keys: {', '.join(unknown)}")
    if not spec.get("image"):
        _invalid(name, "`image` is required")
    ports = spec.get("ports") or {}
    if not isinstance(ports, dict) or not all(
        _NAME.match(str(p)) and isinstance(n, int) for p, n in ports.items()
    ):
        _invalid(name, "`ports` must map port names to numbers")
    environments = tuple(spec.get("environments") or ENVIRONMENTS)
    if set(environments) - set(ENVIRONMENTS):
        _invalid(name, f"`environments` must be among {', '.join(ENVIRONMENTS)}")
    expose = spec.get("expose") or {}
    if set(expose) - set(ports):
        _invalid(name, "`expose` refers to ports that are not in `ports`")
    files = spec.get("files") or {}
    for file in files:
        if not (FILES_DIR / file).is_file():
            _invalid(name, f"file {FILES_DIR.relative_to(ROOT) / file} does not exist")
    resource = spec.get("resource")
    if resource is not None and not ({"name", "value"} <= set(resource)):
        _invalid(name, "`resource` needs `name` and `value`")
    ready = spec.get("ready")
    if ready is not None and not (isinstance(ready, dict) and ready.get("command")):
        _invalid(name, "`ready` needs a `command`")
    return Instance(
        name=name,
        image=spec["image"],
        ports={str(p): int(n) for p, n in ports.items()},
        args=[str(a) for a in spec.get("args") or []],
        env={str(k): str(v) for k, v in (spec.get("env") or {}).items()},
        files={str(f): str(p) for f, p in files.items()},
        ready=[str(c) for c in ready["command"]] if ready else None,
        environments=environments,
        expose={str(p): int(h) for p, h in expose.items()},
        resource=resource,
        app_env={str(k): str(v) for k, v in (spec.get("appEnv") or {}).items()},
    )


def load_instances(environment: str | None = None) -> list[Instance]:
    """Instances of infra.yaml, optionally only those enabled in `environment`."""
    data = load_yaml(INFRA_FILE) or {}
    instances = [_instance(str(name), spec) for name, spec in sorted(data.items())]
    if environment is not None:
        instances = [i for i in instances if environment in i.environments]
    return instances


def load_components() -> dict[str, Any]:
    return (load_yaml(COMPONENTS_FILE) or {}).get("components") or {}


def fill(template: Any, *, host: str, ports: Mapping[str, int]) -> Any:
    """Replace {host} and {port:<name>} in a template from infra.yaml.

    A string that is exactly one port placeholder becomes an integer.
    """
    if isinstance(template, dict):
        return {key: fill(value, host=host, ports=ports) for key, value in template.items()}
    if isinstance(template, list):
        return [fill(value, host=host, ports=ports) for value in template]
    if not isinstance(template, str):
        return template

    def value(match: re.Match[str]) -> str:
        token = match.group(1)
        if token == "host":
            return host
        port = token.removeprefix("port:")
        if port not in ports:
            fail(f"{INFRA_FILE.relative_to(ROOT)}: unknown port '{port}' in {template!r}")
        return str(ports[port])

    filled = _PLACEHOLDER.sub(value, template)
    whole_port = re.fullmatch(r"\{port:[a-z0-9-]+\}", template)
    return int(filled) if whole_port else filled


@dataclass(frozen=True)
class Addresses:
    """Where the instances of one environment are, for sidecars and apps."""

    resources: dict[str, Any]
    """resources-values.yaml content: resource name -> connection settings."""
    app_env: dict[str, str]
    """Environment variables for every app."""


def addresses(
    instances: Sequence[Instance], locate: Mapping[str, tuple[str, Mapping[str, int]]]
) -> Addresses:
    """Fill the `resource` and `appEnv` templates; `locate` gives each
    instance's host and port numbers as seen from the environment."""
    resources: dict[str, Any] = {}
    app_env: dict[str, str] = {}
    for instance in instances:
        host, ports = locate[instance.name]
        if instance.resource is not None:
            resources[instance.resource["name"]] = fill(
                instance.resource["value"], host=host, ports=ports
            )
        for key, template in instance.app_env.items():
            app_env[key] = str(fill(template, host=host, ports=ports))
    return Addresses(resources, app_env)


@dataclass(frozen=True)
class Exposure:
    """A port reachable from the host: `<deployment or instance>.<port name>`."""

    name: str
    host_port: int
    node_port: int


def exposures(deployments: Sequence[Deployment], instances: Sequence[Instance]) -> list[Exposure]:
    """Every exposed port of apps and instances, with a stable NodePort each."""
    declared = [(f"{d.name}.{port}", host) for d in deployments for port, host in d.expose.items()]
    declared += [(f"{i.name}.{port}", host) for i in instances for port, host in i.expose.items()]
    return [
        Exposure(name, host, NODE_PORT_BASE + index)
        for index, (name, host) in enumerate(sorted(declared))
    ]


@dataclass(frozen=True)
class Infra:
    """The infrastructure of one environment, as apps and sidecars see it."""

    resources_values: Path
    app_env: dict[str, str]


def write_infra(record: RunRecord, found: Addresses) -> Infra:
    path = record.directory / "resources-values.yaml"
    path.write_text(yaml.safe_dump({"resources": found.resources}, sort_keys=False))
    return Infra(path, found.app_env)


def dapr_resources_inputs(resources_values: Path) -> list[str]:
    """The chart's two inputs: the system's components and the environment's
    resources-values.yaml."""
    return ["--values", str(COMPONENTS_FILE), "--values", str(resources_values)]
