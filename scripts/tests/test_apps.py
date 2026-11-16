from pathlib import Path

import pytest
import yaml

from scripts.framework import description
from scripts.framework.description import load_deployments


@pytest.fixture
def apps_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(description, "APPS_DIR", tmp_path)
    for name, expose in (("app-a", {"http": 9000}), ("app-b", None)):
        data = {
            "appId": name,
            "member": name,
            "bakeTarget": name,
            "command": name,
            "appPort": 8080,
            "env": {"APP_X": 1},
        }
        if expose:
            data["expose"] = expose
        (tmp_path / f"{name}.yaml").write_text(yaml.safe_dump(data))
    return tmp_path


def test_loads_all_deployments(apps_dir: Path) -> None:
    loaded = {d.name: d for d in load_deployments()}
    assert set(loaded) == {"app-a", "app-b"}
    assert loaded["app-a"].expose == {"http": 9000}
    assert loaded["app-b"].expose == {}
    assert loaded["app-a"].env == {"APP_X": "1"}
    assert loaded["app-a"].app_protocol == "http"


def test_selected_deployment(apps_dir: Path) -> None:
    assert [d.name for d in load_deployments(["app-b"])] == ["app-b"]


def test_unknown_deployment_is_rejected(apps_dir: Path) -> None:
    with pytest.raises(SystemExit, match="unknown deployments"):
        load_deployments(["nope"])


def _deployment(member: str, protocol: str, name: str | None = None) -> description.Deployment:
    name = name or member
    return description.Deployment(
        name=name, path=Path(name), app_id=name, member=member, bake_target=name, command=name,
        app_port=8080, app_protocol=protocol, dapr_config="",
    )  # fmt: skip


def test_integration_sidecar_uses_the_protocol_of_the_members_app() -> None:
    from scripts.tasks.integration import app_protocol

    deployments = [_deployment("web", "http"), _deployment("rpc-app", "grpc")]
    assert app_protocol("rpc-app", deployments) == "grpc"
    assert app_protocol("web", deployments) == "http"
    assert app_protocol("library", deployments) == "http"
    mixed = [*deployments, _deployment("rpc-app", "http", name="rpc-app-http")]
    with pytest.raises(SystemExit, match="different appProtocol"):
        app_protocol("rpc-app", mixed)


def test_only_http_apps_reload_in_dev() -> None:
    from scripts.tasks.dev import reloads

    assert reloads(_deployment("web", "http"), no_reload=False)
    assert not reloads(_deployment("web", "http"), no_reload=True)
    assert not reloads(_deployment("rpc-app", "grpc"), no_reload=False)


def test_grpc_app_is_ready_when_its_port_accepts_connections() -> None:
    import socket

    from scripts.tasks.dev import ready

    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        assert ready(server.getsockname()[1], "grpc")
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        port = unused.getsockname()[1]
    assert not ready(port, "grpc")


@pytest.mark.parametrize(
    ("protocol", "probe"),
    [
        ("http", {"httpGet": {"path": "/readyz", "port": "http"}}),
        ("grpc", {"tcpSocket": {"port": "http"}}),
    ],
)
def test_app_chart_probes_follow_the_protocol(protocol: str, probe: dict) -> None:
    from scripts.framework.repo import PLATFORM_DIR
    from scripts.framework.shell import run

    rendered = run(
        [
            "helm", "template", "app", str(PLATFORM_DIR / "charts" / "app"),
            "--set", "appId=app", "--set", "appPort=8080", f"--set=appProtocol={protocol}",
            "--set", "image.repository=app", "--set", "image.tag=t",
        ],
        capture=True,
        quiet=True,
    ).stdout  # fmt: skip
    documents = [d for d in yaml.safe_load_all(rendered) if d and d["kind"] == "Deployment"]
    container = documents[0]["spec"]["template"]["spec"]["containers"][0]
    readiness = {k: v for k, v in container["readinessProbe"].items() if k != "periodSeconds"}
    assert readiness == probe
