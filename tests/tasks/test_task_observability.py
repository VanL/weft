"""Tests for Task observability features (process titles, logging, mappings)."""

from __future__ import annotations

import json
import time
import types

import pytest

from tests.tasks import sample_targets as targets  # noqa: F401
from weft import helpers as weft_helpers
from weft._constants import (
    CONTROL_STOP,
    TASKSPEC_TID_SHORT_LENGTH,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
)
from weft.core.taskspec import IOSection, SpecSection, StateSection, TaskSpec


@pytest.fixture
def unique_tid() -> str:
    import time

    return str(time.time_ns())


def build_function_spec(
    tid: str,
    *,
    enable_title: bool = True,
    function_target: str = "tests.tasks.sample_targets:echo_payload",
    context_path: str = "ctx-root",
    env: dict[str, str] | None = None,
    metadata: dict[str, object] | None = None,
    reporting_interval: str = "transition",
    polling_interval: float = 0.1,
) -> TaskSpec:
    return TaskSpec(
        tid=tid,
        name="observability-task",
        spec=SpecSection(
            type="function",
            function_target=function_target,
            enable_process_title=enable_title,
            weft_context=context_path,
            env=env,
            reporting_interval=reporting_interval,
            polling_interval=polling_interval,
        ),
        io=IOSection(
            inputs={"inbox": f"T{tid}.inbox"},
            outputs={"outbox": f"T{tid}.outbox"},
            control={
                "ctrl_in": f"T{tid}.ctrl_in",
                "ctrl_out": f"T{tid}.ctrl_out",
            },
        ),
        state=StateSection(),
        metadata=metadata or {},
    )


def drain_queue(queue) -> list[str]:
    messages: list[str] = []
    while True:
        value = queue.read_one()
        if value is None:
            break
        messages.append(value)
    return messages


def test_tid_mapping_written(broker_env, task_factory, unique_tid) -> None:
    db_path, make_queue = broker_env
    mapping_queue = make_queue(WEFT_TID_MAPPINGS_QUEUE)
    drain_queue(mapping_queue)  # clear any previous messages

    spec = build_function_spec(unique_tid)
    task_factory(spec)

    record = mapping_queue.read_one()
    assert record is not None
    data = json.loads(record)
    assert data["full"] == unique_tid
    assert data["short"] == unique_tid[-TASKSPEC_TID_SHORT_LENGTH:]
    assert data["name"] == "observability-task"
    assert data["runner"] == "host"
    assert data["runtime_handle"] is None
    assert isinstance(data["pid"], int)
    assert data["pid"] == data["task_pid"]
    assert isinstance(data.get("caller_pid"), int)
    assert data.get("managed_pids") == []


def test_tid_mapping_includes_metadata_role(
    broker_env,
    task_factory,
    unique_tid,
) -> None:
    _db_path, make_queue = broker_env
    mapping_queue = make_queue(WEFT_TID_MAPPINGS_QUEUE)
    drain_queue(mapping_queue)

    spec = build_function_spec(unique_tid, metadata={"role": "manager"})
    task_factory(spec)

    record = mapping_queue.read_one()
    assert record is not None
    data = json.loads(record)
    assert data["full"] == unique_tid
    assert data["role"] == "manager"


