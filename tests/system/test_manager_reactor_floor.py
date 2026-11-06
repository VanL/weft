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
from weft.context import build_context
from weft.core.control_messages import encode_control_message
from weft.core.task_state import read_task_state_snapshot

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


def test_paused_consumer_does_not_spin_with_queued_input(
    weft_harness: WeftTestHarness,
) -> None:
    """A paused Consumer process with queued input must remain CPU-quiet [CC-2.4], [WT-4.1], [WT-4.3]."""
    ctx = build_context(spec_context=weft_harness.root)
    client = WeftClient(path=str(weft_harness.root))
    spec_path = weft_harness.root / "paused_probe.json"
    spec_path.write_text(
        json.dumps(
            {
                "name": "paused-cpu-probe",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                    "persistent": True,
                },
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )
    submitted = client.submit_spec(spec_path, payload={"payload": "warmup"})
    tid = submitted.tid
    try:
        ctrl_in = ctx.queue(f"T{tid}.ctrl_in", persistent=False)
        inbox = ctx.queue(f"T{tid}.inbox", persistent=True)
        outbox = ctx.queue(f"T{tid}.outbox", persistent=True)

        deadline = time.monotonic() + 15.0
        while outbox.peek_one() is None:
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert outbox.read_one() == "warmup"

        consumer_pid: int | None = None
        while True:
            snapshot = read_task_state_snapshot(ctx, tid)
            runtime_id = (
                snapshot[1].get("runtime_handle", {}).get("id")
                if snapshot is not None
                else None
            )
            if isinstance(runtime_id, str) and runtime_id.isdigit():
                consumer_pid = int(runtime_id)
                break
            assert time.monotonic() < deadline, snapshot
            time.sleep(0.05)

        requester_tid = str(ctrl_in.generate_timestamp())
        requester_ctrl_in_name = f"T{requester_tid}.ctrl_in"
        requester_ctrl_in = ctx.queue(requester_ctrl_in_name, persistent=False)
        ctrl_in.write(encode_control_message("PAUSE", request_id="pause-cpu-probe"))
        ctrl_in.write(
            encode_control_message(
                "STATUS",
                reply_to=requester_ctrl_in_name,
                request_id="status-cpu-probe",
            )
        )
        pause_deadline = time.monotonic() + 10.0
        while True:
            reply_raw = requester_ctrl_in.read_one()
            if isinstance(reply_raw, str):
                reply = json.loads(reply_raw)
                if (
                    reply.get("request_id") == "status-cpu-probe"
                    and reply.get("paused") is True
                ):
                    break
            assert time.monotonic() < pause_deadline
            time.sleep(0.05)

        inbox.write(json.dumps({"payload": "queued-while-paused"}))
        process = psutil.Process(consumer_pid)
        before = process.cpu_times()
        time.sleep(3.0)  # CPU measurement window while paused with queued input.
        after = process.cpu_times()
        cpu_seconds = after.user + after.system - before.user - before.system
        assert cpu_seconds < 0.25, cpu_seconds
        assert inbox.peek_one() is not None
        assert outbox.peek_one() is None

        ctrl_in.write(encode_control_message("RESUME", request_id="resume-cpu-probe"))
        resume_deadline = time.monotonic() + 10.0
        while outbox.peek_one() is None:
            assert time.monotonic() < resume_deadline
            time.sleep(0.05)
        assert outbox.read_one() == "queued-while-paused"
    finally:
        run_cli(
            "task",
            "stop",
            tid,
            cwd=weft_harness.root,
            stdin="",
            harness=weft_harness,
        )
