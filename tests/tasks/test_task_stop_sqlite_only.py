"""SQLite-specific regression for host-runner STOP handling.

This test isolates the original post-0.6.4 regression: STOP can publish a
terminal cancelled state before the host task process has actually exited.
That stale-live-process window is the deterministic precursor to the broker
corruption seen in Linux CI.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.sqlite_only]


@pytest.mark.skipif(
    sys.platform == "darwin",
    reason="Reproduces on Linux CI; local macOS uses a different SQLite/runtime path",
)
def test_stop_exits_process_before_reporting_clean_cancel(tmp_path: Path) -> None:
    """STOP must not leave the launched task process alive after cancellation."""

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
        name="sqlite-stop-regression",
        spec=SpecSection(
            type="command",
            process_target=sys.executable,
            args=["-c", "import time; time.sleep(10)"],
            timeout=30.0,
            working_dir=str(root),
            runner=RunnerSection(name="host", options={}),
        ),
        io=IOSection(
            inputs={"inbox": f"T{tid}.inbox"},
            outputs={"outbox": f"T{tid}.outbox"},
            control={"ctrl_in": f"T{tid}.ctrl_in", "ctrl_out": f"T{tid}.ctrl_out"},
        ),
        state=StateSection(),
    )


def wait_for_status(root: Path, tid: str, expected: str, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    latest = None
    while time.time() < deadline:
        latest = task_cmd.task_status(tid, context_path=root)
        if latest is not None and latest.status == expected:
            return
        time.sleep(0.05)
    raise RuntimeError(
        f"timed out waiting for {expected} status for {tid}; latest={latest!r}"
    )


def wait_for_exit(process: object, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    join = getattr(process, "join", None)
    while time.time() < deadline:
        if callable(join):
            join(timeout=0.05)
        if getattr(process, "exitcode", None) is not None:
            return True
        time.sleep(0.05)
    return False


def assert_integrity(db_path: Path) -> None:
    connection = sqlite3.connect(db_path)
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    finally:
        connection.close()
    if result != ("ok",):
        raise RuntimeError(f"integrity_check failed: {result!r}")


def main(root_arg: str) -> int:
    root = prepare_project_root(Path(root_arg))
    context = build_context(spec_context=root)
    inboxes: list[object] = []
    try:
        for index in range(5):
            tid = str(time.time_ns())
            spec = build_spec(tid, root)
            process = launch_task_process(
                Consumer,
                context.broker_target,
                spec,
                config=context.config,
            )
            inbox = context.queue(spec.io.inputs["inbox"], persistent=True)
            inboxes.append(inbox)
            inbox.write(json.dumps({}))
            wait_for_status(root, tid, "running")
            stopped = task_cmd.stop_tasks([tid], context_path=root)
            if stopped != 1:
                raise RuntimeError(f"stop_tasks returned {stopped} on iteration {index}")
            wait_for_status(root, tid, "cancelled", timeout=20.0)
            if not wait_for_exit(process):
                pid = getattr(process, "pid", None)
                if isinstance(pid, int) and pid > 0:
                    kill_process_tree(pid)
                raise RuntimeError(
                    f"consumer process {pid!r} still alive after cancelled state on iteration {index}"
                )
            assert_integrity(context.database_path)
        return 0
    finally:
        for inbox in inboxes:
            close = getattr(inbox, "close", None)
            if callable(close):
                close()


if __name__ == "__main__":
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
        timeout=180,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
