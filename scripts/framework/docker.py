"""Docker target (dev, integration): infrastructure instances in a Compose project
generated for the run, with ports published on 127.0.0.1."""

from typing import Any

import yaml

from scripts.framework.description import (
    FILES_DIR,
    Addresses,
    Infra,
    Instance,
    addresses,
    load_instances,
    write_infra,
)
from scripts.framework.repo import dns_label
from scripts.framework.runs import RunRecord
from scripts.framework.shell import run


def compose_model(instances: list[Instance], *, expose: bool) -> dict[str, Any]:
    """A Compose project for `instances`.

    Ports are published on 127.0.0.1 only: on a free port, or on the declared
    host port when `expose` is set (interactive environments).
    """
    services: dict[str, Any] = {}
    for instance in instances:
        service: dict[str, Any] = {"image": instance.image}
        if instance.args:
            service["command"] = instance.args
        if instance.env:
            service["environment"] = instance.env
        service["ports"] = [
            f"127.0.0.1:{instance.expose[name] if expose and name in instance.expose else ''}"
            f":{number}"
            for name, number in instance.ports.items()
        ]
        if instance.files:
            service["volumes"] = [
                f"{FILES_DIR / file}:{path}:ro" for file, path in instance.files.items()
            ]
        if instance.ready:
            service["healthcheck"] = {
                "test": ["CMD", *instance.ready],
                "interval": "1s",
                "timeout": "3s",
                "retries": 120,
            }
        services[instance.name] = service
    return {"services": services}


def _compose(project: str, file: str | None, *args: str, capture: bool = False):
    command = ["docker", "compose", "--project-name", project]
    if file:
        command += ["--file", file]
    return run([*command, *args], capture=capture, quiet=capture)


def start_docker(record: RunRecord, environment: str, *, expose: bool) -> Infra:
    """Run the instances enabled in `environment` and wait until they are ready."""
    instances = load_instances(environment)
    if not instances:
        return write_infra(record, Addresses({}, {}))
    project = dns_label(record.run_id)
    file = record.directory / "compose.yaml"
    file.write_text(yaml.safe_dump(compose_model(instances, expose=expose), sort_keys=False))
    record.add("compose", project=project, file=str(file))
    _compose(project, str(file), "up", "--detach", "--wait")

    def locate(instance: Instance) -> tuple[str, dict[str, int]]:
        ports = {}
        for name, number in instance.ports.items():
            published = _compose(
                project, str(file), "port", instance.name, str(number), capture=True
            )
            ports[name] = int(published.stdout.strip().rsplit(":", 1)[1])
        return "127.0.0.1", ports

    return write_infra(record, addresses(instances, {i.name: locate(i) for i in instances}))


def compose_down(project: str, file: str | None) -> None:
    """Remove a Compose project of a run, with its volumes."""
    _compose(project, file, "down", "--volumes", "--remove-orphans")
