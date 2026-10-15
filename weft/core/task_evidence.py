"""Shared read-only task evidence classification helpers.

Spec references:
- docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]
- docs/specifications/05-Message_Flow_and_State.md [MF-3], [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.13], [OBS.13.13], [OBS.17]
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Literal, cast

from simplebroker.ext import BrokerError
from weft._constants import (
    CONTROL_SURFACE_WAIT_TIMEOUT,
    PIPELINE_RUNTIME_METADATA_KEY,
    QUEUE_CTRL_IN_SUFFIX,
    QUEUE_CTRL_OUT_SUFFIX,
    QUEUE_OUTBOX_SUFFIX,
    RUNNER_DIAGNOSTICS_FIELD,
    STATUS_RUNTIMELESS_STALE_AFTER_SECONDS,
    TASK_HISTORY_MAX_MESSAGE_ID,
    TASK_LIFECYCLE_STATUS_VALUES,
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
from weft.core.queue_window import queue_broker
from weft.core.runner_diagnostics import diagnostic_summary
from weft.core.task_state import read_task_state_snapshot
from weft.ext import RunnerHandle
from weft.helpers import (
    closing_queue_iterator,
    handle_has_live_host_process,
)


@dataclass(frozen=True, slots=True)
class TaskObservationConfig:
    """Immutable resolved observation routes and collection policy [MF-5]."""

    mode: Literal["one_shot", "persistent", "pipeline"]
    stream_output: bool
    inbox: str
    outbox: str
    ctrl_in: str
    ctrl_out: str
    pipeline_status: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """Serialize the versioned task-state observation value."""
        return {
            "version": 1,
            "mode": self.mode,
            "stream_output": self.stream_output,
            "routes": {
                "inbox": self.inbox,
                "outbox": self.outbox,
                "ctrl_in": self.ctrl_in,
                "ctrl_out": self.ctrl_out,
                "pipeline_status": self.pipeline_status,
            },
        }


def _queue_identifier(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value != "[REDACTED]"


def parse_task_observation(payload: Any) -> TaskObservationConfig | None:
    """Validate a complete observation wire value once [MF-5]."""
    if not isinstance(payload, dict) or set(payload) != {
        "version",
        "mode",
        "stream_output",
        "routes",
    }:
        return None
    if type(payload["version"]) is not int or payload["version"] != 1:
        return None
    mode = payload["mode"]
    if (
        mode not in ("one_shot", "persistent", "pipeline")
        or type(payload["stream_output"]) is not bool
    ):
        return None
    routes = payload["routes"]
    if not isinstance(routes, dict) or set(routes) != {
        "inbox",
        "outbox",
        "ctrl_in",
        "ctrl_out",
        "pipeline_status",
    }:
        return None
    if not all(
        _queue_identifier(routes[key])
        for key in ("inbox", "outbox", "ctrl_in", "ctrl_out")
    ):
        return None
    if (mode == "pipeline" and not _queue_identifier(routes["pipeline_status"])) or (
        mode != "pipeline" and routes["pipeline_status"] is not None
    ):
        return None
    return TaskObservationConfig(
        cast(Literal["one_shot", "persistent", "pipeline"], mode),
        payload["stream_output"],
        routes["inbox"],
        routes["outbox"],
        routes["ctrl_in"],
        routes["ctrl_out"],
        routes["pipeline_status"],
    )


def observation_config_from_taskspec_payload(
    payload: Any,
) -> TaskObservationConfig | None:
    """Read required configuration, allowing unrelated TaskSpec redaction [MF-5]."""
    if not isinstance(payload, dict):
        return None
    spec, metadata, io = (payload.get(key) for key in ("spec", "metadata", "io"))
    if (
        not isinstance(spec, dict)
        or not isinstance(metadata, dict)
        or not isinstance(io, dict)
    ):
        return None
    if any(
        type(spec.get(key)) is not bool
        for key in ("persistent", "interactive", "stream_output")
    ):
        return None
    role = metadata.get("role")
    if role is not None and (not isinstance(role, str) or role == "[REDACTED]"):
        return None
    mode = (
        "pipeline"
        if role == "pipeline"
        else "persistent"
        if spec["persistent"] or spec["interactive"]
        else "one_shot"
    )
    inputs, outputs, control = (io.get(key) for key in ("inputs", "outputs", "control"))
    if (
        not isinstance(inputs, dict)
        or not isinstance(outputs, dict)
        or not isinstance(control, dict)
    ):
        return None
    pipeline_status = None
    if mode == "pipeline":
        runtime = metadata.get(PIPELINE_RUNTIME_METADATA_KEY, {})
        if not isinstance(runtime, dict) or not isinstance(
            runtime.get("queues", {}), dict
        ):
            return None
        pipeline_status = runtime.get("queues", {}).get("status")
    return parse_task_observation(
        {
            "version": 1,
            "mode": mode,
            "stream_output": spec["stream_output"],
            "routes": {
                "inbox": inputs.get("inbox"),
                "outbox": outputs.get("outbox"),
                "ctrl_in": control.get("ctrl_in"),
                "ctrl_out": control.get("ctrl_out"),
                "pipeline_status": pipeline_status,
            },
        }
    )


def observation_config_from_taskspec(
    taskspec: Any,
    *,
    routes: Mapping[str, str] | None = None,
) -> TaskObservationConfig:
    """Convert an accepted resolved TaskSpec to its canonical config [MF-5]."""
    payload = taskspec.model_dump(
        mode="json",
        include={
            "tid": True,
            "spec": {"persistent", "interactive", "stream_output"},
            "io": True,
            "metadata": {"role", PIPELINE_RUNTIME_METADATA_KEY},
        },
    )
    # Accepted TaskSpecs use the same default expansion as BaseTask queue wiring.
    # Queue payload discovery stays strict and never supplies these defaults.
    prefix = f"T{taskspec.tid}."
    for section, roles in (
        ("inputs", ("inbox",)),
        ("outputs", ("outbox",)),
        ("control", ("ctrl_in", "ctrl_out")),
    ):
        for role in roles:
            if routes is not None:
                payload["io"][section][role] = routes[role]
            else:
                payload["io"][section].setdefault(role, prefix + role)
    if payload["metadata"].get("role") == "pipeline":
        runtime = payload["metadata"].setdefault(PIPELINE_RUNTIME_METADATA_KEY, {})
        if isinstance(runtime, dict):
            queues = runtime.setdefault("queues", {})
            if isinstance(queues, dict):
                queues.setdefault("status", f"P{taskspec.tid}.status")
    config = observation_config_from_taskspec_payload(payload)
    if config is None:
        raise ValueError("TaskSpec does not contain complete observation configuration")
    return config


def parse_task_projection(payload: Any, *, terminal: bool) -> dict[str, Any] | None:
    """Validate lifecycle projection independently of route metadata [MF-5]."""
    required = {"version", "status", "event", "started_at", "metadata"}
    optional = {"completed_at", "return_code", "error"}
    if (
        not isinstance(payload, dict)
        or not required <= set(payload)
        or set(payload) - required - optional
    ):
        return None
    if type(payload["version"]) is not int or payload["version"] != 1:
        return None
    status = payload["status"]
    if (
        not isinstance(status, str)
        or status not in TASK_LIFECYCLE_STATUS_VALUES
        or (status in TERMINAL_TASK_STATUSES) != terminal
    ):
        return None
    if (
        not isinstance(payload["event"], str)
        or not payload["event"].strip()
        or not isinstance(payload["metadata"], dict)
    ):
        return None
    started = payload["started_at"]
    if started is not None and (type(started) is not int or started < 0):
        return None
    if optional & set(payload) and not terminal:
        return None
    if any(
        key in payload and type(payload[key]) is not int
        for key in ("completed_at", "return_code")
    ):
        return None
    completed = payload.get("completed_at")
    if completed is not None and (
        completed < 0 or (started is not None and completed < started)
    ):
        return None
    if "error" in payload and (
        not isinstance(payload["error"], str) or not payload["error"].strip()
    ):
        return None
    return dict(payload) if _json_metadata_valid(payload["metadata"]) else None


def _json_metadata_valid(metadata: dict[str, Any]) -> bool:
    """Reject non-JSON metadata at the queue boundary."""
    try:
        json.dumps(metadata, allow_nan=False)
    except (TypeError, ValueError):
        return False
    return True


def task_state_terminal_evidence(
    tid: str, payload: dict[str, Any], message_id: int
) -> TaskEvidenceSnapshot | None:
    """Convert a validated task-owned terminal projection [MF-5]."""
    projection = parse_task_projection(
        payload.get("projection"), terminal=payload.get("terminal") is True
    )
    if payload.get("full") != tid or projection is None or not payload.get("terminal"):
        return None
    return TaskEvidenceSnapshot(
        tid=tid,
        status=projection["status"],
        classification="terminal_projection",
        source="task_state",
        terminal=True,
        event=projection["event"],
        started_at=projection["started_at"],
        completed_at=projection.get("completed_at"),
        observed_at=message_id,
        error=projection.get("error"),
        return_code=projection.get("return_code"),
        metadata=projection["metadata"],
    )


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

    Spec: docs/specifications/09-Implementation_Plan.md [IP-1.1];
    docs/specifications/05-Message_Flow_and_State.md [MF-5]
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


def state_timestamp_from_log_payload(
    payload: dict[str, Any],
    key: str,
) -> int | None:
    """Return an integer timestamp from a task-log TaskSpec state."""

    taskspec = payload.get("taskspec")
    if not isinstance(taskspec, dict):
        return None
    state = taskspec.get("state")
    if not isinstance(state, dict):
        return None
    value = state.get(key)
    return value if isinstance(value, int) else None


def monitor_failure_classification(snapshot: TaskEvidenceSnapshot) -> str:
    """Return the shared monitor-facing failure classification."""

    if snapshot.classification == "terminal_log" and snapshot.status != "completed":
        return "domain_failure"
    return snapshot.classification


def task_name_from_taskspec(
    taskspec_payload: dict[str, Any] | None,
) -> str | None:
    """Return a non-empty task name from a TaskSpec payload."""

    if not isinstance(taskspec_payload, dict):
        return None
    name = taskspec_payload.get("name")
    return name if isinstance(name, str) and name else None


def status_from_log_payload(payload: Mapping[str, Any]) -> str | None:
    """Return the top-level or nested TaskSpec state status for a log row."""

    status = payload.get("status")
    if isinstance(status, str) and status:
        return status
    taskspec = payload.get("taskspec")
    state = taskspec.get("state") if isinstance(taskspec, Mapping) else None
    state_status = state.get("status") if isinstance(state, Mapping) else None
    return state_status if isinstance(state_status, str) and state_status else None


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
    if not isinstance(source, str) or source not in TASK_TERMINAL_ENVELOPE_SOURCES:
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
    diagnostics = payload.get(RUNNER_DIAGNOSTICS_FIELD)
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
        "evidence_source": "control-pong",
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
        source="control-pong",
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
    broker: Any | None = None,
    ctrl_in_name: str | None = None,
    ctrl_out_name: str | None = None,
) -> TaskEvidenceSnapshot | None:
    """Send a keyed PING and return matching live PONG evidence if visible."""

    default_in, _default_out = control_queue_names_for_tid(tid, taskspec_payload)
    ctrl_in_name = ctrl_in_name or default_in
    result = send_keyed_ping_probe(
        ctx,
        tid=tid,
        ctrl_in_name=ctrl_in_name,
        timeout=timeout,
        broker=broker,
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


def _candidate_is_wrapper_lost(payload: Mapping[str, Any]) -> bool:
    """Whether a terminal envelope is a manager-authored wrapper-lost failsafe.

    Spec: [MF-5]
    """

    return (
        payload.get("source") == "manager"
        and payload.get("error") == WRAPPER_LOST_ERROR
    )


def select_terminal_envelope(
    candidates: Iterable[tuple[dict[str, Any], int]],
) -> tuple[dict[str, Any], int] | None:
    """Select the authoritative terminal ctrl_out envelope from candidates.

    Each candidate is ``(payload, message_id)`` where ``payload`` is a coerced
    terminal envelope and ``message_id`` is its broker queue timestamp. A
    task-owned terminal envelope takes precedence over a manager-authored
    ``wrapper_lost`` failsafe regardless of timestamp: the manager only writes
    ``wrapper_lost`` when no task proof was visible at write time, so a real task
    verdict that becomes durable late must not be overwritten by that failsafe.
    Within the same precedence class, the latest ``message_id`` wins.

    Returns the chosen ``(payload, message_id)`` candidate, or ``None`` when there
    are no candidates.

    Spec: [MF-5]
    """

    best: tuple[dict[str, Any], int] | None = None
    best_key: tuple[int, int] | None = None
    for payload, message_id in candidates:
        message_id_int = int(message_id)
        key = (0 if _candidate_is_wrapper_lost(payload) else 1, message_id_int)
        if best_key is None or key > best_key:
            best = (payload, message_id_int)
            best_key = key
    return best


def _terminal_control_candidates(
    rows: Iterable[tuple[str, int] | str], *, tid: str
) -> Iterator[tuple[dict[str, Any], int]]:
    """Coerce exact-TID terminal rows for the shared selector [MF-5]."""
    for item in rows:
        if not isinstance(item, tuple) or len(item) != 2:
            continue
        body, timestamp = item
        payload = coerce_terminal_envelope(str(body), tid=tid)
        if payload is not None:
            yield payload, int(timestamp)


def _terminal_control_snapshot(
    selected: tuple[dict[str, Any], int] | None,
    *,
    tid: str,
    ctrl_out_name: str,
    taskspec_payload: dict[str, Any] | None,
) -> TaskEvidenceSnapshot | None:
    """Retain producer time separately from queue identity [MF-5], [OBS.13.13]."""
    if selected is None:
        return None
    payload, timestamp = selected
    source = str(payload.get("source"))
    error = payload.get("error")
    error_text = error if isinstance(error, str) else None
    return_code = payload.get("return_code")
    reported_at = payload.get("timestamp")
    classification = (
        "wrapper_lost" if _candidate_is_wrapper_lost(payload) else "terminal_ctrl_out"
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
        metadata={
            "terminal_source": source,
            "terminal_reported_at_ns": (
                reported_at
                if isinstance(reported_at, int) and not isinstance(reported_at, bool)
                else None
            ),
        },
        ack_targets=(QueueAckTarget(queue=ctrl_out_name, message_id=timestamp),),
    )
    return replace(
        snapshot, reconciliation=reconciliation_for_terminal_ctrl_out(snapshot)
    )


def peek_terminal_ctrl_out_evidence(
    ctx: WeftContext,
    *,
    tid: str,
    ctrl_out_name: str,
    taskspec_payload: dict[str, Any] | None = None,
    broker: Any | None = None,
) -> TaskEvidenceSnapshot | None:
    """Peek typed terminal ctrl_out evidence without consuming it [MF-5]."""
    with queue_broker(ctx, ctrl_out_name, broker=broker) as db:
        iterator = db.peek_generator(ctrl_out_name, with_timestamps=True)
        with closing_queue_iterator(iterator) as rows:
            selected = select_terminal_envelope(
                _terminal_control_candidates(rows, tid=tid)
            )
    return _terminal_control_snapshot(
        selected,
        tid=tid,
        ctrl_out_name=ctrl_out_name,
        taskspec_payload=taskspec_payload,
    )


@dataclass(frozen=True, slots=True)
class TerminalControlScan:
    """Detached progress over one finite control horizon [MF-5], [OBS.13.13].

    The worker owns live iterators; only this value crosses worker invocations.
    A restart discards it and repeats inspection from the beginning.
    """

    tid: str
    queue: str
    horizon: int | None
    after_message_id: int | None = None
    best_envelope: tuple[dict[str, Any], int] | None = None


@dataclass(frozen=True, slots=True)
class TerminalControlScanResult:
    """One bounded window; only complete inspection exposes a verdict [MF-5]."""

    scan: TerminalControlScan
    scanned: int
    completed: bool
    evidence: TaskEvidenceSnapshot | None = None


def scan_terminal_ctrl_out_evidence(
    ctx: WeftContext,
    *,
    tid: str,
    ctrl_out_name: str,
    scan_limit: int,
    continuation: TerminalControlScan | None = None,
    taskspec_payload: dict[str, Any] | None = None,
    broker: Any | None = None,
    deadline_monotonic: float | None = None,
) -> TerminalControlScanResult:
    """Inspect at most ``scan_limit`` raw rows in a captured finite horizon.

    Noise and other tasks consume the same budget as terminal rows. The broker
    prefetch batch is bounded by that budget. The caller reserves the full
    window allowance, including unused prefetch, when enforcing a shared budget.
    Prefix reads use a strict upper bound, followed by an exact horizon read,
    avoiding arithmetic at the maximum
    message ID. A filled budget is incomplete, even if it happened to reach the
    last prefix row; the next window establishes exhaustion. All iterators close
    before return. Read failure propagates without returning false progress.

    Spec: [MF-5], [OBS.13.13].
    """
    if scan_limit < 1:
        raise ValueError("scan_limit must be positive")
    if continuation is not None and (
        continuation.tid != tid or continuation.queue != ctrl_out_name
    ):
        raise ValueError("control scan continuation belongs to another task or queue")
    with queue_broker(ctx, ctrl_out_name, broker=broker) as db:
        scan = continuation or TerminalControlScan(
            tid=tid,
            queue=ctrl_out_name,
            horizon=db.latest_pending_timestamp(ctrl_out_name),
        )
        if scan.horizon is None:
            return TerminalControlScanResult(scan=scan, scanned=0, completed=True)
        iterator = db.peek_generator(
            ctrl_out_name,
            with_timestamps=True,
            batch_size=scan_limit,
            after_timestamp=scan.after_message_id,
            before_timestamp=scan.horizon,
        )
        scanned = 0
        exhausted = False
        with closing_queue_iterator(iterator) as rows:
            row_iterator = iter(rows)
            while scanned < scan_limit:
                if (
                    deadline_monotonic is not None
                    and time.monotonic() >= deadline_monotonic
                ):
                    break
                try:
                    body, message_id = next(row_iterator)
                except StopIteration:
                    exhausted = True
                    break
                scanned += 1
                payload = coerce_terminal_envelope(str(body), tid=tid)
                selected = scan.best_envelope
                if payload is not None:
                    selected = select_terminal_envelope(
                        ((payload, int(message_id)),)
                        if selected is None
                        else (selected, (payload, int(message_id)))
                    )
                scan = replace(
                    scan, after_message_id=int(message_id), best_envelope=selected
                )
        if not exhausted or (
            deadline_monotonic is not None and time.monotonic() >= deadline_monotonic
        ):
            return TerminalControlScanResult(
                scan=scan, scanned=scanned, completed=False
            )
        horizon_row = db.peek_one(
            ctrl_out_name,
            exact_timestamp=scan.horizon,
            with_timestamps=True,
            include_claimed=False,
        )
        if horizon_row is not None:
            scanned += 1
            selected = select_terminal_envelope(
                (
                    *(() if scan.best_envelope is None else (scan.best_envelope,)),
                    *_terminal_control_candidates((horizon_row,), tid=tid),
                )
            )
            scan = replace(scan, after_message_id=scan.horizon, best_envelope=selected)
        return TerminalControlScanResult(
            scan=scan,
            scanned=scanned,
            completed=True,
            evidence=_terminal_control_snapshot(
                scan.best_envelope,
                tid=tid,
                ctrl_out_name=ctrl_out_name,
                taskspec_payload=taskspec_payload,
            ),
        )


def peek_final_outbox_evidence(
    ctx: WeftContext,
    *,
    tid: str,
    outbox_name: str,
    taskspec_payload: dict[str, Any] | None,
    broker: Any | None = None,
    observation_config: TaskObservationConfig | None = None,
) -> TaskEvidenceSnapshot | None:
    """Peek conservative final one-shot outbox evidence without consuming it."""

    if (observation_config is not None and observation_config.mode != "one_shot") or (
        observation_config is None
        and (
            task_is_persistent_payload(taskspec_payload)
            or task_is_interactive_payload(taskspec_payload)
        )
    ):
        return None
    stream_buffer: list[str] = []
    values: list[Any] = []
    ack_targets: list[QueueAckTarget] = []
    observed_at: int | None = None
    saw_partial = False
    with queue_broker(ctx, outbox_name, broker=broker) as db:
        iterator = db.peek_generator(outbox_name, with_timestamps=True)
        with closing_queue_iterator(iterator) as rows:
            for item in rows:
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
                if decoded.streamed:
                    saw_partial = True
                    continue
                values.append(decoded.value)
                timestamp_int = int(timestamp)
                ack_targets.append(
                    QueueAckTarget(queue=outbox_name, message_id=timestamp_int)
                )
                observed_at = timestamp_int

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
    *,
    broker: Any | None = None,
) -> QueueMessageCounts | None:
    """Return backend-neutral queue counts when the broker exposes them."""

    try:
        with queue_broker(ctx, queue_name, broker=broker) as db:
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
    broker: Any | None = None,
    observation_config: TaskObservationConfig | None = None,
) -> TaskEvidenceSnapshot | None:
    """Return a recovery diagnostic for claimed one-shot outbox residue.

    Claimed rows prove retained broker state, not readable result content. This
    helper therefore does not decode payloads and does not acknowledge,
    unclaim, or delete anything.

    Spec: [MF-5]
    """

    if (observation_config is not None and observation_config.mode != "one_shot") or (
        observation_config is None
        and (
            task_is_persistent_payload(taskspec_payload)
            or task_is_interactive_payload(taskspec_payload)
        )
    ):
        return None
    counts = queue_message_counts(ctx, outbox_name, broker=broker)
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
    broker: Any | None = None,
) -> TaskEvidenceSnapshot | None:
    """Return terminal task-local evidence when visible."""

    outbox_name, ctrl_out_name = queue_names_for_tid(tid, taskspec_payload)
    ctrl_snapshot = peek_terminal_ctrl_out_evidence(
        ctx,
        tid=tid,
        ctrl_out_name=ctrl_out_name,
        taskspec_payload=taskspec_payload,
        broker=broker,
    )
    if ctrl_snapshot is not None:
        return ctrl_snapshot
    return peek_final_outbox_evidence(
        ctx,
        tid=tid,
        outbox_name=outbox_name,
        taskspec_payload=taskspec_payload,
        broker=broker,
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


def describe_runtime(handle: RunnerHandle | None) -> dict[str, Any] | None:
    """Describe a runtime handle through its authoritative runner boundary."""

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
    except Exception as exc:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-365] exception
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
    runtime = describe_runtime(handle)
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


def _task_history_candidate_ids(
    broker: Any,
    tid: str,
    *,
    after_timestamp: int | None,
    before_timestamp: int | None,
) -> list[int]:
    """Page canonical literal identity candidates through the range [MF-5]."""
    candidates: list[int] = []
    cursor = after_timestamp
    while True:
        page = broker.find_message_ids(
            WEFT_GLOBAL_LOG_QUEUE,
            body_contains=tid,
            after_timestamp=cursor,
            before_timestamp=before_timestamp,
            include_claimed=False,
        )
        if not page:
            return candidates
        candidates.extend(page)
        cursor = page[-1]


def read_task_history(
    context: WeftContext,
    tid: str,
    *,
    broker: Any | None = None,
    after_timestamp: int | None = None,
) -> tuple[list[tuple[dict[str, Any], int]], int | None]:
    """Read exact-TID pending history through a completely inspected horizon.

    The observation owner registers interest before calling this reader.
    Initial reads have no TID-derived lower bound. Later reads borrow the
    prior inspected horizon, including negative progress. The public broker
    search selects substring candidate IDs. Weft fetches only those candidate
    bodies, then selects records by their decoded top-level TID. Backend search
    work and transfer within SimpleBroker remain backend concerns.
    Supported task-log identities contain literal ASCII digits. Failed reads
    return no progress; empty or older queues retain the supplied horizon.

    Spec: [MF-5], [SB-0.4].
    """
    with queue_broker(context, WEFT_GLOBAL_LOG_QUEUE, broker=broker) as db:
        horizon = db.latest_pending_timestamp(WEFT_GLOBAL_LOG_QUEUE)
        if horizon is None or (
            after_timestamp is not None and horizon <= after_timestamp
        ):
            return [], after_timestamp
        before_timestamp = (
            None if horizon == TASK_HISTORY_MAX_MESSAGE_ID else horizon + 1
        )
        candidates = _task_history_candidate_ids(
            db,
            tid,
            after_timestamp=after_timestamp,
            before_timestamp=before_timestamp,
        )
        matched: list[tuple[dict[str, Any], int]] = []
        for message_id in candidates:
            row = db.peek_one(
                WEFT_GLOBAL_LOG_QUEUE,
                exact_timestamp=message_id,
                with_timestamps=True,
                include_claimed=False,
            )
            if row is None:
                continue
            body, _timestamp = row
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and payload.get("tid") == tid:
                matched.append((payload, message_id))
        return matched, horizon


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


def _current_mapping_entry(
    ctx: WeftContext, tid: str, *, broker: Any | None
) -> dict[str, Any] | None:
    """Acquire one current generation together with its observation time [MF-5]."""
    snapshot = read_task_state_snapshot(ctx, tid, broker=broker)
    return {**snapshot[1], "timestamp": snapshot[0]} if snapshot is not None else None


def discover_task_observation(
    ctx: WeftContext,
    tid: str,
    *,
    broker: Any | None = None,
    mapping_entry: dict[str, Any] | None = None,
) -> TaskObservationConfig | None:
    """Discover exact task-owned routes without history or guessed destinations [MF-5]."""
    if mapping_entry is None:
        mapping_entry = _current_mapping_entry(ctx, tid, broker=broker)
    if mapping_entry is not None:
        config = parse_task_observation(mapping_entry.get("observation"))
        if config is not None:
            return config
    name = f"T{tid}.{QUEUE_CTRL_OUT_SUFFIX}"
    selected: tuple[int, TaskObservationConfig] | None = None
    with (
        queue_broker(ctx, name, broker=broker) as db,
        closing_queue_iterator(db.peek_generator(name, with_timestamps=True)) as rows,
    ):
        for raw, message_id in rows:
            envelope = coerce_terminal_envelope(raw, tid=tid)
            if envelope is None or envelope["source"] != "task":
                continue
            spec_payload = envelope.get("taskspec")
            if not isinstance(spec_payload, dict) or spec_payload.get("tid") != tid:
                continue
            config = observation_config_from_taskspec_payload(spec_payload)
            if config is not None and (selected is None or message_id > selected[0]):
                selected = message_id, config
    return selected[1] if selected is not None else None


def _configured_outbox_evidence(
    ctx: WeftContext,
    *,
    tid: str,
    config: TaskObservationConfig | None,
    taskspec_payload: dict[str, Any] | None,
    broker: Any | None,
) -> TaskEvidenceSnapshot | None:
    """Preserve conservative one-shot recovery only with resolved routes [MF-5]."""
    if config is None or config.mode != "one_shot":
        return None
    return peek_final_outbox_evidence(
        ctx,
        tid=tid,
        outbox_name=config.outbox,
        taskspec_payload=taskspec_payload,
        broker=broker,
        observation_config=config,
    ) or claimed_outbox_result_evidence(
        ctx,
        tid=tid,
        outbox_name=config.outbox,
        taskspec_payload=taskspec_payload,
        broker=broker,
        observation_config=config,
    )


def known_tid_evidence(
    ctx: WeftContext,
    *,
    tid: str,
    taskspec_payload: dict[str, Any] | None = None,
    mapping_entry: dict[str, Any] | None = None,
    ping: bool = False,
    probe_timeout: float = CONTROL_SURFACE_WAIT_TIMEOUT,
    broker: Any | None = None,
    observation_config: TaskObservationConfig | None = None,
    discover_observation: bool = True,
) -> TaskEvidenceSnapshot | None:
    """Select task-owned evidence without global history reads [MF-5]."""
    if mapping_entry is None and discover_observation:
        mapping_entry = _current_mapping_entry(ctx, tid, broker=broker)
    config = observation_config or observation_config_from_taskspec_payload(
        taskspec_payload
    )
    if config is None and discover_observation:
        config = discover_task_observation(
            ctx, tid, broker=broker, mapping_entry=mapping_entry
        )
    local = peek_terminal_ctrl_out_evidence(
        ctx,
        tid=tid,
        ctrl_out_name=config.ctrl_out if config else f"T{tid}.{QUEUE_CTRL_OUT_SUFFIX}",
        taskspec_payload=taskspec_payload,
        broker=broker,
    )
    if local is not None and local.metadata.get("terminal_source") == "task":
        return local
    projection = (
        task_state_terminal_evidence(
            tid, mapping_entry, int(mapping_entry.get("timestamp", 0))
        )
        if mapping_entry is not None
        else None
    )
    if projection is not None:
        return projection
    if local is not None:
        return local
    runtime = runtime_evidence(tid=tid, mapping_entry=mapping_entry)
    if runtime is None:
        runtime = _configured_outbox_evidence(
            ctx,
            tid=tid,
            config=config,
            taskspec_payload=taskspec_payload,
            broker=broker,
        )
    if ping and config is not None:
        # PING is permitted only after route discovery; accepted payloads retain
        # their original full configuration for the existing keyed probe.
        pong = ping_pong_evidence(
            ctx,
            tid=tid,
            taskspec_payload=taskspec_payload,
            timeout=probe_timeout,
            broker=broker,
            ctrl_in_name=config.ctrl_in,
            ctrl_out_name=config.ctrl_out,
        )
        if pong is not None:
            return pong
    return runtime or _nonterminal_mapping_evidence(tid, mapping_entry)


def _nonterminal_mapping_evidence(
    tid: str, mapping_entry: dict[str, Any] | None
) -> TaskEvidenceSnapshot | None:
    """Preserve task-owned nonterminal lifecycle when no stronger evidence exists [MF-5]."""
    payload = mapping_entry or {}
    projection = parse_task_projection(
        payload.get("projection"), terminal=payload.get("terminal") is True
    )
    if projection is not None:
        return TaskEvidenceSnapshot(
            tid=tid,
            status=projection["status"],
            classification="task_state_projection",
            source="task_state",
            terminal=False,
            event=projection["event"],
            started_at=projection["started_at"],
            observed_at=payload.get("timestamp"),
            metadata=projection["metadata"],
        )
    return stale_observer_evidence(
        tid=tid,
        prior_live=mapping_entry is not None,
        prior_live_at=payload.get("timestamp"),
    )


def terminal_snapshot_from_evidence(
    evidence: TaskEvidenceSnapshot,
) -> TaskTerminalSnapshot:
    """Convert shared evidence to the compact known-TID terminal snapshot."""

    source = evidence.source
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
