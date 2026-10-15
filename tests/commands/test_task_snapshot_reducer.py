"""Focused tests for the pure MF-5 task snapshot reducer."""

from __future__ import annotations

import ast
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.helpers.task_observation import write_task_observation
from tests.helpers.test_backend import prepare_project_root
from weft._constants import (
    INTERNAL_SERVICE_KEY_METADATA_KEY,
    INTERNAL_SERVICE_KEY_TASK_MONITOR,
)
from weft.commands import system
from weft.commands._task_snapshot_reducer import (
    CollectedTaskSnapshot,
    FoldedTaskRecord,
    RuntimeObservation,
    SnapshotEvidence,
    order_task_snapshots,
    plan_snapshot_probes,
    prepare_snapshot,
    reduce_task_event,
    reduce_task_snapshot,
)
from weft.context import build_context
from weft.core.task_evidence import TaskEvidenceSnapshot
from weft.ext import RunnerHandle
from weft.helpers import tid_short_form

pytestmark = [pytest.mark.shared]

TID = "1700000000000000001"
OTHER_TID = "1700000000000000002"


def _taskspec(
    *,
    status: str = "running",
    started_at: int | None = 1_000_000_000,
    completed_at: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": "example",
        "spec": {"runner": {"name": "host", "options": {}}},
        "state": {
            "status": status,
            "started_at": started_at,
            "completed_at": completed_at,
        },
        "metadata": metadata or {},
    }


def _event(
    *,
    tid: object = TID,
    event: str = "task_started",
    status: str = "running",
    taskspec: object | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "tid": tid,
        "event": event,
        "status": status,
        "taskspec": _taskspec(status=status) if taskspec is None else taskspec,
        **extra,
    }


def _record(**changes: Any) -> FoldedTaskRecord:
    base = FoldedTaskRecord(
        tid=TID,
        tid_short=tid_short_form(TID),
        name="example",
        status="running",
        event="task_started",
        activity="working",
        waiting_on="queue",
        started_at=1_000_000_000,
        completed_at=None,
        return_code=None,
        error=None,
        last_timestamp=2_000_000_000,
        taskspec_payload=_taskspec(),
        metadata={},
        event_payload={},
        runner_diagnostics=None,
        status_reason=None,
    )
    return replace(base, **changes)


def _evidence(**changes: Any) -> SnapshotEvidence:
    base = SnapshotEvidence(
        resolved_runtime_entry=None,
        runtime_handle=None,
        runtime_description=None,
        runtime_observation=None,
        claimed_outbox=None,
        active_service_tid=None,
        selected_active_manager_tid=None,
    )
    return replace(base, **changes)


def _reduce(
    record: FoldedTaskRecord,
    *,
    local: TaskEvidenceSnapshot | None = None,
    stale_reason: str | None = None,
    evidence: SnapshotEvidence | None = None,
    now_ns: int = 5_000_000_000,
) -> CollectedTaskSnapshot:
    draft = prepare_snapshot(record, local_evidence=local)
    plan = plan_snapshot_probes(draft, stale_liveness_reason=stale_reason)
    reduced = reduce_task_snapshot(plan, evidence or _evidence(), now_ns=now_ns)
    assert reduced is not None
    return reduced


def test_reduce_task_event_rejects_malformed_and_filtered_rows() -> None:
    assert reduce_task_event(None, _event(tid=1), 1, tid_filters=None) is None
    assert reduce_task_event(None, _event(), 1, tid_filters={"not-this-task"}) is None
    assert reduce_task_event(None, _event(), 1, tid_filters={TID}) is not None
    assert (
        reduce_task_event(None, _event(), 1, tid_filters={tid_short_form(TID)})
        is not None
    )


