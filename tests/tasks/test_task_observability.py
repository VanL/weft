"""Tests for Task observability features (process titles, logging, mappings)."""

from __future__ import annotations

import json
import sys
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
    assert isinstance(data["pid"], int)
    assert data["pid"] == data["task_pid"]
    assert isinstance(data.get("caller_pid"), int)
    assert data.get("managed_pids") == []


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

    managed_pid_lists = [record.get("managed_pids") or [] for record in records]
    flattened = [pid for sublist in managed_pid_lists for pid in sublist]
    assert any(isinstance(pid, int) for pid in flattened), "managed pid missing"
    for pid in flattened:
        assert isinstance(pid, int)


def test_process_titles_update(
    monkeypatch, broker_env, task_factory, unique_tid
) -> None:
    calls: list[str] = []
    fake_module = types.SimpleNamespace(setproctitle=lambda title: calls.append(title))
    monkeypatch.setitem(sys.modules, "setproctitle", fake_module)

    db_path, make_queue = broker_env
    spec = build_function_spec(unique_tid, enable_title=True)
    task = task_factory(spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    inbox.write(json.dumps({"args": ["payload"]}))

    task._drain_queue()

    assert any(title.endswith(":init") for title in calls)
    assert any(title.endswith(":running") for title in calls)
    assert any(title.endswith(":completed") for title in calls)
    expected_prefix = (
        f"weft-ctx-root-{unique_tid[-TASKSPEC_TID_SHORT_LENGTH:]}:observability-task:"
    )
    assert any(title.startswith(expected_prefix) for title in calls)


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


def test_poll_reporting_emits_periodic_events(
    broker_env, task_factory, unique_tid
) -> None:
    db_path, make_queue = broker_env
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain_queue(log_queue)

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
    task.process_once()
    assert drain_queue(log_queue) == []

    # Advance timer and expect another poll report
    task._last_poll_report_at = time.monotonic() - 0.1
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
        dump = record["taskspec"]
        assert dump["spec"]["env"]["SECRET"] == "value"
        assert dump["spec"]["env"]["VISIBLE"] == "keep"
        assert dump["metadata"]["sensitive"] == "top"
        assert dump["metadata"]["notes"] == "ok"

    monkeypatch.setenv("WEFT_REDACT_TASKSPEC_FIELDS", "")
    weft_helpers.reload_config()
