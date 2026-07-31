"""Pure task snapshot reconstruction policy.

This module folds already-read task events and reconciles already-acquired
runtime and queue evidence. It performs no queue, clock, process, manager, or
runner-plugin I/O.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/09-Implementation_Plan.md [IP-1], [IP-1.0]
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Set
from dataclasses import dataclass, replace
from typing import Any

from weft._constants import (
    INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT,
    INTERNAL_RUNTIME_TASK_CLASS_KEY,
    INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR,
    INTERNAL_SERVICE_KEY_HEARTBEAT,
    INTERNAL_SERVICE_KEY_METADATA_KEY,
    INTERNAL_SERVICE_KEY_TASK_MONITOR,
    TASKSPEC_TID_SHORT_LENGTH,
    TERMINAL_TASK_EVENTS,
    TERMINAL_TASK_STATUSES,
)
from weft.core.task_evidence import TaskEvidenceSnapshot
from weft.ext import RunnerHandle


@dataclass(frozen=True)
class TaskSnapshot:
    """Richer task snapshot used by system status and service collation."""

    tid: str
    tid_short: str
    name: str
    status: str
    event: str
    activity: str | None
    waiting_on: str | None
    started_at: int | None
    completed_at: int | None
    last_timestamp: int
    duration_seconds: float | None
    runner: str | None
    runtime_handle: dict[str, Any] | None
    runtime: dict[str, Any] | None
    metadata: dict[str, Any]
    pipeline_status: dict[str, Any] | None = None
    reconciliation: dict[str, Any] | None = None
    runner_diagnostics: dict[str, Any] | None = None
    return_code: int | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the stable public dictionary representation."""

        payload = {
            "tid": self.tid,
            "tid_short": self.tid_short,
            "name": self.name,
            "status": self.status,
            "event": self.event,
            "activity": self.activity,
            "waiting_on": self.waiting_on,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "return_code": self.return_code,
            "error": self.error,
            "last_timestamp": self.last_timestamp,
            "duration_seconds": self.duration_seconds,
            "runner": self.runner,
            "runtime_handle": self.runtime_handle,
            "runtime": self.runtime,
            "metadata": self.metadata,
        }
        if self.pipeline_status is not None:
            payload["pipeline_status"] = self.pipeline_status
        if self.reconciliation is not None:
            payload["reconciliation"] = self.reconciliation
        if self.runner_diagnostics is not None:
            payload["runner_diagnostics"] = self.runner_diagnostics
        return payload


@dataclass(frozen=True, slots=True)
class CollectedTaskSnapshot:
    """Internal snapshot plus TaskSpec payload collected in the same replay."""

    snapshot: TaskSnapshot
    taskspec_payload: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class FoldedTaskRecord:
    """Immutable result of reducing ordered log events for one TID."""

    tid: str
    tid_short: str
    name: str
    status: str
    event: str
    activity: str | None
    waiting_on: str | None
    started_at: int | None
    completed_at: int | None
    return_code: int | None
    error: str | None
    last_timestamp: int
    taskspec_payload: dict[str, Any] | None
    metadata: dict[str, Any]
    event_payload: dict[str, Any] | None
    runner_diagnostics: dict[str, Any] | None
    status_reason: str | None


@dataclass(frozen=True, slots=True)
class SnapshotDraft:
    """Policy state after lifecycle and task-local evidence, before probes."""

    record: FoldedTaskRecord
    lifecycle_status: str
    public_status: str
    local_evidence: TaskEvidenceSnapshot | None


@dataclass(frozen=True, slots=True)
class RuntimeObservation:
    """Host/runtime liveness result; absence means the probe was not run."""

    live: bool
    evidence: str
    strength: str


@dataclass(frozen=True, slots=True)
class SnapshotProbePlan:
    """Pure policy decision made after stale-liveness classification."""

    draft: SnapshotDraft
    stale_liveness_reason: str | None
    provisional_public_status: str
    acquire_runtime_observation: bool
    acquire_claimed_outbox: bool


@dataclass(frozen=True, slots=True)
class SnapshotEvidence:
    """All I/O observations acquired outside final reduction."""

    resolved_runtime_entry: Mapping[str, Any] | None
    runtime_handle: RunnerHandle | None
    runtime_description: Mapping[str, Any] | None
    runtime_observation: RuntimeObservation | None
    claimed_outbox: TaskEvidenceSnapshot | None
    active_service_tid: str | None
    selected_active_manager_tid: str | None


