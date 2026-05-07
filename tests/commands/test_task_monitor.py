"""Task monitor command tests."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from weft._constants import WEFT_GLOBAL_LOG_QUEUE
from weft.commands.task_monitor import (
    DiskJsonlTaskMonitorSink,
    StdoutTaskMonitorSink,
    TaskMonitorConfig,
    run_task_monitor,
)
from weft.context import build_context

pytestmark = [pytest.mark.shared]


def _taskspec_payload(
    tid: str,
    *,
    name: str = "task-monitor-task",
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
    queue = ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    try:
        queue.write(json.dumps(payload))
    finally:
        queue.close()


def _records(output: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def test_disk_sink_appends_jsonl(tmp_path: Path) -> None:
    sink = DiskJsonlTaskMonitorSink(tmp_path, run_date="2026-05-07")
    sink.write_records(
        [
            {"schema_version": 1, "record_type": "task_summary", "tid": "1"},
            {"schema_version": 1, "record_type": "task_summary", "tid": "2"},
        ]
    )

    path = tmp_path / "2026-05-07.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["tid"] for line in lines] == ["1", "2"]


def test_stdout_sink_writes_jsonl() -> None:
    sink = StdoutTaskMonitorSink()
    sink.write_records([{"schema_version": 1, "record_type": "monitor_run_started"}])

    assert _records(sink.output) == [
        {"schema_version": 1, "record_type": "monitor_run_started"}
    ]


def test_monitor_checkpoint_advances_after_successful_sink_write(workdir) -> None:
    ctx = build_context(spec_context=workdir)
    tid = "1778084345905438720"
    taskspec = _taskspec_payload(tid)
    _write_log(
        ctx,
        {
            "event": "work_completed",
            "status": "completed",
            "tid": tid,
            "taskspec": taskspec,
        },
    )
    checkpoint = workdir / "checkpoint.json"

    result = run_task_monitor(
        TaskMonitorConfig(
            context_path=workdir,
            sink="disk",
            log_dir=workdir / "logs",
            checkpoint_path=checkpoint,
            no_checkpoint=False,
            since_timestamp=0,
            limit=None,
            json_output=False,
        )
    )

    assert result.exit_code == 0
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert isinstance(payload["last_task_log_timestamp"], int)
    assert payload["last_task_log_timestamp"] > 0


def test_monitor_restart_does_not_duplicate_after_checkpoint(workdir) -> None:
    ctx = build_context(spec_context=workdir)
    tid = "1778084345905438721"
    _write_log(
        ctx,
        {
            "event": "work_completed",
            "status": "completed",
            "tid": tid,
            "taskspec": _taskspec_payload(tid),
        },
    )
    log_dir = workdir / "logs"
    checkpoint = workdir / "checkpoint.json"
    config = TaskMonitorConfig(
        context_path=workdir,
        sink="disk",
        log_dir=log_dir,
        checkpoint_path=checkpoint,
        no_checkpoint=False,
        since_timestamp=0,
        limit=None,
        json_output=False,
    )

    first = run_task_monitor(config)
    second = run_task_monitor(
        TaskMonitorConfig(
            context_path=workdir,
            sink="disk",
            log_dir=log_dir,
            checkpoint_path=checkpoint,
            no_checkpoint=False,
            since_timestamp=None,
            limit=None,
            json_output=False,
        )
    )

    assert first.exit_code == 0
    assert second.exit_code == 0
    path = next(log_dir.glob("*.jsonl"))
    task_summaries = [
        record
        for record in _records(path.read_text(encoding="utf-8"))
        if record["record_type"] == "task_summary"
    ]
    assert len(task_summaries) == 1


def test_monitor_crash_window_duplicate_has_stable_summary_id(workdir) -> None:
    ctx = build_context(spec_context=workdir)
    tid = "1778084345905438722"
    _write_log(
        ctx,
        {
            "event": "work_completed",
            "status": "completed",
            "tid": tid,
            "taskspec": _taskspec_payload(tid),
        },
    )
    log_dir = workdir / "logs"
    config = TaskMonitorConfig(
        context_path=workdir,
        sink="disk",
        log_dir=log_dir,
        checkpoint_path=workdir / "missing-checkpoint.json",
        no_checkpoint=True,
        since_timestamp=0,
        limit=None,
        json_output=False,
    )

    assert run_task_monitor(config).exit_code == 0
    assert run_task_monitor(config).exit_code == 0

    path = next(log_dir.glob("*.jsonl"))
    summary_ids = [
        record["summary_id"]
        for record in _records(path.read_text(encoding="utf-8"))
        if record["record_type"] == "task_summary"
    ]
    assert len(summary_ids) == 2
    assert len(set(summary_ids)) == 1


def test_corrupt_checkpoint_fails_clearly(workdir) -> None:
    checkpoint = workdir / "checkpoint.json"
    checkpoint.write_text("not-json", encoding="utf-8")

    result = run_task_monitor(
        TaskMonitorConfig(
            context_path=workdir,
            sink="disk",
            log_dir=workdir / "logs",
            checkpoint_path=checkpoint,
            no_checkpoint=False,
            since_timestamp=None,
            limit=None,
            json_output=False,
        )
    )

    assert result.exit_code == 1
    assert "Invalid task monitor checkpoint" in result.stderr


def test_terminal_log_success_summary_uses_terminal_log(workdir) -> None:
    ctx = build_context(spec_context=workdir)
    tid = "1778084345905438723"
    _write_log(
        ctx,
        {
            "event": "work_started",
            "status": "running",
            "tid": tid,
            "taskspec": _taskspec_payload(tid),
        },
    )
    _write_log(
        ctx,
        {
            "event": "work_completed",
            "status": "completed",
            "tid": tid,
            "taskspec": _taskspec_payload(tid),
        },
    )

    result = run_task_monitor(
        TaskMonitorConfig(
            context_path=workdir,
            sink="stdout",
            no_checkpoint=True,
            since_timestamp=0,
            limit=None,
            json_output=False,
        )
    )

    summaries = [
        record
        for record in _records(result.stdout)
        if record["record_type"] == "task_summary"
    ]
    assert summaries[0]["classification"] == "terminal_log"
    assert summaries[0]["status"] == "completed"
    assert summaries[0]["failure_owner"] is None


def test_task_failure_without_task_monitor_anomaly_is_domain_failure(workdir) -> None:
    ctx = build_context(spec_context=workdir)
    tid = "1778084345905438724"
    _write_log(
        ctx,
        {
            "event": "work_failed",
            "status": "failed",
            "tid": tid,
            "error": "task failed",
            "taskspec": _taskspec_payload(tid),
        },
    )

    result = run_task_monitor(
        TaskMonitorConfig(
            context_path=workdir,
            sink="stdout",
            no_checkpoint=True,
            since_timestamp=0,
            limit=None,
            json_output=False,
        )
    )

    summary = next(
        record
        for record in _records(result.stdout)
        if record["record_type"] == "task_summary"
    )
    assert summary["classification"] == "domain_failure"
    assert summary["failure_owner"] == "task_or_runner"
    assert summary["cleanup_candidate"] is False


def test_active_task_emits_no_task_summary(workdir) -> None:
    ctx = build_context(spec_context=workdir)
    tid = "1778084345905438725"
    _write_log(
        ctx,
        {
            "event": "work_started",
            "status": "running",
            "tid": tid,
            "taskspec": _taskspec_payload(tid),
        },
    )

    result = run_task_monitor(
        TaskMonitorConfig(
            context_path=workdir,
            sink="stdout",
            no_checkpoint=True,
            since_timestamp=0,
            limit=None,
            json_output=False,
        )
    )

    records = _records(result.stdout)
    assert not any(record["record_type"] == "task_summary" for record in records)
    completed = next(
        record for record in records if record["record_type"] == "monitor_run_completed"
    )
    assert completed["active_tasks"] >= 1


def test_wrapper_lost_and_result_without_terminal_do_not_consume_queues(
    workdir,
) -> None:
    ctx = build_context(spec_context=workdir)
    wrapper_tid = "1778084345905438726"
    result_tid = "1778084345905438727"
    _write_log(
        ctx,
        {
            "event": "work_started",
            "status": "running",
            "tid": wrapper_tid,
            "taskspec": _taskspec_payload(wrapper_tid),
        },
    )
    _write_log(
        ctx,
        {
            "event": "work_started",
            "status": "running",
            "tid": result_tid,
            "taskspec": _taskspec_payload(result_tid),
        },
    )
    ctrl_out = ctx.queue(f"T{wrapper_tid}.ctrl_out", persistent=False)
    outbox = ctx.queue(f"T{result_tid}.outbox", persistent=True)
    try:
        ctrl_out.write(
            json.dumps(
                {
                    "type": "terminal",
                    "source": "manager",
                    "tid": wrapper_tid,
                    "status": "failed",
                    "error": "Task wrapper exited before publishing terminal state",
                    "return_code": 1,
                }
            )
        )
        outbox.write(json.dumps({"ok": True}))

        result = run_task_monitor(
            TaskMonitorConfig(
                context_path=workdir,
                sink="stdout",
                no_checkpoint=True,
                since_timestamp=0,
                limit=None,
                json_output=False,
            )
        )

        summaries = {
            record["tid"]: record
            for record in _records(result.stdout)
            if record["record_type"] == "task_summary"
        }
        assert summaries[wrapper_tid]["classification"] == "wrapper_lost"
        assert summaries[wrapper_tid]["failure_owner"] == "weft_lifecycle"
        assert summaries[result_tid]["classification"] == "result_without_terminal"
        assert summaries[result_tid]["failure_owner"] == "weft_lifecycle"
        assert ctrl_out.peek_one() is not None
        assert outbox.peek_one() is not None
    finally:
        ctrl_out.close()
        outbox.close()
