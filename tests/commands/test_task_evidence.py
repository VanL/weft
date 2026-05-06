"""Shared task evidence classification regressions."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft.commands import system as status_cmd
from weft.commands import task_evidence
from weft.commands import tasks as task_cmd
from weft.context import build_context

pytestmark = [pytest.mark.shared]


def _taskspec_payload(
    tid: str,
    *,
    name: str = "evidence-task",
    persistent: bool = False,
) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "type": "function",
        "function_target": "tests.tasks.sample_targets:echo_payload",
        "runner": {"name": "host", "options": {}},
    }
    if persistent:
        spec["persistent"] = True
    return {
        "tid": tid,
        "name": name,
        "spec": spec,
        "io": {
            "outputs": {"outbox": f"T{tid}.outbox"},
            "control": {
                "ctrl_in": f"T{tid}.ctrl_in",
                "ctrl_out": f"T{tid}.ctrl_out",
            },
        },
        "state": {
            "status": "running",
            "started_at": time.time_ns(),
            "completed_at": None,
        },
        "metadata": {"owner": "tests"},
    }


def _write_log(ctx: Any, payload: dict[str, Any]) -> None:
    queue = ctx.queue("weft.log.tasks", persistent=False)
    try:
        queue.write(json.dumps(payload))
    finally:
        queue.close()


def test_wrapper_lost_ctrl_out_classifies_status_without_consuming(
    tmp_path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    taskspec = _taskspec_payload(tid, name="reconcile-log-truncation")
    _write_log(
        ctx,
        {
            "event": "work_started",
            "status": "running",
            "tid": tid,
            "taskspec": taskspec,
        },
    )
    _write_log(
        ctx,
        {
            "event": "task_activity",
            "status": "created",
            "activity": "waiting",
            "waiting_on": f"T{tid}.inbox",
            "tid": tid,
        },
    )
    ctrl_out = ctx.queue(f"T{tid}.ctrl_out", persistent=False)
    try:
        ctrl_out.write(json.dumps({"command": "PING", "status": "ok", "tid": tid}))
        ctrl_out.write(
            json.dumps(
                {
                    "type": "terminal",
                    "source": "manager",
                    "tid": tid,
                    "status": "failed",
                    "error": task_evidence.WRAPPER_LOST_ERROR,
                    "return_code": 1,
                    "timestamp": time.time_ns(),
                }
            )
        )

        evidence = task_evidence.task_local_terminal_evidence(
            ctx,
            tid=tid,
            taskspec_payload=taskspec,
        )

        assert evidence is not None
        assert evidence.status == "failed"
        assert evidence.classification == "wrapper_lost"
        assert evidence.source == "ctrl_out"
        assert evidence.metadata["terminal_source"] == "manager"
        assert evidence.reconciliation is not None
        assert evidence.reconciliation["classification"] == "wrapper_lost"
        assert len(ctrl_out.peek_many(limit=10)) == 2

        terminal_snapshot = task_cmd.task_terminal_snapshot(tid, context=ctx)
        assert terminal_snapshot.status == "failed"
        assert terminal_snapshot.source == "ctrl_out"
        assert terminal_snapshot.metadata["classification"] == "wrapper_lost"
        assert len(ctrl_out.peek_many(limit=10)) == 2

        task_status = task_cmd.task_status(tid, context_path=root)
        assert task_status is not None
        assert task_status.status == "failed"
        assert task_status.error == task_evidence.WRAPPER_LOST_ERROR
        assert task_status.return_code == 1
        assert task_status.reconciliation is not None
        assert task_status.reconciliation["classification"] == "wrapper_lost"

        exit_code, payload = status_cmd.cmd_status(
            json_output=True,
            include_terminal=True,
            spec_context=root,
        )
        assert exit_code == 0
        assert payload is not None
        rows = json.loads(payload)["tasks"]
        assert rows[0]["tid"] == tid
        assert rows[0]["status"] == "failed"
        assert rows[0]["error"] == task_evidence.WRAPPER_LOST_ERROR
        assert rows[0]["return_code"] == 1
        assert rows[0]["reconciliation"]["classification"] == "wrapper_lost"

        exit_code, payload = status_cmd.cmd_status(
            json_output=True,
            spec_context=root,
        )
        assert exit_code == 0
        assert payload is not None
        assert json.loads(payload)["tasks"] == []
    finally:
        ctrl_out.close()


def test_one_shot_outbox_without_terminal_log_classifies_completed(
    tmp_path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    taskspec = _taskspec_payload(tid, name="wazuh-case-rollup")
    _write_log(
        ctx,
        {
            "event": "work_started",
            "status": "running",
            "tid": tid,
            "taskspec": taskspec,
        },
    )
    outbox = ctx.queue(f"T{tid}.outbox", persistent=True)
    try:
        outbox.write(json.dumps({"ok": True}))

        evidence = task_evidence.task_local_terminal_evidence(
            ctx,
            tid=tid,
            taskspec_payload=taskspec,
        )

        assert evidence is not None
        assert evidence.status == "completed"
        assert evidence.classification == "result_without_terminal"
        assert evidence.value == {"ok": True}
        assert outbox.peek_one() is not None

        task_status = task_cmd.task_status(tid, context_path=root)
        assert task_status is not None
        assert task_status.status == "completed"
        assert task_status.reconciliation is not None
        assert (
            task_status.reconciliation["classification"]
            == "result_without_terminal"
        )

        exit_code, payload = status_cmd.cmd_status(
            json_output=True,
            include_terminal=True,
            spec_context=root,
        )
        assert exit_code == 0
        assert payload is not None
        rows = json.loads(payload)["tasks"]
        assert rows[0]["status"] == "completed"
        assert rows[0]["reconciliation"]["classification"] == (
            "result_without_terminal"
        )
        assert outbox.peek_one() is not None
    finally:
        outbox.close()


def test_outbox_evidence_does_not_complete_persistent_or_ambiguous_tasks(
    tmp_path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    persistent_tid = str(time.time_ns())
    persistent_taskspec = _taskspec_payload(persistent_tid, persistent=True)
    persistent_outbox = ctx.queue(f"T{persistent_tid}.outbox", persistent=True)
    ambiguous_tid = str(time.time_ns() + 1)
    ambiguous_taskspec = _taskspec_payload(ambiguous_tid)
    ambiguous_outbox = ctx.queue(f"T{ambiguous_tid}.outbox", persistent=True)
    partial_tid = str(time.time_ns() + 2)
    partial_taskspec = _taskspec_payload(partial_tid)
    partial_outbox = ctx.queue(f"T{partial_tid}.outbox", persistent=True)
    try:
        persistent_outbox.write("done")
        ambiguous_outbox.write("hello")
        ambiguous_outbox.write("world")
        partial_outbox.write(
            json.dumps(
                {
                    "type": "stream",
                    "stream": "stdout",
                    "data": "partial",
                    "final": False,
                }
            )
        )

        assert (
            task_evidence.task_local_terminal_evidence(
                ctx,
                tid=persistent_tid,
                taskspec_payload=persistent_taskspec,
            )
            is None
        )
        assert (
            task_evidence.task_local_terminal_evidence(
                ctx,
                tid=ambiguous_tid,
                taskspec_payload=ambiguous_taskspec,
            )
            is None
        )
        assert (
            task_evidence.task_local_terminal_evidence(
                ctx,
                tid=partial_tid,
                taskspec_payload=partial_taskspec,
            )
            is None
        )
    finally:
        persistent_outbox.close()
        ambiguous_outbox.close()
        partial_outbox.close()
