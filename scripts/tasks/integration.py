"""Integration tests: one Dapr sidecar per workspace member, shared infrastructure.

    uv run poe integration [--workers N] [--packages NAME ...]

For each selected member with tests under tests/integration/<member>/ one task
runs `dapr run ... -- python -m pytest tests/integration/<member>`. All tasks
of a run share one set of backing services and one read-only resources
directory; each task has its own sidecar, app id, ports and pytest process.

A task's sidecar talks to its app over the `appProtocol` that deploy/apps
declares for the member's app (http for members that provide no app), so the
tests of a gRPC app serve it over gRPC.
"""

import argparse
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from packaging.utils import canonicalize_name

from scripts.framework.cleanup import teardown
from scripts.framework.description import Deployment, load_deployments
from scripts.framework.docker import start_docker
from scripts.framework.native import (
    NativeResources,
    SidecarPorts,
    dapr_run_command,
    render_native,
    sidecar_env,
)
from scripts.framework.repo import ROOT, dns_label, workspace_members
from scripts.framework.runs import (
    RunRecord,
    entrypoint,
    run_suffix,
    shield_cleanup,
    start_process,
    stop_process_group,
    stop_process_groups,
)
from scripts.framework.shell import (
    fail,
    log,
    python_executable,
    require_dapr_runtime,
    require_tools,
)

TESTS_DIR = ROOT / "tests" / "integration"


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def select_packages(
    requested: list[str] | None,
    *,
    members: Iterable[str] | None = None,
    tests_dir: Path = TESTS_DIR,
) -> list[str]:
    """Members with integration tests; unknown or test-less names are errors."""
    members = set(workspace_members() if members is None else members)
    with_tests = sorted(name for name in members if (tests_dir / name).is_dir())
    if not requested:
        return with_tests
    requested = [canonicalize_name(name) for name in requested]
    unknown = sorted(set(requested) - set(members))
    if unknown:
        fail(f"not workspace members: {', '.join(unknown)}")
    without = sorted(set(requested) - set(with_tests))
    if without:
        fail(f"no integration tests in tests/integration/ for: {', '.join(without)}")
    return sorted(set(requested))


def app_protocol(member: str, deployments: Iterable[Deployment]) -> str:
    """The protocol of the app that `member` provides, or http when it provides
    none. A member whose apps declare different protocols is an error."""
    protocols = {d.app_protocol for d in deployments if canonicalize_name(d.member) == member}
    if len(protocols) > 1:
        fail(f"apps of {member} declare different appProtocol values: {sorted(protocols)}")
    return protocols.pop() if protocols else "http"


def junit_failures(report: Path) -> int:
    """1 when pytest's report is missing or records failures, else 0.

    The task's status comes from `dapr run`; the report confirms it, so a
    sidecar that exits 0 can never hide failed or uncollected tests.
    """
    if not report.is_file():
        return 1
    root = ElementTree.parse(report).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    bad = sum(int(s.get("failures", 0)) + int(s.get("errors", 0)) for s in suites)
    ran = sum(int(s.get("tests", 0)) for s in suites)
    return 1 if bad or ran == 0 else 0


@dataclass
class TaskResult:
    package: str
    exit_code: int
    seconds: float
    log_file: Path


