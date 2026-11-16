import argparse
from pathlib import Path

import pytest

from scripts.tasks.integration import junit_failures, positive_int, select_packages

MEMBERS = ["alpha", "beta", "no-tests"]


@pytest.fixture
def tests_dir(tmp_path: Path) -> Path:
    for name in ("alpha", "beta", "not-a-member"):
        (tmp_path / name).mkdir()
    return tmp_path


def test_all_members_with_tests_by_default(tests_dir: Path) -> None:
    assert select_packages(None, members=MEMBERS, tests_dir=tests_dir) == ["alpha", "beta"]


def test_selected_member(tests_dir: Path) -> None:
    assert select_packages(["beta"], members=MEMBERS, tests_dir=tests_dir) == ["beta"]


def test_unknown_member_is_rejected_before_setup(tests_dir: Path) -> None:
    with pytest.raises(SystemExit, match="not workspace members: nope"):
        select_packages(["nope"], members=MEMBERS, tests_dir=tests_dir)


def test_member_without_integration_tests_is_rejected(tests_dir: Path) -> None:
    with pytest.raises(SystemExit, match="no integration tests"):
        select_packages(["no-tests"], members=MEMBERS, tests_dir=tests_dir)


@pytest.mark.parametrize("value", ["0", "-1"])
def test_workers_must_be_positive(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        positive_int(value)


def _report(tmp_path: Path, tests: int, failures: int = 0, errors: int = 0) -> Path:
    path = tmp_path / "junit.xml"
    path.write_text(
        f'<testsuites><testsuite tests="{tests}" failures="{failures}" errors="{errors}"/>'
        "</testsuites>"
    )
    return path


def test_junit_passing(tmp_path: Path) -> None:
    assert junit_failures(_report(tmp_path, tests=3)) == 0


@pytest.mark.parametrize(("tests", "failures", "errors"), [(3, 1, 0), (3, 0, 1), (0, 0, 0)])
def test_junit_failing_or_empty(tmp_path: Path, tests: int, failures: int, errors: int) -> None:
    assert junit_failures(_report(tmp_path, tests, failures, errors)) == 1


def test_junit_missing(tmp_path: Path) -> None:
    assert junit_failures(tmp_path / "missing.xml") == 1


def test_package_names_are_normalized(tests_dir: Path) -> None:
    assert select_packages(["Alpha"], members=MEMBERS, tests_dir=tests_dir) == ["alpha"]


def test_underscore_spelling_of_a_member(tmp_path: Path) -> None:
    (tmp_path / "app-runtime").mkdir()
    assert select_packages(["app_runtime"], members=["app-runtime"], tests_dir=tmp_path) == [
        "app-runtime"
    ]
