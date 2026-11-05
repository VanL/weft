"""Manager supervision remains quiet [MA-1] item 5, [WT-4.3]."""

from __future__ import annotations

import json
import sys
import time

import psutil
import pytest

from tests.conftest import run_cli
from tests.helpers.weft_harness import WeftTestHarness
from weft.client import WeftClient

pytestmark = [pytest.mark.shared]


def test_manager_does_not_spin_while_user_task_runs(
    weft_harness: WeftTestHarness,
) -> None:
    """A live user task must not leave an unserviced idle deadline armed."""
    client = WeftClient(path=str(weft_harness.root))
    task = client.submit_command([sys.executable, "-c", "import time; time.sleep(12)"])
    try:
        deadline = time.monotonic() + 15
        while True:
            rc, out, err = run_cli(
                "task",
                "status",
                task.tid,
                "--json",
                cwd=weft_harness.root,
                stdin="",
                harness=weft_harness,
            )
            assert rc == 0, (out, err)
            if json.loads(out).get("status") == "running":
                break
            assert time.monotonic() < deadline, out
            time.sleep(0.05)
        rc, out, err = run_cli(
            "manager",
            "list",
            "--json",
            cwd=weft_harness.root,
            stdin="",
            harness=weft_harness,
        )
        assert rc == 0, (out, err)
        manager = next(row for row in json.loads(out) if row.get("status") == "active")
        pid = manager["runtime_handle"]["observations"]["host_pids"][0]
        process = psutil.Process(pid)
        before = process.cpu_times()
        time.sleep(3.0)  # CPU measurement window, not synchronization.
        after = process.cpu_times()
        cpu_seconds = after.user + after.system - before.user - before.system
        assert cpu_seconds < 1.5, cpu_seconds
    finally:
        run_cli(
            "task",
            "stop",
            task.tid,
            cwd=weft_harness.root,
            stdin="",
            harness=weft_harness,
        )
