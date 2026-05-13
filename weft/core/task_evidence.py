"""Shared read-only task evidence classification helpers.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-3], [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.13], [OBS.17]
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, replace
from typing import Any

from simplebroker.ext import BrokerError
from weft._constants import (
    CONTROL_SURFACE_WAIT_TIMEOUT,
    QUEUE_CTRL_IN_SUFFIX,
    QUEUE_CTRL_OUT_SUFFIX,
    QUEUE_OUTBOX_SUFFIX,
    STATUS_RUNTIMELESS_STALE_AFTER_SECONDS,
    TASK_TERMINAL_ENVELOPE_SOURCES,
    TERMINAL_ENVELOPE_TYPE,
    TERMINAL_TASK_EVENTS,
    TERMINAL_TASK_STATUSES,
    WEFT_GLOBAL_LOG_QUEUE,
    WRAPPER_LOST_ERROR,
)
from weft._runner_plugins import require_runner_plugin
from weft.context import WeftContext
from weft.core.control_probe import send_keyed_ping_probe
from weft.core.outbox import (
    aggregate_public_outputs,
    process_outbox_message,
)
from weft.core.runner_diagnostics import diagnostic_summary
from weft.ext import RunnerHandle
from weft.helpers import handle_has_live_host_process, iter_queue_json_entries


@dataclass(frozen=True, slots=True)
class QueueAckTarget:
    """Exact queue message target safe for post-commit acknowledgement."""

    queue: str
    message_id: int


@dataclass(frozen=True, slots=True)
class TaskTerminalSnapshot:
    """Compact known-TID terminal/live observation.

    This is a non-consuming reconciliation surface. Callers that want cleanup
    must pass ``ack_targets`` to the explicit acknowledgement helper.

    Spec: docs/specifications/13C-Using_Weft_With_Django.md [DJ-2.2]
    """

    tid: str
    status: str
    source: str
    value: Any | None = None
    stdout: str | None = None
    stderr: str | None = None
    error: str | None = None
    return_code: int | None = None
    terminal: bool = False
    ack_targets: tuple[QueueAckTarget, ...] = ()
    observed_at: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TaskEvidenceSnapshot:
    """Read-only classification of the best visible evidence for one task."""

    tid: str
    status: str
    classification: str
    source: str
    terminal: bool
    taskspec_payload: dict[str, Any] | None = None
    event: str | None = None
    activity: str | None = None
    waiting_on: str | None = None
    started_at: int | None = None
    completed_at: int | None = None
    observed_at: int | None = None
    error: str | None = None
    return_code: int | None = None
    value: Any | None = None
    stdout: str | None = None
    stderr: str | None = None
    runtime_handle: dict[str, Any] | None = None
    runtime: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    reconciliation: dict[str, Any] | None = None
    ack_targets: tuple[QueueAckTarget, ...] = ()


@dataclass(frozen=True, slots=True)
class QueueMessageCounts:
    """Visible and retained message counts for one queue."""

    queue: str
    unclaimed: int
    total: int

    @property
    def claimed(self) -> int:
        """Return messages retained by the broker but unavailable to readers."""

        return max(0, self.total - self.unclaimed)


def queue_names_for_tid(
    tid: str,
    taskspec_payload: dict[str, Any] | None,
) -> tuple[str, str]:
    """Return outbox and ctrl_out names for a task."""

    outbox = None
    ctrl_out = None
    if isinstance(taskspec_payload, dict):
        io_section = taskspec_payload.get("io")
        if isinstance(io_section, dict):
            outputs = io_section.get("outputs")
            control = io_section.get("control")
            if isinstance(outputs, dict):
                outbox_candidate = outputs.get("outbox")
                if isinstance(outbox_candidate, str) and outbox_candidate:
                    outbox = outbox_candidate
            if isinstance(control, dict):
                ctrl_out_candidate = control.get("ctrl_out")
                if isinstance(ctrl_out_candidate, str) and ctrl_out_candidate:
                    ctrl_out = ctrl_out_candidate
    prefix = f"T{tid}."
    return (
        outbox or f"{prefix}{QUEUE_OUTBOX_SUFFIX}",
        ctrl_out or f"{prefix}{QUEUE_CTRL_OUT_SUFFIX}",
    )


def control_queue_names_for_tid(
    tid: str,
    taskspec_payload: dict[str, Any] | None,
) -> tuple[str, str]:
    """Return ctrl_in and ctrl_out names for a task."""

    ctrl_in = None
    ctrl_out = None
    if isinstance(taskspec_payload, dict):
        io_section = taskspec_payload.get("io")
        if isinstance(io_section, dict):
            control = io_section.get("control")
            if isinstance(control, dict):
                ctrl_in_candidate = control.get("ctrl_in")
                if isinstance(ctrl_in_candidate, str) and ctrl_in_candidate:
                    ctrl_in = ctrl_in_candidate
                ctrl_out_candidate = control.get("ctrl_out")
                if isinstance(ctrl_out_candidate, str) and ctrl_out_candidate:
                    ctrl_out = ctrl_out_candidate
    prefix = f"T{tid}."
    return (
        ctrl_in or f"{prefix}{QUEUE_CTRL_IN_SUFFIX}",
        ctrl_out or f"{prefix}{QUEUE_CTRL_OUT_SUFFIX}",
    )


def task_is_persistent_payload(taskspec_payload: dict[str, Any] | None) -> bool:
    """Return whether a logged TaskSpec payload is persistent."""

    if not isinstance(taskspec_payload, dict):
        return False
    spec = taskspec_payload.get("spec")
    if not isinstance(spec, dict):
        return False
    return bool(spec.get("persistent"))


def task_is_interactive_payload(taskspec_payload: dict[str, Any] | None) -> bool:
    """Return whether a logged TaskSpec payload is interactive."""

    if not isinstance(taskspec_payload, dict):
        return False
    spec = taskspec_payload.get("spec")
    if not isinstance(spec, dict):
        return False
    return bool(spec.get("interactive"))


def split_stdio(value: Any) -> tuple[str | None, str | None]:
    """Return stdout/stderr fields from a structured result value."""

    if not isinstance(value, dict):
        return None, None
    stdout = value.get("stdout")
    stderr = value.get("stderr")
    return (
        stdout if isinstance(stdout, str) else None,
        stderr if isinstance(stderr, str) else None,
    )


def coerce_terminal_envelope(
    raw: str,
    *,
    tid: str,
) -> dict[str, Any] | None:
    """Return a strict terminal ctrl_out envelope or None."""

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("type") != TERMINAL_ENVELOPE_TYPE:
        return None
    if payload.get("tid") != tid:
        return None
    source = payload.get("source")
    if source not in TASK_TERMINAL_ENVELOPE_SOURCES:
        return None
    status = payload.get("status")
    if not isinstance(status, str) or status not in TERMINAL_TASK_STATUSES:
        return None
    return payload


def terminal_status_from_event(payload: dict[str, Any]) -> str | None:
    """Return the public terminal status represented by a task-log event."""

    if payload.get("event") == "task_activity":
        return None
    status = payload.get("status")
    if not isinstance(status, str):
        taskspec = payload.get("taskspec")
        if isinstance(taskspec, dict):
            state = taskspec.get("state")
            if isinstance(state, dict):
                state_status = state.get("status")
                if isinstance(state_status, str):
                    status = state_status
    if status in TERMINAL_TASK_STATUSES:
        return status
    event = payload.get("event")
    taskspec = payload.get("taskspec")
    state = taskspec.get("state") if isinstance(taskspec, dict) else None
    completed_at = state.get("completed_at") if isinstance(state, dict) else None
    if (
        isinstance(event, str)
        and event in TERMINAL_TASK_EVENTS
        and isinstance(completed_at, int)
    ):
        return TERMINAL_TASK_EVENTS[event]
    return None


def terminal_error_message(payload: dict[str, Any], status: str) -> str | None:
    """Return the best available public error string for a terminal event."""

    error = payload.get("error")
    if isinstance(error, str) and error:
        return error
    taskspec = payload.get("taskspec")
    if isinstance(taskspec, dict):
        state = taskspec.get("state")
        if isinstance(state, dict):
            state_error = state.get("error")
            if isinstance(state_error, str) and state_error:
                return state_error
    diagnostics = payload.get("runner_diagnostics")
    if isinstance(diagnostics, dict):
        summary = diagnostic_summary(diagnostics)
        if summary is not None:
            return summary
    if status == "cancelled":
        return "task cancelled"
    if status == "killed":
        return "task killed"
    return None


def return_code_from_event(payload: dict[str, Any]) -> int | None:
    """Return the best available return code from a task-log event."""

    return_code = payload.get("return_code")
    if isinstance(return_code, int):
        return return_code
    taskspec = payload.get("taskspec")
    state = taskspec.get("state") if isinstance(taskspec, dict) else None
    if isinstance(state, dict) and isinstance(state.get("return_code"), int):
        return int(state["return_code"])
    return None


def reconciliation_for_terminal_ctrl_out(
    snapshot: TaskEvidenceSnapshot,
) -> dict[str, Any]:
    """Build public reconciliation metadata for terminal ctrl_out evidence."""

    reason = (
        "manager_wrapper_lost"
        if snapshot.classification == "wrapper_lost"
        else "terminal_ctrl_out_without_task_log"
    )
    payload: dict[str, Any] = {
        "classification": snapshot.classification,
        "reason": reason,
        "lifecycle_status": snapshot.status,
        "evidence_source": "ctrl_out",
    }
    terminal_source = snapshot.metadata.get("terminal_source")
    if isinstance(terminal_source, str):
        payload["terminal_source"] = terminal_source
    if snapshot.observed_at is not None:
        payload["observed_at"] = snapshot.observed_at
    return payload


def reconciliation_for_result_without_terminal(
    snapshot: TaskEvidenceSnapshot,
) -> dict[str, Any]:
    """Build public reconciliation metadata for final outbox without task log."""

    payload: dict[str, Any] = {
        "classification": "result_without_terminal",
        "reason": "final_outbox_without_terminal_task_log",
        "lifecycle_status": snapshot.status,
        "evidence_source": "outbox",
    }
    if snapshot.observed_at is not None:
        payload["observed_at"] = snapshot.observed_at
    return payload


def reconciliation_for_claimed_result_without_terminal(
    snapshot: TaskEvidenceSnapshot,
) -> dict[str, Any]:
    """Build public reconciliation metadata for claimed outbox residue."""

    payload: dict[str, Any] = {
        "classification": "claimed_result_without_terminal",
        "reason": "claimed_outbox_blocks_result_classification",
        "lifecycle_status": snapshot.status,
        "evidence_source": "outbox",
    }
    queue_name = snapshot.metadata.get("outbox_queue")
    if isinstance(queue_name, str):
        payload["outbox_queue"] = queue_name
    claimed_messages = snapshot.metadata.get("claimed_messages")
    if isinstance(claimed_messages, int):
        payload["claimed_messages"] = claimed_messages
    total_messages = snapshot.metadata.get("total_messages")
    if isinstance(total_messages, int):
        payload["total_messages"] = total_messages
    if snapshot.observed_at is not None:
        payload["observed_at"] = snapshot.observed_at
    return payload


def reconciliation_for_live_pong(
    snapshot: TaskEvidenceSnapshot,
    *,
    reason: str = "matched_control_pong",
) -> dict[str, Any]:
    """Build public reconciliation metadata for matched PONG evidence."""

    payload: dict[str, Any] = {
        "classification": "live_pong",
        "reason": reason,
        "lifecycle_status": snapshot.status,
        "evidence_source": "ctrl_out",
    }
    request_id = snapshot.metadata.get("request_id")
    if isinstance(request_id, str):
        payload["request_id"] = request_id
    if snapshot.observed_at is not None:
        payload["observed_at"] = snapshot.observed_at
    return payload


def _live_pong_snapshot(
    payload: dict[str, Any],
    *,
    tid: str,
    request_id: str,
    observed_at: int,
    taskspec_payload: dict[str, Any] | None,
) -> TaskEvidenceSnapshot:
    status = str(payload["task_status"])
    activity = payload.get("activity")
    waiting_on = payload.get("waiting_on")
    runtime = payload.get("runtime")
    metadata: dict[str, Any] = {"request_id": request_id}
    pong_timestamp = payload.get("timestamp")
    if isinstance(pong_timestamp, int):
        metadata["pong_timestamp"] = pong_timestamp
    runner = payload.get("runner")
    if isinstance(runner, str) and runner:
        metadata["runner"] = runner
    snapshot = TaskEvidenceSnapshot(
        tid=tid,
        status=status,
        classification="live_pong",
        source="ctrl_out",
        terminal=status in TERMINAL_TASK_STATUSES,
        taskspec_payload=taskspec_payload,
        activity=activity if isinstance(activity, str) and activity else None,
        waiting_on=waiting_on if isinstance(waiting_on, str) and waiting_on else None,
        observed_at=observed_at,
        runtime=runtime if isinstance(runtime, dict) else None,
        metadata=metadata,
    )
    return replace(snapshot, reconciliation=reconciliation_for_live_pong(snapshot))


def ping_pong_evidence(
    ctx: WeftContext,
    *,
    tid: str,
    taskspec_payload: dict[str, Any] | None = None,
    timeout: float = CONTROL_SURFACE_WAIT_TIMEOUT,
) -> TaskEvidenceSnapshot | None:
    """Send a keyed PING and return matching live PONG evidence if visible."""

    ctrl_in_name, ctrl_out_name = control_queue_names_for_tid(tid, taskspec_payload)
    result = send_keyed_ping_probe(
        ctx,
        tid=tid,
        ctrl_in_name=ctrl_in_name,
        ctrl_out_name=ctrl_out_name,
        timeout=timeout,
    )
    if result.matched is None:
        return None
    return _live_pong_snapshot(
        result.matched.payload,
        tid=tid,
        request_id=result.matched.request_id,
        observed_at=result.matched.observed_at,
        taskspec_payload=taskspec_payload,
    )


def peek_terminal_ctrl_out_evidence(
    ctx: WeftContext,
    *,
    tid: str,
    ctrl_out_name: str,
    taskspec_payload: dict[str, Any] | None = None,
) -> TaskEvidenceSnapshot | None:
    """Peek typed terminal ctrl_out evidence without consuming it."""

    queue = ctx.queue(ctrl_out_name, persistent=False)
    latest: tuple[dict[str, Any], int] | None = None
    try:
        for item in queue.peek_generator(with_timestamps=True):
            if not isinstance(item, tuple) or len(item) != 2:
                continue
            body, timestamp = item
            payload = coerce_terminal_envelope(str(body), tid=tid)
            if payload is None:
                continue
            timestamp_int = int(timestamp)
            if latest is None or latest[1] <= timestamp_int:
                latest = (payload, timestamp_int)
    finally:
        queue.close()

    if latest is None:
        return None
    payload, timestamp = latest
    source = str(payload.get("source"))
    error = payload.get("error")
    error_text = error if isinstance(error, str) else None
    return_code = payload.get("return_code")
    classification = (
        "wrapper_lost"
        if source == "manager" and error_text == WRAPPER_LOST_ERROR
        else "terminal_ctrl_out"
    )
    snapshot = TaskEvidenceSnapshot(
        tid=tid,
        status=str(payload["status"]),
        classification=classification,
        source="ctrl_out",
        terminal=True,
        taskspec_payload=taskspec_payload,
        observed_at=timestamp,
        error=error_text,
        return_code=return_code if isinstance(return_code, int) else None,
        metadata={"terminal_source": source},
        ack_targets=(QueueAckTarget(queue=ctrl_out_name, message_id=timestamp),),
    )
    return replace(
        snapshot,
        reconciliation=reconciliation_for_terminal_ctrl_out(snapshot),
    )


def peek_final_outbox_evidence(
    ctx: WeftContext,
    *,
    tid: str,
    outbox_name: str,
    taskspec_payload: dict[str, Any] | None,
) -> TaskEvidenceSnapshot | None:
    """Peek conservative final one-shot outbox evidence without consuming it."""

    if task_is_persistent_payload(taskspec_payload) or task_is_interactive_payload(
        taskspec_payload
    ):
        return None
    queue = ctx.queue(outbox_name, persistent=True)
    stream_buffer: list[str] = []
    values: list[Any] = []
    ack_targets: list[QueueAckTarget] = []
    observed_at: int | None = None
    saw_partial = False
    try:
        for item in queue.peek_generator(with_timestamps=True):
            if not isinstance(item, tuple) or len(item) != 2:
                continue
            body, timestamp = item
            final, decoded = process_outbox_message(
                str(body),
                stream_buffer,
                emit_stream=False,
            )
            if not final:
                saw_partial = True
                continue
            if decoded is None:
                continue
            values.append(decoded.value)
            timestamp_int = int(timestamp)
            ack_targets.append(
                QueueAckTarget(queue=outbox_name, message_id=timestamp_int)
            )
            observed_at = timestamp_int
    finally:
        queue.close()

    if saw_partial or len(values) != 1:
        return None
    value = aggregate_public_outputs(values)
    stdout, stderr = split_stdio(value)
    snapshot = TaskEvidenceSnapshot(
        tid=tid,
        status="completed",
        classification="result_without_terminal",
        source="outbox",
        terminal=True,
        taskspec_payload=taskspec_payload,
        observed_at=observed_at,
        value=value,
        stdout=stdout,
        stderr=stderr,
        ack_targets=tuple(ack_targets),
    )
    return replace(
        snapshot,
        reconciliation=reconciliation_for_result_without_terminal(snapshot),
    )


def queue_message_counts(
    ctx: WeftContext,
    queue_name: str,
) -> QueueMessageCounts | None:
    """Return backend-neutral queue counts when the broker exposes them."""

    try:
        with ctx.broker() as db:
            stats = db.get_queue_stat(queue_name)
    except (BrokerError, OSError, RuntimeError):  # pragma: no cover - best effort
        return None

    return QueueMessageCounts(
        queue=queue_name,
        unclaimed=max(0, int(stats.pending)),
        total=max(0, int(stats.total)),
    )


def claimed_outbox_result_evidence(
    ctx: WeftContext,
    *,
    tid: str,
    outbox_name: str,
    taskspec_payload: dict[str, Any] | None,
) -> TaskEvidenceSnapshot | None:
    """Return a recovery diagnostic for claimed one-shot outbox residue.

    Claimed rows prove retained broker state, not readable result content. This
    helper therefore does not decode payloads and does not acknowledge,
    unclaim, or delete anything.

    Spec: [MF-5]
    """

    if task_is_persistent_payload(taskspec_payload) or task_is_interactive_payload(
        taskspec_payload
    ):
        return None
    counts = queue_message_counts(ctx, outbox_name)
    if counts is None or counts.total <= 0:
        return None
    if counts.unclaimed > 0 or counts.claimed <= 0:
        return None
    observed_at = time.time_ns()
    snapshot = TaskEvidenceSnapshot(
        tid=tid,
        status="failed",
        classification="claimed_result_without_terminal",
        source="outbox",
        terminal=True,
        taskspec_payload=taskspec_payload,
        observed_at=observed_at,
        error=(
            f"Task {tid} result output is claimed in {outbox_name} and requires "
            "explicit recovery"
        ),
        metadata={
            "outbox_queue": outbox_name,
            "claimed_messages": counts.claimed,
            "total_messages": counts.total,
            "unclaimed_messages": counts.unclaimed,
        },
    )
    return replace(
        snapshot,
        reconciliation=reconciliation_for_claimed_result_without_terminal(snapshot),
    )


def task_local_terminal_evidence(
    ctx: WeftContext,
    *,
    tid: str,
    taskspec_payload: dict[str, Any] | None,
) -> TaskEvidenceSnapshot | None:
    """Return terminal task-local evidence when visible."""

    outbox_name, ctrl_out_name = queue_names_for_tid(tid, taskspec_payload)
    ctrl_snapshot = peek_terminal_ctrl_out_evidence(
        ctx,
        tid=tid,
        ctrl_out_name=ctrl_out_name,
        taskspec_payload=taskspec_payload,
    )
    if ctrl_snapshot is not None:
        return ctrl_snapshot
    return peek_final_outbox_evidence(
        ctx,
        tid=tid,
        outbox_name=outbox_name,
        taskspec_payload=taskspec_payload,
    )


def log_terminal_evidence(
    payload: dict[str, Any],
    *,
    tid: str,
    timestamp: int | None = None,
) -> TaskEvidenceSnapshot | None:
    """Return terminal task-log evidence from one payload when present."""

    status = terminal_status_from_event(payload)
    if status is None:
        return None
    taskspec = payload.get("taskspec")
    taskspec_payload = taskspec if isinstance(taskspec, dict) else None
    return TaskEvidenceSnapshot(
        tid=tid,
        status=status,
        classification="terminal_log",
        source="log",
        terminal=True,
        taskspec_payload=taskspec_payload,
        event=payload.get("event") if isinstance(payload.get("event"), str) else None,
        observed_at=timestamp,
        error=terminal_error_message(payload, status),
        return_code=return_code_from_event(payload),
    )


def _runtime_handle_from_mapping(entry: dict[str, Any] | None) -> RunnerHandle | None:
    if not isinstance(entry, dict):
        return None
    payload = entry.get("runtime_handle")
    if not isinstance(payload, dict):
        return None
    try:
        return RunnerHandle.from_dict(payload)
    except ValueError:
        return None


def _runtime_description(handle: RunnerHandle | None) -> dict[str, Any] | None:
    if handle is None:
        return None
    if handle.control.get("authority") == "external-supervisor":
        return {
            "runner": handle.runner,
            "id": handle.id,
            "state": "unknown",
            "metadata": {
                **dict(handle.observations),
                **dict(handle.metadata),
            },
        }
    try:
        plugin = require_runner_plugin(handle.runner)
        runtime = plugin.describe(handle)
    except Exception as exc:  # pragma: no cover - defensive integration guard
        return {
            "runner": handle.runner,
            "id": handle.id,
            "state": "unknown",
            "metadata": {"describe_error": str(exc)},
        }
    if runtime is None:
        return None
    return runtime.to_dict()


def runtime_evidence(
    *,
    tid: str,
    mapping_entry: dict[str, Any] | None,
) -> TaskEvidenceSnapshot | None:
    """Return live runtime evidence from the latest TID mapping."""

    handle = _runtime_handle_from_mapping(mapping_entry)
    runtime = _runtime_description(handle)
    live = False
    if handle is not None and handle.control.get("authority") == "host-pid":
        live = handle_has_live_host_process(handle)
    elif isinstance(runtime, dict):
        state = runtime.get("state")
        live = isinstance(state, str) and state.strip().lower() not in {
            "missing",
            "exited",
            "dead",
            "stopped",
            "terminated",
            "unknown",
        }
    if not live:
        return None
    return TaskEvidenceSnapshot(
        tid=tid,
        status="running",
        classification="live",
        source="runtime",
        terminal=False,
        runtime_handle=handle.to_dict() if handle is not None else None,
        runtime=runtime,
    )


def bounded_log_terminal_evidence(
    ctx: WeftContext,
    *,
    tid: str,
) -> tuple[TaskEvidenceSnapshot | None, bool, int | None]:
    """Return latest bounded log terminal evidence and prior-live hints."""

    log_queue = ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    latest_terminal: TaskEvidenceSnapshot | None = None
    prior_live = False
    latest_prior_live_at: int | None = None
    try:
        since_timestamp = int(tid) - 1 if tid.isdigit() else None
        for payload, timestamp in iter_queue_json_entries(
            log_queue,
            since_timestamp=since_timestamp,
        ):
            if payload.get("tid") != tid:
                continue
            status = payload.get("status")
            if isinstance(status, str) and status in {"spawning", "running"}:
                prior_live = True
                latest_prior_live_at = int(timestamp)
            terminal = log_terminal_evidence(payload, tid=tid, timestamp=timestamp)
            if terminal is not None:
                latest_terminal = terminal
        return latest_terminal, prior_live, latest_prior_live_at
    finally:
        log_queue.close()


def stale_observer_evidence(
    *,
    tid: str,
    prior_live: bool,
    prior_live_at: int | None,
    created_waiting: bool = False,
) -> TaskEvidenceSnapshot | None:
    """Return stale observer fallback evidence for a previously-live task."""

    if not prior_live or prior_live_at is None:
        return None
    age_ns = time.time_ns() - prior_live_at
    stale_ns = int(STATUS_RUNTIMELESS_STALE_AFTER_SECONDS * 1_000_000_000)
    if age_ns <= stale_ns:
        return TaskEvidenceSnapshot(
            tid=tid,
            status="pending",
            classification="unknown",
            source="observer",
            terminal=False,
        )
    classification = "stale_created" if created_waiting else "stale_liveness"
    return TaskEvidenceSnapshot(
        tid=tid,
        status="pending",
        classification=classification,
        source="observer",
        error="Task is not live and no terminal task-local state is visible",
        terminal=False,
        observed_at=time.time_ns(),
        reconciliation={
            "classification": classification,
            "reason": "not_live_without_terminal_task_local_state",
            "lifecycle_status": "pending",
            "evidence_source": "observer",
        },
    )


def known_tid_evidence(
    ctx: WeftContext,
    *,
    tid: str,
    taskspec_payload: dict[str, Any] | None = None,
    mapping_entry: dict[str, Any] | None = None,
    ping: bool = False,
    probe_timeout: float = CONTROL_SURFACE_WAIT_TIMEOUT,
) -> TaskEvidenceSnapshot | None:
    """Return the best non-consuming evidence for a known full TID."""

    log_snapshot, log_prior_live, log_prior_live_at = bounded_log_terminal_evidence(
        ctx,
        tid=tid,
    )
    read_only_snapshot: TaskEvidenceSnapshot | None = log_snapshot

    if read_only_snapshot is None:
        local_snapshot = task_local_terminal_evidence(
            ctx,
            tid=tid,
            taskspec_payload=taskspec_payload,
        )
        if local_snapshot is not None:
            read_only_snapshot = local_snapshot

    if read_only_snapshot is None:
        runtime_snapshot = runtime_evidence(tid=tid, mapping_entry=mapping_entry)
        if runtime_snapshot is not None:
            read_only_snapshot = runtime_snapshot

    if read_only_snapshot is None:
        prior_live = log_prior_live or isinstance(
            (mapping_entry or {}).get("runtime_handle"),
            dict,
        )
        prior_live_at = log_prior_live_at
        mapping_timestamp = (
            mapping_entry.get("timestamp") if isinstance(mapping_entry, dict) else None
        )
        if isinstance(mapping_timestamp, int):
            prior_live_at = max(prior_live_at or 0, mapping_timestamp)
        stale_snapshot = stale_observer_evidence(
            tid=tid,
            prior_live=prior_live,
            prior_live_at=prior_live_at,
        )
        if stale_snapshot is not None:
            outbox_name, _ctrl_out_name = queue_names_for_tid(tid, taskspec_payload)
            claimed_snapshot = claimed_outbox_result_evidence(
                ctx,
                tid=tid,
                outbox_name=outbox_name,
                taskspec_payload=taskspec_payload,
            )
            read_only_snapshot = claimed_snapshot or stale_snapshot

    if not ping:
        return read_only_snapshot

    pong_snapshot = ping_pong_evidence(
        ctx,
        tid=tid,
        taskspec_payload=taskspec_payload,
        timeout=probe_timeout,
    )
    if pong_snapshot is None:
        return read_only_snapshot
    if read_only_snapshot is None:
        return pong_snapshot

    existing_at = read_only_snapshot.observed_at
    pong_at = pong_snapshot.observed_at
    if (
        read_only_snapshot.terminal
        and isinstance(existing_at, int)
        and isinstance(pong_at, int)
        and existing_at >= pong_at
    ):
        return read_only_snapshot
    if read_only_snapshot.terminal and (
        not isinstance(existing_at, int) or not isinstance(pong_at, int)
    ):
        return read_only_snapshot
    if read_only_snapshot.terminal:
        return replace(
            pong_snapshot,
            reconciliation=reconciliation_for_live_pong(
                pong_snapshot,
                reason="live_pong_after_terminal_evidence",
            ),
        )
    if (
        isinstance(existing_at, int)
        and isinstance(pong_at, int)
        and existing_at > pong_at
    ):
        return read_only_snapshot
    return pong_snapshot


def terminal_snapshot_from_evidence(
    evidence: TaskEvidenceSnapshot,
) -> TaskTerminalSnapshot:
    """Convert shared evidence to the compact known-TID terminal snapshot."""

    source = evidence.source
    if source == "log":
        source = "log_fallback"
    metadata = {
        **dict(evidence.metadata),
        "classification": evidence.classification,
    }
    if evidence.runtime is not None:
        metadata["runtime"] = evidence.runtime
    return TaskTerminalSnapshot(
        tid=evidence.tid,
        status=evidence.status,
        source=source,
        value=evidence.value,
        stdout=evidence.stdout,
        stderr=evidence.stderr,
        error=evidence.error,
        return_code=evidence.return_code,
        terminal=evidence.terminal,
        ack_targets=evidence.ack_targets,
        observed_at=evidence.observed_at,
        metadata=metadata,
    )
