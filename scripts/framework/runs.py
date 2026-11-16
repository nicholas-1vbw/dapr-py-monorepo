"""Runs: their records, interrupts and the processes they start.

Every task that creates something gets a run id and records each resource in
.run/<run-id>/environment.json right after creating it, so that a partially
failed or interrupted run can always be cleaned up (see cleanup.py).
"""

import contextlib
import json
import os
import secrets
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.framework import repo
from scripts.framework.repo import ROOT, display_path
from scripts.framework.shell import fail, log

TERMINATION_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


_deferred_depth = 0


_deferred_signal: int | None = None


def _interrupt(signum: int, _frame: object) -> None:
    global _deferred_signal
    if _deferred_depth:
        _deferred_signal = signum
        return
    raise KeyboardInterrupt(signal.Signals(signum).name)


@contextlib.contextmanager
def interrupts_deferred() -> Iterator[None]:
    """Delay Ctrl+C, SIGTERM and SIGHUP until the block has finished.

    Used where an interruption would leave something untracked, such as between
    starting a process and recording it. Signal handlers run in the main thread,
    so other threads are not affected.
    """
    global _deferred_depth, _deferred_signal
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    _deferred_depth += 1
    try:
        yield
    finally:
        _deferred_depth -= 1
        if not _deferred_depth and _deferred_signal is not None:
            pending, _deferred_signal = _deferred_signal, None
            raise KeyboardInterrupt(signal.Signals(pending).name)


def shield_cleanup() -> None:
    """Ignore further termination signals so that cleanup can finish."""
    for sig in TERMINATION_SIGNALS:
        signal.signal(sig, signal.SIG_IGN)


def entrypoint(main: Callable[[], None]) -> None:
    """Run a task's main(); report failed commands without a traceback.

    SIGTERM and SIGHUP (for example a closed terminal) are handled like
    Ctrl+C, so every task cleans up what it created on all of them.
    """
    for sig in TERMINATION_SIGNALS:
        signal.signal(sig, _interrupt)
    try:
        main()
    except subprocess.CalledProcessError as error:
        command = " ".join(str(part) for part in error.cmd)
        raise SystemExit(f"error: command failed (exit {error.returncode}): {command}") from None


def new_run_id(kind: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{kind}-{stamp}-{secrets.token_hex(4)}"


def run_suffix(run_id: str) -> str:
    """The random part of a run id, used in names derived from the run."""
    return run_id.rsplit("-", 1)[-1]


@dataclass
class RunRecord:
    """What one run created, written to .run/<run-id>/environment.json.

    Every resource is recorded right after it is created, so a partially
    failed run can still be cleaned up with `poe teardown --run-id`.
    """

    run_id: str
    owned: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)

    @property
    def directory(self) -> Path:
        return repo.RUNS_DIR / self.run_id

    @property
    def path(self) -> Path:
        return self.directory / "environment.json"

    @classmethod
    def create(cls, kind: str) -> RunRecord:
        record = cls(new_run_id(kind))
        record.directory.mkdir(parents=True, exist_ok=False)
        record.save()
        log(f"run id: {record.run_id} (records in {display_path(record.directory)})")
        return record

    @classmethod
    def load(cls, run_id: str) -> RunRecord:
        path = repo.RUNS_DIR / run_id / "environment.json"
        if not path.is_file():
            fail(f"no run record at {path}")
        data = json.loads(path.read_text())
        return cls(run_id=data["run_id"], owned=data["owned"])

    def add(self, kind: str, **details: Any) -> None:
        with self._lock:
            self.owned.append({"kind": kind, **details})
            self.save()

    def remove(self, kind: str, **details: Any) -> None:
        with self._lock:
            self.owned = [
                item
                for item in self.owned
                if not (item["kind"] == kind and all(item.get(k) == v for k, v in details.items()))
            ]
            self.save()

    def discard(self, item: dict[str, Any]) -> None:
        """Remove this exact entry if it is still recorded."""
        with self._lock:
            if item in self.owned:
                self.owned.remove(item)
                self.save()

    def save(self) -> None:
        """Write the record atomically; several threads of a run may record resources."""
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            data = json.dumps({"run_id": self.run_id, "owned": self.owned}, indent=2)
            temporary = self.path.with_suffix(".json.tmp")
            temporary.write_text(data)
            temporary.replace(self.path)


