"""Images built with Docker Bake (docker-bake.hcl is the only place defining targets)."""

import json
import re
from pathlib import Path

from scripts.framework.repo import ROOT, versions
from scripts.framework.shell import log, require_tools, run


def bake_variables(tag: str, prefix: str | None = None) -> dict[str, str]:
    uv_version = re.search(r"uv (\S+)", run(["uv", "--version"], capture=True, quiet=True).stdout)
    assert uv_version, "cannot determine the uv version"
    variables = {
        "PYTHON_VERSION": (ROOT / ".python-version").read_text().strip(),
        "UV_VERSION": uv_version.group(1),
        "BUF_VERSION": versions()["buf"]["version"],
        "TAG": tag,
    }
    if prefix:
        variables["IMAGE_PREFIX"] = prefix
    return variables


def build(
    targets: list[str],
    *,
    tag: str,
    load: bool,
    output_dir: Path,
    prefix: str | None = None,
    platforms: str | None = None,
    push: bool = False,
) -> dict[str, str]:
    """Build `targets` and return {bake target: image reference}.

    `platforms` builds every target, including the shared wheelhouse, for each
    listed platform; `push` publishes the images under `prefix`.
    """
    require_tools("docker")
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata_file = output_dir / "bake-metadata.json"
    command = ["docker", "buildx", "bake", "--file", "docker-bake.hcl"]
    command += ["--metadata-file", str(metadata_file)]
    if platforms:
        command += ["--set", f"*.platform={platforms}"]
    if push:
        command.append("--push")
    elif load:
        command.append("--load")
    run([*command, *targets], env=bake_variables(tag, prefix))
    metadata = json.loads(metadata_file.read_text())
    images = {
        target: data["image.name"].split(",")[0]
        for target, data in metadata.items()
        if isinstance(data, dict) and "image.name" in data
    }
    (output_dir / "images.json").write_text(json.dumps(images, indent=2))
    for target, image in images.items():
        log(f"{target}: {image}")
    return images


def split_image(reference: str) -> tuple[str, str]:
    """`repo/name:tag` -> (`repo/name`, `tag`)."""
    repository, _, tag = reference.rpartition(":")
    return repository, tag


def remove_image(reference: str) -> None:
    result = run(["docker", "image", "rm", reference], capture=True, check=False)
    if result.returncode != 0 and "No such image" not in result.stderr:
        raise RuntimeError(result.stderr.strip())
