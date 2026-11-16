"""The environment description and how it becomes Docker and Kubernetes inputs,
with a synthetic infra.yaml."""

from pathlib import Path

import pytest
import yaml

from scripts.framework import description as environment
from scripts.framework.description import (
    Deployment,
    Exposure,
    addresses,
    exposures,
    fill,
    load_instances,
)
from scripts.framework.docker import compose_model
from scripts.framework.kubernetes import chart_values

INFRA = {
    "kv": {
        "image": "kv:1",
        "ports": {"main": 7000},
        "ready": {"command": ["kv-cli", "ping"]},
        "environments": ["dev", "integration", "kind"],
        "resource": {
            "name": "kv",
            "value": {"kind": "redis", "host": "{host}", "port": "{port:main}"},
        },
    },
    "traces": {
        "image": "traces:1",
        "args": ["--memory"],
        "env": {"MODE": "local"},
        "ports": {"otlp": 4317, "ui": 9999},
        "files": {"traces.conf": "/etc/traces.conf"},
        "environments": ["dev", "kind"],
        "expose": {"ui": 19999},
        "resource": {
            "name": "tracing",
            "value": {"kind": "otlp", "endpoint": "{host}:{port:otlp}"},
        },
        "appEnv": {"COLLECTOR": "http://{host}:{port:otlp}"},
    },
}


@pytest.fixture
def infra(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    files = tmp_path / "files"
    files.mkdir()
    (files / "traces.conf").write_text("level: info\n")
    path = tmp_path / "infra.yaml"
    path.write_text(yaml.safe_dump(INFRA))
    monkeypatch.setattr(environment, "INFRA_FILE", path)
    monkeypatch.setattr(environment, "FILES_DIR", files)
    monkeypatch.setattr(environment, "ROOT", tmp_path)
    import scripts.framework.docker
    import scripts.framework.kubernetes

    monkeypatch.setattr(scripts.framework.docker, "FILES_DIR", files)
    monkeypatch.setattr(scripts.framework.kubernetes, "FILES_DIR", files)
    return path


def test_instances_per_environment(infra: Path) -> None:
    assert [i.name for i in load_instances()] == ["kv", "traces"]
    assert [i.name for i in load_instances("integration")] == ["kv"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"kv": {"image": "x", "ports": {"main": 1}, "colour": "red"}}, "unknown keys: colour"),
        ({"kv": {"ports": {"main": 1}}}, "`image` is required"),
        ({"Bad_Name": {"image": "x", "ports": {"main": 1}}}, "lowercase DNS label"),
        ({"kv": {"image": "x", "ports": {"main": 1}, "expose": {"other": 1}}}, "`expose` refers"),
        ({"kv": {"image": "x", "ports": {"main": 1}, "environments": ["prod"]}}, "`environments`"),
        ({"kv": {"image": "x", "ports": {"main": 1}, "files": {"none": "/x"}}}, "does not exist"),
    ],
)
def test_invalid_descriptions_are_reported(infra: Path, change: dict, message: str) -> None:
    infra.write_text(yaml.safe_dump(change))
    with pytest.raises(SystemExit, match=message):
        load_instances()


def test_fill_keeps_port_numbers_as_integers() -> None:
    template = {"host": "{host}", "port": "{port:main}", "url": "http://{host}:{port:main}/x"}
    assert fill(template, host="h", ports={"main": 1}) == {
        "host": "h",
        "port": 1,
        "url": "http://h:1/x",
    }


def test_fill_rejects_unknown_ports() -> None:
    with pytest.raises(SystemExit, match="unknown port 'nope'"):
        fill("{port:nope}", host="h", ports={})


def test_addresses_for_one_target(infra: Path) -> None:
    instances = load_instances("dev")
    found = addresses(
        instances,
        {"kv": ("127.0.0.1", {"main": 41000}), "traces": ("127.0.0.1", {"otlp": 42000, "ui": 1})},
    )
    assert found.resources == {
        "kv": {"kind": "redis", "host": "127.0.0.1", "port": 41000},
        "tracing": {"kind": "otlp", "endpoint": "127.0.0.1:42000"},
    }
    assert found.app_env == {"COLLECTOR": "http://127.0.0.1:42000"}


def _deployment(name: str, expose: dict[str, int]) -> Deployment:
    return Deployment(
        name=name, path=Path(name), app_id=name, member=name, bake_target=name, command=name,
        app_port=8080, app_protocol="http", dapr_config="", expose=expose,
    )  # fmt: skip


def test_exposures_get_stable_node_ports(infra: Path) -> None:
    found = exposures([_deployment("app-a", {"http": 9000})], load_instances())
    assert found == [
        Exposure("app-a.http", 9000, 30000),
        Exposure("traces.ui", 19999, 30001),
    ]


def test_compose_model(infra: Path) -> None:
    model = compose_model(load_instances("dev"), expose=True)
    traces = model["services"]["traces"]
    assert traces["command"] == ["--memory"]
    assert traces["environment"] == {"MODE": "local"}
    assert traces["ports"] == ["127.0.0.1::4317", "127.0.0.1:19999:9999"]
    assert traces["volumes"][0].endswith("traces.conf:/etc/traces.conf:ro")
    assert model["services"]["kv"]["healthcheck"]["test"] == ["CMD", "kv-cli", "ping"]
    assert compose_model(load_instances("dev"), expose=False)["services"]["traces"]["ports"] == [
        "127.0.0.1::4317",
        "127.0.0.1::9999",
    ]


def test_chart_values(infra: Path) -> None:
    instances = load_instances("kind")
    values = chart_values(instances, exposures([], instances))["instances"]
    assert values["traces"]["nodePorts"] == {"ui": 30000}
    assert values["traces"]["files"] == {
        "traces.conf": {"path": "/etc/traces.conf", "content": "level: info\n"}
    }
    assert values["kv"]["ready"] == {"command": ["kv-cli", "ping"]}
    assert "nodePorts" not in values["kv"]


def test_dev_watches_sources_of_dependencies() -> None:
    from scripts.framework.repo import workspace_members
    from scripts.tasks.dev import source_dirs

    for name, directory in workspace_members().items():
        assert str(directory / "src") in source_dirs(name.upper().replace("-", "_"))
