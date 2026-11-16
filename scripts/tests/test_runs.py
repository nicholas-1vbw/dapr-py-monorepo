import json

import pytest

from scripts.framework import repo, runs
from scripts.framework.repo import dns_label, workspace_members
from scripts.framework.runs import RunRecord


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        (("it", "a1b2", "app_runtime"), "it-a1b2-app-runtime"),
        (("E2E", "--x--"), "e2e-x"),
        (("a" * 80,), "a" * 63),
    ],
)
def test_dns_label(parts: tuple[str, ...], expected: str) -> None:
    assert dns_label(*parts) == expected


def test_run_record_persists_each_change(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(repo, "RUNS_DIR", tmp_path)
    record = RunRecord.create("it")
    record.add("compose", project="p1")
    record.add("process", name="x", pid=42)
    record.remove("process", pid=42)

    saved = json.loads(record.path.read_text())
    assert saved["owned"] == [{"kind": "compose", "project": "p1"}]
    assert RunRecord.load(record.run_id).owned == saved["owned"]


def test_run_ids_are_unique() -> None:
    assert runs.new_run_id("it") != runs.new_run_id("it")


def test_workspace_members_use_normalized_names() -> None:
    members = workspace_members()
    assert members
    for name, directory in members.items():
        assert name == name.lower()
        assert "_" not in name
        assert (directory / "pyproject.toml").is_file()


def test_kind_down_forgets_only_that_cluster(tmp_path, monkeypatch) -> None:
    from scripts.framework import kubernetes as kind

    monkeypatch.setattr(repo, "RUNS_DIR", tmp_path)
    removed: list[str] = []
    monkeypatch.setattr(kind, "remove_image", removed.append)

    mine = RunRecord.create("kind")
    mine.add("cluster-name", name="local")
    mine.add("kind-cluster", name="local", kubeconfig=None)
    mine.add("image", reference="app:mine")
    other = RunRecord.create("kind")
    other.add("cluster-name", name="other")
    other.add("kind-cluster", name="other", kubeconfig=None)
    other.add("image", reference="app:other")

    assert kind.forget_cluster("local")
    assert removed == ["app:mine"]
    # Tearing down the old run later must not delete a new cluster of the same name.
    assert RunRecord.load(mine.run_id).owned == [{"kind": "cluster-name", "name": "local"}]
    assert len(RunRecord.load(other.run_id).owned) == 3


def test_kind_down_keeps_images_it_could_not_remove(tmp_path, monkeypatch) -> None:
    from scripts.framework import kubernetes as kind

    monkeypatch.setattr(repo, "RUNS_DIR", tmp_path)

    def remove(reference: str) -> None:
        if reference == "app:busy":
            raise RuntimeError("image is in use")

    monkeypatch.setattr(kind, "remove_image", remove)
    record = RunRecord.create("kind")
    record.add("cluster-name", name="local")
    record.add("image", reference="app:busy")
    record.add("image", reference="app:free")

    assert not kind.forget_cluster("local")
    images = [i["reference"] for i in RunRecord.load(record.run_id).owned if i["kind"] == "image"]
    assert images == ["app:busy"]


def test_concurrent_records_stay_valid(tmp_path, monkeypatch) -> None:
    import threading

    monkeypatch.setattr(repo, "RUNS_DIR", tmp_path)
    record = RunRecord.create("kind")

    def add_many(prefix: str) -> None:
        for i in range(200):
            record.add("image", reference=f"{prefix}-{i}" * (1 + i % 7))

    threads = [threading.Thread(target=add_many, args=(p,)) for p in ("a", "b", "c")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(RunRecord.load(record.run_id).owned) == 600


def test_reused_pid_is_not_signalled(monkeypatch) -> None:
    signalled: list[int] = []
    monkeypatch.setattr(runs, "process_start_time", lambda pid: "Mon Jan  1 00:00:00 2030")
    monkeypatch.setattr(runs, "_signal_group", lambda pid, sig: signalled.append(sig) or False)
    runs.stop_process_group(12345, started="Sun Dec 31 23:59:59 2029")
    assert signalled == []


def test_process_start_time_identifies_live_processes() -> None:
    import os

    assert runs.process_start_time(os.getpid())
    assert runs.process_start_time(2**22 + 12345) is None


def test_kind_down_also_cleans_records_without_a_label(tmp_path, monkeypatch) -> None:
    from scripts.framework import kubernetes as kind

    monkeypatch.setattr(repo, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(kind, "remove_image", lambda reference: None)
    record = RunRecord.create("kind")
    record.add("kind-cluster", name="local", kubeconfig=None)
    record.add("image", reference="app:old")

    assert kind.forget_cluster("local")
    assert RunRecord.load(record.run_id).owned == []


def test_kind_down_also_retires_clusters_kept_by_e2e(tmp_path, monkeypatch) -> None:
    from scripts.framework import kubernetes as kind

    monkeypatch.setattr(repo, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(kind, "remove_image", lambda reference: None)
    kept = RunRecord.create("e2e")
    kept.add("kind-cluster", name="e2e-1234", kubeconfig="/tmp/kubeconfig")

    assert kind.forget_cluster("e2e-1234")
    assert RunRecord.load(kept.run_id).owned == []


def test_start_time_without_ps_is_unknown(monkeypatch) -> None:
    import subprocess

    def missing(*args, **kwargs):
        raise FileNotFoundError("ps")

    monkeypatch.setattr(subprocess, "run", missing)
    assert runs.process_start_time(12345) is None
