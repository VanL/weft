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

from weft.helpers import kill_process_tree

pytestmark = [pytest.mark.sqlite_only]


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

import psutil

from tests.helpers.test_backend import prepare_project_root
from weft.commands import tasks as task_cmd
from weft.context import build_context
from weft.core.launcher import launch_task_process
from weft.core.tasks import Consumer
from weft.core.taskspec import IOSection, RunnerSection, SpecSection, StateSection, TaskSpec
from weft.helpers import kill_process_tree


def build_spec(tid: str, root: Path, ready: Path, release: Path) -> TaskSpec:
    return TaskSpec(
        tid=tid,
        name="sqlite-stop-regression",
        spec=SpecSection(
            type="command",
            process_target=sys.executable,
            args=[
                "-c",
                "from tests.tasks.sample_targets import signal_ready_and_wait_for_release; "
                "import sys; signal_ready_and_wait_for_release(sys.argv[1], sys.argv[2])",
                str(ready),
                str(release),
            ],
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
    pid = getattr(process, "pid", None)
    if not isinstance(pid, int) or pid <= 0:
        return True
    deadline = time.time() + timeout
    join = getattr(process, "join", None)
    while time.time() < deadline:
        if callable(join):
            join(timeout=0.05)
        if getattr(process, "exitcode", None) is not None:
            return True
        try:
            ps_process = psutil.Process(pid)
            if (
                not ps_process.is_running()
                or ps_process.status() == psutil.STATUS_ZOMBIE
            ):
                return True
        except psutil.Error:
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
    bootstrap_queue = context.queue("weft.test.bootstrap", persistent=False)
    try:
        bootstrap_queue.generate_timestamp()
    finally:
        bootstrap_queue.close()
    try:
        for index in range(5):
            tid = str(time.time_ns())
            ready = root / f"ready-{index}"
            release = root / f"release-{index}"
            spec = build_spec(tid, root, ready, release)
            inbox = context.queue(spec.io.inputs["inbox"], persistent=True)
            inboxes.append(inbox)
            inbox.write(json.dumps({}))
            process = launch_task_process(
                Consumer,
                context.broker_target,
                spec,
                config=context.config,
            )
            try:
                # Setup is bounded by the unchanged outer 180s aggregate guard.
                # Worker entry follows work_started; wrapper PID liveness does not.
                print(f"iteration={index} tid={tid} phase=readiness", file=sys.stderr, flush=True)
                while not ready.exists():
                    process.join(timeout=0.05)
                    if process.exitcode is not None:
                        raise RuntimeError(
                            f"consumer exited before readiness: {process.exitcode}; "
                            f"latest={task_cmd.task_status(tid, context_path=root)!r}"
                        )
                wait_for_status(root, tid, "running")
                print(f"iteration={index} tid={tid} phase=stop", file=sys.stderr, flush=True)
                stopped = task_cmd.stop_tasks([tid], context_path=root)
                if stopped != 1:
                    raise RuntimeError(f"stop_tasks returned {stopped} on iteration {index}")
                wait_for_status(root, tid, "cancelled", timeout=20.0)
                if not wait_for_exit(process):
                    raise RuntimeError(
                        f"consumer process {process.pid!r} still alive after cancelled state on iteration {index}"
                    )
                print(f"iteration={index} tid={tid} phase=integrity", file=sys.stderr, flush=True)
                assert_integrity(context.database_path)
            finally:
                release.touch()
                if not wait_for_exit(process):
                    kill_process_tree(process.pid)
                    process.join(timeout=5.0)
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
    with subprocess.Popen(
        [sys.executable, str(script), str(tmp_path / "runtime-root")],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=180)
        except subprocess.TimeoutExpired:
            # The aggregate guard must also clean up the script's owned children.
            kill_process_tree(process.pid)
            stdout, stderr = process.communicate()
            pytest.fail(
                f"STOP script exceeded 180s; stdout={stdout!r}; stderr={stderr!r}"
            )

    assert process.returncode == 0, stderr or stdout