@dataclass(frozen=True, slots=True)
class _SelectedSnapshotPolicy:
    public_status: str
    local_evidence: TaskEvidenceSnapshot | None
    reconciliation: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class _ResolvedSnapshotFields:
    completed_at: int | None
    return_code: int | None
    error: str | None
    last_timestamp: int


def reduce_task_event(
    current: FoldedTaskRecord | None,
    payload: Mapping[str, Any],
    timestamp: int,
    *,
    tid_filters: Set[str] | None,
) -> FoldedTaskRecord | None:
    """Apply one already-read event without I/O or shared mutation."""

    tid = payload.get("tid")
    if not isinstance(tid, str):
        return current
    if (
        tid_filters is not None
        and tid not in tid_filters
        and tid[-TASKSPEC_TID_SHORT_LENGTH:] not in tid_filters
    ):
        return current

    record = current or FoldedTaskRecord(
        tid=tid,
        tid_short=tid[-TASKSPEC_TID_SHORT_LENGTH:],
        name=tid,
        status="created",
        event="unknown",
        activity=None,
        waiting_on=None,
        started_at=None,
        completed_at=None,
        return_code=None,
        error=None,
        last_timestamp=timestamp,
        taskspec_payload=None,
        metadata={},
        event_payload=None,
        runner_diagnostics=None,
        status_reason=None,
    )
    current_terminal = record.status in TERMINAL_TASK_STATUSES
    event = payload.get("event", "unknown")

    if event == "task_activity":
        if current_terminal:
            return record
        status = payload.get("status")
        next_status = status if isinstance(status, str) and status else record.status
        if next_status in TERMINAL_TASK_STATUSES:
            activity = None
            waiting_on = None
        else:
            activity = _normalized_text(payload.get("activity"))
            waiting_on = _normalized_text(payload.get("waiting_on"))
        return replace(
            record,
            status=next_status,
            event=event,
            activity=activity,
            waiting_on=waiting_on,
            last_timestamp=timestamp,
        )

    taskspec = payload.get("taskspec")
    if not isinstance(taskspec, dict):
        return record
    state_raw = taskspec.get("state") or {}
    state = state_raw if isinstance(state_raw, Mapping) else {}
    status, status_reason = _reconcile_lifecycle_status(payload, state)
    if current_terminal and status not in TERMINAL_TASK_STATUSES:
        return record

    started_at = state.get("started_at")
    completed_at = state.get("completed_at")
    return_code = state.get("return_code")
    state_error = state.get("error")
    payload_error = payload.get("error")
    metadata = taskspec.get("metadata") or {}
    terminal = status in TERMINAL_TASK_STATUSES
    activity = None if terminal else record.activity
    waiting_on = None if terminal else record.waiting_on
    incoming_activity = _normalized_text(payload.get("activity"))
    incoming_waiting_on = _normalized_text(payload.get("waiting_on"))
    if not terminal and incoming_activity is not None:
        activity = incoming_activity
    if not terminal and incoming_waiting_on is not None:
        waiting_on = incoming_waiting_on

    return FoldedTaskRecord(
        tid=tid,
        tid_short=tid[-TASKSPEC_TID_SHORT_LENGTH:],
        name=str(taskspec.get("name") or payload.get("name") or tid),
        status=status,
        event=event if isinstance(event, str) else record.event,
        activity=activity,
        waiting_on=waiting_on,
        started_at=started_at if isinstance(started_at, int) else None,
        completed_at=completed_at if isinstance(completed_at, int) else None,
        return_code=return_code if isinstance(return_code, int) else None,
        error=(
            payload_error
            if isinstance(payload_error, str) and payload_error
            else state_error
            if isinstance(state_error, str) and state_error
            else None
        ),
        last_timestamp=timestamp,
        taskspec_payload=dict(taskspec),
        metadata=dict(metadata) if isinstance(metadata, dict) else {},
        event_payload=dict(payload),
        runner_diagnostics=(
            dict(diagnostics)
            if isinstance((diagnostics := payload.get("runner_diagnostics")), Mapping)
            else None
        ),
        status_reason=status_reason,
    )


