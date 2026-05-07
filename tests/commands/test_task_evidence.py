"""Shared task evidence classification regressions."""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import pytest

from tests.helpers.test_backend import prepare_project_root
from tests.tasks.test_task_execution import make_function_taskspec
from weft.commands import system as status_cmd
from weft.commands import task_evidence
from weft.commands import tasks as task_cmd
from weft.context import build_context
from weft.core.tasks import Consumer

pytestmark = [pytest.mark.shared]

LIVE_PONG_PROBE_TIMEOUT = 8.0
LIVE_PONG_DRIVE_TIMEOUT = 10.0


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


def _write_dead_runtime_mapping(ctx: Any, tid: str) -> None:
    queue = ctx.queue("weft.state.tid_mappings", persistent=False)
    try:
        queue.write(
            json.dumps(
                {
                    "short": tid[-10:],
                    "full": tid,
                    "runner": "host",
                    "timestamp": time.time_ns(),
                    "runtime_handle": {
                        "runner": "host",
                        "kind": "process",
                        "id": "999999999",
                        "control": {"authority": "host-pid"},
                        "observations": {"host_pids": [999_999_999]},
                        "metadata": {},
                    },
                }
            )
        )
    finally:
        queue.close()


def _probe_with_task(
    ctx: Any,
    task: Consumer,
    *,
    tid: str,
    taskspec: dict[str, Any],
) -> task_evidence.TaskEvidenceSnapshot | None:
    result: dict[str, task_evidence.TaskEvidenceSnapshot | None] = {}
    errors: list[BaseException] = []

    def probe() -> None:
        try:
            result["evidence"] = task_evidence.probe_live_pong_evidence(
                ctx,
                tid=tid,
                taskspec_payload=taskspec,
                timeout=LIVE_PONG_PROBE_TIMEOUT,
            )
        except BaseException as exc:  # pragma: no cover - thread handoff
            errors.append(exc)

    thread = threading.Thread(target=probe)
    thread.start()
    deadline = time.monotonic() + LIVE_PONG_DRIVE_TIMEOUT
    while thread.is_alive() and time.monotonic() < deadline:
        task.process_once()
        time.sleep(0.01)
    thread.join(timeout=1.0)
    assert not thread.is_alive(), "live PONG probe did not finish before test deadline"
    if errors:
        raise errors[0]
    return result.get("evidence")


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
        assert task_status.reconciliation["classification"] == "result_without_terminal"

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


