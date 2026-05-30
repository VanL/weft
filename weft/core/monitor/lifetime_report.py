"""Reusable TaskMonitor lifetime report builders.

The records built here are operational evidence only. They do not decide
cleanup eligibility, scan queues, write files, or mutate Monitor tables.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.13], [OBS.13.12], [OBS.17]
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import Any

from weft._constants import (
    TASK_LIFETIME_REPORT_RECORD_TYPE,
    TASK_MONITOR_POLICY_MONITOR_STORE_LIFECYCLE,
    TERMINAL_TASK_EVENTS,
    TERMINAL_TASK_STATUSES,
    WEFT_LOG_TASKS_EXTERNAL_SCHEMA_VERSION,
)
from weft.core.monitor.store import MonitorTaskCollationRecord
from weft.core.pruning.models import CleanupCandidate
from weft.core.queue_window import QueueWindowRow


def build_collation_lifetime_report(
    record: MonitorTaskCollationRecord,
    *,
    monitor_tid: str,
    emitted_at_ns: int,
    source_policy: str = TASK_MONITOR_POLICY_MONITOR_STORE_LIFECYCLE,
    report_kind: str = "monitor_collation",
    close_reason: str,
    observations: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a lifetime report from one Monitor collation record."""

    subject = {"tid": record.tid}
    lifetime = _lifetime_from_collation(record, close_reason=close_reason)
    return build_lifetime_report(
        monitor_tid=monitor_tid,
        emitted_at_ns=emitted_at_ns,
        source_policy=source_policy,
        report_kind=report_kind,
        completeness="collated",
        subject=subject,
        lifetime=lifetime,
        taskspec=_taskspec_from_collation(record),
        monitor=_monitor_from_collation(record),
        observations=observations,
    )


