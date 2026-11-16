"""Rendering Dapr resources for native sidecars, with a synthetic environment."""

from pathlib import Path

import pytest
import yaml

from scripts.framework import description, native, repo
from scripts.framework.native import render_native
from scripts.framework.runs import RunRecord

COMPONENTS = {
    "components": {
        "store-a": {
            "type": "state.redis",
            "version": "v1",
            "resource": "kv",
            "scopes": ["app-a"],
            "metadata": {},
        }
    }
}
RESOURCES_VALUES = {
    "resources": {
        "kv": {"kind": "redis", "host": "127.0.0.1", "port": 1234},
        "tracing": {"kind": "otlp", "endpoint": "127.0.0.1:4317", "insecure": True},
    }
}


@pytest.fixture
def inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[RunRecord, Path]:
    monkeypatch.setattr(repo, "RUNS_DIR", tmp_path / "runs")
    components = tmp_path / "components.yaml"
    components.write_text(yaml.safe_dump(COMPONENTS))
    monkeypatch.setattr(description, "COMPONENTS_FILE", components)
    values = tmp_path / "resources-values.yaml"
    values.write_text(yaml.safe_dump(RESOURCES_VALUES))
    return RunRecord.create("test"), values


def test_components_keep_scopes(inputs) -> None:
    native = render_native(*inputs, omit_scopes=False)
    (component,) = yaml.safe_load_all((native.resources_dir / "components.yaml").read_text())
    assert component["metadata"]["name"] == "store-a"
    assert component["scopes"] == ["app-a"]


def test_integration_mode_omits_scopes(inputs) -> None:
    native = render_native(*inputs, omit_scopes=True)
    (component,) = yaml.safe_load_all((native.resources_dir / "components.yaml").read_text())
    assert "scopes" not in component


def test_named_configuration(inputs) -> None:
    native = render_native(*inputs, omit_scopes=False)
    config = yaml.safe_load(native.config_file("app-config").read_text())
    assert config["spec"]["tracing"]["otel"]["endpointAddress"] == "127.0.0.1:4317"


def test_tracing_resource_is_optional(inputs) -> None:
    record, values = inputs
    values.write_text(yaml.safe_dump({"resources": {"kv": RESOURCES_VALUES["resources"]["kv"]}}))
    config = yaml.safe_load(
        render_native(record, values, omit_scopes=False).config_file("app-config").read_text()
    )
    assert "otel" not in config["spec"]["tracing"]


def test_empty_name_selects_an_empty_configuration(inputs) -> None:
    native = render_native(*inputs, omit_scopes=False)
    config = yaml.safe_load(native.config_file("").read_text())
    assert config["spec"] == {}


def test_unknown_configuration_is_rejected(inputs) -> None:
    native = render_native(*inputs, omit_scopes=False)
    with pytest.raises(SystemExit, match="'other' is not defined"):
        native.config_file("other")


def test_a_configuration_named_empty_is_not_overwritten(
    inputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    rendered = yaml.safe_dump_all(
        [{"kind": "Configuration", "metadata": {"name": "empty"}, "spec": {"x": 1}}]
    )
    monkeypatch.setattr(native, "run", lambda args, **kw: type("R", (), {"stdout": rendered})())
    rendered_files = render_native(*inputs, omit_scopes=False)
    assert yaml.safe_load(rendered_files.config_file("empty").read_text())["spec"] == {"x": 1}
    assert yaml.safe_load(rendered_files.config_file("").read_text())["spec"] == {}
