"""Teardown of a run: removes what its record lists, newest first."""

import json

from scripts.framework.docker import compose_down
from scripts.framework.images import remove_image
from scripts.framework.kubernetes import delete_cluster
from scripts.framework.runs import RunRecord, stop_process_group
from scripts.framework.shell import log


def teardown(record: RunRecord) -> bool:
    """Remove what the run owns, newest first. Safe to call repeatedly."""
    ok = True
    for item in reversed(list(record.owned)):
        kind = item["kind"]
        try:
            if kind == "process":
                stop_process_group(item["pid"], started=item.get("started"))
            elif kind == "compose":
                compose_down(item["project"], item.get("file"))
            elif kind == "cluster-name":
                continue  # a label of `kind up` runs, not a resource: keep it
            elif kind == "image":
                remove_image(item["reference"])
            elif kind == "kind-cluster":
                delete_cluster(item["name"], item.get("kubeconfig"))
            else:
                log(f"unknown resource kind in record: {kind}")
                continue
        except Exception as error:  # report and keep cleaning up
            ok = False
            log(f"failed to remove {json.dumps(item)}: {error}")
            continue
        record.discard(item)
    if ok:
        log(f"run {record.run_id}: all owned resources removed")
    return ok