def test_reduce_task_event_preserves_activity_and_terminal_precedence() -> None:
    activity = reduce_task_event(
        None,
        {
            "tid": TID,
            "event": "task_activity",
            "status": "running",
            "activity": " working ",
            "waiting_on": " queue ",
        },
        1,
        tid_filters=None,
    )
    assert activity is not None
    assert activity.taskspec_payload is None
    assert activity.activity == "working"
    assert activity.waiting_on == "queue"

    cleared_activity = reduce_task_event(
        activity,
        {
            "tid": TID,
            "event": "task_activity",
            "status": "running",
        },
        2,
        tid_filters=None,
    )
    assert cleared_activity is not None
    assert cleared_activity.activity is None
    assert cleared_activity.waiting_on is None

    running = reduce_task_event(
        activity,
        _event(),
        3,
        tid_filters=None,
    )
    assert running is not None
    completed_payload = _event(
        event="task_completed",
        status="completed",
        taskspec=_taskspec(
            status="completed",
            started_at=1,
            completed_at=3,
        ),
    )
    completed = reduce_task_event(
        running,
        completed_payload,
        4,
        tid_filters=None,
    )
    assert completed is not None
    assert completed.status == "completed"
    assert completed.activity is None
    assert completed.waiting_on is None

    assert (
        reduce_task_event(
            completed,
            {
                "tid": TID,
                "event": "task_activity",
                "status": "running",
                "activity": "regressed",
            },
            5,
            tid_filters=None,
        )
        == completed
    )
    assert (
        reduce_task_event(
            completed,
            _event(status="running"),
            6,
            tid_filters=None,
        )
        == completed
    )
    replacement = reduce_task_event(
        completed,
        _event(
            event="task_failed",
            status="failed",
            taskspec=_taskspec(status="failed", completed_at=7),
            error="replacement",
        ),
        7,
        tid_filters=None,
    )
    assert replacement is not None
    assert replacement.status == "failed"
    assert replacement.error == "replacement"
    assert replacement.last_timestamp == 7


def test_reduce_task_event_keeps_placeholder_for_non_taskspec_row() -> None:
    placeholder = reduce_task_event(
        None,
        _event(taskspec="not-an-object"),
        9,
        tid_filters=None,
    )
    assert placeholder is not None
    assert placeholder.taskspec_payload is None
    assert placeholder.last_timestamp == 9


def test_reduce_task_event_applies_status_precedence_and_copies_inputs() -> None:
    metadata: dict[str, object] = {"tag": ["original"]}
    diagnostics: dict[str, object] = {"phase": {"name": "startup"}}
    taskspec = _taskspec(status="completed", completed_at=3, metadata=metadata)
    payload = _event(
        event="task_completed",
        status="running",
        taskspec=taskspec,
        runner_diagnostics=diagnostics,
    )
    record = reduce_task_event(None, payload, 7, tid_filters=None)
    assert record is not None
    assert record.status == "completed"
    assert record.event_payload is not payload
    assert record.metadata is not metadata
    assert record.runner_diagnostics is not diagnostics

    payload["status"] = "failed"
    metadata["new"] = True
    diagnostics["new"] = True
    assert record.status == "completed"
    assert "new" not in record.metadata
    assert record.runner_diagnostics == {"phase": {"name": "startup"}}


@pytest.mark.parametrize(
    ("lifecycle_status", "local", "stale_reason", "runtime_probe", "claimed_probe"),
    [
        ("completed", None, None, True, False),
        (
            "running",
            TaskEvidenceSnapshot(
                tid=TID,
                status="completed",
                classification="terminal_control",
                source="ctrl_out",
                terminal=True,
                reconciliation={"classification": "terminal_control"},
            ),
            None,
            True,
            False,
        ),
        (
            "running",
            TaskEvidenceSnapshot(
                tid=TID,
                status="completed",
                classification="readable_result",
                source="outbox",
                terminal=True,
            ),
            None,
            True,
            True,
        ),
        ("running", None, None, False, True),
        (
            "running",
            None,
            "superseded_internal_service_record",
            True,
            True,
        ),
    ],
)
def test_plan_snapshot_probes_preserves_conditional_probe_order(
    lifecycle_status: str,
    local: TaskEvidenceSnapshot | None,
    stale_reason: str | None,
    runtime_probe: bool,
    claimed_probe: bool,
) -> None:
    draft = prepare_snapshot(
        _record(status=lifecycle_status),
        local_evidence=local,
    )
    plan = plan_snapshot_probes(draft, stale_liveness_reason=stale_reason)
    assert plan.acquire_runtime_observation is runtime_probe
    assert plan.acquire_claimed_outbox is claimed_probe


