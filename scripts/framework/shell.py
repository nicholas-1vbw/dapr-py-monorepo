"""Running external commands: logging, failures, tools, ports, waiting."""

import os
import random
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from typing import Any

from scripts.framework import repo
from scripts.framework.repo import ROOT, versions


def log(message: str) -> None:
    print(f"==> {message}", flush=True)


def fail(message: str) -> None:
    raise SystemExit(f"error: {message}")


def run(
    command: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    capture: bool = False,
    check: bool = True,
    quiet: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a command given as an argument list (never through a shell)."""
    if not quiet:
        log(" ".join(command))
    full_env = {**os.environ, **(env or {})}
    if command[0] == "helm":
        full_env.update(helm_env())
    return subprocess.run(
        list(command),
        cwd=ROOT,
        env=full_env,
        check=check,
        text=True,
        capture_output=capture,
    )


def helm_env() -> dict[str, str]:
    """Isolate Helm repository settings from the user's global configuration,
    so charts referenced with `--repo` resolve the same on every machine."""
    helm_dir = repo.RUNS_DIR / "helm"
    return {
        "HELM_REPOSITORY_CONFIG": str(helm_dir / "repositories.yaml"),
        "HELM_REPOSITORY_CACHE": str(helm_dir / "cache"),
    }


TOOL_HINTS = {
    "buf": "https://buf.build/docs/cli/installation/",
    "dapr": "https://docs.dapr.io/getting-started/install-dapr-cli/",
    "docker": "https://docs.docker.com/get-docker/",
    "helm": "https://helm.sh/docs/intro/install/",
    "kind": "https://kind.sigs.k8s.io/docs/user/quick-start/#installation",
    "kubectl": "https://kubernetes.io/docs/tasks/tools/",
}


def require_tools(*names: str) -> None:
    missing = [name for name in names if shutil.which(name) is None]
    if missing:
        hints = "\n".join(f"  {name}: {TOOL_HINTS.get(name, '')}" for name in missing)
        fail(f"required tools are not installed: {', '.join(missing)}\n{hints}")


def require_dapr_runtime() -> None:
    """The native sidecar runtime must match the pinned Dapr version."""
    require_tools("dapr")
    expected = versions()["dapr"]["runtimeVersion"]
    output = run(["dapr", "--version"], capture=True, quiet=True).stdout
    match = re.search(r"Runtime version:\s*(\S+)", output)
    installed = match.group(1) if match else "n/a"
    if installed != expected:
        fail(
            f"Dapr runtime {expected} is required, found {installed}. "
            f"Install it with: dapr init --slim --runtime-version {expected}"
        )


# Ports are picked below the operating systems' ephemeral ranges (Linux 32768+,
# macOS 49152+): an ephemeral port that is free now can be taken by any outgoing
# connection before the process that should listen on it starts.
_PORT_RANGE = range(20000, 32000)


_handed_out: set[int] = set()


_port_lock = threading.Lock()


def free_port() -> int:
    """A currently free TCP port on 127.0.0.1, never returned twice by this process."""
    with _port_lock:
        for _ in range(200):
            port = random.choice(_PORT_RANGE)
            if port in _handed_out:
                continue
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                try:
                    sock.bind(("127.0.0.1", port))
                except OSError:
                    continue
            _handed_out.add(port)
            return port
    fail("no free TCP port found")
    raise AssertionError  # unreachable


def wait_until(check: Any, *, timeout: float, interval: float = 0.5, what: str) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            if check():
                return
        except SystemExit:
            raise
        except Exception:
            pass
        if time.monotonic() >= deadline:
            fail(f"timed out after {timeout:.0f}s waiting for {what}")
        time.sleep(interval)


def python_executable() -> str:
    """The prepared workspace interpreter; workers use it without syncing."""
    return sys.executable