def test_tid_mapping_records_worker_pid(broker_env, task_factory, unique_tid) -> None:
    db_path, make_queue = broker_env
    mapping_queue = make_queue(WEFT_TID_MAPPINGS_QUEUE)
    drain_queue(mapping_queue)

    spec = build_function_spec(unique_tid)
    task = task_factory(spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    inbox.write(json.dumps({"args": ["payload"]}))

    task._drain_queue()

    records = [json.loads(msg) for msg in drain_queue(mapping_queue)]
    assert records, "expected at least one mapping record"
    runtime_records = [record for record in records if record.get("runtime_handle")]
    assert runtime_records, "expected runtime handle mapping update"
    runtime_record = runtime_records[-1]
    runtime_handle = runtime_record["runtime_handle"]
    assert runtime_record["runner"] == "host"
    assert isinstance(runtime_handle, dict)
    assert runtime_handle["runner_name"] == "host"
    assert runtime_handle["runtime_id"]
    assert runtime_handle["host_pids"]
    assert runtime_record["managed_pids"] == runtime_handle["host_pids"]


def test_tid_mapping_deduplicates_identical_payloads(
    broker_env, task_factory, unique_tid
) -> None:
    db_path, make_queue = broker_env
    mapping_queue = make_queue(WEFT_TID_MAPPINGS_QUEUE)
    drain_queue(mapping_queue)

    spec = build_function_spec(unique_tid)
    task = task_factory(spec)

    def _peek_payloads() -> list[dict[str, object]]:
        return [json.loads(msg) for msg in mapping_queue.peek_many(limit=10) or []]

    initial = _peek_payloads()
    assert len(initial) == 1

    task._register_tid_mapping()
    after_duplicate = _peek_payloads()
    assert len(after_duplicate) == 1

    task.register_managed_pid(99999)
    after_update = _peek_payloads()
    assert len(after_update) == 2
    assert any(99999 in payload.get("managed_pids", []) for payload in after_update)

    drain_queue(mapping_queue)


def test_process_titles_update(task_factory, unique_tid) -> None:
    calls: list[str] = []
    fake_module = types.SimpleNamespace(setproctitle=lambda title: calls.append(title))
    spec = build_function_spec(unique_tid, enable_title=False)
    task = task_factory(spec)
    task._setproctitle_module = fake_module
    task.enable_process_title = True
    task._update_process_title("init")
    task._update_process_title("running")
    task._update_process_title("completed")

    assert any(title.endswith(":init:waiting") for title in calls)
    assert any(title.endswith(":running:waiting") for title in calls)
    assert any(title.endswith(":completed") for title in calls)
    expected_prefix = (
        f"weft-ctx-root-{unique_tid[-TASKSPEC_TID_SHORT_LENGTH:]}:observability-task:"
    )
    assert any(title.startswith(expected_prefix) for title in calls)


def test_process_title_keeps_status_token_and_uses_activity_detail(
    task_factory, unique_tid
) -> None:
    calls: list[str] = []
    fake_module = types.SimpleNamespace(setproctitle=lambda title: calls.append(title))
    spec = build_function_spec(unique_tid, enable_title=False)
    task = task_factory(spec)
    task._setproctitle_module = fake_module
    task.enable_process_title = True
    task.taskspec.mark_started(pid=task._task_pid)
    task.taskspec.mark_running(pid=task._task_pid)
    task._set_activity("waiting", waiting_on=task._queue_names["inbox"])
    task._update_process_title("running")

    assert calls
    assert any(title.endswith(":running:waiting") for title in calls)


def test_state_logging_records_events(broker_env, task_factory, unique_tid) -> None:
    db_path, make_queue = broker_env
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain_queue(log_queue)

    spec = build_function_spec(unique_tid)
    task = task_factory(spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    inbox.write(json.dumps({"args": ["payload"]}))

    task._drain_queue()

    records = [json.loads(msg) for msg in drain_queue(log_queue)]
    events = [record["event"] for record in records]
    statuses = [record["status"] for record in records]

    assert "task_initialized" in events
    assert "work_started" in events
    assert "work_completed" in events
    assert statuses[0] == "created"
    assert statuses[-1] == "completed"


def test_state_logging_records_failure(broker_env, task_factory, unique_tid) -> None:
    db_path, make_queue = broker_env
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain_queue(log_queue)

    spec = build_function_spec(
        unique_tid, function_target="tests.tasks.sample_targets:fail_payload"
    )
    task = task_factory(spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    inbox.write(json.dumps({"args": ["payload"]}))

    task._drain_queue()

    records = [json.loads(msg) for msg in drain_queue(log_queue)]
    events = [record["event"] for record in records]
    statuses = [record["status"] for record in records]
    assert "work_failed" in events
    assert statuses[-1] == "failed"


def test_control_stop_logged_and_cancelled(
    broker_env, task_factory, unique_tid
) -> None:
    db_path, make_queue = broker_env
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain_queue(log_queue)

    spec = build_function_spec(unique_tid)
    task = task_factory(spec)
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_in.write(CONTROL_STOP)

    task._drain_queue()

    records = [json.loads(msg) for msg in drain_queue(log_queue)]
    events = [record["event"] for record in records]
    statuses = [record["status"] for record in records]
    assert "control_stop" in events
    assert statuses[-1] == "cancelled"
    assert task.should_stop is True
    assert task.taskspec.state.status == "cancelled"


def test_activity_change_emits_one_lightweight_log_event(
    broker_env, task_factory, unique_tid
) -> None:
    _db_path, make_queue = broker_env
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain_queue(log_queue)

    spec = build_function_spec(unique_tid)
    task = task_factory(spec)
    drain_queue(log_queue)

    task._set_activity("working")
    task._set_activity("working")
    task._set_activity("waiting", waiting_on=task._queue_names["inbox"])

    records = [json.loads(msg) for msg in drain_queue(log_queue)]
    activity_records = [
        record for record in records if record["event"] == "task_activity"
    ]

    assert len(activity_records) == 2
    assert activity_records[0]["activity"] == "working"
    assert "waiting_on" not in activity_records[0]
    assert activity_records[1]["activity"] == "waiting"
    assert activity_records[1]["waiting_on"] == task._queue_names["inbox"]


def test_poll_reporting_emits_periodic_events(
    monkeypatch: pytest.MonkeyPatch,
    broker_env,
    task_factory,
    unique_tid,
) -> None:
    db_path, make_queue = broker_env
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain_queue(log_queue)

    current_time = 100.0

    def _fake_monotonic() -> float:
        return current_time

    monkeypatch.setattr("weft.core.tasks.base.time.monotonic", _fake_monotonic)

    spec = build_function_spec(
        unique_tid,
        reporting_interval="poll",
        polling_interval=0.05,
    )
    task = task_factory(spec)
    drain_queue(log_queue)  # discard task_initialized

    task._last_poll_report_at = time.monotonic() - 0.1
    task.process_once()

    records = [json.loads(msg) for msg in drain_queue(log_queue)]
    assert len(records) == 1
    poll_event = records[0]
    assert poll_event["event"] == "poll_report"
    assert poll_event["summary"]["status"] == task.taskspec.state.status

    # Next call without waiting should not emit another report
    current_time += 0.01
    task.process_once()
    assert drain_queue(log_queue) == []

    # Advance timer and expect another poll report
    current_time += 0.1
    task.process_once()
    records = [json.loads(msg) for msg in drain_queue(log_queue)]
    assert len(records) == 1
    assert records[0]["event"] == "poll_report"


def test_state_logging_respects_redaction(
    monkeypatch, broker_env, task_factory, unique_tid
) -> None:
    monkeypatch.setenv(
        "WEFT_REDACT_TASKSPEC_FIELDS", "spec.env.SECRET,metadata.sensitive"
    )
    weft_helpers.reload_config()

    db_path, make_queue = broker_env
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain_queue(log_queue)

    spec = build_function_spec(
        unique_tid,
        env={"SECRET": "value", "VISIBLE": "keep"},
        metadata={"sensitive": "top", "notes": "ok"},
    )
    task = task_factory(spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    inbox.write(json.dumps({"args": ["payload"]}))

    task._drain_queue()

    records = [json.loads(msg) for msg in drain_queue(log_queue)]
    assert records, "expected state change records"
    for record in records:
        if "taskspec" not in record:
            continue
        dump = record["taskspec"]
        assert dump["spec"]["env"]["SECRET"] == "[REDACTED]"
        assert dump["spec"]["env"]["VISIBLE"] == "keep"
        assert dump["metadata"]["sensitive"] == "[REDACTED]"
        assert dump["metadata"]["notes"] == "ok"

    monkeypatch.setenv("WEFT_REDACT_TASKSPEC_FIELDS", "")
    weft_helpers.reload_config()
    db_path, make_queue = broker_env
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain_queue(log_queue)

    spec = build_function_spec(
        unique_tid,
        env={"SECRET": "value", "VISIBLE": "keep"},
        metadata={"sensitive": "top", "notes": "ok"},
    )
    task = task_factory(spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    inbox.write(json.dumps({"args": ["payload"]}))

    task._drain_queue()

    records = [json.loads(msg) for msg in drain_queue(log_queue)]
    assert records, "expected state change records"
    for record in records:
        if "taskspec" not in record:
            continue
        dump = record["taskspec"]
        assert dump["spec"]["env"]["SECRET"] == "value"
        assert dump["spec"]["env"]["VISIBLE"] == "keep"
        assert dump["metadata"]["sensitive"] == "top"
        assert dump["metadata"]["notes"] == "ok"

    monkeypatch.setenv("WEFT_REDACT_TASKSPEC_FIELDS", "")
    weft_helpers.reload_config()
