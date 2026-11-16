"""End-to-end tests on a fresh kind cluster.

    uv run poe e2e [--keep]

Builds the images, provisions a dedicated kind cluster (same steps as
`poe kind up`, with its own name and free host ports), runs the BDD tests in
tests/e2e against it, saves diagnostics and deletes the cluster.
"""

import argparse
import json
import sys

from scripts.framework.cleanup import teardown
from scripts.framework.description import load_deployments
from scripts.framework.kubernetes import KubeTarget, collect_diagnostics, provision
from scripts.framework.repo import dns_label
from scripts.framework.runs import RunRecord, entrypoint, run_suffix, shield_cleanup
from scripts.framework.shell import log, python_executable, run


def main() -> None:
    parser = argparse.ArgumentParser(prog="poe e2e", description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--keep", action="store_true", help="keep the cluster for inspection after the tests"
    )
    args = parser.parse_args()

    record = RunRecord.create("e2e")
    name = dns_label("e2e", run_suffix(record.run_id))
    kubeconfig = record.directory / "kubeconfig"
    target = KubeTarget(kubeconfig=kubeconfig, context=f"kind-{name}")
    exit_code = 1
    try:
        environment = provision(record, name=name, kubeconfig=kubeconfig, free_host_ports=True)
        result = run(
            [
                python_executable(), "-m", "pytest", "tests/e2e", "-p", "no:cacheprovider",
                "--junitxml", str(record.directory / "reports" / "junit.xml"),
            ],
            env={"E2E_ENDPOINTS": json.dumps(environment.endpoints)},
            check=False,
        )  # fmt: skip
        exit_code = result.returncode
    except KeyboardInterrupt:
        log("interrupted")
    finally:
        shield_cleanup()
        # Also after a failed deployment: that is when the logs matter most.
        if kubeconfig.exists():
            collect_diagnostics(record, target, load_deployments())
        if args.keep:
            log(f"cluster kept; remove it with: uv run poe teardown --run-id {record.run_id}")
        elif not teardown(record):
            exit_code = exit_code or 1
    sys.exit(exit_code)


if __name__ == "__main__":
    entrypoint(main)
