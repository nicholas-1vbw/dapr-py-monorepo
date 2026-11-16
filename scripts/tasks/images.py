"""Build images with Docker Bake.

    uv run poe images [TARGET ...] [--tag TAG] [--no-load]
    uv run poe images --prefix REGISTRY/PATH --tag TAG --platform linux/amd64,linux/arm64 --push

Targets are defined in docker-bake.hcl (default group: all app images). The
Python, uv and Buf versions come from .python-version, the running uv and
deploy/platform/versions.yaml.
"""

import argparse

from scripts.framework.images import build
from scripts.framework.repo import ROOT
from scripts.framework.runs import entrypoint
from scripts.framework.shell import fail


def main() -> None:
    parser = argparse.ArgumentParser(prog="poe images", description=(__doc__ or "").splitlines()[0])
    parser.add_argument("targets", nargs="*", metavar="TARGET", help="Bake targets (default group)")
    parser.add_argument("--tag", default="dev", help="image tag (default: dev)")
    parser.add_argument(
        "--no-load", action="store_true", help="keep images in the build cache only"
    )
    parser.add_argument("--prefix", help="image name prefix, e.g. registry.example.com/team")
    parser.add_argument("--platform", help="platforms, e.g. linux/amd64,linux/arm64")
    parser.add_argument("--push", action="store_true", help="push the images instead of loading")
    args = parser.parse_args()
    if args.push and not args.prefix:
        fail("--push needs --prefix: the registry and path to push to")
    build(
        args.targets,
        tag=args.tag,
        load=not args.no_load,
        output_dir=ROOT / ".run" / "images",
        prefix=args.prefix,
        platforms=args.platform,
        push=args.push,
    )


if __name__ == "__main__":
    entrypoint(main)
