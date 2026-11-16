"""Kubernetes target (kind): the cluster, the standard Dapr control plane, the
infrastructure instances (deploy/platform/charts/infra), the Dapr resources and
one release per app."""

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from scripts.framework import repo
from scripts.framework.description import (
    FILES_DIR,
    NODE_PORT_BASE,
    Deployment,
    Exposure,
    Infra,
    Instance,
    addresses,
    dapr_resources_inputs,
    exposures,
    load_deployments,
    load_instances,
    write_infra,
)
from scripts.framework.images import build, remove_image, split_image
from scripts.framework.repo import PLATFORM_DIR, ROOT, load_yaml, versions
from scripts.framework.runs import RunRecord
from scripts.framework.shell import fail, free_port, log, require_tools, run

RESOURCES_CHART = PLATFORM_DIR / "charts" / "dapr-resources"


INFRA_CHART = PLATFORM_DIR / "charts" / "infra"


DAPR_NAMESPACE = "dapr-system"


@dataclass(frozen=True)
class KubeTarget:
    kubeconfig: Path | None
    context: str
    namespace: str = "default"

    def helm_args(self) -> list[str]:
        args = ["--kube-context", self.context]
        if self.kubeconfig:
            args += ["--kubeconfig", str(self.kubeconfig)]
        return args

    def kubectl_args(self) -> list[str]:
        args = ["--context", self.context]
        if self.kubeconfig:
            args += ["--kubeconfig", str(self.kubeconfig)]
        return args


def install_dapr(target: KubeTarget) -> None:
    """Install the standard Dapr control plane from the official chart."""
    dapr = versions()["dapr"]
    run([
        "helm", "upgrade", "--install", "dapr", "dapr",
        "--repo", dapr["chartRepository"], "--version", dapr["chartVersion"],
        "--namespace", DAPR_NAMESPACE, "--create-namespace",
        "--wait", "--timeout", "10m", *target.helm_args(),
    ])  # fmt: skip


def chart_values(instances: list[Instance], exposed: list[Exposure]) -> dict[str, Any]:
    """Values of the infra chart: the instances, with file contents and NodePorts."""
    node_ports = {e.name: e.node_port for e in exposed}
    values: dict[str, Any] = {}
    for instance in instances:
        spec: dict[str, Any] = {"image": instance.image, "ports": instance.ports}
        if instance.args:
            spec["args"] = instance.args
        if instance.env:
            spec["env"] = instance.env
        if instance.ready:
            spec["ready"] = {"command": instance.ready}
        if instance.files:
            spec["files"] = {
                file: {"path": path, "content": (FILES_DIR / file).read_text()}
                for file, path in instance.files.items()
            }
        exposed_ports = {port: node_ports[f"{instance.name}.{port}"] for port in instance.expose}
        if exposed_ports:
            spec["nodePorts"] = exposed_ports
        values[instance.name] = spec
    return {"instances": values}


def install_k8s_infra(record: RunRecord, target: KubeTarget, exposed: list[Exposure]) -> Infra:
    """Install the instances enabled in kind and describe them for the cluster."""
    instances = load_instances("kind")
    values_file = record.directory / "values" / "infra.yaml"
    values_file.parent.mkdir(parents=True, exist_ok=True)
    values_file.write_text(yaml.safe_dump(chart_values(instances, exposed), sort_keys=False))
    run([
        "helm", "upgrade", "--install", "infra", str(INFRA_CHART),
        "--namespace", target.namespace, "--values", str(values_file),
        "--wait", "--timeout", "5m", *target.helm_args(),
    ])  # fmt: skip
    suffix = f"{target.namespace}.svc.cluster.local"
    located = {i.name: (f"{i.name}.{suffix}", i.ports) for i in instances}
    return write_infra(record, addresses(instances, located))


def install_dapr_resources(target: KubeTarget, resources_values: Path) -> None:
    run([
        "helm", "upgrade", "--install", "dapr-resources", str(RESOURCES_CHART),
        "--namespace", target.namespace, *dapr_resources_inputs(resources_values),
        "--wait", *target.helm_args(),
    ])  # fmt: skip


KIND_CONFIG = PLATFORM_DIR / "kind.yaml"


APP_CHART = PLATFORM_DIR / "charts" / "app"