def prepare_snapshot(
    record: FoldedTaskRecord,
    *,
    local_evidence: TaskEvidenceSnapshot | None,
) -> SnapshotDraft:
    """Apply lifecycle and local-evidence precedence before external probes."""

    public_status = (
        local_evidence.status
        if local_evidence is not None and local_evidence.terminal
        else record.status
    )
    return SnapshotDraft(
        record=record,
        lifecycle_status=record.status,
        public_status=public_status,
        local_evidence=local_evidence,
    )


def plan_snapshot_probes(
    draft: SnapshotDraft,
    *,
    stale_liveness_reason: str | None,
) -> SnapshotProbePlan:
    """Choose provisional status and the remaining probes without I/O."""

    provisional_status = draft.public_status
    if stale_liveness_reason in {
        "superseded_internal_service_record",
        "internal_service_runtime_missing_after_stale_window",
    }:
        provisional_status = "failed"
    acquire_claimed = draft.lifecycle_status not in TERMINAL_TASK_STATUSES and (
        draft.local_evidence is None or draft.local_evidence.reconciliation is None
    )
    return SnapshotProbePlan(
        draft=draft,
        stale_liveness_reason=stale_liveness_reason,
        provisional_public_status=provisional_status,
        acquire_runtime_observation=provisional_status in TERMINAL_TASK_STATUSES,
        acquire_claimed_outbox=acquire_claimed,
    )


def reduce_task_snapshot(
    probe_plan: SnapshotProbePlan,
    evidence: SnapshotEvidence,
    *,
    now_ns: int,
) -> CollectedTaskSnapshot | None:
    """Apply MF-5 precedence and build one public snapshot."""

    record = probe_plan.draft.record
    taskspec = record.taskspec_payload
    if taskspec is None:
        return None

    selected = _select_snapshot_policy(
        probe_plan,
        evidence,
        taskspec=taskspec,
    )
    fields = _resolve_snapshot_fields(record, selected.local_evidence)
    duration = _duration_seconds(
        started_at=record.started_at,
        completed_at=fields.completed_at,
        now_ns=now_ns,
    )
    activity = record.activity
    waiting_on = record.waiting_on
    if selected.public_status in TERMINAL_TASK_STATUSES:
        activity = None
        waiting_on = None

    runtime_description = evidence.runtime_description
    snapshot = TaskSnapshot(
        tid=record.tid,
        tid_short=record.tid_short,
        name=record.name,
        status=selected.public_status,
        event=record.event,
        activity=activity,
        waiting_on=waiting_on,
        started_at=record.started_at,
        completed_at=fields.completed_at,
        return_code=fields.return_code,
        error=fields.error,
        last_timestamp=fields.last_timestamp,
        duration_seconds=duration,
        runner=runner_name_for_snapshot(
            taskspec=taskspec,
            mapping_entry=evidence.resolved_runtime_entry,
        ),
        runtime_handle=(
            evidence.runtime_handle.to_dict()
            if evidence.runtime_handle is not None
            else None
        ),
        runtime=(
            dict(runtime_description)
            if isinstance(runtime_description, Mapping)
            else None
        ),
        metadata=dict(record.metadata),
        reconciliation=selected.reconciliation,
        runner_diagnostics=(
            dict(record.runner_diagnostics)
            if record.runner_diagnostics is not None
            else None
        ),
    )
    return CollectedTaskSnapshot(snapshot=snapshot, taskspec_payload=dict(taskspec))


