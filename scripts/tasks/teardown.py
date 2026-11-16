"""Remove what a run created, from its record.

    uv run poe teardown --run-id ID

Tasks clean up after themselves, including on Ctrl+C, SIGTERM and SIGHUP. Use
this when a run was killed with SIGKILL or the machine stopped.
"""

import argparse

from scripts.framework.cleanup import teardown
from scripts.framework.runs import RunRecord, entrypoint
from scripts.framework.shell import fail


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="poe teardown", description=(__doc__ or "").splitlines()[0]
    )
    parser.add_argument("--run-id", required=True, help="id printed by the task (see .run/)")
    args = parser.parse_args()
    if not teardown(RunRecord.load(args.run_id)):
        fail("some resources could not be removed; see messages above")


if __name__ == "__main__":
    entrypoint(main)