def build_candidate_lifetime_report(
    candidate: CleanupCandidate,
    *,
    monitor_tid: str,
    emitted_at_ns: int,
    report_kind: str | None = None,
    completeness: str | None = None,
    observations: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a baseline report for one cleanup candidate."""

    subject = {
        "queue": candidate.queue,
        "message_id": candidate.message_id,
    }
    if candidate.tid is not None:
        subject["tid"] = candidate.tid
    metadata = dict(candidate.metadata)
    taskspec = _taskspec_from_mapping(metadata.pop("taskspec", None))
    candidate_observations: dict[str, Any] = {
        "candidate_class": candidate.candidate_class,
        "reason": candidate.reason,
    }
    if candidate.payload_sha256 is not None:
        candidate_observations["payload_sha256"] = candidate.payload_sha256
    if candidate.payload_size_bytes is not None:
        candidate_observations["payload_size_bytes"] = candidate.payload_size_bytes
    if metadata:
        candidate_observations["metadata"] = metadata
    candidate_observations.update(dict(observations or {}))
    return build_lifetime_report(
        monitor_tid=monitor_tid,
        emitted_at_ns=emitted_at_ns,
        source_policy=candidate.policy,
        report_kind=report_kind or candidate.candidate_class,
        completeness=completeness or "state_only",
        subject=subject,
        lifetime=_lifetime_from_candidate(
            candidate,
            taskspec=taskspec,
            close_reason=candidate.reason,
        ),
        taskspec=taskspec,
        monitor=_monitor_from_candidate(candidate),
        observations=candidate_observations,
    )


def build_raw_row_lifetime_report(
    row: QueueWindowRow,
    *,
    monitor_tid: str,
    emitted_at_ns: int,
    source_policy: str,
    report_kind: str,
    close_reason: str,
    tid: str | None = None,
    completeness: str = "raw_row",
    observations: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a degraded report for one raw broker row."""

    payload = _json_object(row.body)
    subject: dict[str, Any] = {
        "queue": row.queue,
        "message_id": row.message_id,
    }
    if tid is not None:
        subject["tid"] = tid
    elif payload is not None:
        payload_tid = payload.get("tid")
        if isinstance(payload_tid, str) and payload_tid:
            subject["tid"] = payload_tid
            tid = payload_tid
    raw_observations = {
        "payload_size_bytes": len(row.body.encode("utf-8")),
        **dict(observations or {}),
    }
    taskspec = _taskspec_from_task_log_payload(payload)
    return build_lifetime_report(
        monitor_tid=monitor_tid,
        emitted_at_ns=emitted_at_ns,
        source_policy=source_policy,
        report_kind=report_kind,
        completeness=completeness,
        subject=subject,
        lifetime=_lifetime_from_task_log_payload(
            payload,
            tid=tid,
            close_reason=close_reason,
        ),
        taskspec=taskspec,
        monitor={
            "queue": row.queue,
            "message_id": row.message_id,
        },
        observations=raw_observations,
    )


def build_inferred_tid_lifetime_report(
    *,
    tid: str,
    monitor_tid: str,
    emitted_at_ns: int,
    source_policy: str,
    report_kind: str,
    close_reason: str,
    queue_names: Sequence[str] = (),
    observations: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a baseline report for a TID inferred from runtime evidence."""

    merged_observations: dict[str, Any] = dict(observations or {})
    if queue_names:
        merged_observations["queue_names"] = list(queue_names)
    monitor = {"queue_names": list(queue_names)} if queue_names else None
    return build_lifetime_report(
        monitor_tid=monitor_tid,
        emitted_at_ns=emitted_at_ns,
        source_policy=source_policy,
        report_kind=report_kind,
        completeness="inferred",
        subject={"tid": tid},
        lifetime=baseline_lifetime(tid=tid, close_reason=close_reason),
        monitor=monitor,
        observations=merged_observations,
    )


def build_lifetime_report(
    *,
    monitor_tid: str,
    emitted_at_ns: int,
    source_policy: str,
    report_kind: str,
    completeness: str,
    subject: Mapping[str, Any],
    lifetime: Mapping[str, Any],
    taskspec: Mapping[str, Any] | None = None,
    monitor: Mapping[str, Any] | None = None,
    observations: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a complete task lifetime report envelope."""

    subject_dict = dict(subject)
    lifetime_dict = _complete_lifetime(lifetime)
    report: dict[str, Any] = {
        "schema_version": WEFT_LOG_TASKS_EXTERNAL_SCHEMA_VERSION,
        "record_type": TASK_LIFETIME_REPORT_RECORD_TYPE,
        "report_id": stable_report_id(
            source_policy=source_policy,
            report_kind=report_kind,
            completeness=completeness,
            subject=subject_dict,
            lifetime=lifetime_dict,
            monitor=monitor,
            observations=observations,
        ),
        "emitted_at_ns": int(emitted_at_ns),
        "monitor_tid": monitor_tid,
        "source_policy": source_policy,
        "report_kind": report_kind,
        "completeness": completeness,
        "subject": subject_dict,
        "lifetime": lifetime_dict,
        "taskspec": dict(taskspec) if taskspec is not None else None,
        "monitor": dict(monitor) if monitor is not None else None,
        "observations": dict(observations or {}),
    }
    return report


def stable_report_id(
    *,
    source_policy: str,
    report_kind: str,
    completeness: str,
    subject: Mapping[str, Any],
    lifetime: Mapping[str, Any],
    monitor: Mapping[str, Any] | None = None,
    observations: Mapping[str, Any] | None = None,
) -> str:
    """Return a deterministic report id from stable report identity fields."""

    identity = {
        "record_type": TASK_LIFETIME_REPORT_RECORD_TYPE,
        "source_policy": source_policy,
        "report_kind": report_kind,
        "completeness": completeness,
        "subject": dict(subject),
        "close_reason": lifetime.get("close_reason"),
        "terminal_message_id": (
            monitor.get("terminal_message_id") if isinstance(monitor, Mapping) else None
        ),
        "last_message_id": (
            monitor.get("last_message_id") if isinstance(monitor, Mapping) else None
        ),
        "collation_kind": (
            monitor.get("collation_kind") if isinstance(monitor, Mapping) else None
        ),
        "service": (monitor.get("service") if isinstance(monitor, Mapping) else None),
        "observation_identity": _observation_identity(observations),
    }
    encoded = json.dumps(
        identity,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return f"task-lifetime:{sha256(encoded).hexdigest()}"


def baseline_lifetime(
    *,
    tid: str | None,
    close_reason: str,
) -> dict[str, Any]:
    """Return the common lifetime shape with unknown values set to ``None``."""

    return _complete_lifetime({"tid": tid, "close_reason": close_reason})


def _lifetime_from_candidate(
    candidate: CleanupCandidate,
    *,
    taskspec: Mapping[str, Any] | None,
    close_reason: str,
) -> dict[str, Any]:
    metadata = candidate.metadata
    state = _mapping(taskspec.get("state") if taskspec is not None else None)
    status = _string(metadata.get("status")) or _string(
        state.get("status") if state is not None else None
    )
    event = _string(metadata.get("event"))
    terminal_status = _terminal_status(event=event, status=status)
    return _complete_lifetime(
        {
            "tid": candidate.tid,
            "name": _string(taskspec.get("name") if taskspec is not None else None),
            "runner": _runner_from_taskspec(taskspec),
            "parent_tid": _metadata_string(taskspec, "parent_tid"),
            "role": _metadata_string(taskspec, "role"),
            "status": status,
            "terminal_seen": terminal_status is not None,
            "terminal_status": terminal_status,
            "return_code": _int_or_none(
                state.get("return_code") if state is not None else None
            ),
            "started_at_ns": _int_or_none(
                state.get("started_at") if state is not None else None
            ),
            "completed_at_ns": _int_or_none(
                state.get("completed_at") if state is not None else None
            ),
            "close_reason": close_reason,
        }
    )


def _monitor_from_candidate(candidate: CleanupCandidate) -> dict[str, Any]:
    monitor: dict[str, Any] = {
        "queue": candidate.queue,
        "message_id": candidate.message_id,
        "candidate_class": candidate.candidate_class,
        "reason": candidate.reason,
    }
    if candidate.payload_sha256 is not None:
        monitor["payload_sha256"] = candidate.payload_sha256
    if candidate.payload_size_bytes is not None:
        monitor["payload_size_bytes"] = candidate.payload_size_bytes
    return monitor


def _lifetime_from_task_log_payload(
    payload: Mapping[str, Any] | None,
    *,
    tid: str | None,
    close_reason: str,
) -> dict[str, Any]:
    taskspec = _taskspec_from_task_log_payload(payload)
    state = _mapping(taskspec.get("state") if taskspec is not None else None)
    status = _string(payload.get("status") if payload is not None else None) or _string(
        state.get("status") if state is not None else None
    )
    event = _string(payload.get("event") if payload is not None else None)
    terminal_status = _terminal_status(event=event, status=status)
    return _complete_lifetime(
        {
            "tid": tid,
            "name": _string(taskspec.get("name") if taskspec is not None else None),
            "runner": _runner_from_taskspec(taskspec),
            "parent_tid": _metadata_string(taskspec, "parent_tid"),
            "role": _metadata_string(taskspec, "role"),
            "status": status,
            "terminal_seen": terminal_status is not None,
            "terminal_status": terminal_status,
            "return_code": _int_or_none(
                state.get("return_code") if state is not None else None
            ),
            "started_at_ns": _int_or_none(
                state.get("started_at") if state is not None else None
            ),
            "completed_at_ns": _int_or_none(
                state.get("completed_at") if state is not None else None
            ),
            "close_reason": close_reason,
        }
    )


def _lifetime_from_collation(
    record: MonitorTaskCollationRecord,
    *,
    close_reason: str,
) -> dict[str, Any]:
    return _complete_lifetime(
        {
            "tid": record.tid,
            "name": record.name,
            "runner": record.runner,
            "parent_tid": record.parent_tid,
            "role": record.role,
            "status": record.status,
            "terminal_seen": record.terminal_seen,
            "terminal_status": record.terminal_status,
            "return_code": record.return_code,
            "first_seen_at_ns": record.first_seen_at_ns,
            "last_seen_at_ns": record.last_seen_at_ns,
            "started_at_ns": record.started_at_ns,
            "completed_at_ns": record.completed_at_ns,
            "close_reason": close_reason,
        }
    )


def _taskspec_from_task_log_payload(
    payload: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if payload is None:
        return None
    return _taskspec_from_mapping(payload.get("taskspec"))


def _taskspec_from_mapping(value: object) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return dict(value)
    return None


def _taskspec_from_collation(record: MonitorTaskCollationRecord) -> dict[str, Any]:
    """Return the TaskSpec-shaped payload that anchors a collated report."""

    taskspec = dict(record.taskspec_summary)
    taskspec.setdefault("tid", record.tid)
    if record.name is not None:
        taskspec.setdefault("name", record.name)

    state = taskspec.get("state")
    if isinstance(state, Mapping):
        state_dict = dict(state)
    else:
        state_dict = {}
    if record.status is not None:
        state_dict.setdefault("status", record.status)
    if record.started_at_ns is not None:
        state_dict.setdefault("started_at", record.started_at_ns)
    if record.completed_at_ns is not None:
        state_dict.setdefault("completed_at", record.completed_at_ns)
    if record.return_code is not None:
        state_dict.setdefault("return_code", record.return_code)
    if state_dict:
        taskspec["state"] = state_dict

    metadata = taskspec.get("metadata")
    if isinstance(metadata, Mapping):
        metadata_dict = dict(metadata)
    else:
        metadata_dict = {}
    if record.parent_tid is not None:
        metadata_dict.setdefault("parent_tid", record.parent_tid)
    if record.role is not None:
        metadata_dict.setdefault("role", record.role)
    if metadata_dict:
        taskspec["metadata"] = metadata_dict

    return taskspec


def _monitor_from_collation(record: MonitorTaskCollationRecord) -> dict[str, Any]:
    """Return compact Monitor provenance for a collated lifetime report."""

    classification = record.service_classification()
    monitor: dict[str, Any] = {
        "first_message_id": record.first_message_id,
        "last_message_id": record.last_message_id,
        "terminal_message_id": record.terminal_message_id,
        "terminal_event": record.terminal_event,
        "collation_kind": classification.kind,
        "reserved_probe_needed": record.reserved_probe_needed,
    }
    if record.suspect_reason is not None:
        monitor["suspect_reason"] = record.suspect_reason
    if record.disposition_reason is not None:
        monitor["disposition_reason"] = record.disposition_reason
    if classification.is_service_record:
        monitor["service"] = classification.to_summary()
    return monitor


def _json_object(value: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def _runner_from_taskspec(taskspec: Mapping[str, Any] | None) -> str | None:
    spec = _mapping(taskspec.get("spec") if taskspec is not None else None)
    runner = _mapping(spec.get("runner") if spec is not None else None)
    return _string(runner.get("name") if runner is not None else None)


def _metadata_string(
    taskspec: Mapping[str, Any] | None,
    key: str,
) -> str | None:
    metadata = _mapping(taskspec.get("metadata") if taskspec is not None else None)
    return _string(metadata.get(key) if metadata is not None else None)


def _terminal_status(*, event: str | None, status: str | None) -> str | None:
    if event is not None:
        return TERMINAL_TASK_EVENTS.get(event)
    if status in TERMINAL_TASK_STATUSES:
        return status
    return None


def _complete_lifetime(values: Mapping[str, Any]) -> dict[str, Any]:
    complete = {
        "tid": None,
        "name": None,
        "runner": None,
        "parent_tid": None,
        "role": None,
        "status": None,
        "terminal_seen": False,
        "terminal_status": None,
        "return_code": None,
        "first_seen_at_ns": None,
        "last_seen_at_ns": None,
        "started_at_ns": None,
        "completed_at_ns": None,
        "close_reason": None,
    }
    complete.update(dict(values))
    return complete


def _observation_identity(observations: Mapping[str, Any] | None) -> dict[str, Any]:
    if observations is None:
        return {}
    selected: dict[str, Any] = {}
    for key in (
        "queue_names",
        "queue_name",
        "message_id",
        "message_ids",
        "candidate_class",
        "reason",
        "payload_sha256",
    ):
        if key in observations:
            selected[key] = observations[key]
    return selected