@dataclass(frozen=True)
class Environment:
    target: KubeTarget
    deployments: list[Deployment]
    endpoints: dict[str, str]
    """Exposed ports by name (`<deployment or instance>.<port>`) -> URL on the host."""


def cluster_exists(name: str) -> bool:
    return name in run(["kind", "get", "clusters"], capture=True, quiet=True).stdout.split()


def host_port_mappings(name: str) -> dict[int, int]:
    """NodePort -> host port mappings of an existing cluster's control-plane node."""
    output = run(
        ["docker", "inspect", "--format", "{{json .HostConfig.PortBindings}}",
         f"{name}-control-plane"],
        capture=True, quiet=True,
    ).stdout  # fmt: skip
    bindings = json.loads(output) or {}
    mappings = {}
    for container, hosts in bindings.items():
        port = int(container.split("/")[0])
        if hosts and NODE_PORT_BASE <= port <= 32767:
            mappings[port] = int(hosts[0]["HostPort"])
    return mappings


def check_reusable(name: str, wanted: dict[int, int]) -> None:
    """An existing cluster keeps the port mappings it was created with."""
    if not cluster_exists(name):
        return
    mapped = host_port_mappings(name)
    if mapped != wanted:
        fail(
            f"kind cluster {name} was created with other exposed ports "
            f"({mapped} instead of {wanted}, NodePort: host port). "
            "Remove it first with `uv run poe kind down`."
        )


def create_cluster(
    record: RunRecord, name: str, *, port_mappings: dict[int, int], kubeconfig: Path | None
) -> None:
    """Create the cluster from deploy/platform/kind.yaml plus the host port
    mappings of the exposed ports (NodePort -> host port, on 127.0.0.1)."""
    if kubeconfig is None and cluster_exists(name):
        log(f"reusing kind cluster {name}")
        return
    if kubeconfig is not None and cluster_exists(name):
        # Never record (and later delete) a cluster this run did not create.
        fail(f"a kind cluster named {name} already exists; not creating or deleting it")
    config = load_yaml(KIND_CONFIG)
    control_plane = next(n for n in config["nodes"] if n.get("role") == "control-plane")
    control_plane.setdefault("extraPortMappings", []).extend(
        {"containerPort": node_port, "hostPort": host_port, "listenAddress": "127.0.0.1"}
        for node_port, host_port in sorted(port_mappings.items())
    )
    config_file = record.directory / "kind.yaml"
    config_file.write_text(yaml.safe_dump(config, sort_keys=False))
    record.add("kind-cluster", name=name, kubeconfig=str(kubeconfig) if kubeconfig else None)
    args = ["kind", "create", "cluster", "--name", name, "--config", str(config_file)]
    if kubeconfig:
        args += ["--kubeconfig", str(kubeconfig)]
    run([*args, "--wait", "2m"])


def deploy_app(
    record: RunRecord,
    target: KubeTarget,
    deployment: Deployment,
    image: str,
    *,
    app_env: dict[str, str],
    node_ports: dict[str, int],
) -> None:
    """Install one app release: its deployment input plus this environment's
    image, infrastructure environment variables and exposure."""
    repository, tag = split_image(image)
    overrides: dict = {"image": {"repository": repository, "tag": tag}}
    if app_env:
        overrides["env"] = {**deployment.env, **app_env}
    node_port = node_ports.get(f"{deployment.name}.http")
    if node_port is not None:
        overrides["service"] = {"enabled": True, "type": "NodePort", "nodePort": node_port}
    overrides_file = record.directory / "values" / f"{deployment.name}.yaml"
    overrides_file.parent.mkdir(parents=True, exist_ok=True)
    overrides_file.write_text(yaml.safe_dump(overrides, sort_keys=False))
    run([
        "helm", "upgrade", "--install", deployment.app_id, str(APP_CHART),
        "--namespace", target.namespace,
        "--values", str(deployment.path), "--values", str(overrides_file),
        "--wait", "--timeout", "5m", *target.helm_args(),
    ])  # fmt: skip


def build_images(record: RunRecord, targets: list[str]) -> dict[str, str]:
    """Build images tagged with the run id; record them so teardown removes them."""
    images = build(targets, tag=record.run_id, load=True, output_dir=record.directory)
    for image in sorted(set(images.values())):
        record.add("image", reference=image)
    return images