def test_reduce_task_snapshot_reports_terminal_runtime_conflicts() -> None:
    record = _record(
        status="completed",
        completed_at=3_000_000_000,
        status_reason="contradictory_terminal_event_status",
    )
    absent = _reduce(record)
    assert absent.snapshot.reconciliation == {
        "classification": "stale_status_payload",
        "reason": "contradictory_terminal_event_status",
        "lifecycle_status": "completed",
        "runtime_evidence": "none",
        "runtime_evidence_strength": "unknown",
    }

    live = _reduce(
        record,
        evidence=_evidence(
            runtime_observation=RuntimeObservation(
                live=True,
                evidence="runner",
                strength="strong",
            )
        ),
    )
    assert live.snapshot.reconciliation == {
        "classification": "runtime_conflict",
        "reason": "contradictory_terminal_event_status",
        "lifecycle_status": "completed",
        "runtime_status": "running",
        "runtime_evidence": "runner",
        "runtime_evidence_strength": "strong",
    }


def test_reduce_task_snapshot_applies_local_and_claimed_field_precedence() -> None:
    local = TaskEvidenceSnapshot(
        tid=TID,
        status="failed",
        classification="terminal_control",
        source="ctrl_out",
        terminal=True,
        observed_at=4_000_000_000,
        error="local error",
        return_code=17,
        reconciliation={"classification": "terminal_control"},
    )
    local_result = _reduce(_record(), local=local)
    assert local_result.snapshot.status == "failed"
    assert local_result.snapshot.completed_at == 4_000_000_000
    assert local_result.snapshot.last_timestamp == 4_000_000_000
    assert local_result.snapshot.return_code == 17
    assert local_result.snapshot.error == "local error"
    assert local_result.snapshot.activity is None
    assert local_result.snapshot.duration_seconds == 3.0

    claimed = replace(
        local,
        classification="claimed_result_without_terminal",
        source="outbox",
        observed_at=6_000_000_000,
        reconciliation={"classification": "claimed_result_without_terminal"},
    )
    claimed_result = _reduce(
        _record(),
        evidence=_evidence(claimed_outbox=claimed),
        now_ns=7_000_000_000,
    )
    assert claimed_result.snapshot.status == "failed"
    assert claimed_result.snapshot.completed_at is None
    assert claimed_result.snapshot.last_timestamp == 6_000_000_000
    assert claimed_result.snapshot.duration_seconds == 6.0


def test_reduce_task_snapshot_handles_stale_and_manager_reconciliation() -> None:
    generic = _reduce(
        _record(),
        stale_reason="host_process_not_live",
    )
    assert generic.snapshot.status == "running"
    assert generic.snapshot.reconciliation == {
        "classification": "stale_liveness",
        "reason": "host_process_not_live",
        "lifecycle_status": "running",
        "public_status": "running",
        "evidence_source": "runtime",
    }

    internal_metadata = {
        "internal": True,
        "role": "task_monitor",
        INTERNAL_SERVICE_KEY_METADATA_KEY: INTERNAL_SERVICE_KEY_TASK_MONITOR,
    }
    internal = _reduce(
        _record(
            metadata=internal_metadata,
            taskspec_payload=_taskspec(metadata=internal_metadata),
        ),
        stale_reason="superseded_internal_service_record",
        evidence=_evidence(active_service_tid=OTHER_TID),
    )
    assert internal.snapshot.status == "failed"
    assert internal.snapshot.reconciliation is not None
    assert internal.snapshot.reconciliation["service_key"] == (
        INTERNAL_SERVICE_KEY_TASK_MONITOR
    )
    assert internal.snapshot.reconciliation["active_service_tid"] == OTHER_TID

    manager_taskspec = _taskspec(metadata={"role": "manager"})
    manager = _reduce(
        _record(metadata={"role": "manager"}, taskspec_payload=manager_taskspec),
        evidence=_evidence(selected_active_manager_tid=OTHER_TID),
    )
    assert manager.snapshot.status == "failed"
    assert manager.snapshot.reconciliation == {
        "classification": "superseded_manager_record",
        "reason": "different_active_manager_selected",
        "lifecycle_status": "failed",
        "active_manager_tid": OTHER_TID,
    }


def test_reduce_task_snapshot_duration_and_optional_output_contract() -> None:
    running = _reduce(_record(), now_ns=500_000_000)
    assert running.snapshot.duration_seconds == 0.0
    assert "reconciliation" not in running.snapshot.to_dict()
    assert "runner_diagnostics" not in running.snapshot.to_dict()
    assert "pipeline_status" not in running.snapshot.to_dict()

    missing = _reduce(_record(started_at=None))
    assert missing.snapshot.duration_seconds is None
    completed = _reduce(
        _record(status="completed", completed_at=3_500_000_000),
    )
    assert completed.snapshot.duration_seconds == 2.5