class Runner:
    def __init__(
        self,
        record: RunRecord,
        resources: NativeResources,
        app_env: dict[str, str],
        deployments: list[Deployment],
    ) -> None:
        self.record = record
        self.deployments = deployments
        self.resources = resources
        self.app_env = app_env
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.running: dict[str, subprocess.Popen[bytes]] = {}

    def run_task(self, package: str) -> TaskResult:
        task_dir = self.record.directory / "packages" / package
        log_file = task_dir / "logs" / "task.log"
        if self.cancelled.is_set():
            return TaskResult(package, -1, 0.0, log_file)
        app_id = dns_label("it", run_suffix(self.record.run_id), package)
        ports = SidecarPorts.allocate()
        pytest = [
            python_executable(), "-m", "pytest", str(TESTS_DIR / package),
            "--rootdir", str(ROOT), "-p", "no:cacheprovider",
            "--junitxml", str(task_dir / "reports" / "junit.xml"),
        ]  # fmt: skip
        command = dapr_run_command(
            app_id=app_id,
            ports=ports,
            app_protocol=app_protocol(package, self.deployments),
            resources_dir=self.resources.resources_dir,
            config_file=self.resources.config_file(""),
            command=pytest,
        )
        env = {
            **sidecar_env(),
            **self.app_env,
            "IT_RUN_ID": self.record.run_id,
            "IT_APP_ID": app_id,
            "IT_APP_PORT": str(ports.app),
        }
        started = time.monotonic()
        with self.lock:
            # Checked again under the lock: cancel() may have run since the first
            # check, and a task started now would not be stopped by it.
            if self.cancelled.is_set():
                return TaskResult(package, -1, 0.0, log_file)
            process = start_process(
                self.record, f"it:{package}", command, env=env, log_file=log_file
            )
            self.running[package] = process
        log(f"[{package}] started (app id {app_id}), log: {log_file.relative_to(ROOT)}")
        exit_code = process.wait()
        # `dapr run` has exited; stop anything left in its group (such as daprd)
        # before the task is forgotten.
        stop_process_group(process.pid)
        with self.lock:
            self.running.pop(package, None)
            self.record.remove("process", pid=process.pid)
        if self.cancelled.is_set():
            log(f"[{package}] stopped")
            return TaskResult(package, -1, time.monotonic() - started, log_file)
        exit_code = exit_code or junit_failures(task_dir / "reports" / "junit.xml")
        log(f"[{package}] {'passed' if exit_code == 0 else f'FAILED ({exit_code})'}")
        return TaskResult(package, exit_code, time.monotonic() - started, log_file)

    def cancel(self) -> None:
        self.cancelled.set()
        with self.lock:
            pids = [process.pid for process in self.running.values()]
        stop_process_groups([(pid, None) for pid in pids])


def run_tasks(runner: Runner, packages: list[str], workers: int) -> list[TaskResult]:
    """Run package tasks with at most `workers` at a time.

    On Ctrl+C the running tasks are stopped right away and pending ones are
    not started; tasks run in their own process groups and do not receive the
    interrupt themselves.
    """
    pool = ThreadPoolExecutor(max_workers=workers)
    futures = [pool.submit(runner.run_task, package) for package in packages]
    try:
        return [future.result() for future in futures]
    except KeyboardInterrupt:
        log("interrupted: stopping running tasks")
        runner.cancel()
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="poe integration", description=(__doc__ or "").splitlines()[0]
    )
    parser.add_argument(
        "--workers", type=positive_int, default=1,
        help="number of package tasks running at the same time (default: 1)",
    )  # fmt: skip
    parser.add_argument("--packages", nargs="+", metavar="NAME", help="workspace members to test")
    args = parser.parse_args()

    packages = select_packages(args.packages)
    if not packages:
        fail("no integration tests found")
    require_tools("docker", "helm")
    require_dapr_runtime()

    record = RunRecord.create("it")
    results: list[TaskResult] = []
    try:
        infra = start_docker(record, "integration", expose=False)
        resources = render_native(record, infra.resources_values, omit_scopes=True)
        runner = Runner(record, resources, infra.app_env, load_deployments())
        log(f"running {len(packages)} package task(s) with {args.workers} worker(s)")
        results = run_tasks(runner, packages, args.workers)
    except KeyboardInterrupt:
        log("interrupted")
    finally:
        shield_cleanup()
        cleaned = teardown(record)

    print()
    for result in results:
        status = "passed" if result.exit_code == 0 else f"FAILED (exit {result.exit_code})"
        log_path = result.log_file.relative_to(ROOT)
        print(f"  {result.package:<20} {status:<20} {result.seconds:6.1f}s  {log_path}")
    failed = [r for r in results if r.exit_code != 0]
    if len(results) < len(packages) or failed:
        sys.exit(1)
    if not cleaned:
        fail(
            "tests passed but cleanup failed; retry with: "
            f"uv run poe teardown --run-id {record.run_id}"
        )


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    entrypoint(main)
