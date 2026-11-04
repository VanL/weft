"""Task observation helpers shared by task commands and event streams.

Deadline arithmetic and snapshot synthesis used by both ``weft task status``
and the task event streams, so neither command module imports the other.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/14-Python_API_Surfaces.md [PY-2]
"""

from __future__ import annotations

import time
from typing import Any

from weft._constants import TERMINAL_TASK_STATUSES
from weft.core.task_evidence import TaskEvidenceSnapshot
from weft.helpers import encode_tid

from ._task_snapshot_reducer import TaskSnapshot
from .types import TaskSnapshot as PublicTaskSnapshot


def _deadline_from_timeout(timeout: float | None) -> float | None:
    if timeout is None:
        return None
    return time.monotonic() + max(0.0, timeout)


def _remaining_timeout(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    return max(0.0, deadline - time.monotonic())


def _deadline_expired(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline


def _state_from_taskspec(taskspec_payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(taskspec_payload, dict):
        return {}
    state = taskspec_payload.get("state")
    return state if isinstance(state, dict) else {}


def _public_snapshot(
    status_snapshot: TaskSnapshot,
    *,
    taskspec_payload: dict[str, Any] | None,
) -> PublicTaskSnapshot:
    state = _state_from_taskspec(taskspec_payload)
    error = state.get("error")
    return_code = state.get("return_code")
    return PublicTaskSnapshot(
        tid=status_snapshot.tid,
        tid_short=status_snapshot.tid_short,
        name=status_snapshot.name,
        status=status_snapshot.status,
        event=status_snapshot.event,
        activity=status_snapshot.activity,
        waiting_on=status_snapshot.waiting_on,
        return_code=(
            status_snapshot.return_code
            if isinstance(status_snapshot.return_code, int)
            else return_code
            if isinstance(return_code, int)
            else None
        ),
        started_at=status_snapshot.started_at,
        completed_at=status_snapshot.completed_at,
        error=(
            status_snapshot.error
            if isinstance(status_snapshot.error, str) and status_snapshot.error
            else error
            if isinstance(error, str) and error
            else None
        ),
        last_timestamp=status_snapshot.last_timestamp,
        duration_seconds=status_snapshot.duration_seconds,
        runner=status_snapshot.runner,
        runtime_handle=status_snapshot.runtime_handle,
        runtime=status_snapshot.runtime,
        metadata=dict(status_snapshot.metadata),
        pipeline_status=status_snapshot.pipeline_status,
        reconciliation=(
            dict(status_snapshot.reconciliation)
            if isinstance(status_snapshot.reconciliation, dict)
            else None
        ),
        runner_diagnostics=(
            dict(status_snapshot.runner_diagnostics)
            if isinstance(status_snapshot.runner_diagnostics, dict)
            else None
        ),
    )


def _task_snapshot_from_live_pong(
    tid: str,
    evidence: TaskEvidenceSnapshot,
    *,
    base_snapshot: TaskSnapshot | None,
    taskspec_payload: dict[str, Any] | None,
) -> TaskSnapshot:
    state = taskspec_payload.get("state") if isinstance(taskspec_payload, dict) else {}
    state = state if isinstance(state, dict) else {}
    spec = taskspec_payload.get("spec") if isinstance(taskspec_payload, dict) else {}
    spec = spec if isinstance(spec, dict) else {}
    metadata: dict[str, Any] = {}
    if base_snapshot is not None:
        metadata.update(base_snapshot.metadata)
    task_metadata = (
        taskspec_payload.get("metadata") if isinstance(taskspec_payload, dict) else None
    )
    if base_snapshot is None and isinstance(task_metadata, dict):
        metadata.update(task_metadata)

    observed_at = evidence.observed_at or time.time_ns()
    started_at = (
        base_snapshot.started_at
        if base_snapshot is not None
        else state.get("started_at")
        if isinstance(state.get("started_at"), int)
        else None
    )
    completed_at = (
        base_snapshot.completed_at
        if base_snapshot is not None
        else state.get("completed_at")
        if isinstance(state.get("completed_at"), int)
        else None
    )
    if (
        evidence.status not in TERMINAL_TASK_STATUSES
        or evidence.classification == "claimed_result_without_terminal"
    ):
        completed_at = None
    elif not isinstance(completed_at, int):
        completed_at = evidence.completed_at or (
            observed_at
            if evidence.classification == "result_without_terminal"
            else None
        )

    if isinstance(started_at, int) and not isinstance(completed_at, int):
        duration = max(0.0, (time.time_ns() - started_at) / 1_000_000_000)
    elif isinstance(started_at, int) and isinstance(completed_at, int):
        duration = max(0.0, (completed_at - started_at) / 1_000_000_000)
    else:
        duration = None

    runtime = evidence.runtime or (
        base_snapshot.runtime if base_snapshot is not None else None
    )
    runner = (
        str(runtime.get("runner"))
        if isinstance(runtime, dict) and isinstance(runtime.get("runner"), str)
        else base_snapshot.runner
        if base_snapshot is not None
        else _runner_name_from_taskspec(spec)
    )
    name = (
        base_snapshot.name
        if base_snapshot is not None
        else str(taskspec_payload.get("name") or tid)
        if isinstance(taskspec_payload, dict)
        else tid
    )
    return TaskSnapshot(
        tid=tid,
        tid_short=encode_tid(tid),
        name=name,
        status=evidence.status,
        event="live_pong",
        activity=evidence.activity
        if evidence.status not in TERMINAL_TASK_STATUSES
        else None,
        waiting_on=evidence.waiting_on
        if evidence.status not in TERMINAL_TASK_STATUSES
        else None,
        started_at=started_at if isinstance(started_at, int) else None,
        completed_at=completed_at if isinstance(completed_at, int) else None,
        return_code=evidence.return_code
        if evidence.return_code is not None
        else base_snapshot.return_code
        if base_snapshot is not None
        else None,
        error=evidence.error
        if evidence.error is not None
        else base_snapshot.error
        if base_snapshot is not None
        else None,
        last_timestamp=observed_at,
        duration_seconds=duration,
        runner=runner,
        runtime_handle=base_snapshot.runtime_handle
        if base_snapshot is not None
        else None,
        runtime=runtime,
        metadata=metadata,
        pipeline_status=base_snapshot.pipeline_status
        if base_snapshot is not None
        else None,
        reconciliation=evidence.reconciliation,
        runner_diagnostics=base_snapshot.runner_diagnostics
        if base_snapshot is not None
        else None,
    )


def _runner_name_from_taskspec(spec: dict[str, Any]) -> str | None:
    runner = spec.get("runner")
    if isinstance(runner, dict):
        name = runner.get("name")
        return name if isinstance(name, str) and name else None
    return None
