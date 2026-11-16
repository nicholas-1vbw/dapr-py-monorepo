"""Structure of the framework (scripts/).

- It stays independent of the system it deploys: it reads the environment
  description (deploy/environment, deploy/apps) as data. A script that imports
  system code, or contains a name or port number the description defines, would
  make replacing the example system a change of the framework.
- Tasks (scripts/tasks) are entry points built on the framework modules
  (scripts/framework); they do not import each other, and framework modules
  depend on lower layers only.
"""

import ast
import sys
from pathlib import Path

import pytest

from scripts.framework.repo import ROOT, load_yaml

SCRIPTS = sorted(
    p for p in (ROOT / "scripts").rglob("*.py") if "tests" not in p.relative_to(ROOT).parts
)

# Framework modules by layer; a module may import modules of lower layers only.
FRAMEWORK_LAYERS = {
    "repo": 0,
    "shell": 1,
    "runs": 2,
    "description": 3,
    "images": 3,
    "docker": 4,
    "native": 4,
    "kubernetes": 4,
    "cleanup": 5,
}
ALLOWED_THIRD_PARTY = {"yaml", "packaging"}

# Words and numbers the framework uses for its own purposes (command arguments,
# file names, record kinds, port ranges). A system may use the same words as
# names, so they never count as references to the system. A new framework
# constant that equals a name of the system is reported; add it here only if it
# is the framework's own word.
FRAMEWORK_VOCABULARY: frozenset[str | int] = frozenset(
    {
        "10m",
        "1s",
        "2m",
        "3s",
        "5m",
        "app",
        "apps",
        "args",
        "bake",
        "buf",
        "buildx",
        "cache",
        "charts",
        "cluster",
        "cluster-name",
        "clusters",
        "command",
        "components",
        "compose",
        "config",
        "configurations",
        "content",
        "control-plane",
        "create",
        "dapr",
        "dapr-resources",
        "dapr-system",
        "daprd",
        "default",
        "delete",
        "dependencies",
        "deploy",
        "dev",
        "diagnostics",
        "docker",
        "docker-image",
        "down",
        "e2e",
        "enabled",
        "env",
        "environment",
        "environments",
        "errors",
        "events",
        "expose",
        "failures",
        "false",
        "file",
        "files",
        "get",
        "healthcheck",
        "helm",
        "host",
        "http",
        "image",
        "images",
        "infra",
        "inspect",
        "instances",
        "integration",
        "interrupted",
        "interval",
        "it",
        "kind",
        "kind-cluster",
        "kubeconfig",
        "kubectl",
        "lint",
        "load",
        "logs",
        "member",
        "members",
        "metadata",
        "name",
        "nodes",
        "none",
        "owned",
        "packages",
        "passed",
        "path",
        "pid",
        "platform",
        "pods",
        "port",
        "ports",
        "process",
        "project",
        "ps",
        "pytest",
        "r",
        "ready",
        "reference",
        "reports",
        "repository",
        "resource",
        "resources",
        "retries",
        "rm",
        "role",
        "run",
        "scopes",
        "service",
        "services",
        "spec",
        "src",
        "started",
        "stopping",
        "tag",
        "targets",
        "teardown",
        "template",
        "test",
        "tests",
        "testsuite",
        "timeout",
        "tool",
        "true",
        "type",
        "up",
        "upgrade",
        "uv",
        "value",
        "values",
        "version",
        "volumes",
        "wb",
        "wide",
        "workspace",
        0,
        1,
        2,
        4,
        63,
        120,
        200,
        20000,
        30000,
        32000,
        32767,
    }
)


def _constants(path: Path) -> set[str]:
    """String and integer constants of a script, except the framework's own words."""
    values = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if not isinstance(node, ast.Constant):
            continue
        if isinstance(node.value, (str, int)) and not isinstance(node.value, bool):
            values.add(str(node.value))
    return values - {str(word) for word in FRAMEWORK_VOCABULARY}


def _described_values() -> set[str]:
    """Names and ports defined by the environment description."""
    values: set[str] = set()
    for path in sorted((ROOT / "deploy" / "apps").glob("*.yaml")):
        data = load_yaml(path) or {}
        values |= {path.stem, str(data.get("appId")), str(data.get("member"))}
        values |= {str(data.get("bakeTarget")), str(data.get("command"))}
        values |= {str(p) for p in (data.get("expose") or {}).values()}
    infra = load_yaml(ROOT / "deploy" / "environment" / "infra.yaml") or {}
    for name, spec in infra.items():
        values.add(name)
        values |= {str(p) for p in (spec.get("ports") or {}).values()}
        values |= {str(p) for p in (spec.get("expose") or {}).values()}
        if spec.get("resource"):
            values.add(str(spec["resource"]["name"]))
        values.add(str(spec.get("image")))
    components = load_yaml(ROOT / "deploy" / "environment" / "components.yaml") or {}
    for name, spec in (components.get("components") or {}).items():
        values.add(name)
        values |= {str(s) for s in spec.get("scopes") or []}
    return values - {"None", ""}


def _name(path: Path) -> str:
    return path.relative_to(ROOT / "scripts").as_posix()


@pytest.mark.parametrize("path", SCRIPTS, ids=_name)
def test_scripts_contain_no_names_of_the_system(path: Path) -> None:
    leaked = sorted(_constants(path) & _described_values())
    assert not leaked, f"{path.name} refers to the deployed system: {leaked}"


@pytest.mark.parametrize("path", SCRIPTS, ids=_name)
def test_scripts_import_only_the_framework(path: Path) -> None:
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules = [node.module]
        else:
            continue
        for module in modules:
            top = module.split(".")[0]
            allowed = top in sys.stdlib_module_names or top in ALLOWED_THIRD_PARTY
            assert allowed or top == "scripts", f"{path.name} imports {module}"


def _framework_imports(path: Path) -> set[str]:
    imported = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "scripts.framework":
                imported |= {alias.name for alias in node.names}
            elif node.module.startswith("scripts.framework."):
                imported.add(node.module.split(".")[2])
            elif node.module.startswith("scripts.tasks"):
                imported.add("tasks")
    return imported


def test_every_framework_module_has_a_layer() -> None:
    modules = {p.stem for p in (ROOT / "scripts" / "framework").glob("*.py")} - {"__init__"}
    assert modules == set(FRAMEWORK_LAYERS)


@pytest.mark.parametrize(
    "path", sorted((ROOT / "scripts" / "framework").glob("[!_]*.py")), ids=_name
)
def test_framework_modules_depend_on_lower_layers(path: Path) -> None:
    layer = FRAMEWORK_LAYERS[path.stem]
    for module in _framework_imports(path):
        assert module != "tasks", f"{_name(path)} imports a task"
        assert FRAMEWORK_LAYERS[module] < layer, f"{_name(path)} imports {module}"


@pytest.mark.parametrize("path", sorted((ROOT / "scripts" / "tasks").glob("[!_]*.py")), ids=_name)
def test_tasks_do_not_import_each_other(path: Path) -> None:
    assert "tasks" not in _framework_imports(path), f"{_name(path)} imports another task"
