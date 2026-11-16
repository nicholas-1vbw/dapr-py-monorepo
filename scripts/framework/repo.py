"""Repository layout: paths, the project name, workspace members, tool versions."""

import re
import tomllib
from pathlib import Path
from typing import Any

import yaml
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[2]


RUNS_DIR = ROOT / ".run"


PLATFORM_DIR = ROOT / "deploy" / "platform"


def display_path(path: Path) -> str:
    """Path relative to the repository when possible, for messages."""
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def load_yaml(path: Path) -> Any:
    with path.open() as file:
        return yaml.safe_load(file)


def versions() -> dict[str, Any]:
    """deploy/platform/versions.yaml: versions of the platform tools."""
    return load_yaml(PLATFORM_DIR / "versions.yaml")


def dns_label(*parts: str, max_length: int = 63) -> str:
    """Join parts into a lowercase DNS label, usable as a Dapr app id or name."""
    label = re.sub(r"[^a-z0-9-]+", "-", "-".join(parts).lower()).strip("-")
    label = re.sub(r"-{2,}", "-", label)
    return label[:max_length].rstrip("-")


def project_name() -> str:
    """Name of the repository's project (root pyproject.toml), as a DNS label."""
    root = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return dns_label(root["project"]["name"])


def workspace_members() -> dict[str, Path]:
    """Normalized distribution name -> directory, from the uv workspace."""
    root = tomllib.loads((ROOT / "pyproject.toml").read_text())
    members: dict[str, Path] = {}
    for pattern in root["tool"]["uv"]["workspace"]["members"]:
        for directory in sorted(ROOT.glob(pattern)):
            manifest = directory / "pyproject.toml"
            if manifest.is_file():
                name = tomllib.loads(manifest.read_text())["project"]["name"]
                members[canonicalize_name(name)] = directory
    return members
