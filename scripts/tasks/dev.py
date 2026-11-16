"""Run apps natively, each with a Dapr sidecar, on local infrastructure.

    uv run poe dev [--apps NAME ...] [--no-reload]

NAME refers to deploy/apps/<NAME>.yaml (default: all). The infrastructure
instances enabled for `dev` in deploy/environment/infra.yaml run in Docker for
this session; everything is removed when you press Ctrl+C.

HTTP apps restart on code changes (unless --no-reload); gRPC apps do not and
need a restart of the session.
"""

import argparse
import json
import socket
import sys
import time
import tomllib
import urllib.request
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from scripts.framework.cleanup import teardown
from scripts.framework.description import Deployment, load_deployments
from scripts.framework.docker import start_docker
from scripts.framework.native import SidecarPorts, dapr_run_command, render_native, sidecar_env
from scripts.framework.repo import workspace_members
from scripts.framework.runs import RunRecord, entrypoint, shield_cleanup, start_process
from scripts.framework.shell import fail, log, require_dapr_runtime, require_tools, wait_until


def source_dirs(member: str) -> list[str]:
    """Source directories of a member and of the workspace members it depends on."""
    members = workspace_members()
    seen: set[str] = set()
    pending = [canonicalize_name(member)]
    while pending:
        name = pending.pop()
        if name in seen or name not in members:
            continue
        seen.add(name)
        manifest = tomllib.loads((members[name] / "pyproject.toml").read_text())
        for requirement in manifest["project"].get("dependencies", []):
            pending.append(canonicalize_name(Requirement(requirement).name))
    return sorted(str(members[name] / "src") for name in seen)


# The apps are local: probe them directly, whatever proxies the shell sets.
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def ready(port: int, protocol: str) -> bool:
    """An HTTP app answers /readyz once its startup checks passed; a gRPC app
    opens its port only then."""
    if protocol == "grpc":
        with socket.socket() as sock:
            sock.settimeout(2)
            return sock.connect_ex(("127.0.0.1", port)) == 0
    with DIRECT.open(f"http://127.0.0.1:{port}/readyz", timeout=2) as response:
        return response.status == 200


def reloads(deployment: Deployment, no_reload: bool) -> bool:
    """Only HTTP apps (uvicorn) can restart on code changes."""
    return not no_reload and deployment.app_protocol == "http"


def main() -> None:
    parser = argparse.ArgumentParser(prog="poe dev", description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--apps", nargs="+", metavar="NAME", help="deployments to run")
    parser.add_argument("--no-reload", action="store_true", help="do not restart on code changes")
    args = parser.parse_args()

    deployments = load_deployments(args.apps)
    require_tools("docker", "helm")
    require_dapr_runtime()
    bin_dir = Path(sys.executable).parent

    record = RunRecord.create("dev")
    try:
        infra = start_docker(record, "dev", expose=True)
        native = render_native(record, infra.resources_values, omit_scopes=False)
        config_files = {d.name: native.config_file(d.dapr_config) for d in deployments}
        processes = []
        app_ports: dict[str, tuple[int, str]] = {}
        for deployment in deployments:
            ports = SidecarPorts.allocate(deployment.expose.get("http"))
            env = {
                **sidecar_env(),
                **deployment.env,
                # Loopback only: other machines must not reach the apps around Dapr.
                "APP_HOST": "127.0.0.1",
                "APP_PORT": str(ports.app),
                "APP_RELOAD": str(reloads(deployment, args.no_reload)).lower(),
                "APP_RELOAD_DIRS": json.dumps(source_dirs(deployment.member)),
                **infra.app_env,
            }
            command = dapr_run_command(
                app_id=deployment.app_id,
                ports=ports,
                app_protocol=deployment.app_protocol,
                resources_dir=native.resources_dir,
                config_file=config_files[deployment.name],
                command=[str(bin_dir / deployment.command)],
            )
            processes.append(start_process(record, f"dev:{deployment.name}", command, env=env))
            app_ports[deployment.name] = (ports.app, deployment.app_protocol)

        def all_ready() -> bool:
            if any(p.poll() is not None for p in processes):
                fail("an app or its sidecar exited during startup; see the output above")
            return all(ready(port, protocol) for port, protocol in app_ports.values())

        wait_until(all_ready, timeout=120, what="the apps to be ready")

        log(f"ready: {', '.join(app_ports)}. Press Ctrl+C to stop.")

        while all(process.poll() is None for process in processes):
            time.sleep(0.5)
        log("an app or its sidecar exited; stopping the session")
        exit_code = 1
    except KeyboardInterrupt:
        log("stopping")
        exit_code = 0
    finally:
        shield_cleanup()
        cleaned = teardown(record)
    if not cleaned:
        fail(f"cleanup incomplete; retry with: uv run poe teardown --run-id {record.run_id}")
    sys.exit(exit_code)


if __name__ == "__main__":
    entrypoint(main)
