"""Tests for task stop/kill helpers against launched task processes."""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import replace
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import Any, Literal

import psutil
import pytest

from simplebroker import Config, Queue
from tests.helpers.test_backend import prepare_project_root
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import CONTROL_KILL
from weft._exceptions import CommandUsageError, ControlRejected, TaskNotFound
from weft.commands import events as event_cmd
from weft.commands import tasks as task_cmd
from weft.commands.control_convergence import (
    ControlConvergenceAction,
    ControlConvergenceEvidence,
    ControlConvergenceState,
    control_convergence_machine,
    reduce_control_convergence,
)
from weft.commands.types import (
    TaskControlFailure,
    TaskControlResult,
    TaskEvent,
    TaskPingResult,
    TaskSnapshot,
)
from weft.context import WeftContext, build_context
from weft.core import task_evidence
from weft.core.control_messages import encode_control_message
from weft.core.control_probe import ControlProbeResult, MatchedPong
from weft.core.launcher import launch_task_process
from weft.core.monitor.store import open_monitor_store
from weft.core.task_evidence import TaskEvidenceSnapshot
from weft.core.task_state import task_state_queue_name
from weft.core.tasks import Consumer
from weft.core.taskspec import IOSection, SpecSection, StateSection, TaskSpec
from weft.ext import RunnerHandle
from weft.helpers import (
    iter_queue_json_entries,
    pid_is_live,
    process_create_time,
    tid_short_form,
)

pytestmark = [pytest.mark.shared]


@pytest.fixture(autouse=True)
def isolated_command_context(weft_harness: WeftTestHarness) -> WeftContext:
    """Real context resolution must never open the caller's working database.

    Control calls may be mocked while their context and TID preflight still
    use the real broker. Share the standard harness for the whole module.
    """
    return weft_harness.context


def _public_task_snapshot(tid: str, *, name: str = "demo") -> TaskSnapshot:
    return TaskSnapshot(
        tid=tid,
        name=name,
        status="running",
        return_code=None,
        started_at=None,
        completed_at=None,
        error=None,
        runtime_handle=None,
        metadata={},
    )


def test_canonical_task_list_and_status_return_public_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tid = "1777000000000000123"
    snapshot = _public_task_snapshot(tid)
    monkeypatch.setattr(task_cmd, "list_task_snapshots", lambda **kwargs: [snapshot])
    monkeypatch.setattr(task_cmd, "task_snapshot", lambda *args, **kwargs: snapshot)

    assert task_cmd.cmd_task_list(status="running", all=True) == (snapshot,)
    assert task_cmd.cmd_task_status(tid) is snapshot


def test_canonical_task_status_enriches_process_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tid = "1777000000000000124"
    snapshot = _public_task_snapshot(tid)
    monkeypatch.setattr(task_cmd, "task_snapshot", lambda *args, **kwargs: snapshot)
    monkeypatch.setattr(
        task_cmd,
        "_process_fields_for_tid",
        lambda *args, **kwargs: {
            "host_pids": (11, 12),
            "managed_pids": (11, 12),
            "live_managed_pids": (12,),
        },
    )

    assert task_cmd.cmd_task_status(tid, process=True) == replace(
        snapshot,
        host_pids=(11, 12),
        managed_pids=(11, 12),
        live_managed_pids=(12,),
    )


def test_process_enrichment_uses_scoped_host_pids_and_live_subset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = build_context(spec_context=Path.cwd())
    handle = type(
        "FakeHandle",
        (),
        {"scoped_host_pids": lambda self: (21, 22)},
    )()
    monkeypatch.setattr(
        task_cmd,
        "mapping_for_tid",
        lambda *args, **kwargs: {"runtime_handle": {"runner": "host"}},
    )
    monkeypatch.setattr(
        task_cmd.system_cmd,
        "_runtime_handle_from_mapping",
        lambda mapping: handle,
    )
    monkeypatch.setattr(task_cmd, "pid_is_live", lambda pid: pid == 22)

    assert task_cmd._process_fields_for_tid(ctx, "1777000000000000124") == {
        "host_pids": (21, 22),
        "managed_pids": (21, 22),
        "live_managed_pids": (22,),
    }


def test_canonical_task_status_watch_returns_typed_event_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tid = "1777000000000000124"
    snapshot = _public_task_snapshot(tid)
    event = TaskEvent(
        tid=tid, event_type="running", timestamp=123, payload={"status": "running"}
    )
    events: Iterator[TaskEvent] = iter((event,))
    monkeypatch.setattr(task_cmd, "task_snapshot", lambda *args, **kwargs: snapshot)
    monkeypatch.setattr(event_cmd, "follow_task_events", lambda *args, **kwargs: events)

    stream = task_cmd.cmd_task_status(tid, watch=True)
    assert not isinstance(stream, TaskSnapshot)
    assert stream is not events
    assert list(stream) == [event]


def test_canonical_task_ping_returns_structured_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tid = "1777000000000000125"
    snapshot = _public_task_snapshot(tid)
    monkeypatch.setattr(
        task_cmd,
        "task_ping",
        lambda *args, **kwargs: {
            "timed_out": False,
            "error": None,
            "observed_at": 99,
            "pong": {"status": "running"},
        },
    )
    monkeypatch.setattr(task_cmd, "task_snapshot", lambda *args, **kwargs: snapshot)

    assert task_cmd.cmd_task_ping(tid) == TaskPingResult(
        tid=tid,
        acknowledged=True,
        timed_out=False,
        error=None,
        observed_at=99,
        pong={"status": "running"},
        snapshot=snapshot,
    )


