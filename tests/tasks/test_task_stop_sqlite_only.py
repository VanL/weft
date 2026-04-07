"""SQLite-specific regressions for task STOP lifecycle handling.

These tests lock down the host-runner STOP path that previously left the
active control poller thread alive during task teardown, which could corrupt
the SQLite broker under load.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft.context import build_context
from weft.core.tasks import Consumer
from weft.core.taskspec import (
    IOSection,
    RunnerSection,
    SpecSection,
    StateSection,
    TaskSpec,
)

pytestmark = [pytest.mark.sqlite_only]


def _build_long_running_spec(tid: str, *, working_dir: Path) -> TaskSpec:
    return TaskSpec(
        tid=tid,
        name="sqlite-stop-regression",
        spec=SpecSection(
            type="command",
            process_target=sys.executable,
            args=["-c", "import time; time.sleep(10)"],
            timeout=30.0,
            working_dir=str(working_dir),
            runner=RunnerSection(name="host", options={}),
        ),
        io=IOSection(
            inputs={"inbox": f"T{tid}.inbox"},
            outputs={"outbox": f"T{tid}.outbox"},
            control={"ctrl_in": f"T{tid}.ctrl_in", "ctrl_out": f"T{tid}.ctrl_out"},
        ),
        state=StateSection(),
    )
def test_active_control_poller_waits_for_background_thread_to_finish(
    tmp_path: Path,
) -> None:
    """The active control poller must fully join before task teardown continues."""

    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    tid = str(time.time_ns())
    consumer = Consumer(
        context.broker_target,
        _build_long_running_spec(tid, working_dir=root),
    )

    started = threading.Event()
    finished = threading.Event()

    def slow_poller(done: threading.Event) -> None:
        started.set()
        done.wait()
        time.sleep(0.3)
        finished.set()

    consumer._poll_control_queue_while_active = slow_poller  # type: ignore[method-assign]

    with consumer._active_control_poller():
        assert started.wait(timeout=1.0)

    assert finished.is_set()
    consumer.stop(join=False)


@pytest.mark.skipif(
    sys.platform == "darwin",
    reason="SQLite corruption regression reproduces on Linux CI; macOS teardown hangs instead",
)
def test_repeated_stop_preserves_sqlite_integrity(
    tmp_path: Path,
) -> None:
    """Repeated STOP handling must leave the broker database valid."""

    script = tmp_path / "stop_integrity_check.py"
    script.write_text(
        """
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

from tests.helpers.test_backend import prepare_project_root
from weft.commands import tasks as task_cmd
from weft.context import build_context
from weft.core.launcher import launch_task_process
from weft.core.tasks import Consumer
from weft.core.taskspec import IOSection, RunnerSection, SpecSection, StateSection, TaskSpec
from weft.helpers import kill_process_tree


def build_spec(tid: str, root: Path) -> TaskSpec:
    return TaskSpec(
        tid=tid,
        name='sqlite-stop-regression',
        spec=SpecSection(
            type='command',
            process_target=sys.executable,
            args=['-c', 'import time; time.sleep(10)'],
            timeout=30.0,
            working_dir=str(root),
            runner=RunnerSection(name='host', options={}),
        ),
        io=IOSection(
            inputs={'inbox': f'T{tid}.inbox'},
            outputs={'outbox': f'T{tid}.outbox'},
            control={'ctrl_in': f'T{tid}.ctrl_in', 'ctrl_out': f'T{tid}.ctrl_out'},
        ),
        state=StateSection(),
    )


def wait_for_status(root: Path, tid: str, expected: str, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        snapshot = task_cmd.task_status(tid, context_path=root)
        if snapshot is not None and snapshot.status == expected:
            return
        time.sleep(0.05)
    raise RuntimeError(f'timed out waiting for {expected} status for {tid}')


def wait_for_exit(process: object, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    join = getattr(process, 'join', None)
    while time.time() < deadline:
        if callable(join):
            join(timeout=0.05)
        if getattr(process, 'exitcode', None) is not None:
            return True
        time.sleep(0.05)
    return False


def assert_integrity(db_path: Path) -> None:
    connection = sqlite3.connect(db_path)
    try:
        result = connection.execute('PRAGMA integrity_check').fetchone()
    finally:
        connection.close()
    if result != ('ok',):
        raise RuntimeError(f'integrity_check failed: {result}')


def main(root_arg: str) -> int:
    root = prepare_project_root(Path(root_arg))
    context = build_context(spec_context=root)
    inboxes: list[object] = []
    try:
        for index in range(10):
            tid = str(time.time_ns())
            spec = build_spec(tid, root)
            process = launch_task_process(Consumer, context.broker_target, spec, config=context.config)
            inbox = context.queue(spec.io.inputs['inbox'], persistent=True)
            inboxes.append(inbox)
            inbox.write(json.dumps({}))
            wait_for_status(root, tid, 'running')
            stopped = task_cmd.stop_tasks([tid], context_path=root)
            if stopped != 1:
                raise RuntimeError(f'stop_tasks returned {stopped} on iteration {index}')
            wait_for_status(root, tid, 'cancelled')
            if not wait_for_exit(process):
                pid = getattr(process, 'pid', None)
                if isinstance(pid, int) and pid > 0:
                    kill_process_tree(pid)
                wait_for_exit(process, timeout=2.0)
            assert_integrity(context.database_path)
        return 0
    finally:
        for inbox in inboxes:
            close = getattr(inbox, 'close', None)
            if callable(close):
                close()


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1]))
""".strip()
        + "\n",
        encoding="utf-8",
    )

    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, str(script), str(tmp_path / "runtime-root")],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
