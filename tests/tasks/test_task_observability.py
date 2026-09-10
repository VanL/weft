"""Tests for Task observability features (process titles, logging, mappings)."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import get_args

import pytest

from simplebroker.ext import BrokerError
from tests.helpers.queue_payloads import terminal_envelopes
from tests.helpers.reactor_driver import drive_until
from tests.tasks import sample_targets as targets  # noqa: F401
from weft import helpers as weft_helpers
from weft._constants import (
    CONTROL_PAUSE,
    CONTROL_STOP,
    PROCESS_TITLE_CONTEXT_LENGTH,
    PROCESS_TITLE_DETAILS_LENGTH,
    PROCESS_TITLE_HANDOFF_LENGTH,
    PROCESS_TITLE_MAX_LENGTH,
    PROCESS_TITLE_NAME_LENGTH,
    PROCESS_TITLE_STATUSES,
    TASK_LIFECYCLE_STATUS_VALUES,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
)
from weft.core import process_title
from weft.core.control_messages import encode_control_message
from weft.core.manager import Manager
from weft.core.taskspec import IOSection, SpecSection, StateSection, TaskSpec
from weft.ext import RunnerHandle
from weft.helpers import tid_short_form


@pytest.fixture
def unique_tid() -> str:
    import time

    return str(time.time_ns())


def build_function_spec(
    tid: str,
    *,
    enable_title: bool = True,
    function_target: str = "tests.tasks.sample_targets:echo_payload",
    name: str = "observability-task",
    context_path: str = "ctx-root",
    env: dict[str, str] | None = None,
    metadata: dict[str, object] | None = None,
    reporting_interval: str = "transition",
    polling_interval: float = 0.1,
) -> TaskSpec:
    return TaskSpec(
        tid=tid,
        name=name,
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


def drive_task_until(task, predicate, *, timeout: float = 5.0) -> None:
    drive_until(
        predicate,
        bool,
        step=task.process_once,
        wait=task.wait_for_activity,
        timeout=timeout,
        pending_work=(task._has_pending_worker_results,),
        diagnostics=lambda: {
            "status": task.taskspec.state.status,
            "should_stop": task.should_stop,
            "worker_snapshot": task._worker_activity_snapshot(),
        },
    )


def test_drive_task_until_applies_ready_result_after_wall_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DelayedReadyTask:
        def __init__(self) -> None:
            self.completed = False
            self.pending = False
            self.process_calls = 0

        def process_once(self) -> None:
            self.process_calls += 1
            if self.process_calls == 1:
                self.pending = True
                return
            self.pending = False
            self.completed = True

        def _has_pending_worker_results(self) -> bool:
            return self.pending

        def wait_for_activity(self, *, timeout: float) -> None:
            raise AssertionError(f"unexpected wait after deadline: {timeout}")

    monotonic_values = iter((0.0, 6.0))
    monkeypatch.setattr(time, "monotonic", lambda: next(monotonic_values))
    task = DelayedReadyTask()

    drive_task_until(task, lambda: task.completed, timeout=5.0)

    assert task.process_calls == 2


def test_tid_mapping_written(broker_env, task_factory, unique_tid) -> None:
    _db_path, make_queue = broker_env
    mapping_queue = make_queue(WEFT_TID_MAPPINGS_QUEUE)
    drain_queue(mapping_queue)  # clear any previous messages

    spec = build_function_spec(unique_tid)
    task_factory(spec)

    record = mapping_queue.read_one()
    assert record is not None
    data = json.loads(record)
    assert data["full"] == unique_tid
    assert data["short"] == tid_short_form(unique_tid)
    assert data["name"] == "observability-task"
    assert data["runner"] == "host"
    runtime_handle = data["runtime_handle"]
    assert runtime_handle["runner"] == "host"
    assert runtime_handle["kind"] == "process"
    assert runtime_handle["control"] == {"authority": "host-pid"}
    assert runtime_handle["metadata"] == {"source": "weft-task-process"}
    assert int(runtime_handle["id"]) in runtime_handle["observations"]["host_pids"]
    assert "pid" not in data
    assert "task_pid" not in data
    assert "caller_pid" not in data
    assert "managed_pids" not in data


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


def test_tid_mapping_records_runtime_identity_from_start_hooks(
    broker_env, task_factory, unique_tid
) -> None:
    _db_path, make_queue = broker_env
    mapping_queue = make_queue(WEFT_TID_MAPPINGS_QUEUE)
    drain_queue(mapping_queue)

    spec = build_function_spec(unique_tid)
    task = task_factory(spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    inbox.write(json.dumps({"args": ["payload"]}))

    def _runtime_mapping() -> dict[str, object] | None:
        records = [
            json.loads(message) for message in mapping_queue.peek_many(limit=10) or []
        ]
        return next(
            (
                record
                for record in reversed(records)
                if record.get("full") == unique_tid
                and isinstance(record.get("runtime_handle"), dict)
                and record["runtime_handle"].get("id") != str(task._task_pid)
                and record["runtime_handle"].get("metadata", {}).get("source")
                != "weft-task-process"
            ),
            None,
        )

    runtime_record = drive_until(
        _runtime_mapping,
        lambda record: record is not None,
        step=task.process_once,
        wait=task.wait_for_activity,
        timeout=20.0 if os.name == "nt" else 10.0,
        pending_work=(task._has_pending_worker_results,),
        diagnostics=lambda: {
            "status": task.taskspec.state.status,
            "should_stop": task.should_stop,
            "worker_snapshot": task._worker_activity_snapshot(),
        },
    )

    assert runtime_record is not None
    runtime_handle = runtime_record["runtime_handle"]
    assert runtime_record["runner"] == "host"
    assert isinstance(runtime_handle, dict)
    assert runtime_handle["runner"] == "host"
    assert runtime_handle["id"]
    assert runtime_handle["observations"]["host_pids"]
    assert "managed_pids" not in runtime_record


def _forbid_mapping_history_reads(monkeypatch, queue_type):
    """Make any mapping-history read fail loudly at the queue seam."""

    real_peek_generator = queue_type.peek_generator
    real_peek_many = getattr(queue_type, "peek_many", None)

    def poisoned_peek_generator(queue, *args, **kwargs):
        if queue.name == WEFT_TID_MAPPINGS_QUEUE:
            raise AssertionError("mapping history read attempted")
        return real_peek_generator(queue, *args, **kwargs)

    monkeypatch.setattr(queue_type, "peek_generator", poisoned_peek_generator)
    if real_peek_many is not None:

        def poisoned_peek_many(queue, *args, **kwargs):
            if queue.name == WEFT_TID_MAPPINGS_QUEUE:
                raise AssertionError("mapping history read attempted")
            return real_peek_many(queue, *args, **kwargs)

        monkeypatch.setattr(queue_type, "peek_many", poisoned_peek_many)


def test_tid_mapping_registration_appends_without_history_read(
    broker_env, task_factory, unique_tid, monkeypatch
) -> None:
    """Registration is one edge-triggered append and never replays the queue.

    Verifies [OBS.6a]: every write is a new fact. A direct re-registration
    appends a valid equivalent row (no writer-side payload oracle), a real
    change appends a new snapshot, and no path reads mapping history.
    """

    _db_path, make_queue = broker_env
    mapping_queue = make_queue(WEFT_TID_MAPPINGS_QUEUE)
    drain_queue(mapping_queue)
    _forbid_mapping_history_reads(monkeypatch, type(mapping_queue))

    spec = build_function_spec(unique_tid)
    task = task_factory(spec)

    rows = [json.loads(message) for message in drain_queue(mapping_queue)]
    assert rows, "construction must append at least one snapshot"

    assert task._register_tid_mapping() is True
    equivalent = [json.loads(message) for message in drain_queue(mapping_queue)]
    assert len(equivalent) == 1

    task.register_runtime_handle(
        RunnerHandle(
            runner="host",
            kind="process",
            id=str(task._task_pid),
            control={"authority": "host-pid"},
            observations={"host_pids": [task._task_pid]},
        )
    )
    changed = [json.loads(message) for message in drain_queue(mapping_queue)]
    assert len(changed) == 1
    for row in rows + equivalent + changed:
        assert row["full"] == unique_tid
        assert row["short"] == tid_short_form(unique_tid)
        assert "runner" in row and "runtime_handle" in row
        assert row["name"] == spec.name
        assert "hostname" in row and "started" in row
        assert row["terminal"] is False


def test_terminal_transition_publishes_mapping_exactly_once(
    broker_env, task_factory, unique_tid, monkeypatch
) -> None:
    """The terminal transition is the edge; repeated terminal reports are not.

    The terminal mapping row publishes once per task on the first terminal
    report and later terminal reports do not republish it.
    """

    _db_path, make_queue = broker_env
    mapping_queue = make_queue(WEFT_TID_MAPPINGS_QUEUE)
    task_log = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain_queue(mapping_queue)
    _forbid_mapping_history_reads(monkeypatch, type(mapping_queue))

    spec = build_function_spec(unique_tid)
    task = task_factory(spec)
    drain_queue(mapping_queue)
    drain_queue(task_log)

    task.taskspec.mark_running(pid=task._task_pid)
    task.taskspec.mark_completed(return_code=0)
    task._report_state_change(event="work_completed")
    task._report_state_change(event="task_completed")

    terminal_rows = [json.loads(message) for message in drain_queue(mapping_queue)]
    assert len(terminal_rows) == 1
    assert terminal_rows[0]["terminal"] is True


def test_activity_transitions_publish_current_fields(
    broker_env, task_factory, unique_tid, monkeypatch
) -> None:
    """Actual activity changes publish activity/waiting_on; no-ops publish nothing.

    Edge detection lives at the call site: `_set_activity` suppresses an
    unchanged `(activity, waiting_on)` pair before registration is invoked.
    """

    _db_path, make_queue = broker_env
    mapping_queue = make_queue(WEFT_TID_MAPPINGS_QUEUE)
    drain_queue(mapping_queue)
    _forbid_mapping_history_reads(monkeypatch, type(mapping_queue))

    spec = build_function_spec(unique_tid)
    task = task_factory(spec)
    drain_queue(mapping_queue)

    task._set_activity("working")
    task._set_activity("working")
    task._set_activity("waiting", waiting_on="T1.inbox")

    rows = [json.loads(message) for message in drain_queue(mapping_queue)]
    assert [row.get("activity") for row in rows] == ["working", "waiting"]
    assert rows[0].get("waiting_on") is None
    assert rows[1].get("waiting_on") == "T1.inbox"


def test_terminal_mapping_write_failure_retries_on_next_terminal_report(
    broker_env, task_factory, unique_tid, monkeypatch
) -> None:
    """A faulted terminal append stays best effort and retries until success.

    The terminal row is the task's last liveness evidence, so the
    once-published flag latches only on a successful write; lifecycle
    publication is never aborted by the mapping failure.
    """

    _db_path, make_queue = broker_env
    mapping_queue = make_queue(WEFT_TID_MAPPINGS_QUEUE)
    task_log = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain_queue(mapping_queue)
    _forbid_mapping_history_reads(monkeypatch, type(mapping_queue))

    spec = build_function_spec(unique_tid)
    task = task_factory(spec)
    drain_queue(mapping_queue)
    drain_queue(task_log)

    queue_type = type(mapping_queue)
    real_write = queue_type.write
    fault_state = {"armed": True}

    def faulting_write(queue, message, *args, **kwargs):
        if queue.name == WEFT_TID_MAPPINGS_QUEUE and fault_state["armed"]:
            fault_state["armed"] = False
            raise BrokerError("mapping append failed")
        return real_write(queue, message, *args, **kwargs)

    monkeypatch.setattr(queue_type, "write", faulting_write)

    task.taskspec.mark_running(pid=task._task_pid)
    task.taskspec.mark_completed(return_code=0)
    task._report_state_change(event="work_completed")
    assert drain_queue(mapping_queue) == []
    assert task._terminal_tid_mapping_published is False

    state_events = [json.loads(message) for message in drain_queue(task_log)]
    assert any(event.get("event") == "work_completed" for event in state_events)

    task._report_state_change(event="task_completed")
    retried = [json.loads(message) for message in drain_queue(mapping_queue)]
    assert len(retried) == 1
    assert retried[0]["terminal"] is True
    assert task._terminal_tid_mapping_published is True


def test_terminal_state_report_publishes_terminal_tid_mapping_when_activity_empty(
    broker_env,
    task_factory,
    unique_tid,
) -> None:
    _db_path, make_queue = broker_env
    mapping_queue = make_queue(WEFT_TID_MAPPINGS_QUEUE)
    drain_queue(mapping_queue)

    spec = build_function_spec(unique_tid)
    task = task_factory(spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    inbox.write(json.dumps({"args": ["payload"]}))

    drive_task_until(task, lambda: task.taskspec.state.status == "completed")

    mappings = [json.loads(message) for message in drain_queue(mapping_queue)]
    task_mappings = [row for row in mappings if row.get("full") == unique_tid]
    assert task._activity is None
    assert task_mappings[0]["terminal"] is False
    assert task_mappings[-1]["terminal"] is True


def test_terminal_mapping_scan_failure_does_not_block_terminal_evidence(
    broker_env,
    task_factory,
    unique_tid,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _db_path, make_queue = broker_env
    mapping_queue = make_queue(WEFT_TID_MAPPINGS_QUEUE)
    task_log = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    spec = build_function_spec(unique_tid)
    task = task_factory(spec)
    ctrl_out = make_queue(spec.io.control["ctrl_out"])
    drain_queue(task_log)
    drain_queue(ctrl_out)
    queue_type = type(mapping_queue)
    real_peek_generator = queue_type.peek_generator

    def fail_during_mapping_scan(queue, *args, **kwargs):
        if queue.name != WEFT_TID_MAPPINGS_QUEUE:
            return real_peek_generator(queue, *args, **kwargs)

        def rows():
            yield ("{}", 1)
            raise RuntimeError("mapping history iteration failed")

        return rows()

    monkeypatch.setattr(queue_type, "peek_generator", fail_during_mapping_scan)
    task.taskspec.mark_running(pid=task._task_pid)
    task.taskspec.mark_completed(return_code=0)

    task._report_state_change(event="work_completed")
    task._send_terminal_envelope()

    state_events = [json.loads(message) for message in drain_queue(task_log)]
    assert state_events[-1]["event"] == "work_completed"
    assert state_events[-1]["status"] == "completed"
    assert terminal_envelopes(ctrl_out, tid=unique_tid, source="task")


def test_process_titles_update(task_factory, unique_tid, monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(process_title, "set_process_title", calls.append)
    spec = build_function_spec(unique_tid, enable_title=False)
    task = task_factory(spec)
    task.enable_process_title = True
    task._update_process_title("init")
    task._update_process_title("running")
    task._update_process_title("completed")

    assert any(title.endswith(":init:waiting") for title in calls)
    assert any(title.endswith(":running:waiting") for title in calls)
    assert any(title.endswith(":completed") for title in calls)
    expected_prefix = f"weft-ctx-root-{tid_short_form(unique_tid)}:observability-task:"
    assert any(title.startswith(expected_prefix) for title in calls)


def test_process_title_keeps_status_token_and_uses_activity_detail(
    task_factory, unique_tid, monkeypatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(process_title, "set_process_title", calls.append)
    spec = build_function_spec(unique_tid, enable_title=False)
    task = task_factory(spec)
    task.enable_process_title = True
    task.taskspec.mark_started(pid=task._task_pid)
    task.taskspec.mark_running(pid=task._task_pid)
    task._set_activity("waiting", waiting_on=task._queue_names["inbox"])
    task._update_process_title("running")

    assert calls
    assert any(title.endswith(":running:waiting") for title in calls)


def test_process_title_sanitizes_dynamic_segments(
    task_factory, unique_tid: str, monkeypatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(process_title, "set_process_title", calls.append)
    spec = build_function_spec(
        unique_tid,
        enable_title=False,
        name="; rm -rf /",
        context_path="/tmp/ctx;rm -rf",
    )
    task = task_factory(spec)
    task.enable_process_title = True

    task._update_process_title(
        "running;cat /etc/passwd",
        " waiting:\nqueue;rm ",
    )

    assert calls == [
        (
            f"weft-ctxrm-rf-{tid_short_form(unique_tid)}"
            ":rm-rf:runningcatetcpasswd:waitingqueuerm"
        )
    ]
    assert re.fullmatch(
        r"weft-[A-Za-z0-9_-]+-[0-9]+:[A-Za-z0-9_-]+:[A-Za-z0-9_-]+(?::[A-Za-z0-9_-]+)?",
        calls[0],
    )


def test_state_logging_records_events(broker_env, task_factory, unique_tid) -> None:
    _db_path, make_queue = broker_env
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain_queue(log_queue)

    spec = build_function_spec(unique_tid)
    task = task_factory(spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    inbox.write(json.dumps({"args": ["payload"]}))

    drive_task_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )

    records = [json.loads(msg) for msg in drain_queue(log_queue)]
    events = [record["event"] for record in records]
    statuses = [record["status"] for record in records]

    assert "task_initialized" in events
    assert "work_started" in events
    assert "work_completed" in events
    assert statuses[0] == "created"
    assert statuses[-1] == "completed"


def test_success_terminal_ctrl_out_published_when_completed_log_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    broker_env,
    task_factory,
    unique_tid,
) -> None:
    _db_path, make_queue = broker_env
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain_queue(log_queue)

    spec = build_function_spec(unique_tid)
    task = task_factory(spec)
    drain_queue(log_queue)
    original_report_state_change = task._report_state_change

    def drop_completed_log(event: str, **extra: object) -> None:
        if event == "work_completed":
            return
        original_report_state_change(event, **extra)

    monkeypatch.setattr(task, "_report_state_change", drop_completed_log)
    inbox = make_queue(spec.io.inputs["inbox"])
    inbox.write(json.dumps({"args": ["payload"]}))

    drive_task_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )

    outbox = make_queue(spec.io.outputs["outbox"])
    assert outbox.read_one() == "payload"
    records = [json.loads(msg) for msg in drain_queue(log_queue)]
    assert not any(record["event"] == "work_completed" for record in records)
    ctrl_out = make_queue(spec.io.control["ctrl_out"])
    envelopes = terminal_envelopes(ctrl_out, tid=unique_tid, source="task")
    assert len(envelopes) == 1
    assert envelopes[0]["status"] == "completed"
    assert envelopes[0]["return_code"] == 0


def test_state_logging_records_failure(broker_env, task_factory, unique_tid) -> None:
    _db_path, make_queue = broker_env
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain_queue(log_queue)

    spec = build_function_spec(
        unique_tid, function_target="tests.tasks.sample_targets:fail_payload"
    )
    task = task_factory(spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    inbox.write(json.dumps({"args": ["payload"]}))

    drive_task_until(
        task,
        lambda: task.taskspec.state.status == "failed",
    )

    records = [json.loads(msg) for msg in drain_queue(log_queue)]
    events = [record["event"] for record in records]
    statuses = [record["status"] for record in records]
    assert "work_failed" in events
    assert statuses[-1] == "failed"


def test_state_logging_propagates_unserializable_payload(
    task_factory, unique_tid: str
) -> None:
    spec = build_function_spec(unique_tid)
    task = task_factory(spec)

    with pytest.raises(TypeError):
        task._report_state_change("task_custom", marker=object())


def test_control_response_broker_error_is_best_effort(
    monkeypatch: pytest.MonkeyPatch,
    task_factory,
    unique_tid: str,
) -> None:
    spec = build_function_spec(unique_tid)
    task = task_factory(spec)

    def _fail_write(_payload: str) -> None:
        raise BrokerError("ctrl-out unavailable")

    monkeypatch.setattr(task._ctrl_out_queue, "write", _fail_write)

    task._send_control_response("PING", "ok", message="PONG")


def test_control_stop_logged_and_cancelled(
    broker_env, task_factory, unique_tid
) -> None:
    _db_path, make_queue = broker_env
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain_queue(log_queue)

    spec = build_function_spec(unique_tid)
    task = task_factory(spec)
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_in.write(encode_control_message(CONTROL_STOP))

    task.process_once()

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
    _db_path, make_queue = broker_env
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain_queue(log_queue)

    current_time = 100.0

    def _fake_monotonic() -> float:
        return current_time

    spec = build_function_spec(
        unique_tid,
        reporting_interval="poll",
        polling_interval=0.05,
    )
    task = task_factory(spec)
    drain_queue(log_queue)  # discard task_initialized

    with monkeypatch.context() as scoped_monkeypatch:
        scoped_monkeypatch.setattr(
            "weft.core.tasks.base.time.monotonic",
            _fake_monotonic,
        )

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

    _db_path, make_queue = broker_env
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain_queue(log_queue)

    unredacted_tid = str(int(unique_tid) + 1)
    spec = build_function_spec(
        unredacted_tid,
        env={"SECRET": "value", "VISIBLE": "keep"},
        metadata={"sensitive": "top", "notes": "ok"},
    )
    task = task_factory(spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    inbox.write(json.dumps({"args": ["payload"]}))

    drive_task_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )

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
    _db_path, make_queue = broker_env
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

    drive_task_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )

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


def test_title_error_is_nonfatal_and_preserved(task_factory, unique_tid, monkeypatch):
    monkeypatch.setattr(
        process_title, "set_process_title", lambda title: "title unavailable"
    )
    task = task_factory(build_function_spec(unique_tid))
    assert task.taskspec.state.process_title_error == "title unavailable"
    assert task.taskspec.state.error is None
    assert task.taskspec.state.status == "created"
    assert task._control_snapshot_fields()["process_title_error"] == "title unavailable"
    monkeypatch.setattr(process_title, "set_process_title", lambda title: None)
    task._update_process_title("running")
    assert task.taskspec.state.process_title_error == "title unavailable"


def test_live_turn_ticks_without_replacing_explicit_title(
    task_factory, unique_tid, monkeypatch
):
    task = task_factory(build_function_spec(unique_tid, enable_title=False))
    calls = []
    monkeypatch.setattr(
        process_title, "set_process_title", lambda title: calls.append("set")
    )
    monkeypatch.setattr(process_title, "tick", lambda: calls.append("tick"))
    monkeypatch.setattr(task, "_process_reactor_turn", lambda: calls.append("turn"))
    task.enable_process_title = True
    task.process_once()
    assert calls == ["turn", "tick"]
    task.enable_process_title = False
    task.process_once()
    assert calls == ["turn", "tick", "turn"]


@pytest.mark.parametrize(
    "requested, expected", [(None, 0.25), (10.0, 0.25), (0.1, 0.1)]
)
def test_title_deadline_caps_shared_wait(
    task_factory, unique_tid, monkeypatch, requested, expected
):
    task = task_factory(build_function_spec(unique_tid, enable_title=False))
    task.process_once()
    waits = []
    monkeypatch.setattr(task, "_wait_for_reactor_activity", waits.append)
    monkeypatch.setattr(process_title, "seconds_until_due", lambda: 0.25)
    task.enable_process_title = True
    task.wait_for_activity(requested)
    assert waits == [expected]


def test_stopped_turn_does_not_activate_title(task_factory, unique_tid, monkeypatch):
    task = task_factory(build_function_spec(unique_tid, enable_title=False))
    calls = []
    monkeypatch.setattr(
        process_title, "set_process_title", lambda title: calls.append(title)
    )
    monkeypatch.setattr(process_title, "tick", lambda: calls.append("tick"))
    monkeypatch.setattr(task, "_process_reactor_turn", task.stop)
    task.enable_process_title = True
    task.process_once()
    assert "tick" not in calls


def test_title_failure_does_not_prevent_success(
    broker_env, task_factory, unique_tid, monkeypatch
):
    _, make_queue = broker_env
    monkeypatch.setattr(
        process_title, "set_process_title", lambda title: "native setter failed"
    )
    monkeypatch.setattr(process_title, "tick", lambda: None)
    spec = build_function_spec(unique_tid)
    task = task_factory(spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    log = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    inbox.write("hello")
    drive_task_until(task, lambda: task.taskspec.state.status == "completed")
    assert task.taskspec.state.error is None
    assert task.taskspec.state.return_code == 0
    assert task.taskspec.state.process_title_error == "native setter failed"
    events = [json.loads(value) for value in drain_queue(log)]
    completed = [event for event in events if event.get("event") == "work_completed"]
    assert completed
    assert (
        completed[-1]["taskspec"]["state"]["process_title_error"]
        == "native setter failed"
    )


def test_off_owner_title_does_not_call_native_setter(
    task_factory, unique_tid, monkeypatch
):
    task = task_factory(build_function_spec(unique_tid, enable_title=False))
    task.process_once()
    calls = []
    monkeypatch.setattr(
        process_title,
        "set_process_title",
        lambda title: calls.append((threading.current_thread(), title)),
    )
    monkeypatch.setattr(process_title, "tick", lambda: "activation failed")
    task.enable_process_title = True
    worker = threading.Thread(target=task._set_activity, args=("working",))
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert calls == []
    task.process_once()
    assert calls == []
    assert task.taskspec.state.process_title_error == "activation failed"
    monkeypatch.setattr(process_title, "tick", lambda: None)
    task.process_once()
    assert task.taskspec.state.process_title_error == "activation failed"


def test_pause_title_survives_owner_turn(
    broker_env, task_factory, unique_tid, monkeypatch
):
    _, make_queue = broker_env
    task = task_factory(build_function_spec(unique_tid, enable_title=False))
    task.taskspec.mark_running(pid=task._task_pid)
    calls = []
    monkeypatch.setattr(process_title, "set_process_title", calls.append)
    monkeypatch.setattr(process_title, "tick", lambda: None)
    task.enable_process_title = True
    make_queue(task.taskspec.io.control["ctrl_in"]).write(
        encode_control_message(CONTROL_PAUSE)
    )
    task.process_once()
    assert task._paused
    assert task.taskspec.state.status == "running"
    assert len(calls) == 1
    assert ":paused" in calls[0]
    task.process_once()
    assert len(calls) == 1


def test_manager_drain_title_survives_owner_turn(broker_env, unique_tid, monkeypatch):
    db_path, _ = broker_env
    spec = build_function_spec(
        unique_tid,
        enable_title=False,
        function_target="weft.core.manager:Manager",
        name="manager",
    )
    manager = Manager(db_path, spec)
    try:
        manager.taskspec.mark_running(pid=manager._task_pid)
        calls = []
        monkeypatch.setattr(process_title, "set_process_title", calls.append)
        monkeypatch.setattr(process_title, "tick", lambda: None)
        monkeypatch.setattr(
            manager,
            "_process_reactor_turn",
            lambda: manager._begin_leadership_drain(
                leader_tid=str(int(unique_tid) - 1)
            ),
        )
        manager.enable_process_title = True
        manager.process_once()
        assert manager._draining
        assert manager.taskspec.state.status == "running"
        assert len(calls) == 1
        assert ":manager:draining" in calls[0]
        manager.process_once()
        assert len(calls) == 1
    finally:
        manager.stop(join=False)
        manager.cleanup()


def test_every_defined_title_status_fits_the_handoff_capacity(
    task_factory, unique_tid: str
) -> None:
    """The title status bound derives from the defined status sets.

    Verifies:
    - the TaskSpec status Literal and TASK_LIFECYCLE_STATUS_VALUES agree
    - at the widest context, name, and details, the longest defined status
      formats to exactly PROCESS_TITLE_MAX_LENGTH, which the native handoff
      capacity exceeds by the NUL reservation

    Spec: [OBS.4]
    """
    literal = set(get_args(StateSection.model_fields["status"].annotation))
    assert literal == TASK_LIFECYCLE_STATUS_VALUES

    spec = build_function_spec(
        unique_tid,
        enable_title=False,
        name="n" * PROCESS_TITLE_NAME_LENGTH,
        context_path="/tmp/" + "c" * PROCESS_TITLE_CONTEXT_LENGTH,
    )
    task = task_factory(spec)
    details = "d" * PROCESS_TITLE_DETAILS_LENGTH
    titles = {
        status: task._format_process_title(status, details)
        for status in PROCESS_TITLE_STATUSES
    }
    assert max(len(title) for title in titles.values()) == PROCESS_TITLE_MAX_LENGTH
    assert PROCESS_TITLE_MAX_LENGTH < PROCESS_TITLE_HANDOFF_LENGTH