def provision(
    record: RunRecord, *, name: str, kubeconfig: Path | None, free_host_ports: bool = False
) -> Environment:
    """Build images and create the cluster in parallel, then install everything.

    Exposed ports get their declared host ports, or free ones with
    `free_host_ports` (for runs next to other environments).
    """
    require_tools("docker", "kind", "kubectl", "helm")
    deployments = load_deployments()
    if not deployments:
        fail("no deployments in deploy/apps")
    exposed = exposures(deployments, load_instances("kind"))
    host_ports = {e.name: free_port() if free_host_ports else e.host_port for e in exposed}
    port_mappings = {e.node_port: host_ports[e.name] for e in exposed}
    if kubeconfig is None:
        check_reusable(name, port_mappings)
    targets = sorted({d.bake_target for d in deployments})
    with ThreadPoolExecutor(max_workers=2) as pool:
        images_future = pool.submit(build_images, record, targets)
        cluster_future = pool.submit(
            create_cluster, record, name, port_mappings=port_mappings, kubeconfig=kubeconfig
        )
        images = images_future.result()
        cluster_future.result()

    target = KubeTarget(kubeconfig=kubeconfig, context=f"kind-{name}")
    install_dapr(target)
    infra = install_k8s_infra(record, target, exposed)
    install_dapr_resources(target, infra.resources_values)
    for image in sorted(set(images.values())):
        run(["kind", "load", "docker-image", image, "--name", name])
    node_ports = {e.name: e.node_port for e in exposed}
    for deployment in deployments:
        deploy_app(
            record,
            target,
            deployment,
            images[deployment.bake_target],
            app_env=infra.app_env,
            node_ports=node_ports,
        )
    endpoints = {name: f"http://127.0.0.1:{port}" for name, port in host_ports.items()}
    return Environment(target=target, deployments=deployments, endpoints=endpoints)


def collect_diagnostics(
    record: RunRecord, target: KubeTarget, deployments: list[Deployment]
) -> None:
    """Save pod status, events and app/sidecar logs under the run directory.

    Works on a partially provisioned cluster: missing resources are noted in the
    saved output instead of failing.
    """
    out = record.directory / "diagnostics"
    out.mkdir(parents=True, exist_ok=True)
    kubectl = ["kubectl", *target.kubectl_args()]

    def save(file: str, args: list[str]) -> None:
        result = subprocess.run([*kubectl, *args], cwd=ROOT, capture_output=True, text=True)
        (out / file).write_text(result.stdout + result.stderr)

    save("pods.txt", ["get", "pods", "--all-namespaces", "-o", "wide"])
    save("events.txt", ["get", "events", "--all-namespaces", "--sort-by=.lastTimestamp"])
    for deployment in deployments:
        for container in ("app", "daprd"):
            save(
                f"{deployment.app_id}-{container}.log",
                ["logs", f"deployment/{deployment.app_id}", "-c", container, "--tail=-1"],
            )
    log(f"diagnostics saved to {out.relative_to(ROOT)}")


def forget_cluster(cluster: str) -> bool:
    """After `kind down`: remove the images built for this cluster and drop the
    cluster from every run record (`kind up` and `e2e --keep`), so that tearing
    down an old run can never delete a newer cluster with the same name.

    Returns False when some image could not be removed (it stays recorded).
    """
    ok = True
    for path in sorted(repo.RUNS_DIR.glob("*/environment.json")):
        record = RunRecord.load(path.parent.name)
        if not any(
            i["kind"] in ("cluster-name", "kind-cluster") and i.get("name") == cluster
            for i in record.owned
        ):
            continue
        record.remove("kind-cluster", name=cluster)
        for item in [i for i in record.owned if i["kind"] == "image"]:
            try:
                remove_image(item["reference"])
            except RuntimeError as error:
                ok = False
                log(f"could not remove image {item['reference']}: {error}")
                continue
            record.remove("image", reference=item["reference"])
    return ok


def delete_cluster(name: str, kubeconfig: str | None) -> None:
    run(
        [
            "kind",
            "delete",
            "cluster",
            "--name",
            name,
            *(["--kubeconfig", kubeconfig] if kubeconfig else []),
        ]
    )
