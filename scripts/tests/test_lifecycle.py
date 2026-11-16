"""Process tracking, interrupts and kind cluster ownership."""

import os
import signal
from pathlib import Path

import pytest
import yaml

from scripts.framework import kubernetes as kind
from scripts.framework import repo
from scripts.framework import runs as run_lib
from scripts.framework.runs import RunRecord


@pytest.fixture
def runs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(repo, "RUNS_DIR", tmp_path)
    return tmp_path


def test_start_time_does_not_depend_on_locale_or_time_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    pid = os.getpid()
    monkeypatch.setenv("TZ", "UTC")
    first = run_lib.process_start_time(pid)
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    monkeypatch.setenv("LC_ALL", "C")
    assert run_lib.process_start_time(pid) == first


def test_same_process_accepts_records_in_the_current_shell_format() -> None:
    pid = os.getpid()
    legacy = run_lib._ps_start_time(pid, os.environ)
    assert run_lib.same_process(pid, legacy)
    assert run_lib.same_process(pid, run_lib.process_start_time(pid))
    assert not run_lib.same_process(pid, "Thu Jan  1 00:00:00 1970")


def test_interrupt_is_delayed_until_the_block_ends() -> None:
    steps: list[str] = []

    def deferred_block() -> None:
        with run_lib.interrupts_deferred():
            run_lib._interrupt(signal.SIGTERM, None)
            steps.append("end of block")

    with pytest.raises(KeyboardInterrupt, match="SIGTERM"):
        deferred_block()
    assert steps == ["end of block"]


def test_interrupt_outside_a_deferred_block_is_immediate() -> None:
    with pytest.raises(KeyboardInterrupt):
        run_lib._interrupt(signal.SIGHUP, None)


def test_discard_tolerates_entries_removed_elsewhere(runs_dir: Path) -> None:
    record = RunRecord.create("it")
    record.add("compose", project="p")
    item = dict(record.owned[0])
    record.discard(item)
    record.discard(item)  # already gone: no error
    assert record.owned == []


def test_stop_several_groups_at_once() -> None:
    import subprocess
    import time

    processes = [subprocess.Popen(["sleep", "30"], start_new_session=True) for _ in range(3)]
    started = time.monotonic()
    run_lib.stop_process_groups([(p.pid, None) for p in processes], grace_seconds=5)
    assert all(p.wait(timeout=5) is not None for p in processes)
    assert time.monotonic() - started < 5


def test_fresh_cluster_never_takes_over_an_existing_one(
    runs_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(kind, "cluster_exists", lambda name: True)
    record = RunRecord.create("e2e")
    with pytest.raises(SystemExit, match="already exists"):
        kind.create_cluster(record, "e2e-x", port_mappings={}, kubeconfig=runs_dir / "kubeconfig")
    assert record.owned == []


def test_cluster_config_adds_exposed_ports(runs_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = {
        "kind": "Cluster",
        "apiVersion": "kind.x-k8s.io/v1alpha4",
        "nodes": [
            {
                "role": "control-plane",
                "extraPortMappings": [{"containerPort": 80, "hostPort": 8000}],
            },
            {"role": "worker"},
        ],
    }
    source = runs_dir / "kind.yaml"
    source.write_text(yaml.safe_dump(config))
    monkeypatch.setattr(kind, "KIND_CONFIG", source)
    monkeypatch.setattr(kind, "cluster_exists", lambda name: False)
    monkeypatch.setattr(kind, "run", lambda *args, **kwargs: None)

    record = RunRecord.create("kind")
    kind.create_cluster(record, "c", port_mappings={30001: 9001, 30000: 9000}, kubeconfig=None)
    written = yaml.safe_load((record.directory / "kind.yaml").read_text())
    mappings = written["nodes"][0]["extraPortMappings"]
    assert [(m["containerPort"], m["hostPort"]) for m in mappings] == [
        (80, 8000),
        (30000, 9000),
        (30001, 9001),
    ]
    assert all(m["listenAddress"] == "127.0.0.1" for m in mappings[1:])