def start_process(
    record: RunRecord,
    name: str,
    command: Sequence[str],
    *,
    env: Mapping[str, str],
    log_file: Path | None = None,
) -> subprocess.Popen[bytes]:
    """Start a process in its own process group and record it."""
    log_file_handle = None
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file_handle = log_file.open("wb")
    try:
        # The process runs in its own session and would outlive us untracked if
        # an interrupt arrived before it is recorded.
        with interrupts_deferred():
            process = subprocess.Popen(
                list(command),
                cwd=ROOT,
                env={**os.environ, **env},
                stdout=log_file_handle,
                stderr=subprocess.STDOUT if log_file_handle else None,
                start_new_session=True,
            )
            record.add(
                "process", name=name, pid=process.pid, started=process_start_time(process.pid)
            )
    finally:
        if log_file_handle:
            log_file_handle.close()
    return process


def process_start_time(pid: int) -> str | None:
    """Start time of a process as reported by `ps`, or None when it does not exist.

    Together with the pid it identifies a process: a pid reused later by another
    process has a different start time.
    """
    return _ps_start_time(pid, {**os.environ, **STABLE_PS_ENV})


# `ps` prints start times in the local time zone and language; fixed values make
# the recorded text comparable across shells.
STABLE_PS_ENV = {"TZ": "UTC", "LC_ALL": "C"}


def _ps_start_time(pid: int, env: Mapping[str, str]) -> str | None:
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
            env=dict(env),
        )
    except OSError:
        # Without `ps` the start time is unknown; the process is still recorded.
        return None
    return " ".join(result.stdout.split()) or None


def same_process(pid: int, started: str | None) -> bool:
    """Whether `pid` is still the process recorded with start time `started`.

    True when nothing was recorded or the process no longer exists (its group
    may still have members to stop). Records written in another shell's format
    are also matched against the current environment's format.
    """
    if started is None:
        return True
    current = process_start_time(pid)
    if current is None:
        return True
    started = " ".join(started.split())
    return started in (current, _ps_start_time(pid, os.environ))


def stop_process_group(
    pid: int, *, started: str | None = None, grace_seconds: float = 15.0
) -> None:
    """Terminate a process group started by `start_process` (idempotent)."""
    stop_process_groups([(pid, started)], grace_seconds=grace_seconds)


def stop_process_groups(
    groups: Sequence[tuple[int, str | None]], *, grace_seconds: float = 15.0
) -> None:
    """Terminate several process groups together: SIGTERM to all, one shared
    grace period, then SIGKILL to the ones still alive.

    A group whose leader pid now belongs to another process (compared by start
    time) is left alone. A group whose leader already exited is still signalled:
    the group id is not reused while members remain.
    """
    alive = []
    for pid, started in groups:
        if not same_process(pid, started):
            log(f"pid {pid} now belongs to another process; not signalling it")
            continue
        if _signal_group(pid, signal.SIGTERM):
            alive.append(pid)
    deadline = time.monotonic() + grace_seconds
    while alive and time.monotonic() < deadline:
        for pid in alive:
            _reap(pid)
        alive = [pid for pid in alive if _signal_group(pid, 0)]
        if alive:
            time.sleep(0.2)
    for pid in alive:
        _signal_group(pid, signal.SIGKILL)
        _reap(pid)


def _signal_group(pid: int, sig: int) -> bool:
    """Send `sig` to the group; False when no live process is left in it."""
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        return False
    except PermissionError:
        # macOS reports a group whose only member is an unreaped leader this way.
        _reap(pid)
        return False
    return True


def _reap(pid: int) -> None:
    with contextlib.suppress(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)