def test_one_shot_outbox_evidence_beats_dead_runtime_reconciliation(
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
    _write_dead_runtime_mapping(ctx, tid)
    outbox = ctx.queue(f"T{tid}.outbox", persistent=True)
    try:
        outbox.write(json.dumps({"case_count": 3}))

        task_status = task_cmd.task_status(tid, context_path=root)

        assert task_status is not None
        assert task_status.status == "completed"
        assert task_status.completed_at is not None
        assert task_status.reconciliation is not None
        assert task_status.reconciliation["classification"] == "result_without_terminal"
        assert task_status.reconciliation["evidence_source"] == "outbox"
        assert outbox.peek_one() is not None
    finally:
        outbox.close()


def test_claimed_outbox_without_terminal_reports_recovery_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    taskspec = _taskspec_payload(tid, name="wazuh-case-rollup")
    monkeypatch.setattr(task_evidence, "STATUS_RUNTIMELESS_STALE_AFTER_SECONDS", -1.0)
    _write_log(
        ctx,
        {
            "event": "work_started",
            "status": "running",
            "tid": tid,
            "taskspec": taskspec,
        },
    )
    _write_dead_runtime_mapping(ctx, tid)
    outbox = ctx.queue(f"T{tid}.outbox", persistent=True)
    try:
        outbox.write(json.dumps({"case_count": 3}))
        assert outbox.read_one() is not None

        counts = task_evidence.queue_message_counts(ctx, f"T{tid}.outbox")
        assert counts is not None
        assert counts.total == 1
        assert counts.unclaimed == 0
        assert counts.claimed == 1

        evidence = task_evidence.known_tid_evidence(
            ctx,
            tid=tid,
            taskspec_payload=taskspec,
        )

        assert evidence is not None
        assert evidence.status == "failed"
        assert evidence.classification == "claimed_result_without_terminal"
        assert evidence.value is None
        assert evidence.reconciliation is not None
        assert evidence.reconciliation["claimed_messages"] == 1

        task_status = task_cmd.task_status(tid, context_path=root)
        assert task_status is not None
        assert task_status.status == "failed"
        assert task_status.completed_at is None
        assert task_status.reconciliation is not None
        assert (
            task_status.reconciliation["classification"]
            == "claimed_result_without_terminal"
        )
        assert task_status.reconciliation["claimed_messages"] == 1

        after_counts = task_evidence.queue_message_counts(ctx, f"T{tid}.outbox")
        assert after_counts == counts
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


def test_probe_live_pong_ignores_unmatched_and_preserves_terminal_ctrl_out(
    tmp_path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    task_spec = make_function_taskspec(
        tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    taskspec = task_spec.model_dump(mode="json")
    _write_log(
        ctx,
        {
            "event": "work_started",
            "status": "running",
            "tid": tid,
            "taskspec": taskspec,
        },
    )
    task = Consumer(ctx.broker_target, task_spec, config=ctx.broker_config)
    task.taskspec.mark_running()
    ctrl_out = ctx.queue(f"T{tid}.ctrl_out", persistent=False)
    try:
        ctrl_out.write(
            json.dumps(
                {
                    "command": "PING",
                    "status": "ok",
                    "message": "PONG",
                    "request_id": "old-request",
                    "tid": tid,
                    "task_status": "failed",
                    "timestamp": time.time_ns(),
                }
            )
        )
        ctrl_out.write(
            json.dumps(
                {
                    "type": "terminal",
                    "source": "manager",
                    "tid": tid,
                    "status": "failed",
                    "error": task_evidence.WRAPPER_LOST_ERROR,
                    "timestamp": time.time_ns(),
                }
            )
        )

        evidence = _probe_with_task(ctx, task, tid=tid, taskspec=taskspec)

        assert evidence is not None
        assert evidence.classification == "live_pong"
        assert evidence.status == "running"
        assert evidence.reconciliation is not None
        assert evidence.reconciliation["classification"] == "live_pong"
        assert evidence.reconciliation["request_id"] != "old-request"
        messages = ctrl_out.peek_many(limit=10)
        assert len(messages) == 3
        assert any(
            isinstance(message, str) and '"type": "terminal"' in message
            for message in messages
        )
    finally:
        ctrl_out.close()
        task.stop(join=False)


def test_known_tid_probe_live_pong_updates_task_status(tmp_path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    task_spec = make_function_taskspec(
        tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    taskspec = task_spec.model_dump(mode="json")
    _write_log(
        ctx,
        {
            "event": "work_started",
            "status": "running",
            "tid": tid,
            "taskspec": taskspec,
        },
    )
    task = Consumer(ctx.broker_target, task_spec, config=ctx.broker_config)
    task.taskspec.mark_running()

    try:
        result: dict[str, status_cmd.TaskSnapshot | None] = {}
        errors: list[BaseException] = []

        def probe_status() -> None:
            try:
                result["snapshot"] = task_cmd.task_status(
                    tid,
                    context_path=root,
                    probe_live=True,
                    probe_timeout=LIVE_PONG_PROBE_TIMEOUT,
                )
            except BaseException as exc:  # pragma: no cover - thread handoff
                errors.append(exc)

        thread = threading.Thread(target=probe_status)
        thread.start()
        deadline = time.monotonic() + LIVE_PONG_DRIVE_TIMEOUT
        while thread.is_alive() and time.monotonic() < deadline:
            task.process_once()
            time.sleep(0.01)
        thread.join(timeout=1.0)
        assert not thread.is_alive(), "task status live probe did not finish"
        if errors:
            raise errors[0]

        snapshot = result["snapshot"]
        assert snapshot is not None
        assert snapshot.status == "running"
        assert snapshot.event == "live_pong"
        assert snapshot.reconciliation is not None
        assert snapshot.reconciliation["classification"] == "live_pong"
    finally:
        task.stop(join=False)


def test_project_status_does_not_active_ping_tasks_by_default(tmp_path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    taskspec = _taskspec_payload(tid)
    _write_log(
        ctx,
        {
            "event": "work_started",
            "status": "running",
            "tid": tid,
            "taskspec": taskspec,
        },
    )
    ctrl_in = ctx.queue(f"T{tid}.ctrl_in", persistent=True)
    try:
        exit_code, payload = status_cmd.cmd_status(
            json_output=True,
            include_terminal=True,
            spec_context=root,
        )

        assert exit_code == 0
        assert payload is not None
        assert ctrl_in.peek_one() is None
    finally:
        ctrl_in.close()