def _select_snapshot_policy(
    probe_plan: SnapshotProbePlan,
    evidence: SnapshotEvidence,
    *,
    taskspec: Mapping[str, Any],
) -> _SelectedSnapshotPolicy:
    lifecycle_status = probe_plan.draft.lifecycle_status
    public_status = probe_plan.provisional_public_status
    local_evidence = probe_plan.draft.local_evidence
    reconciliation = _reconciliation_diagnostic(
        lifecycle_status=public_status,
        status_reason=probe_plan.draft.record.status_reason,
        runtime_observation=evidence.runtime_observation,
    )
    if local_evidence is not None and local_evidence.reconciliation is not None:
        reconciliation = dict(local_evidence.reconciliation)
    elif evidence.claimed_outbox is not None:
        local_evidence = evidence.claimed_outbox
        reconciliation = (
            dict(local_evidence.reconciliation)
            if local_evidence.reconciliation is not None
            else None
        )
        public_status = local_evidence.status
    elif probe_plan.stale_liveness_reason is not None:
        reconciliation = _stale_liveness_reconciliation(
            reason=probe_plan.stale_liveness_reason,
            lifecycle_status=lifecycle_status,
            public_status=public_status,
            service_key=service_key_from_taskspec(taskspec),
            active_service_tid=evidence.active_service_tid,
        )
    record = probe_plan.draft.record
    if (
        lifecycle_status not in TERMINAL_TASK_STATUSES
        and (local_evidence is None or not local_evidence.terminal)
        and _is_manager_task_payload(taskspec)
        and evidence.selected_active_manager_tid is not None
        and record.tid != evidence.selected_active_manager_tid
    ):
        public_status = "failed"
        reconciliation = _superseded_manager_reconciliation(
            active_manager_tid=evidence.selected_active_manager_tid,
        )
    return _SelectedSnapshotPolicy(
        public_status=public_status,
        local_evidence=local_evidence,
        reconciliation=reconciliation,
    )


def _resolve_snapshot_fields(
    record: FoldedTaskRecord,
    local_evidence: TaskEvidenceSnapshot | None,
) -> _ResolvedSnapshotFields:
    completed_at = record.completed_at
    return_code = record.return_code
    error = record.error
    last_timestamp = record.last_timestamp
    if local_evidence is not None:
        if local_evidence.return_code is not None:
            return_code = local_evidence.return_code
        if local_evidence.error is not None:
            error = local_evidence.error
        if (
            completed_at is None
            and local_evidence.observed_at is not None
            and local_evidence.classification != "claimed_result_without_terminal"
        ):
            completed_at = local_evidence.observed_at
        if local_evidence.observed_at is not None:
            last_timestamp = max(last_timestamp, local_evidence.observed_at)
    return _ResolvedSnapshotFields(
        completed_at=completed_at,
        return_code=return_code,
        error=error,
        last_timestamp=last_timestamp,
    )


def order_task_snapshots(
    records: Iterable[CollectedTaskSnapshot],
    *,
    include_terminal: bool,
) -> list[CollectedTaskSnapshot]:
    """Apply the existing terminal filter and stable ordering."""

    result = [
        record
        for record in records
        if include_terminal or record.snapshot.status not in TERMINAL_TASK_STATUSES
    ]
    result.sort(
        key=lambda record: (
            record.snapshot.status not in {"running", "spawning"},
            record.snapshot.tid,
        )
    )
    return result


def _normalized_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _reconcile_lifecycle_status(
    payload: Mapping[str, Any],
    state: Mapping[str, Any],
) -> tuple[str, str | None]:
    payload_status = payload.get("status")
    if isinstance(payload_status, str) and payload_status in TERMINAL_TASK_STATUSES:
        return payload_status, None
    state_status = state.get("status")
    if isinstance(state_status, str) and state_status in TERMINAL_TASK_STATUSES:
        return state_status, None
    completed_at = state.get("completed_at")
    event = payload.get("event")
    if (
        isinstance(completed_at, int)
        and isinstance(event, str)
        and event in TERMINAL_TASK_EVENTS
    ):
        return TERMINAL_TASK_EVENTS[event], "contradictory_terminal_event_status"
    if isinstance(payload_status, str) and payload_status:
        return payload_status, None
    if isinstance(state_status, str) and state_status:
        return state_status, None
    return "created", None


def _reconciliation_diagnostic(
    *,
    lifecycle_status: str,
    status_reason: str | None,
    runtime_observation: RuntimeObservation | None,
) -> dict[str, Any] | None:
    if lifecycle_status not in TERMINAL_TASK_STATUSES:
        return None
    observation = runtime_observation or RuntimeObservation(
        live=False,
        evidence="none",
        strength="unknown",
    )
    if status_reason == "contradictory_terminal_event_status":
        diagnostic: dict[str, Any] = {
            "classification": (
                "runtime_conflict" if observation.live else "stale_status_payload"
            ),
            "reason": status_reason,
            "lifecycle_status": lifecycle_status,
            "runtime_evidence": observation.evidence,
            "runtime_evidence_strength": observation.strength,
        }
        if observation.live:
            diagnostic["runtime_status"] = "running"
        return diagnostic
    if not observation.live:
        return None
    reason = (
        "weak_host_pid_ignored_for_terminal_lifecycle"
        if observation.evidence == "host-pid" and observation.strength == "weak"
        else "terminal_lifecycle_with_live_runtime"
    )
    return {
        "classification": "runtime_conflict",
        "reason": reason,
        "lifecycle_status": lifecycle_status,
        "runtime_status": "running",
        "runtime_evidence": observation.evidence,
        "runtime_evidence_strength": observation.strength,
    }


