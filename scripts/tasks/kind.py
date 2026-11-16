"""Local Kubernetes environment on kind: a standard Dapr cluster with the system.

    uv run poe kind up   [--name NAME]
    uv run poe kind down [--name NAME]

`up` builds the images, creates the cluster (or reuses it), installs the Dapr
control plane, the infrastructure instances enabled for `kind` in
deploy/environment/infra.yaml, the Dapr resources and every app in
deploy/apps. Ports declared under `expose` are reachable on the same host ports
as in `poe dev`. Running it again upgrades the releases.
"""

import argparse
import json

from scripts.framework.kubernetes import forget_cluster, provision
from scripts.framework.repo import project_name
from scripts.framework.runs import RunRecord, entrypoint
from scripts.framework.shell import fail, log, require_tools, run

DEFAULT_CLUSTER = project_name()


def main() -> None:
    parser = argparse.ArgumentParser(prog="poe kind", description=(__doc__ or "").splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    up = sub.add_parser("up", help="create or update the environment")
    up.add_argument("--name", default=DEFAULT_CLUSTER)
    down = sub.add_parser("down", help="delete the cluster")
    down.add_argument("--name", default=DEFAULT_CLUSTER)
    args = parser.parse_args()

    if args.command == "down":
        require_tools("kind")
        run(["kind", "delete", "cluster", "--name", args.name])
        if not forget_cluster(args.name):
            fail("the cluster was deleted, but some images could not be removed (see above)")
        return

    record = RunRecord.create("kind")
    record.add("cluster-name", name=args.name)
    environment = provision(record, name=args.name, kubeconfig=None)
    (record.directory / "endpoints.json").write_text(json.dumps(environment.endpoints, indent=2))
    remove = "uv run poe kind down" + (
        "" if args.name == DEFAULT_CLUSTER else f" --name {args.name}"
    )
    log(f"ready (kubectl context {environment.target.context}). Remove with: {remove}")


if __name__ == "__main__":
    entrypoint(main)
