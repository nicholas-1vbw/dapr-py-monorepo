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