def _stale_liveness_reconciliation(
    *,
    reason: str,
    lifecycle_status: str,
    public_status: str,
    service_key: str | None,
    active_service_tid: str | None,
) -> dict[str, Any]:
    internal_reasons = {
        "superseded_internal_service_record",
        "internal_service_runtime_missing_after_stale_window",
    }
    payload: dict[str, Any] = {
        "classification": reason if reason in internal_reasons else "stale_liveness",
        "reason": reason,
        "lifecycle_status": lifecycle_status,
        "public_status": public_status,
        "evidence_source": (
            "service-registry" if reason in internal_reasons else "runtime"
        ),
    }
    if service_key is not None:
        payload["service_key"] = service_key
    if active_service_tid is not None:
        payload["active_service_tid"] = active_service_tid
    return payload


def _superseded_manager_reconciliation(*, active_manager_tid: str) -> dict[str, Any]:
    return {
        "classification": "superseded_manager_record",
        "reason": "different_active_manager_selected",
        "lifecycle_status": "failed",
        "active_manager_tid": active_manager_tid,
    }


def _duration_seconds(
    *,
    started_at: int | None,
    completed_at: int | None,
    now_ns: int,
) -> float | None:
    if started_at is None:
        return None
    end = completed_at if completed_at is not None else now_ns
    return max(0.0, (end - started_at) / 1_000_000_000)


def runner_name_for_snapshot(
    *,
    taskspec: Mapping[str, Any],
    mapping_entry: Mapping[str, Any] | None,
) -> str | None:
    """Resolve the public runner name from acquired snapshot values."""

    if mapping_entry is not None:
        mapped_runner = mapping_entry.get("runner")
        if isinstance(mapped_runner, str) and mapped_runner.strip():
            return mapped_runner
        handle_payload = mapping_entry.get("runtime_handle")
        if isinstance(handle_payload, Mapping):
            try:
                return RunnerHandle.from_dict(handle_payload).runner
            except ValueError:
                pass
    spec = taskspec.get("spec")
    if not isinstance(spec, Mapping):
        return None
    runner = spec.get("runner")
    if not isinstance(runner, Mapping):
        return "host"
    name = runner.get("name", "host")
    return name if isinstance(name, str) and name.strip() else "host"


def _is_manager_task_payload(taskspec: Mapping[str, Any]) -> bool:
    metadata = taskspec.get("metadata")
    return isinstance(metadata, Mapping) and metadata.get("role") == "manager"


def service_key_from_taskspec(
    taskspec_payload: Mapping[str, Any],
) -> str | None:
    """Return the internal-service key claimed by a TaskSpec payload."""

    metadata = taskspec_payload.get("metadata")
    if not isinstance(metadata, Mapping):
        return None
    key = metadata.get(INTERNAL_SERVICE_KEY_METADATA_KEY)
    if not isinstance(key, str) or key not in {
        INTERNAL_SERVICE_KEY_HEARTBEAT,
        INTERNAL_SERVICE_KEY_TASK_MONITOR,
    }:
        return None
    if metadata.get("internal") is True:
        return key
    role = metadata.get("role")
    if key == INTERNAL_SERVICE_KEY_HEARTBEAT and role == "heartbeat_service":
        return key
    if key == INTERNAL_SERVICE_KEY_TASK_MONITOR and role == "task_monitor":
        return key
    runtime_class = metadata.get(INTERNAL_RUNTIME_TASK_CLASS_KEY)
    if (
        key == INTERNAL_SERVICE_KEY_HEARTBEAT
        and runtime_class == INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT
    ) or (
        key == INTERNAL_SERVICE_KEY_TASK_MONITOR
        and runtime_class == INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR
    ):
        return key
    return None