@pytest.mark.parametrize(
    ("command", "expected"), [("stop", "stop_task"), ("kill", "kill_task")]
)
def test_canonical_task_control_returns_selected_ids_and_snapshots(
    command: Literal["stop", "kill"],
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tids = ("1777000000000000126", "1777000000000000127")
    snapshots = [
        _public_task_snapshot(tid, name=f"job-{index}")
        for index, tid in enumerate(tids)
    ]
    calls: list[str] = []
    monkeypatch.setattr(task_cmd, "list_task_snapshots", lambda **kwargs: snapshots)
    monkeypatch.setattr(task_cmd, expected, lambda tid, **kwargs: calls.append(tid))
    monkeypatch.setattr(
        task_cmd,
        "task_snapshot",
        lambda tid, **kwargs: replace(_public_task_snapshot(tid), status="cancelled"),
    )

    function = task_cmd.cmd_task_stop if command == "stop" else task_cmd.cmd_task_kill
    result = function(all=True)

    assert calls == list(tids)
    assert result == TaskControlResult(
        command=command,
        requested=tids,
        accepted=tids,
        failures=(),
        snapshots=tuple(
            replace(_public_task_snapshot(tid), status="cancelled") for tid in tids
        ),
    )


def test_canonical_task_control_attempts_every_selected_task_and_reports_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tids = ("1777000000000000126", "1777000000000000127")
    snapshots = tuple(_public_task_snapshot(tid) for tid in tids)
    calls: list[str] = []
    monkeypatch.setattr(task_cmd, "list_task_snapshots", lambda **kwargs: snapshots)

    def stop_task(tid: str, **kwargs: object) -> None:
        calls.append(tid)
        if tid == tids[0]:
            raise ControlRejected("control was rejected")

    monkeypatch.setattr(task_cmd, "stop_task", stop_task)
    monkeypatch.setattr(
        task_cmd,
        "task_snapshot",
        lambda tid, **kwargs: replace(_public_task_snapshot(tid), status="cancelled"),
    )

    result = task_cmd.cmd_task_stop(all=True)

    assert calls == list(tids)
    assert result.requested == tids
    assert result.accepted == (tids[1],)
    assert result.failures == (
        TaskControlFailure(
            tid=tids[0],
            error="control was rejected",
            error_type="ControlRejected",
        ),
    )


def test_canonical_task_control_rejects_mixed_explicit_and_sweep_scope(
    weft_harness: WeftTestHarness,
) -> None:
    with pytest.raises(CommandUsageError, match="cannot be combined"):
        task_cmd._task_control_result(
            "stop",
            None,
            tids=("1777000000000000126",),
            all_tasks=True,
            pattern=None,
            context_path=None,
            runtime_context=weft_harness.context,
        )


def test_stop_task_rejects_unknown_task_before_sending_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[str] = []
    monkeypatch.setattr(task_cmd, "resolve_full_tid", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(task_cmd, "task_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(task_cmd, "mapping_for_tid", lambda *_args, **_kwargs: None)

    def _record_sent(tids: Sequence[str], **_kwargs: object) -> int:
        sent.extend(tids)
        return len(tids)

    monkeypatch.setattr(
        task_cmd,
        "stop_tasks",
        _record_sent,
    )

    with pytest.raises(TaskNotFound, match="not found"):
        task_cmd.stop_task("1777000000000000999")

    assert sent == []


def test_stop_task_rejects_terminal_task_without_sending_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tid = "1777000000000000999"
    sent: list[str] = []
    monkeypatch.setattr(task_cmd, "resolve_full_tid", lambda *_args, **_kwargs: tid)
    monkeypatch.setattr(
        task_cmd,
        "task_status",
        lambda *_args, **_kwargs: replace(
            _public_task_snapshot(tid), status="completed"
        ),
    )
    monkeypatch.setattr(task_cmd, "mapping_for_tid", lambda *_args, **_kwargs: {})

    def _record_sent(tids: Sequence[str], **_kwargs: object) -> int:
        sent.extend(tids)
        return len(tids)

    monkeypatch.setattr(
        task_cmd,
        "stop_tasks",
        _record_sent,
    )

    with pytest.raises(ControlRejected, match=f"Task {tid} already completed"):
        task_cmd.stop_task(tid)

    assert sent == []


def test_kill_task_allows_terminal_known_task_to_reach_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tid = "1777000000000000999"
    sent: list[str] = []
    monkeypatch.setattr(task_cmd, "resolve_full_tid", lambda *_args, **_kwargs: tid)
    monkeypatch.setattr(
        task_cmd,
        "task_status",
        lambda *_args, **_kwargs: replace(
            _public_task_snapshot(tid), status="completed"
        ),
    )
    monkeypatch.setattr(task_cmd, "mapping_for_tid", lambda *_args, **_kwargs: {})

    def _record_sent(tids: Sequence[str], **_kwargs: object) -> int:
        sent.extend(tids)
        return len(tids)

    monkeypatch.setattr(
        task_cmd,
        "kill_tasks",
        _record_sent,
    )

    task_cmd.kill_task(tid)

    assert sent == [tid]


def test_kill_task_reports_terminal_task_without_runtime_residue_honestly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tid = "1777000000000000999"
    monkeypatch.setattr(task_cmd, "resolve_full_tid", lambda *_args, **_kwargs: tid)
    monkeypatch.setattr(
        task_cmd,
        "task_status",
        lambda *_args, **_kwargs: replace(
            _public_task_snapshot(tid), status="completed"
        ),
    )
    monkeypatch.setattr(task_cmd, "mapping_for_tid", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(task_cmd, "kill_tasks", lambda _tids, **_kwargs: 0)

    with pytest.raises(
        ControlRejected,
        match=f"Task {tid} already completed; no live runtime was found to kill",
    ):
        task_cmd.kill_task(tid)


@pytest.mark.parametrize("operation_name", ["stop_task", "kill_task"])
def test_single_task_control_precheck_preserves_live_context(
    monkeypatch: pytest.MonkeyPatch,
    weft_harness: WeftTestHarness,
    operation_name: str,
) -> None:
    tid = "1777000000000000999"
    observed: dict[str, object] = {}
    monkeypatch.setattr(task_cmd, "resolve_full_tid", lambda *_args, **_kwargs: tid)

    def fake_status(*_args: object, **kwargs: object) -> None:
        observed.update(kwargs)

    monkeypatch.setattr(task_cmd, "task_status", fake_status)
    monkeypatch.setattr(task_cmd, "mapping_for_tid", lambda *_args, **_kwargs: None)

    with pytest.raises(TaskNotFound, match="not found"):
        getattr(task_cmd, operation_name)(tid, context=weft_harness.context)

    assert observed["context"] is weft_harness.context


def test_terminal_completion_race_in_stop_sweep_is_reported_as_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tid = "1777000000000000999"
    selected = replace(_public_task_snapshot(tid), status="running")
    monkeypatch.setattr(task_cmd, "list_task_snapshots", lambda **_kwargs: [selected])
    monkeypatch.setattr(task_cmd, "resolve_full_tid", lambda *_args, **_kwargs: tid)
    monkeypatch.setattr(
        task_cmd,
        "task_status",
        lambda *_args, **_kwargs: replace(selected, status="completed"),
    )
    monkeypatch.setattr(task_cmd, "mapping_for_tid", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(task_cmd, "stop_tasks", lambda tids, **_kwargs: len(tids))
    monkeypatch.setattr(
        task_cmd,
        "task_snapshot",
        lambda *_args, **_kwargs: replace(selected, status="completed"),
    )

    with pytest.raises(ControlRejected, match=f"Task {tid} already completed") as exc:
        task_cmd.cmd_task_stop(all=True)

    assert exc.value.failures == (
        TaskControlFailure(
            tid=tid,
            error=f"Task {tid} already completed",
            error_type="ControlRejected",
        ),
    )


def test_terminal_snapshot_status_fallback_preserves_live_context(
    monkeypatch: pytest.MonkeyPatch,
    weft_harness: WeftTestHarness,
) -> None:
    tid = "1777000000000000999"
    observed: dict[str, object] = {}
    monkeypatch.setattr(
        task_cmd.task_evidence,
        "known_tid_evidence",
        lambda *_args, **_kwargs: None,
    )

    def fake_status(*_args: object, **kwargs: object) -> None:
        observed.update(kwargs)

    monkeypatch.setattr(task_cmd, "_task_status", fake_status)

    task_cmd.task_terminal_snapshot(tid, context=weft_harness.context)

    assert observed["context"] is weft_harness.context


def test_resolve_tid_preserves_live_context(
    monkeypatch: pytest.MonkeyPatch,
    weft_harness: WeftTestHarness,
) -> None:
    observed: dict[str, object] = {}

    def fake_task_tid(**kwargs: object) -> str:
        observed.update(kwargs)
        return "1777000000000000999"

    monkeypatch.setattr(task_cmd, "task_tid", fake_task_tid)

    task_cmd.resolve_tid(tid="00999", context=weft_harness.context)

    assert observed["context"] is weft_harness.context


def test_canonical_task_control_raises_with_all_failures_when_none_are_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tids = ("1777000000000000126", "1777000000000000127")
    monkeypatch.setattr(
        task_cmd,
        "stop_task",
        lambda tid, **kwargs: (_ for _ in ()).throw(ControlRejected(f"no {tid}")),
    )

    with pytest.raises(ControlRejected, match=tids[0]) as exc_info:
        task_cmd._task_control_result(
            "stop",
            None,
            tids=tids,
            all_tasks=False,
            pattern=None,
            context_path=None,
        )

    assert exc_info.value.failures == tuple(
        TaskControlFailure(tid=tid, error=f"no {tid}", error_type="ControlRejected")
        for tid in tids
    )


def test_canonical_task_control_empty_explicit_selection_is_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        task_cmd,
        "stop_task",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected")),
    )

    result = task_cmd._task_control_result(
        "stop",
        None,
        tids=(),
        all_tasks=False,
        pattern=None,
        context_path=None,
    )

    assert result.requested == ()
    assert result.accepted == ()
    assert result.failures == ()


def test_canonical_task_control_records_invalid_explicit_tid_and_continues(
    monkeypatch: pytest.MonkeyPatch,
    weft_harness: WeftTestHarness,
) -> None:
    attempted: list[str] = []
    valid_tid = "1777000000000000999"
    monkeypatch.setattr(
        task_cmd,
        "stop_task",
        lambda tid, **_kwargs: attempted.append(tid),
    )
    monkeypatch.setattr(task_cmd, "task_snapshot", lambda *_args, **_kwargs: None)

    result = task_cmd._task_control_result(
        "stop",
        None,
        tids=("not-a-tid", valid_tid),
        all_tasks=False,
        pattern=None,
        context_path=None,
        runtime_context=weft_harness.context,
    )

    assert attempted == [valid_tid]
    assert result.requested == ("not-a-tid", valid_tid)
    assert result.accepted == (valid_tid,)
    assert result.failures[0].tid == "not-a-tid"
    assert result.failures[0].error_type == "InvalidTID"


def test_canonical_task_control_preserves_duplicate_invalid_requests(
    monkeypatch: pytest.MonkeyPatch,
    weft_harness: WeftTestHarness,
) -> None:
    monkeypatch.setattr(
        task_cmd,
        "stop_task",
        lambda *_args, **_kwargs: None,
    )
    with pytest.raises(ControlRejected) as exc_info:
        task_cmd._task_control_result(
            "stop",
            None,
            tids=("bad", "bad"),
            all_tasks=False,
            pattern=None,
            context_path=None,
            runtime_context=weft_harness.context,
        )

    assert [failure.tid for failure in exc_info.value.failures] == ["bad", "bad"]


def test_canonical_task_control_pattern_selects_matching_active_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tids = ("1777000000000000126", "1777000000000000127")
    snapshots = (
        _public_task_snapshot(tids[0], name="job-one"),
        _public_task_snapshot(tids[1], name="other"),
    )
    calls: list[str] = []
    monkeypatch.setattr(task_cmd, "list_task_snapshots", lambda **kwargs: snapshots)
    monkeypatch.setattr(task_cmd, "stop_task", lambda tid, **kwargs: calls.append(tid))
    monkeypatch.setattr(task_cmd, "task_snapshot", lambda *args, **kwargs: None)

    result = task_cmd.cmd_task_stop(pattern="job-*")

    assert calls == [tids[0]]
    assert result.requested == (tids[0],)
    assert result.accepted == (tids[0],)


def test_canonical_task_tid_returns_full_tid_and_rejects_ambiguous_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from weft._exceptions import CommandUsageError

    full_tid = "1777000000000000128"
    monkeypatch.setattr(task_cmd, "resolve_tid", lambda **kwargs: full_tid)

    assert task_cmd.cmd_task_tid(tid="00128") == full_tid
    with pytest.raises(CommandUsageError, match="exactly one"):
        task_cmd.cmd_task_tid(tid="00128", pid=12)


class MonitorStoreReadFailure(Exception):
    """Ordinary dynamic monitor-store failure at the status fallback boundary."""


class MonitorStoreReadSignal(BaseException):
    """Fatal monitor-store signal that the status fallback must not contain."""


def test_monitor_store_snapshot_reports_ordinary_dynamic_store_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ordinary store failure becomes the existing bounded unknown snapshot."""

    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    tid = "1777000000000000789"

    def fail_open(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise MonitorStoreReadFailure("store detail")

    monkeypatch.setattr(task_cmd, "open_monitor_store", fail_open)

    snapshot = task_cmd._monitor_store_task_snapshot(
        context,
        tid,
        include_terminal=True,
    )

    assert snapshot is not None
    assert snapshot.tid == tid
    assert snapshot.status == "unknown"
    assert snapshot.event == "monitor_store_unavailable"
    assert snapshot.reconciliation == {
        "classification": "monitor_store_unavailable",
        "reason": "store_read_failed",
    }
    assert snapshot.error == "monitor store unavailable: store detail"


def test_monitor_store_snapshot_treats_all_tables_absent_as_no_evidence(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)

    snapshot = task_cmd._monitor_store_task_snapshot(
        context,
        "1777000000000000791",
        include_terminal=True,
    )

    assert snapshot is None


def test_monitor_store_snapshot_treats_partial_schema_as_unavailable(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    with context.broker() as broker, broker.sidecar(transaction=True) as session:
        session.run(
            "CREATE TABLE weft_monitor_meta ("
            "key TEXT PRIMARY KEY, value_json TEXT NOT NULL, "
            "updated_at_ns INTEGER NOT NULL)"
        )

    snapshot = task_cmd._monitor_store_task_snapshot(
        context,
        "1777000000000000792",
        include_terminal=True,
    )

    assert snapshot is not None
    assert snapshot.event == "monitor_store_unavailable"
    assert snapshot.reconciliation == {
        "classification": "monitor_store_unavailable",
        "reason": "store_read_failed",
    }


def test_monitor_store_snapshot_treats_unsupported_schema_as_unavailable(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    open_monitor_store(context).ensure_schema()
    with context.broker() as broker, broker.sidecar(transaction=True) as session:
        session.run(
            "UPDATE weft_monitor_meta SET value_json = ? WHERE key = ?",
            ('{"version":999}', "schema_version"),
        )

    snapshot = task_cmd._monitor_store_task_snapshot(
        context,
        "1777000000000000793",
        include_terminal=True,
    )

    assert snapshot is not None
    assert snapshot.event == "monitor_store_unavailable"
    assert snapshot.reconciliation == {
        "classification": "monitor_store_unavailable",
        "reason": "store_read_failed",
    }


def test_monitor_store_snapshot_treats_complete_unversioned_schema_as_unavailable(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    open_monitor_store(context).ensure_schema()
    with context.broker() as broker, broker.sidecar(transaction=True) as session:
        session.run(
            "DELETE FROM weft_monitor_meta WHERE key = ?",
            ("schema_version",),
        )

    snapshot = task_cmd._monitor_store_task_snapshot(
        context,
        "1777000000000000794",
        include_terminal=True,
    )

    assert snapshot is not None
    assert snapshot.event == "monitor_store_unavailable"
    assert snapshot.reconciliation == {
        "classification": "monitor_store_unavailable",
        "reason": "store_read_failed",
    }


def test_monitor_store_snapshot_propagates_fatal_dynamic_store_signal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The store fallback contains ordinary failures, not BaseException signals."""

    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    signal = MonitorStoreReadSignal()

    def fail_open(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise signal

    monkeypatch.setattr(task_cmd, "open_monitor_store", fail_open)

    with pytest.raises(MonitorStoreReadSignal) as exc_info:
        task_cmd._monitor_store_task_snapshot(
            context,
            "1777000000000000790",
            include_terminal=True,
        )

    assert exc_info.value is signal


@pytest.mark.parametrize(
    ("timeout", "expected"),
    [
        (None, None),
        (-5.0, 100.0),
        (0.0, 100.0),
        (5.0, 105.0),
    ],
)
def test_deadline_from_timeout_clamps_at_zero(
    monkeypatch: pytest.MonkeyPatch,
    timeout: float | None,
    expected: float | None,
) -> None:
    monkeypatch.setattr(task_cmd.time, "monotonic", lambda: 100.0)

    assert task_cmd._deadline_from_timeout(timeout) == expected


@pytest.mark.parametrize(
    ("deadline", "remaining", "expired"),
    [
        (None, None, False),
        (99.0, 0.0, True),
        (100.0, 0.0, True),
        (101.0, 1.0, False),
    ],
)
def test_deadline_helpers_share_exact_expiry_boundary(
    monkeypatch: pytest.MonkeyPatch,
    deadline: float | None,
    remaining: float | None,
    expired: bool,
) -> None:
    monkeypatch.setattr(task_cmd.time, "monotonic", lambda: 100.0)

    assert task_cmd._remaining_timeout(deadline) == remaining
    assert task_cmd._deadline_expired(deadline) is expired


@pytest.mark.parametrize(
    ("overall_deadline", "public_deadline", "kill_deadline", "expected"),
    [
        (15.0, None, None, 2.0),
        (15.0, 11.0, None, 2.0),
        (15.0, 9.0, None, 2.0),
        (9.0, 11.5, None, 1.5),
        (9.0, 9.0, None, None),
        (9.0, None, None, None),
        (15.0, 11.0, 10.5, 0.5),
        (15.0, None, 9.0, None),
        (9.0, 11.5, 10.5, 1.5),
        (9.0, 9.0, 10.5, None),
        (9.0, 11.5, 9.0, 1.5),
        (9.0, None, 9.0, None),
    ],
)
def test_control_surface_wait_timeout_preserves_three_clock_precedence(
    overall_deadline: float,
    public_deadline: float | None,
    kill_deadline: float | None,
    expected: float | None,
) -> None:
    assert (
        task_cmd._control_surface_wait_timeout(
            overall_deadline=overall_deadline,
            public_signal_deadline=public_deadline,
            kill_ack_deadline=kill_deadline,
            now=10.0,
            interval=2.0,
        )
        == expected
    )


class _FakeQueueChangeMonitor:
    def __init__(
        self, queues: Sequence[Queue], *, config: Config | None = None
    ) -> None:
        del config
        self.queue_names = [queue.name for queue in queues]
        self.queue_persistence = [
            (
                queue.name,
                getattr(queue, "persistent", getattr(queue, "_persistent", None)),
            )
            for queue in queues
        ]
        self.wait_calls: list[float | None] = []
        self.close_calls = 0

    def wait(self, timeout: float | None) -> bool:
        self.wait_calls.append(timeout)
        return False

    def close(self) -> None:
        self.close_calls += 1


def _runtime_handle(
    runner: str,
    runtime_id: str,
    *,
    kind: str = "process",
    authority: str = "host-pid",
    host_pids: list[int] | None = None,
    observations: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    observed = dict(observations or {})
    if host_pids is not None:
        observed["host_pids"] = host_pids
    return {
        "runner": runner,
        "kind": kind,
        "id": runtime_id,
        "control": {"authority": authority},
        "observations": observed,
        "metadata": metadata or {},
    }


def _make_taskspec(
    tid: str,
    *,
    function_target: str = "tests.tasks.sample_targets:simulate_work",
) -> TaskSpec:
    return TaskSpec(
        tid=tid,
        name="task-func",
        spec=SpecSection(
            type="function",
            function_target=function_target,
        ),
        io=IOSection(
            inputs={"inbox": f"T{tid}.inbox"},
            outputs={"outbox": f"T{tid}.outbox"},
            control={"ctrl_in": f"T{tid}.ctrl_in", "ctrl_out": f"T{tid}.ctrl_out"},
        ),
        state=StateSection(),
    )


def _write_logged_pipeline_task(ctx: WeftContext, tid: str) -> None:
    log_queue = ctx.queue("weft.log.tasks", persistent=False)
    log_queue.write(
        json.dumps(
            {
                "event": "task_started",
                "status": "running",
                "tid": tid,
                "taskspec": {
                    "tid": tid,
                    "name": "demo-pipeline",
                    "spec": {
                        "type": "function",
                        "function_target": "weft.core.tasks.pipeline:runtime",
                    },
                    "io": {
                        "outputs": {"outbox": f"P{tid}.outbox"},
                        "control": {
                            "ctrl_in": f"P{tid}.ctrl_in",
                            "ctrl_out": f"P{tid}.ctrl_out",
                        },
                    },
                    "state": {"status": "running"},
                    "metadata": {
                        "role": "pipeline",
                        "_weft_pipeline_runtime": {
                            "queues": {"status": f"P{tid}.status"},
                        },
                    },
                },
            }
        )
    )


def test_terminal_snapshot_reads_outbox_without_consuming(tmp_path: Path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    outbox = ctx.queue(f"T{tid}.outbox", persistent=True)
    outbox.write(json.dumps({"ok": True}))

    snapshot = task_cmd.task_terminal_snapshot(tid, context=ctx)

    assert snapshot.status == "completed"
    assert snapshot.source == "outbox"
    assert snapshot.value == {"ok": True}
    assert snapshot.terminal is True
    assert len(snapshot.ack_targets) == 1
    assert outbox.peek_one() is not None


def test_terminal_snapshot_reads_only_typed_terminal_ctrl_out(tmp_path: Path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    ctrl_out = ctx.queue(f"T{tid}.ctrl_out", persistent=False)
    ctrl_out.write(json.dumps({"command": "PING", "status": "ok", "tid": tid}))
    ctrl_out.write(json.dumps({"type": "stream", "stream": "stderr", "data": "x"}))
    ctrl_out.write(
        json.dumps(
            {
                "type": "terminal",
                "source": "task",
                "tid": tid,
                "status": "failed",
                "error": "boom",
                "return_code": 1,
            }
        )
    )

    snapshot = task_cmd.task_terminal_snapshot(tid, context=ctx)

    assert snapshot.status == "failed"
    assert snapshot.source == "ctrl_out"
    assert snapshot.error == "boom"
    assert snapshot.return_code == 1
    assert len(snapshot.ack_targets) == 1
    assert ctrl_out.peek_many(limit=3)


def test_ack_terminal_snapshot_deletes_exact_message_only(tmp_path: Path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    ctrl_out = ctx.queue(f"T{tid}.ctrl_out", persistent=False)
    ctrl_out.write(json.dumps({"command": "PING", "status": "ok", "tid": tid}))
    ctrl_out.write(
        json.dumps(
            {
                "type": "terminal",
                "source": "task",
                "tid": tid,
                "status": "failed",
            }
        )
    )
    ctrl_out.write(json.dumps({"command": "STATUS", "status": "ok", "tid": tid}))

    snapshot = task_cmd.task_terminal_snapshot(tid, context=ctx)

    assert task_cmd.ack_terminal_snapshot(snapshot, context=ctx) is True
    remaining = ctrl_out.peek_many(limit=10)
    assert len(remaining) == 2
    assert all("terminal" not in message for message in remaining)


def _live_evidence(tid: str, status: str = "running") -> TaskEvidenceSnapshot:
    return TaskEvidenceSnapshot(
        tid=tid,
        status=status,
        classification="live_runtime",
        source="runtime",
        terminal=False,
    )


def _terminal_evidence(tid: str) -> TaskEvidenceSnapshot:
    return TaskEvidenceSnapshot(
        tid=tid,
        status="completed",
        classification="terminal_outbox",
        source="outbox",
        terminal=True,
        value={"ok": True},
    )


@pytest.mark.timeout(10)
@pytest.mark.parametrize("live_status", ["running", "pending"])
def test_terminal_snapshot_positive_timeout_stops_at_deadline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    live_status: str,
) -> None:
    """A positive timeout bounds continuing nonterminal evidence.

    Verifies:
    - The evidence branch exits at the deadline instead of polling forever
    - Expiry returns the latest honest nonterminal snapshot, not a timeout
    """
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    calls: list[str] = []

    def _always_live(_ctx: Any, *, tid: str, **_kwargs: Any) -> TaskEvidenceSnapshot:
        calls.append(tid)
        return _live_evidence(tid, live_status)

    monkeypatch.setattr(task_evidence, "known_tid_evidence", _always_live)

    started = time.monotonic()
    snapshot = task_cmd.task_terminal_snapshot(tid, timeout=0.05, context=ctx)
    elapsed = time.monotonic() - started

    # 0.05 s budget + at most one TASK_EVIDENCE_POLL_INTERVAL sleep + slack;
    # the mark.timeout above turns a regression into a failure, not a hang.
    assert elapsed < 1.0
    assert snapshot.status == live_status
    assert snapshot.terminal is False
    assert snapshot.ack_targets == ()
    assert calls, "evidence was consulted"


def test_terminal_snapshot_zero_timeout_observes_live_evidence_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """timeout=0 keeps its single-observation behavior."""
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    calls: list[str] = []

    def _always_live(_ctx: Any, *, tid: str, **_kwargs: Any) -> TaskEvidenceSnapshot:
        calls.append(tid)
        return _live_evidence(tid)

    monkeypatch.setattr(task_evidence, "known_tid_evidence", _always_live)

    snapshot = task_cmd.task_terminal_snapshot(tid, context=ctx)

    assert snapshot.status == "running"
    assert snapshot.terminal is False
    assert calls == [tid]


def test_terminal_snapshot_returns_terminal_evidence_before_deadline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Polling still converges on terminal evidence within the budget."""
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    observations: list[str] = []

    def _live_then_terminal(
        _ctx: Any,
        *,
        tid: str,
        **_kwargs: Any,
    ) -> TaskEvidenceSnapshot:
        observations.append(tid)
        if len(observations) < 2:
            return _live_evidence(tid)
        return _terminal_evidence(tid)

    monkeypatch.setattr(task_evidence, "known_tid_evidence", _live_then_terminal)

    snapshot = task_cmd.task_terminal_snapshot(tid, timeout=5.0, context=ctx)

    assert snapshot.status == "completed"
    assert snapshot.terminal is True
    assert snapshot.value == {"ok": True}
    assert len(observations) == 2


def test_task_ping_returns_probe_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    calls: list[dict[str, Any]] = []

    def _fake_probe(
        ctx_arg: WeftContext,
        *,
        tid: str,
        ctrl_in_name: str,
        timeout: float,
    ) -> ControlProbeResult:
        calls.append(
            {
                "ctx": ctx_arg,
                "tid": tid,
                "ctrl_in_name": ctrl_in_name,
                "timeout": timeout,
            }
        )
        payload = {
            "command": "PING",
            "status": "ok",
            "message": "PONG",
            "tid": tid,
            "request_id": "req-1",
            "task_status": "running",
            "extended": {"depth": 2},
        }
        return ControlProbeResult(
            request_id="req-1",
            matched=MatchedPong(
                payload=payload,
                observed_at=123,
                request_id="req-1",
            ),
        )

    monkeypatch.setattr(task_cmd, "send_keyed_ping_probe", _fake_probe)

    payload = task_cmd.task_ping(tid, timeout=0.25, context=ctx)

    assert payload == {
        "timed_out": False,
        "error": None,
        "observed_at": 123,
        "pong": {
            "command": "PING",
            "status": "ok",
            "message": "PONG",
            "tid": tid,
            "request_id": "req-1",
            "task_status": "running",
            "extended": {"depth": 2},
        },
    }
    assert calls == [
        {
            "ctx": ctx,
            "tid": tid,
            "ctrl_in_name": f"T{tid}.ctrl_in",
            "timeout": 0.25,
        }
    ]


def test_task_ping_reports_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    monkeypatch.setattr(
        task_cmd,
        "send_keyed_ping_probe",
        lambda *_args, **_kwargs: ControlProbeResult(
            request_id="req-timeout",
            timed_out=True,
        ),
    )

    payload = task_cmd.task_ping(tid, timeout=0.0, context=ctx)

    assert payload == {
        "timed_out": True,
        "error": None,
        "observed_at": None,
        "pong": None,
    }


def test_control_convergence_machine_covers_all_transitions() -> None:
    cases: tuple[
        tuple[
            str,
            ControlConvergenceState,
            ControlConvergenceEvidence,
            ControlConvergenceAction,
            ControlConvergenceState,
        ],
        ...,
    ] = (
        (
            "wait for first evidence",
            "command_sent",
            ControlConvergenceEvidence(command=CONTROL_KILL),
            "wait",
            "command_sent",
        ),
        (
            "kill terminal after command",
            "command_sent",
            ControlConvergenceEvidence(
                command=CONTROL_KILL,
                terminal_status="killed",
            ),
            "accept_terminal",
            "terminal_observed",
        ),
        (
            "stop terminal after ack",
            "accepted",
            ControlConvergenceEvidence(
                command="STOP",
                terminal_status="cancelled",
            ),
            "accept_terminal",
            "terminal_observed",
        ),
        (
            "kill terminal after runner escalation",
            "escalating_runner",
            ControlConvergenceEvidence(
                command=CONTROL_KILL,
                terminal_status="killed",
                runner_fallback_attempted=True,
            ),
            "accept_terminal",
            "terminal_observed",
        ),
        (
            "kill terminal after host escalation",
            "escalating_host",
            ControlConvergenceEvidence(
                command=CONTROL_KILL,
                terminal_status="killed",
                runner_fallback_attempted=True,
                host_fallback_attempted=True,
            ),
            "accept_terminal",
            "terminal_observed",
        ),
        (
            "runtime dead after command",
            "command_sent",
            ControlConvergenceEvidence(
                command=CONTROL_KILL,
                runtime_dead_after_control=True,
            ),
            "accept_dead_runtime",
            "runtime_dead_after_control",
        ),
        (
            "runtime dead after accepted",
            "accepted",
            ControlConvergenceEvidence(
                command=CONTROL_KILL,
                runtime_dead_after_control=True,
            ),
            "accept_dead_runtime",
            "runtime_dead_after_control",
        ),
        (
            "runtime dead after runner escalation",
            "escalating_runner",
            ControlConvergenceEvidence(
                command=CONTROL_KILL,
                runtime_dead_after_control=True,
                runner_fallback_attempted=True,
            ),
            "accept_dead_runtime",
            "runtime_dead_after_control",
        ),
        (
            "runtime dead after host escalation",
            "escalating_host",
            ControlConvergenceEvidence(
                command=CONTROL_KILL,
                runtime_dead_after_control=True,
                runner_fallback_attempted=True,
                host_fallback_attempted=True,
            ),
            "accept_dead_runtime",
            "runtime_dead_after_control",
        ),
        (
            "ack waits",
            "command_sent",
            ControlConvergenceEvidence(command=CONTROL_KILL, ack_seen=True),
            "wait",
            "accepted",
        ),
        (
            "accepted ack waits",
            "accepted",
            ControlConvergenceEvidence(command=CONTROL_KILL, ack_seen=True),
            "wait",
            "accepted",
        ),
        (
            "command wait expires to runner escalation",
            "accepted",
            ControlConvergenceEvidence(
                command=CONTROL_KILL,
                ack_seen=True,
                observation_budget_expired=True,
            ),
            "escalate_runner",
            "escalating_runner",
        ),
        (
            "runner escalation expires to host escalation",
            "escalating_runner",
            ControlConvergenceEvidence(
                command=CONTROL_KILL,
                runner_fallback_attempted=True,
                observation_budget_expired=True,
            ),
            "escalate_host",
            "escalating_host",
        ),
        (
            "host escalation expires unknown",
            "escalating_host",
            ControlConvergenceEvidence(
                command=CONTROL_KILL,
                runner_fallback_attempted=True,
                host_fallback_attempted=True,
                observation_budget_expired=True,
            ),
            "report_unknown",
            "unknown",
        ),
        (
            "stop runner escalation expires unknown",
            "escalating_runner",
            ControlConvergenceEvidence(
                command="STOP",
                runner_fallback_attempted=True,
                observation_budget_expired=True,
            ),
            "report_unknown",
            "unknown",
        ),
    )
    seen_transitions: set[str] = set()
    seen_states: set[ControlConvergenceState] = set()
    seen_actions: set[ControlConvergenceAction] = set()

    for label, current, evidence, expected_action, expected_target in cases:
        decision = reduce_control_convergence(current, evidence)
        assert decision.action == expected_action, label
        assert decision.target == expected_target, label
        seen_transitions.add(decision.transition_id)
        seen_states.update((decision.source, decision.target))
        seen_actions.add(decision.action)

    control_convergence_machine.assert_all_states_reachable(("command_sent",))
    control_convergence_machine.assert_transition_ids_covered(seen_transitions)
    control_convergence_machine.assert_states_covered(seen_states)
    control_convergence_machine.assert_actions_covered(seen_actions)


def test_control_convergence_does_not_accept_kill_ack_or_wrong_terminal() -> None:
    ack = reduce_control_convergence(
        "command_sent",
        ControlConvergenceEvidence(command=CONTROL_KILL, ack_seen=True),
    )
    wrong_terminal = reduce_control_convergence(
        "command_sent",
        ControlConvergenceEvidence(
            command=CONTROL_KILL,
            terminal_status="failed",
            observation_budget_expired=True,
        ),
    )

    assert ack.target == "accepted"
    assert ack.action == "wait"
    assert wrong_terminal.target == "escalating_runner"
    assert wrong_terminal.action == "escalate_runner"


def _wait_for_registered_worker_pid(
    ctx: WeftContext, tid: str, timeout: float = 15.0, *, ready_path: Path
) -> int | None:
    deadline = time.monotonic() + timeout
    mapping_queue = ctx.queue(task_state_queue_name(tid), persistent=False)
    try:
        while time.monotonic() < deadline:
            ready_pid = ready_path.read_text().strip() if ready_path.exists() else ""
            if ready_pid:
                for payload, _timestamp in iter_queue_json_entries(mapping_queue):
                    if payload.get("full") != tid:
                        continue
                    runtime_handle = payload.get("runtime_handle")
                    if isinstance(runtime_handle, dict):
                        try:
                            handle = RunnerHandle.from_dict(runtime_handle)
                        except (TypeError, ValueError):
                            continue
                        if int(ready_pid) in handle.scoped_host_pids():
                            return int(ready_pid)
            time.sleep(0.05)
        return None
    finally:
        mapping_queue.close()


def _wait_for_process_exit(
    pid: int,
    *,
    process: BaseProcess | None = None,
    timeout: float = 5.0,
) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process is not None:
            process.join(timeout=0.05)
            if not process.is_alive():
                return True
        if not pid_is_live(pid):
            return True
        time.sleep(0.05)
    return False


def _launch_running_task(
    tmp_path: Path,
) -> tuple[TaskSpec, BaseProcess, psutil.Process]:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    ready = root / f"{tid}.ready"
    release = root / f"{tid}.release"
    spec = _make_taskspec(
        tid,
        function_target="tests.tasks.sample_targets:signal_ready_and_wait_for_release",
    )
    assert spec.tid is not None
    process = launch_task_process(
        Consumer,
        ctx.broker_target,
        spec,
        config=ctx.config,
    )
    assert process.pid is not None
    handed_off = False
    try:
        inbox = ctx.queue(spec.io.inputs["inbox"], persistent=True)
        try:
            inbox.write(json.dumps({"args": [str(ready), str(release)]}))
        finally:
            inbox.close()
        worker_pid = _wait_for_registered_worker_pid(ctx, spec.tid, ready_path=ready)
        assert worker_pid is not None, (
            f"worker did not become ready: tid={tid}, pid={process.pid}, "
            f"exitcode={process.exitcode}, target_ready={ready.exists()}"
        )
        worker = psutil.Process(worker_pid)
        assert worker.is_running() and pid_is_live(worker.pid)
        handed_off = True
        return spec, process, worker
    finally:
        if not handed_off:
            release.touch()
            _cleanup_running_task(process, None)


def _cleanup_running_task(process: BaseProcess, worker: psutil.Process | None) -> None:
    """Reap the owned Consumer and only retained worker identities."""
    descendants = {} if worker is None else {worker.pid: worker}
    if worker is not None and worker.is_running():
        try:
            descendants.update(
                (child.pid, child) for child in worker.children(recursive=True)
            )
        except psutil.NoSuchProcess:
            pass
    if process.is_alive():
        try:
            descendants.update(
                (child.pid, child)
                for child in psutil.Process(process.pid).children(recursive=True)
            )
        except psutil.NoSuchProcess:
            pass
        process.kill()
    # multiprocessing must reap its own child; psutil.wait would consume waitpid.
    process.join(timeout=5.0)
    for descendant in descendants.values():
        try:
            descendant.kill()
        except psutil.NoSuchProcess:
            pass
    _gone, alive = psutil.wait_procs(list(descendants.values()), timeout=5.0)
    assert not process.is_alive(), "failed to reap owned Consumer"
    assert all(
        not descendant.is_running() or not pid_is_live(descendant.pid)
        for descendant in alive
    ), "failed to reap owned worker descendants"


@pytest.mark.parametrize("after_worker_ready", [False, True])
def test_running_task_setup_failure_reaps_launched_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after_worker_ready: bool
) -> None:
    """Failed readiness still leaves the helper responsible for its child."""
    processes: list[BaseProcess] = []
    workers: list[psutil.Process] = []
    launch = launch_task_process
    wait_for_worker = _wait_for_registered_worker_pid

    def capture_launch(*args: Any, **kwargs: Any) -> BaseProcess:
        process = launch(*args, **kwargs)
        processes.append(process)
        return process

    def fail_readiness(*args: Any, **kwargs: Any) -> None:
        if after_worker_ready:
            worker_pid = wait_for_worker(*args, **kwargs)
            assert worker_pid is not None
            workers.append(psutil.Process(worker_pid))
        raise RuntimeError("injected readiness failure")

    monkeypatch.setitem(globals(), "launch_task_process", capture_launch)
    monkeypatch.setitem(globals(), "_wait_for_registered_worker_pid", fail_readiness)
    try:
        with pytest.raises(RuntimeError, match="injected readiness failure"):
            _launch_running_task(tmp_path)
        assert processes
        assert not processes[0].is_alive(), "setup abandoned its live Consumer"
        assert all(not worker.is_running() for worker in workers)
    finally:
        for process in processes:
            _cleanup_running_task(process, workers[0] if workers else None)


def test_stop_tasks_terminates_active_process_tree(tmp_path: Path) -> None:
    spec, process, worker = _launch_running_task(tmp_path)
    assert spec.tid is not None
    assert process.pid is not None
    try:
        stopped = task_cmd.stop_tasks([spec.tid], context_path=tmp_path)
        assert stopped == 1
        assert _wait_for_process_exit(process.pid, process=process)
        assert _wait_for_process_exit(worker.pid)
    finally:
        _cleanup_running_task(process, worker)


def test_await_control_surface_uses_queue_monitor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    created_monitors: list[_FakeQueueChangeMonitor] = []
    snapshots = iter(
        [
            None,
            task_cmd.system_cmd.TaskSnapshot(
                tid=tid,
                tid_short=tid_short_form(tid),
                name="task-func",
                status="completed",
                event="work_completed",
                activity=None,
                waiting_on=None,
                started_at=None,
                completed_at=time.time_ns(),
                last_timestamp=time.time_ns(),
                duration_seconds=None,
                runner=None,
                runtime_handle=None,
                runtime=None,
                metadata={},
            ),
        ]
    )

    def _fake_monitor(
        queues: Sequence[Queue], *, config: Config | None = None
    ) -> _FakeQueueChangeMonitor:
        monitor = _FakeQueueChangeMonitor(queues, config=config)
        created_monitors.append(monitor)
        return monitor

    monkeypatch.setattr(task_cmd, "QueueChangeMonitor", _fake_monitor)
    monkeypatch.setattr(task_cmd, "mapping_for_tid", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        task_cmd, "load_latest_taskspec_payload", lambda *_args, **_kwargs: None
    )
    observed_contexts: list[object] = []

    def fake_status(
        *_args: object, **kwargs: object
    ) -> task_cmd.system_cmd.TaskSnapshot | None:
        observed_contexts.append(kwargs.get("context"))
        return next(snapshots)

    monkeypatch.setattr(task_cmd, "_task_status", fake_status)

    # Use the production wait budget: this test still builds real queue handles,
    # and PG-backed setup under xdist can exhaust artificial sub-second budgets.
    entry, snapshot = task_cmd._await_control_surface(ctx, tid)

    assert entry is None
    assert snapshot is not None
    assert snapshot.status == "completed"
    assert len(created_monitors) == 1
    assert Counter(created_monitors[0].queue_names) == Counter(
        [
            task_state_queue_name(tid),
            "weft.log.tasks",
            f"T{tid}.ctrl_out",
        ]
    )
    assert created_monitors[0].wait_calls
    assert observed_contexts
    assert all(observed is ctx for observed in observed_contexts)


def test_await_control_surface_rebinds_late_names_and_closes_each_surface_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Late TaskSpec endpoints replace the watched surface without leaking it."""

    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    initial_ctrl_out = f"T{tid}.ctrl_out"
    late_ctrl_out = f"custom.{tid}.ctrl_out"
    late_pipeline_status = f"custom.{tid}.pipeline.status"
    terminal_payload = {
        "type": "terminal",
        "source": "task",
        "tid": tid,
        "status": "cancelled",
        "timestamp": time.time_ns(),
    }
    seed_queue = ctx.queue(late_ctrl_out, persistent=False)
    try:
        seed_queue.write("{malformed")
        seed_queue.write(json.dumps(["not", "a", "mapping"]))
        seed_queue.write(json.dumps(terminal_payload))
        seed_queue.write(json.dumps({"command": "STATUS", "status": "late"}))
    finally:
        seed_queue.close()

    class _TrackedQueue:
        def __init__(self, queue: object, *, name: str, persistent: bool) -> None:
            self._queue = queue
            self.name = name
            self.persistent = persistent
            self.close_calls = 0

        def __getattr__(self, name: str) -> object:
            return getattr(self._queue, name)

        def close(self) -> None:
            self.close_calls += 1
            self._queue.close()  # type: ignore[attr-defined]

    original_queue = type(ctx).queue
    opened_queues: list[_TrackedQueue] = []

    def _tracking_queue(
        context: object,
        name: str,
        *,
        persistent: bool = False,
    ) -> _TrackedQueue:
        queue = original_queue(context, name, persistent=persistent)  # type: ignore[arg-type]
        tracked = _TrackedQueue(queue, name=name, persistent=persistent)
        opened_queues.append(tracked)
        return tracked

    initial_taskspec: dict[str, Any] = {
        "tid": tid,
        "io": {"control": {"ctrl_out": initial_ctrl_out}},
    }
    late_taskspec = {
        "tid": tid,
        "name": "late-surface",
        "spec": {"runner": {"name": "host"}},
        "io": {
            "control": {
                "ctrl_in": f"custom.{tid}.ctrl_in",
                "ctrl_out": late_ctrl_out,
            }
        },
        "state": {"status": "running"},
        "metadata": {
            "role": "pipeline",
            "_weft_pipeline_runtime": {"queues": {"status": late_pipeline_status}},
        },
    }
    taskspecs = iter([initial_taskspec, late_taskspec])
    created_monitors: list[_FakeQueueChangeMonitor] = []

    def _fake_monitor(
        queues: Sequence[Queue], *, config: Config | None = None
    ) -> _FakeQueueChangeMonitor:
        monitor = _FakeQueueChangeMonitor(queues, config=config)
        created_monitors.append(monitor)
        return monitor

    monkeypatch.setattr(type(ctx), "queue", _tracking_queue)
    monkeypatch.setattr(task_cmd, "QueueChangeMonitor", _fake_monitor)
    monkeypatch.setattr(task_cmd, "mapping_for_tid", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        task_cmd,
        "load_latest_taskspec_payload",
        lambda *_args, **_kwargs: next(taskspecs),
    )
    monkeypatch.setattr(
        task_cmd,
        "_task_status",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("replacement ctrl_out terminal should finish first")
        ),
    )

    entry, snapshot = task_cmd._await_control_surface(ctx, tid)

    assert entry is None
    assert snapshot is not None
    assert snapshot.status == "cancelled"
    assert snapshot.name == "late-surface"
    assert len(created_monitors) == 2
    assert Counter(created_monitors[0].queue_names) == Counter(
        [task_state_queue_name(tid), "weft.log.tasks", initial_ctrl_out]
    )
    assert Counter(created_monitors[1].queue_names) == Counter(
        [
            task_state_queue_name(tid),
            "weft.log.tasks",
            late_ctrl_out,
            late_pipeline_status,
        ]
    )
    assert Counter(created_monitors[0].queue_persistence) == Counter(
        [
            (task_state_queue_name(tid), True),
            ("weft.log.tasks", True),
            (initial_ctrl_out, True),
        ]
    )
    assert Counter(created_monitors[1].queue_persistence) == Counter(
        [
            (task_state_queue_name(tid), True),
            ("weft.log.tasks", True),
            (late_ctrl_out, True),
            (late_pipeline_status, True),
        ]
    )
    assert [monitor.close_calls for monitor in created_monitors] == [1, 1]
    assert len(opened_queues) == 7
    assert all(queue.close_calls == 1 for queue in opened_queues)

    remaining_queue = original_queue(ctx, late_ctrl_out, persistent=False)
    try:
        remaining = remaining_queue.peek_many(limit=10)
    finally:
        remaining_queue.close()
    assert len(remaining) == 1
    assert json.loads(str(remaining[0])) == {
        "command": "STATUS",
        "status": "late",
    }


def test_await_control_surface_public_grace_outlives_expired_kill_ack(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A later public signal keeps the expired-overall tail waiting."""

    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    ctrl_out = ctx.queue(f"T{tid}.ctrl_out", persistent=False)
    ctrl_out.write(json.dumps({"command": CONTROL_KILL, "status": "ack", "tid": tid}))

    class _Clock:
        value = 0.0

        def __call__(self) -> float:
            return self.value

    clock = _Clock()
    created_monitors: list[_FakeQueueChangeMonitor] = []

    class _ScriptedMonitor(_FakeQueueChangeMonitor):
        def wait(self, timeout: float | None) -> bool:
            self.wait_calls.append(timeout)
            if len(self.wait_calls) == 1:
                clock.value = 1.1
                ctrl_out.write(
                    json.dumps({"command": "STATUS", "status": "ok", "tid": tid})
                )
            else:
                clock.value = 2.2
            return False

    def _fake_monitor(
        queues: Sequence[Queue], *, config: Config | None = None
    ) -> _ScriptedMonitor:
        monitor = _ScriptedMonitor(queues, config=config)
        created_monitors.append(monitor)
        return monitor

    monkeypatch.setattr(task_cmd.time, "monotonic", clock)
    monkeypatch.setattr(task_cmd, "CONTROL_SURFACE_WAIT_INTERVAL", 1.0)
    monkeypatch.setattr(task_cmd, "QueueChangeMonitor", _fake_monitor)
    monkeypatch.setattr(task_cmd, "mapping_for_tid", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        task_cmd, "load_latest_taskspec_payload", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(task_cmd, "_task_status", lambda *_args, **_kwargs: None)

    try:
        entry, snapshot = task_cmd._await_control_surface(ctx, tid, timeout=0.5)
    finally:
        ctrl_out.close()

    assert entry is None
    assert snapshot is None
    assert len(created_monitors) == 1
    assert created_monitors[0].wait_calls == [0.5, 1.0]


def test_await_control_surface_closes_partial_replacement_on_open_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A failed surface replacement closes displaced and partial resources."""

    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    initial_ctrl_out = f"T{tid}.ctrl_out"
    late_ctrl_out = f"custom.{tid}.ctrl_out"
    construction_error = RuntimeError("replacement queue construction failed")

    class _TrackedQueue:
        def __init__(self, queue: object, *, name: str) -> None:
            self._queue = queue
            self.name = name
            self.close_calls = 0

        def __getattr__(self, name: str) -> object:
            return getattr(self._queue, name)

        def close(self) -> None:
            self.close_calls += 1
            if self.close_calls == 1:
                self._queue.close()  # type: ignore[attr-defined]

    original_queue = type(ctx).queue
    opened_queues: list[_TrackedQueue] = []
    open_calls = 0

    def _tracking_queue(
        context: object,
        name: str,
        *,
        persistent: bool = False,
    ) -> _TrackedQueue:
        nonlocal open_calls
        open_calls += 1
        if open_calls == 5:
            raise construction_error
        queue = original_queue(context, name, persistent=persistent)  # type: ignore[arg-type]
        tracked = _TrackedQueue(queue, name=name)
        opened_queues.append(tracked)
        return tracked

    initial_taskspec = {
        "tid": tid,
        "io": {"control": {"ctrl_out": initial_ctrl_out}},
    }
    late_taskspec = {
        "tid": tid,
        "io": {"control": {"ctrl_out": late_ctrl_out}},
    }
    taskspecs = iter([initial_taskspec, late_taskspec])
    created_monitors: list[_FakeQueueChangeMonitor] = []

    def _fake_monitor(
        queues: Sequence[Queue], *, config: Config | None = None
    ) -> _FakeQueueChangeMonitor:
        monitor = _FakeQueueChangeMonitor(queues, config=config)
        created_monitors.append(monitor)
        return monitor

    monkeypatch.setattr(type(ctx), "queue", _tracking_queue)
    monkeypatch.setattr(task_cmd, "QueueChangeMonitor", _fake_monitor)
    monkeypatch.setattr(
        task_cmd,
        "load_latest_taskspec_payload",
        lambda *_args, **_kwargs: next(taskspecs),
    )

    with pytest.raises(RuntimeError) as exc_info:
        task_cmd._await_control_surface(ctx, tid)

    assert exc_info.value is construction_error
    assert open_calls == 5
    assert len(created_monitors) == 1
    assert created_monitors[0].close_calls == 1
    assert len(opened_queues) == 4
    assert all(queue.close_calls == 1 for queue in opened_queues)


def test_await_control_surface_does_not_promote_kill_ack_to_terminal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    ctrl_out = ctx.queue(f"T{tid}.ctrl_out", persistent=False)
    ctrl_out.write(json.dumps({"command": CONTROL_KILL, "status": "ack", "tid": tid}))

    monkeypatch.setattr(task_cmd, "QueueChangeMonitor", _FakeQueueChangeMonitor)
    monkeypatch.setattr(task_cmd, "mapping_for_tid", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        task_cmd, "load_latest_taskspec_payload", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(task_cmd, "CONTROL_SURFACE_WAIT_INTERVAL", 0.001)
    monkeypatch.setattr(
        task_cmd,
        "_task_status",
        lambda *_args, **_kwargs: task_cmd.system_cmd.TaskSnapshot(
            tid=tid,
            tid_short=tid_short_form(tid),
            name="task-func",
            status="running",
            event="task_started",
            activity=None,
            waiting_on=None,
            started_at=time.time_ns(),
            completed_at=None,
            last_timestamp=time.time_ns(),
            duration_seconds=None,
            runner=None,
            runtime_handle=None,
            runtime=None,
            metadata={},
        ),
    )

    _entry, snapshot = task_cmd._await_control_surface(ctx, tid, timeout=0.001)

    assert snapshot is not None
    assert snapshot.status == "running"
    assert snapshot.event == "task_started"


def test_observe_control_envelopes_keeps_same_drain_observation_times(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Public and KILL clocks retain their last per-envelope sample."""

    tid = str(time.time_ns())
    messages = iter(
        [
            json.dumps({"command": "STATUS", "status": "ok", "tid": tid}),
            json.dumps({"command": CONTROL_KILL, "status": "ack", "tid": tid}),
            json.dumps({"type": "stream", "stream": "stderr", "data": "x"}),
            None,
        ]
    )

    class _ScriptedQueue:
        def read_one(self) -> str | None:
            return next(messages)

    ctrl_out: Any = _ScriptedQueue()
    monotonic_samples = iter([10.0, 20.0, 30.0])
    monkeypatch.setattr(task_cmd.time, "monotonic", lambda: next(monotonic_samples))

    observation = task_cmd._observe_control_envelopes(
        ctrl_out,
        tid=tid,
        taskspec_payload={},
    )

    assert next(messages, "exhausted") == "exhausted"
    assert observation.terminal_snapshot is None
    assert observation.public_signal_observed_at == 20.0
    assert observation.kill_ack_observed_at == 30.0


def test_await_control_surface_accepts_terminal_ctrl_out_without_log_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    ctrl_out = ctx.queue(f"T{tid}.ctrl_out", persistent=False)
    ctrl_out.write(
        json.dumps(
            {
                "type": "terminal",
                "source": "task",
                "tid": tid,
                "status": "cancelled",
                "timestamp": time.time_ns(),
            }
        )
    )

    monkeypatch.setattr(task_cmd, "QueueChangeMonitor", _FakeQueueChangeMonitor)
    monkeypatch.setattr(task_cmd, "mapping_for_tid", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        task_cmd,
        "load_latest_taskspec_payload",
        lambda *_args, **_kwargs: {
            "tid": tid,
            "name": "terminal-proof",
            "spec": {"runner": {"name": "host"}},
            "state": {"status": "running"},
            "metadata": {"kind": "test"},
        },
    )
    monkeypatch.setattr(
        task_cmd,
        "_task_status",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("terminal ctrl_out should be sufficient")
        ),
    )

    entry, snapshot = task_cmd._await_control_surface(ctx, tid)

    assert entry is None
    assert snapshot is not None
    assert snapshot.status == "cancelled"
    assert snapshot.event == "ctrl_out_terminal"
    assert snapshot.name == "terminal-proof"
    assert snapshot.metadata == {"kind": "test"}


def test_kill_tasks_terminates_active_process_tree(tmp_path: Path) -> None:
    spec, process, worker = _launch_running_task(tmp_path)
    assert spec.tid is not None
    assert process.pid is not None
    try:
        killed = task_cmd.kill_tasks([spec.tid], context_path=tmp_path)
        assert killed >= 1
        assert _wait_for_process_exit(process.pid, process=process)
        assert _wait_for_process_exit(worker.pid)
    finally:
        _cleanup_running_task(process, worker)


def test_stop_tasks_uses_runner_handle_when_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    mapping_queue = ctx.queue(task_state_queue_name(tid), persistent=False)
    ctrl_queue = ctx.queue(f"T{tid}.ctrl_in", persistent=False)
    calls: list[tuple[str, dict[str, Any], float]] = []

    class FakeRunnerPlugin:
        def stop(self, handle: RunnerHandle, *, timeout: float = 2.0) -> bool:
            calls.append(("stop", handle.to_dict(), timeout))
            return True

    mapping_queue.write(
        json.dumps(
            {
                "short": tid[-6:],
                "full": tid,
                "runner": "fake",
                "runtime_handle": _runtime_handle(
                    "fake",
                    "runtime-123",
                    host_pids=[33333],
                    metadata={"scope": "test"},
                ),
                "name": "task-func",
                "hostname": "test-host",
            }
        )
    )

    monkeypatch.setattr(
        task_cmd, "require_runner_plugin", lambda name: FakeRunnerPlugin()
    )
    monkeypatch.setattr(task_cmd, "_pid_exists", lambda pid: False)
    monkeypatch.setattr(
        task_cmd,
        "terminate_verified_process_tree",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("should not fall back to direct PID stop")
        ),
    )

    stopped = task_cmd.stop_tasks([tid], context_path=root)

    assert stopped == 1
    assert calls == [
        (
            "stop",
            _runtime_handle(
                "fake",
                "runtime-123",
                host_pids=[33333],
                metadata={"scope": "test"},
            ),
            0.2,
        )
    ]
    assert ctrl_queue.read_one() == encode_control_message("STOP")


def test_stop_tasks_prefers_task_process_over_runner_handle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    mapping_queue = ctx.queue(task_state_queue_name(tid), persistent=False)
    ctrl_queue = ctx.queue(f"T{tid}.ctrl_in", persistent=False)
    terminate_calls: list[tuple[int, float, bool]] = []
    plugin_calls: list[tuple[str, dict[str, Any], float]] = []

    class FakeRunnerPlugin:
        def stop(self, handle: RunnerHandle, *, timeout: float = 2.0) -> bool:
            plugin_calls.append(("stop", handle.to_dict(), timeout))
            return True

    mapping_queue.write(
        json.dumps(
            {
                "short": tid[-6:],
                "full": tid,
                "runner": "fake",
                "runtime_handle": _runtime_handle(
                    "fake",
                    "runtime-123",
                    host_pids=[33333],
                    metadata={"scope": "test"},
                ),
                "name": "task-func",
                "hostname": "test-host",
            }
        )
    )

    monkeypatch.setattr(
        task_cmd, "require_runner_plugin", lambda name: FakeRunnerPlugin()
    )
    monkeypatch.setattr(task_cmd, "_pid_exists", lambda pid: pid == 11111)
    monkeypatch.setattr(
        task_cmd,
        "terminate_verified_process_tree",
        lambda pid, create_time, *, timeout, kill: terminate_calls.append(
            (pid, timeout, not kill)
        ),
    )

    stopped = task_cmd.stop_tasks([tid], context_path=root)

    assert stopped == 1
    assert terminate_calls == []
    assert plugin_calls == [
        (
            "stop",
            _runtime_handle(
                "fake",
                "runtime-123",
                host_pids=[33333],
                metadata={"scope": "test"},
            ),
            0.2,
        )
    ]
    assert ctrl_queue.read_one() == encode_control_message("STOP")


def test_kill_tasks_uses_runner_handle_when_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    mapping_queue = ctx.queue(task_state_queue_name(tid), persistent=False)
    calls: list[tuple[str, dict[str, Any], float]] = []

    class FakeRunnerPlugin:
        def kill(self, handle: RunnerHandle, *, timeout: float = 2.0) -> bool:
            calls.append(("kill", handle.to_dict(), timeout))
            return True

    mapping_queue.write(
        json.dumps(
            {
                "short": tid[-6:],
                "full": tid,
                "runner": "fake",
                "runtime_handle": _runtime_handle(
                    "fake",
                    "runtime-123",
                    host_pids=[33333],
                    metadata={"scope": "test"},
                ),
                "name": "task-func",
                "hostname": "test-host",
            }
        )
    )

    monkeypatch.setattr(
        task_cmd, "require_runner_plugin", lambda name: FakeRunnerPlugin()
    )
    monkeypatch.setattr(task_cmd, "_pid_exists", lambda pid: False)
    monkeypatch.setattr(
        task_cmd,
        "terminate_verified_process_tree",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("should not fall back to direct PID kill")
        ),
    )

    killed = task_cmd.kill_tasks([tid], context_path=root)

    assert killed == 1
    assert calls == [
        (
            "kill",
            _runtime_handle(
                "fake",
                "runtime-123",
                host_pids=[33333],
                metadata={"scope": "test"},
            ),
            0.2,
        )
    ]


def test_kill_tasks_does_not_count_runner_success_while_observed_pid_lives(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    mapping_queue = ctx.queue(task_state_queue_name(tid), persistent=False)
    calls: list[tuple[str, dict[str, Any], float]] = []
    force_calls: list[int] = []
    runtime_handle = _runtime_handle(
        "fake",
        "runtime-123",
        host_pids=[33333],
        metadata={"scope": "test"},
    )
    mapping_payload: dict[str, object] = {
        "short": tid[-6:],
        "full": tid,
        "runner": "fake",
        "runtime_handle": runtime_handle,
        "name": "task-func",
        "hostname": "test-host",
    }

    class FakeRunnerPlugin:
        def kill(self, handle: RunnerHandle, *, timeout: float = 2.0) -> bool:
            calls.append(("kill", handle.to_dict(), timeout))
            return True

    def _running_surface(
        _ctx: WeftContext, _tid: str, *, timeout: float = 0.0
    ) -> tuple[dict[str, object], task_cmd.system_cmd.TaskSnapshot]:
        del timeout
        return mapping_payload, task_cmd.system_cmd.TaskSnapshot(
            tid=tid,
            tid_short=tid_short_form(tid),
            name="task-func",
            status="running",
            event="task_started",
            activity=None,
            waiting_on=None,
            started_at=time.time_ns(),
            completed_at=None,
            last_timestamp=time.time_ns(),
            duration_seconds=None,
            runner="fake",
            runtime_handle=runtime_handle,
            runtime=None,
            metadata={},
        )

    mapping_queue.write(json.dumps(mapping_payload))
    monkeypatch.setattr(
        task_cmd, "require_runner_plugin", lambda name: FakeRunnerPlugin()
    )
    monkeypatch.setattr(task_cmd, "_await_control_surface", _running_surface)
    monkeypatch.setattr(task_cmd, "_pid_exists", lambda pid: pid == 33333)

    # PID 33333 is a stand-in, not a real process, so it carries no genuine
    # create_time. Simulate a verified identity match (as a real host-pid
    # mapping entry would have) so the force-kill guard signals it -- this
    # keeps the test's intent (force-kill fires while the runner-observed
    # PID still lives) independent of the create-time verification added to
    # close the reused-PID defect.
    def _record_force_calls(
        pid: int, create_time: float | None, *, timeout: float, kill: bool
    ) -> bool:
        force_calls.append(pid)
        return True

    monkeypatch.setattr(
        task_cmd,
        "terminate_verified_process_tree",
        _record_force_calls,
    )

    killed = task_cmd.kill_tasks([tid], context_path=root)

    assert killed == 0
    assert calls == [("kill", runtime_handle, 0.2)]
    assert force_calls == [33333]


def test_task_stop_stops_pipeline_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    ctrl_queue = ctx.queue(f"P{tid}.ctrl_in", persistent=False)
    _write_logged_pipeline_task(ctx, tid)
    monkeypatch.setattr(
        task_cmd, "_await_control_surface", lambda ctx, tid: (None, None)
    )

    stopped = task_cmd.stop_tasks([tid], context_path=root)

    assert stopped == 1
    assert ctrl_queue.read_one() == encode_control_message("STOP")


def test_task_kill_kills_pipeline_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    ctrl_queue = ctx.queue(f"P{tid}.ctrl_in", persistent=False)
    _write_logged_pipeline_task(ctx, tid)
    monkeypatch.setattr(
        task_cmd, "_await_control_surface", lambda ctx, tid: (None, None)
    )
    monkeypatch.setattr(task_cmd, "_kill_via_fallback", lambda _entry: True)

    killed = task_cmd.kill_tasks([tid], context_path=root)

    assert killed == 1
    assert ctrl_queue.read_one() == encode_control_message("KILL")


def test_stop_tasks_does_not_force_terminal_consumer_for_external_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    mapping_queue = ctx.queue(task_state_queue_name(tid), persistent=False)
    ctrl_queue = ctx.queue(f"T{tid}.ctrl_in", persistent=False)
    mapping_queue.write(
        json.dumps(
            {
                "short": tid[-6:],
                "full": tid,
                "runner": "docker",
                "runtime_handle": _runtime_handle(
                    "docker",
                    "runtime-123",
                    kind="container",
                    authority="external-supervisor",
                    observations={"container_id": "runtime-123"},
                    metadata={"image": "python:3.13-alpine"},
                ),
                "name": "task-func",
                "hostname": "test-host",
            }
        )
    )

    monkeypatch.setattr(
        task_cmd,
        "task_status",
        lambda *args, **kwargs: task_cmd.system_cmd.TaskSnapshot(
            tid=tid,
            tid_short=tid_short_form(tid),
            name="docker-task",
            status="cancelled",
            event="control_stop",
            activity=None,
            waiting_on=None,
            started_at=None,
            completed_at=None,
            last_timestamp=time.time_ns(),
            duration_seconds=None,
            runner="docker",
            runtime_handle=_runtime_handle(
                "docker",
                "runtime-123",
                kind="container",
                authority="external-supervisor",
                observations={"container_id": "runtime-123"},
                metadata={"image": "python:3.13-alpine"},
            ),
            runtime={
                "runner": "docker",
                "id": "runtime-123",
                "state": "missing",
                "metadata": {"image": "python:3.13-alpine"},
            },
            metadata={},
        ),
    )
    monkeypatch.setattr(
        task_cmd,
        "terminate_verified_process_tree",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("external runners must not force-stop the consumer PID")
        ),
    )

    stopped = task_cmd.stop_tasks([tid], context_path=root)

    assert stopped == 1
    assert ctrl_queue.read_one() == encode_control_message("STOP")


def test_stop_tasks_does_not_force_stop_consumer_without_runner_handle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    mapping_queue = ctx.queue(task_state_queue_name(tid), persistent=False)
    ctrl_queue = ctx.queue(f"T{tid}.ctrl_in", persistent=False)
    mapping_queue.write(
        json.dumps(
            {
                "short": tid[-6:],
                "full": tid,
                "runner": "host",
                "runtime_handle": None,
                "name": "task-func",
                "hostname": "test-host",
            }
        )
    )

    monkeypatch.setattr(
        task_cmd,
        "task_status",
        lambda *args, **kwargs: task_cmd.system_cmd.TaskSnapshot(
            tid=tid,
            tid_short=tid_short_form(tid),
            name="host-task",
            status="running",
            event="work_started",
            activity=None,
            waiting_on=None,
            started_at=None,
            completed_at=None,
            last_timestamp=time.time_ns(),
            duration_seconds=None,
            runner="host",
            runtime_handle=None,
            runtime=None,
            metadata={},
        ),
    )
    monkeypatch.setattr(
        task_cmd,
        "terminate_verified_process_tree",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("graceful stop must not terminate the consumer PID")
        ),
    )

    stopped = task_cmd.stop_tasks([tid], context_path=root)

    assert stopped == 1
    assert ctrl_queue.read_one() == encode_control_message("STOP")


def test_kill_tasks_does_not_force_terminal_consumer_for_external_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    mapping_queue = ctx.queue(task_state_queue_name(tid), persistent=False)
    mapping_queue.write(
        json.dumps(
            {
                "short": tid[-6:],
                "full": tid,
                "runner": "macos-sandbox",
                "runtime_handle": _runtime_handle(
                    "macos-sandbox",
                    "runtime-123",
                    kind="sandboxed-process",
                    host_pids=[33333],
                    observations={"sandbox_profile": "allow-default.sb"},
                    metadata={"profile": "allow-default.sb"},
                ),
                "name": "task-func",
                "hostname": "test-host",
            }
        )
    )

    monkeypatch.setattr(
        task_cmd,
        "task_status",
        lambda *args, **kwargs: task_cmd.system_cmd.TaskSnapshot(
            tid=tid,
            tid_short=tid_short_form(tid),
            name="sandbox-task",
            status="killed",
            event="control_kill",
            activity=None,
            waiting_on=None,
            started_at=None,
            completed_at=None,
            last_timestamp=time.time_ns(),
            duration_seconds=None,
            runner="macos-sandbox",
            runtime_handle=_runtime_handle(
                "macos-sandbox",
                "runtime-123",
                kind="sandboxed-process",
                host_pids=[33333],
                observations={"sandbox_profile": "allow-default.sb"},
                metadata={"profile": "allow-default.sb"},
            ),
            runtime={
                "runner": "macos-sandbox",
                "id": "runtime-123",
                "state": "missing",
                "metadata": {"profile": "allow-default.sb"},
            },
            metadata={},
        ),
    )
    monkeypatch.setattr(
        task_cmd,
        "terminate_verified_process_tree",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("external runners must not force-kill the consumer PID")
        ),
    )

    killed = task_cmd.kill_tasks([tid], context_path=root)

    assert killed == 1


def _latest_mapping_entry(ctx: WeftContext, tid: str) -> dict[str, Any] | None:
    mapping_queue = ctx.queue(task_state_queue_name(tid), persistent=False)
    latest: dict[str, Any] | None = None
    latest_timestamp = -1
    for payload, timestamp in iter_queue_json_entries(mapping_queue):
        if payload.get("full") != tid or timestamp < latest_timestamp:
            continue
        latest = payload
        latest_timestamp = timestamp
    return latest


def test_force_kill_task_processes_kills_pid_with_matching_create_time(
    tmp_path: Path,
) -> None:
    """A host-pid mapping entry whose create_time matches the live process is
    force-killed (Spec: [CC-3.2]). `_force_kill_task_processes` is the
    genuinely reachable, previously-unverified signal path: it force-kills
    any host pids in the mapping regardless of whether a runner-plugin kill
    already ran, so it must independently guard with `pid_matches_create_time`.
    """
    spec, process, worker = _launch_running_task(tmp_path)
    assert spec.tid is not None
    assert process.pid is not None
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    try:
        entry = _latest_mapping_entry(ctx, spec.tid)
        assert entry is not None
        handle = RunnerHandle.from_dict(entry["runtime_handle"])
        assert handle.control.get("authority") == "host-pid"
        recorded = dict(handle.scoped_host_processes())
        assert recorded
        # Confirm the latest mapping's recorded create_time genuinely matches
        # each live process it asks the force-kill path to signal.
        for pid, create_time in recorded.items():
            assert create_time is not None
            assert create_time == pytest.approx(process_create_time(pid), abs=0.001)

        task_killed = task_cmd._force_kill_task_processes(entry)

        assert task_killed is True
        assert all(_wait_for_process_exit(pid) for pid in recorded)
    finally:
        _cleanup_running_task(process, worker)


def test_force_kill_task_processes_refuses_stale_create_time(tmp_path: Path) -> None:
    """A mapping entry with a mismatched create_time must not be signaled --
    the command refuses to signal that PID and flows into the same outcome a
    dead task takes today, instead of killing a PID that may have been
    reused by an unrelated process (Spec: [CC-3.2])."""
    spec, process, worker = _launch_running_task(tmp_path)
    assert spec.tid is not None
    assert process.pid is not None
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    try:
        entry = _latest_mapping_entry(ctx, spec.tid)
        assert entry is not None
        handle = RunnerHandle.from_dict(entry["runtime_handle"])
        recorded = dict(handle.scoped_host_processes())
        assert recorded
        target_pid = next(iter(recorded))
        handle_payload = dict(entry["runtime_handle"])
        # Corrupt the recorded create_time so it no longer matches the live
        # process -- simulating a stale mapping pointing at a reused PID.
        handle_payload["observations"] = dict(handle_payload["observations"])
        handle_payload["observations"]["host_processes"] = [
            {"pid": target_pid, "create_time": 1.0}
        ]
        stale_entry = dict(entry)
        stale_entry["runtime_handle"] = handle_payload

        task_killed = task_cmd._force_kill_task_processes(stale_entry)

        assert task_killed is False
        # The real mapped process was never touched, so it is still alive.
        assert pid_is_live(target_pid)
    finally:
        _cleanup_running_task(process, worker)


def test_force_kill_task_processes_records_attempt_while_verified_pid_lingers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A verified host fallback is recorded before Windows releases the PID.

    Windows can keep a terminated PID observable while another process owns an
    open handle. Final runtime-death proof is a separate control-convergence
    step, so this helper reports the verified kill attempt rather than an
    immediate PID-disappearance result.
    """

    entry = {
        "runtime_handle": _runtime_handle(
            "host",
            "runtime-123",
            host_pids=[33333],
            metadata={"scope": "test"},
        ),
    }
    kill_calls: list[int] = []

    def _record_kill_calls(
        pid: int, create_time: float | None, *, timeout: float, kill: bool
    ) -> bool:
        kill_calls.append(pid)
        return True

    monkeypatch.setattr(
        task_cmd,
        "terminate_verified_process_tree",
        _record_kill_calls,
    )
    monkeypatch.setattr(task_cmd, "_pid_exists", lambda pid: True)

    task_killed = task_cmd._force_kill_task_processes(entry)

    assert task_killed is True
    assert kill_calls == [33333]


def test_force_kill_task_processes_refuses_unknown_create_time(
    tmp_path: Path,
) -> None:
    """A live PID without an exact recorded identity grants no control [CC-3.2]."""
    spec, process, worker = _launch_running_task(tmp_path)
    assert spec.tid is not None
    assert process.pid is not None
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    try:
        entry = _latest_mapping_entry(ctx, spec.tid)
        assert entry is not None
        handle = RunnerHandle.from_dict(entry["runtime_handle"])
        recorded_with_create_time = dict(handle.scoped_host_processes())
        assert recorded_with_create_time
        target_pid = next(iter(recorded_with_create_time))
        handle_payload = dict(entry["runtime_handle"])
        # Strip create-time identity entirely -- only host_pids remain, which
        # is what `scoped_host_processes()` falls back to as (pid, None).
        observations = dict(handle_payload["observations"])
        observations.pop("host_processes", None)
        observations["host_pids"] = [target_pid]
        handle_payload["observations"] = observations
        no_create_time_entry = dict(entry)
        no_create_time_entry["runtime_handle"] = handle_payload

        handle = RunnerHandle.from_dict(handle_payload)
        recorded = dict(handle.scoped_host_processes())
        assert recorded[target_pid] is None

        task_killed = task_cmd._force_kill_task_processes(no_create_time_entry)

        assert task_killed is False
        assert task_cmd._pid_exists(target_pid)
    finally:
        _cleanup_running_task(process, worker)


def test_stop_and_kill_via_fallback_guard_is_defensive_and_unreachable_today(
    tmp_path: Path,
) -> None:
    """Fallback controls a live exact host identity through its plugin [CC-3.2]."""
    spec, process, worker = _launch_running_task(tmp_path)
    assert spec.tid is not None
    assert process.pid is not None
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    try:
        entry = _latest_mapping_entry(ctx, spec.tid)
        assert entry is not None
        handle = RunnerHandle.from_dict(entry["runtime_handle"])
        assert handle.control.get("authority") == "host-pid"
        recorded = dict(handle.scoped_host_processes())
        assert recorded
        target_pid = next(iter(recorded))

        stopped = task_cmd._stop_via_fallback(entry)

        assert stopped is True
        assert _wait_for_process_exit(target_pid)
    finally:
        _cleanup_running_task(process, worker)


def test_snapshot_watch_unresolved_selector_keeps_timeout_behavior(
    tmp_path: Path,
) -> None:
    """An unresolved selector does not become an invalid namespace queue."""
    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    with pytest.raises(TimeoutError, match="watching task unresolved"):
        list(task_cmd.watch_task_status("unresolved", timeout=0, context=ctx))