def test_order_task_snapshots_filters_terminal_and_orders_active_first() -> None:
    records = [
        _reduce(_record(tid="3", tid_short="3", status="completed")),
        _reduce(_record(tid="2", tid_short="2", status="spawning")),
        _reduce(_record(tid="1", tid_short="1", status="running")),
        _reduce(_record(tid="0", tid_short="0", status="created")),
    ]
    assert [
        record.snapshot.tid
        for record in order_task_snapshots(records, include_terminal=True)
    ] == ["1", "2", "0", "3"]
    assert [
        record.snapshot.tid
        for record in order_task_snapshots(records, include_terminal=False)
    ] == ["1", "2", "0"]


def test_reducer_has_no_io_or_runtime_probe_dependencies() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "weft"
        / "commands"
        / "_task_snapshot_reducer.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    forbidden_imports = {
        "simplebroker",
        "weft.context",
        "weft._runner_plugins",
        "weft.commands.system",
        "time",
    }
    forbidden_calls = {"handle_has_live_host_process", "pid_is_live"}
    imported: set[str] = set()
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                imported.add(node.module)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called.add(node.func.id)
    assert not {
        name
        for name in imported
        if any(
            name == item or name.startswith(f"{item}.") for item in forbidden_imports
        )
    }
    assert called.isdisjoint(forbidden_calls)


def test_snapshot_evidence_accepts_value_runner_handle() -> None:
    handle = RunnerHandle(
        runner="host",
        kind="process",
        id="123",
        control={"authority": "external-supervisor"},
    )
    result = _reduce(
        _record(),
        evidence=_evidence(runtime_handle=handle),
    )
    assert result.snapshot.runtime_handle == handle.to_dict()


def test_collector_preserves_exact_compact_snapshot_contract(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    log_queue = ctx.queue("weft.log.tasks", persistent=False)
    payload = _event(activity="working", waiting_on="queue")
    try:
        timestamp = log_queue.write(json.dumps(payload))
    finally:
        log_queue.close()

    records = system._collect_task_snapshot_records(
        ctx,
        include_terminal=True,
        include_log_history=True,
        tid_filters=None,
        now_ns=6_000_000_000,
    )
    assert len(records) == 1
    assert records[0].snapshot.to_dict() == {
        "tid": TID,
        "tid_short": tid_short_form(TID),
        "name": "example",
        "status": "running",
        "event": "task_started",
        "activity": "working",
        "waiting_on": "queue",
        "started_at": 1_000_000_000,
        "completed_at": None,
        "return_code": None,
        "error": None,
        "last_timestamp": timestamp,
        "duration_seconds": 5.0,
        "runner": "host",
        "runtime_handle": None,
        "runtime": None,
        "metadata": {},
    }


def test_collector_combines_tid_filters_terminal_filter_and_ordering(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    rows = [
        ("1700000000000000004", "completed"),
        ("1700000000000000003", "created"),
        ("1700000000000000002", "spawning"),
        ("1700000000000000001", "running"),
    ]
    for tid, status in rows:
        write_task_observation(
            ctx,
            _event(
                tid=tid,
                status=status,
                event="task_completed" if status == "completed" else "task_started",
                taskspec=_taskspec(
                    status=status,
                    completed_at=2_000_000_000 if status == "completed" else None,
                ),
            ),
        )

    all_records = system._collect_task_snapshot_records(
        ctx,
        include_terminal=True,
        tid_filters=None,
        now_ns=6_000_000_000,
    )
    assert [record.snapshot.tid for record in all_records] == [
        "1700000000000000001",
        "1700000000000000002",
        "1700000000000000003",
        "1700000000000000004",
    ]

    filtered = system._collect_task_snapshot_records(
        ctx,
        include_terminal=False,
        tid_filters={
            "1700000000000000001",
            "1700000000000000002",
            "1700000000000000004",
        },
        now_ns=6_000_000_000,
    )
    assert [record.snapshot.tid for record in filtered] == [
        "1700000000000000001",
        "1700000000000000002",
    ]
