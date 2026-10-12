"""Task listing and control helpers.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-1.2.3]
- docs/specifications/10-CLI_Interface.md [CLI-1.2.1]
- docs/specifications/10-CLI_Interface.md [CLI-1.3]
- docs/specifications/01-Core_Components.md [CC-3.2]
- docs/specifications/05-Message_Flow_and_State.md [MF-3], [MF-5]
- docs/specifications/12-Pipeline_Composition_and_UX.md [PL-5.2], [PL-5.3]
- docs/specifications/14-Python_API_Surfaces.md [PY-2]
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Iterable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, replace
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Literal, cast

import weft.commands.system as system_cmd
from simplebroker import Queue
from weft._constants import (
    CONTROL_KILL,
    CONTROL_STOP,
    CONTROL_SURFACE_WAIT_INTERVAL,
    CONTROL_SURFACE_WAIT_TIMEOUT,
    QUEUE_CTRL_IN_SUFFIX,
    TASK_PID_EXIT_POLL_INTERVAL,
    TASK_PING_TIMEOUT_SECONDS,
    TERMINAL_ENVELOPE_TYPE,
    WEFT_GLOBAL_LOG_QUEUE,
)
from weft._exceptions import (
    CommandUsageError,
    ControlRejected,
    InvalidTID,
    TaskNotFound,
)
from weft._runner_plugins import require_runner_plugin
from weft.commands.types import (
    CommandStream,
    TaskControlFailure,
    TaskControlResult,
    TaskEvent,
    TaskPingResult,
    TaskSnapshot,
    TaskTerminalSnapshot,
)
from weft.context import WeftContext, build_context
from weft.core import task_evidence
from weft.core.control_messages import encode_control_message
from weft.core.control_probe import send_keyed_ping_probe
from weft.core.monitor.store import (
    MonitorTaskCollationRecord,
)
from weft.core.queue_wait import QueueChangeMonitor
from weft.core.queue_window import iter_broker_queue_json_entries, queue_broker
from weft.core.runner_diagnostics import diagnostic_summary
from weft.core.task_state import (
    latest_task_state_rows,
    read_task_state_snapshot,
    task_state_queue_name,
)
from weft.helpers import (
    pid_is_live,
    terminate_verified_process_tree,
    tid_short_form,
)
from weft.helpers.message_ids import is_task_tid

from ._boundary import typed_command_errors
from ._result_wait import result_observation
from ._task_history import (
    load_latest_taskspec_payload,
    monitor_terminal_evidence,
    pipeline_status_queue_name,
    read_monitor_task_record,
)
from ._task_snapshot_reducer import CollectedTaskSnapshot
from .control_convergence import (
    ControlConvergenceEvidence,
    reduce_control_convergence,
)

logger = logging.getLogger(__name__)


def format_runner_diagnostics(diagnostics: Mapping[str, Any] | None) -> str | None:
    """Return a compact runner diagnostic summary for user-facing surfaces."""

    return diagnostic_summary(diagnostics)


def _resolve_context(context_path: str | os.PathLike[str] | None) -> WeftContext:
    return build_context(spec_context=context_path)


def _coerce_context(
    *,
    context: WeftContext | None = None,
    context_path: str | os.PathLike[str] | None = None,
) -> WeftContext:
    if context is not None:
        return context
    return _resolve_context(context_path)


def _read_tid_state_entries(
    ctx: WeftContext, tids: Iterable[str] | None = None
) -> list[dict[str, Any]]:
    return [
        payload
        for _message_id, payload in sorted(
            latest_task_state_rows(ctx, tids=tids).values()
        )
    ]


def mapping_for_tid(
    ctx: WeftContext, tid: str, *, broker: Any | None = None
) -> dict[str, Any] | None:
    full = resolve_full_tid(ctx, tid, broker=broker) or tid.strip().lstrip("T")
    row = read_task_state_snapshot(ctx, full, broker=broker)
    return row[1] if row is not None else None


def _load_taskspec_payload_bounded(
    ctx: WeftContext,
    tid: str,
    *,
    broker: Any | None = None,
) -> dict[str, Any] | None:
    """Use the shared filtered TaskSpec reader for one full TID.

    Spec: docs/specifications/10-CLI_Interface.md [CLI-1.2.1]
    """

    if not tid.isdigit():
        return None
    return load_latest_taskspec_payload(ctx, tid, broker=broker)


def resolve_full_tid(
    ctx: WeftContext, raw: str, *, broker: Any | None = None
) -> str | None:
    """Resolve one full or unambiguous derived short TID.

    Spec: docs/specifications/10-CLI_Interface.md [CLI-1.2.3].
    """
    candidate = raw.strip().lstrip("T")
    if not candidate:
        return None
    if is_task_tid(candidate):
        return candidate
    matches = system_cmd._read_tid_states(ctx, broker=broker).get(candidate, [])
    if len(matches) > 1:
        raise CommandUsageError(
            f"Ambiguous short TID {candidate}: {', '.join(matches)}"
        )
    return matches[0] if matches else None


def task_tid(
    *,
    tid: str | None = None,
    pid: int | None = None,
    reverse: str | None = None,
    context: WeftContext | None = None,
    context_path: str | os.PathLike[str] | None = None,
) -> str | None:
    """TID resolution: short-to-full, PID-to-TID, and reverse lookup.

    Spec: docs/specifications/10-CLI_Interface.md [CLI-1.2.3] (task tid)
    """
    ctx = _coerce_context(context=context, context_path=context_path)
    if reverse:
        value = reverse.strip().lstrip("T")
        if is_task_tid(value):
            return tid_short_form(value)
        return None
    if pid is not None:
        entries = list(_read_tid_state_entries(ctx))
        for entry in reversed(entries):
            if pid in _host_pids_from_mapping(entry):
                full = entry.get("full")
                return full if isinstance(full, str) else None
        return None
    if tid:
        return resolve_full_tid(ctx, tid) or tid.strip().lstrip("T")
    return None


def list_tasks(
    *,
    status_filter: str | None = None,
    include_terminal: bool = False,
    context: WeftContext | None = None,
    context_path: str | os.PathLike[str] | None = None,
) -> list[system_cmd.TaskSnapshot]:
    ctx = _coerce_context(context=context, context_path=context_path)
    snapshots = system_cmd._collect_task_snapshots(
        ctx, include_terminal=include_terminal, tid_filters=None
    )
    if status_filter:
        snapshots = [s for s in snapshots if s.status == status_filter]
    return snapshots


def _state_from_taskspec(taskspec_payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(taskspec_payload, dict):
        return {}
    state = taskspec_payload.get("state")
    return state if isinstance(state, dict) else {}


def _task_is_persistent_payload(taskspec_payload: dict[str, Any] | None) -> bool:
    return task_evidence.task_is_persistent_payload(taskspec_payload)


def _queue_names_for_tid(
    tid: str,
    taskspec_payload: dict[str, Any] | None,
) -> tuple[str, str]:
    return task_evidence.queue_names_for_tid(tid, taskspec_payload)


def _peek_final_outbox_snapshot(
    ctx: WeftContext,
    *,
    tid: str,
    outbox_name: str,
    taskspec_payload: dict[str, Any] | None,
) -> TaskTerminalSnapshot | None:
    evidence = task_evidence.peek_final_outbox_evidence(
        ctx,
        tid=tid,
        outbox_name=outbox_name,
        taskspec_payload=taskspec_payload,
    )
    if evidence is None:
        return None
    return task_evidence.terminal_snapshot_from_evidence(evidence)


def _coerce_terminal_envelope(
    raw: str,
    *,
    tid: str,
) -> dict[str, Any] | None:
    try:
        return task_evidence.coerce_terminal_envelope(raw, tid=tid)
    except TypeError:
        return None


def _peek_terminal_ctrl_out_snapshot(
    ctx: WeftContext,
    *,
    tid: str,
    ctrl_out_name: str,
) -> TaskTerminalSnapshot | None:
    evidence = task_evidence.peek_terminal_ctrl_out_evidence(
        ctx,
        tid=tid,
        ctrl_out_name=ctrl_out_name,
    )
    if evidence is None:
        return None
    return task_evidence.terminal_snapshot_from_evidence(evidence)


def _public_snapshot(
    status_snapshot: system_cmd.TaskSnapshot,
    *,
    taskspec_payload: dict[str, Any] | None,
) -> TaskSnapshot:
    state = _state_from_taskspec(taskspec_payload)
    error = state.get("error")
    return_code = state.get("return_code")
    return TaskSnapshot(
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


def _raise_watch_timeout(
    *,
    tid: str,
    timeout: float | None,
) -> None:
    raise TimeoutError(f"Timed out after {timeout} seconds watching task {tid}")


@typed_command_errors
def task_terminal_snapshot(
    tid: str,
    *,
    timeout: float = 0.0,
    context: WeftContext | None = None,
    context_path: str | os.PathLike[str] | None = None,
) -> TaskTerminalSnapshot:
    """Return a bounded, non-consuming known-TID terminal/live snapshot.

    A positive `timeout` bounds the observation only. On expiry this returns
    the latest honest nonterminal snapshot; it never publishes a task timeout
    and never consumes a result.

    Spec: docs/specifications/09-Implementation_Plan.md [IP-1.1];
    docs/specifications/05-Message_Flow_and_State.md [MF-5]
    """

    ctx = _coerce_context(context=context, context_path=context_path)
    try:
        full_tid = resolve_full_tid(ctx, tid) or tid.strip().lstrip("T")
    except ValueError:
        full_tid = tid.strip().lstrip("T")
    if not full_tid or not (full_tid.isascii() and full_tid.isdecimal()):
        return TaskTerminalSnapshot(
            tid=full_tid or tid,
            status="missing",
            source="normalization",
            terminal=True,
        )
    return _task_terminal_snapshot(full_tid, timeout=timeout, context=ctx)


def _terminal_observation_queue_names(
    tid: str, taskspec_payload: dict[str, Any] | None
) -> list[str]:
    """Register the actual terminal evidence surfaces before reading [MF-5]."""
    outbox, control = task_evidence.queue_names_for_tid(tid, taskspec_payload)
    names = [WEFT_GLOBAL_LOG_QUEUE, task_state_queue_name(tid), outbox, control]
    pipeline = pipeline_status_queue_name(tid, taskspec_payload)
    if pipeline is not None:
        names.append(pipeline)
    return names


def _terminal_observation_snapshot(
    ctx: WeftContext,
    tid: str,
    *,
    routing_payload: dict[str, Any] | None,
    task_events: Sequence[tuple[dict[str, Any], int]],
    log_evidence: task_evidence.LogFold,
    broker: Any,
    log_queue: Queue,
    monitor_lookup: tuple[MonitorTaskCollationRecord | None, str | None],
) -> TaskTerminalSnapshot:
    """Select one terminal turn using acquired history and shared priority [MF-5]."""
    evidence = task_evidence.known_tid_evidence(
        ctx,
        tid=tid,
        taskspec_payload=routing_payload,
        mapping_entry=mapping_for_tid(ctx, tid, broker=broker),
        broker=broker,
        log_evidence=log_evidence,
    )
    retained = monitor_terminal_evidence(monitor_lookup[0], acquired_evidence=evidence)
    if retained is not None:
        return task_evidence.terminal_snapshot_from_evidence(retained)
    if evidence is not None:
        return task_evidence.terminal_snapshot_from_evidence(evidence)
    record = _task_status_record(
        tid,
        include_terminal=True,
        context=ctx,
        broker=broker,
        observation_queue=log_queue,
        accepted_taskspec_payload=routing_payload,
        task_events=task_events,
        log_evidence=log_evidence,
        monitor_lookup=monitor_lookup,
    )
    snapshot = _terminal_snapshot_from_status_snapshot(
        record.snapshot if record is not None else None
    )
    return snapshot or TaskTerminalSnapshot(
        tid=tid, status="unknown", source="observer"
    )


@typed_command_errors
def _task_terminal_snapshot(
    tid: str,
    *,
    timeout: float = 0.0,
    context: WeftContext | None = None,
    context_path: str | os.PathLike[str] | None = None,
    accepted_taskspec_payload: dict[str, Any] | None = None,
) -> TaskTerminalSnapshot:
    """Observe terminal state with one advancing history acquisition [MF-5].

    Accepted configuration governs routing; observed history governs fresh state.
    Positive timeouts wait on registered queue activity and the actual deadline.
    """
    ctx = _coerce_context(context=context, context_path=context_path)
    full_tid = tid
    deadline = time.monotonic() + timeout if timeout > 0 else None
    names = _terminal_observation_queue_names(full_tid, accepted_taskspec_payload)
    with ExitStack() as resources:
        queues, monitor = resources.enter_context(result_observation(ctx, names))
        log_queue = queues[WEFT_GLOBAL_LOG_QUEUE]
        task_events: list[tuple[dict[str, Any], int]] = []
        log_evidence = task_evidence.LogFold()
        cursor: int | None = None
        monitor_lookup: tuple[MonitorTaskCollationRecord | None, str | None] | None = (
            None
        )
        while True:
            with log_queue.get_connection() as broker:
                rows, cursor = task_evidence.read_task_history(
                    ctx, full_tid, broker=broker, after_timestamp=cursor
                )
                task_events.extend(rows)
                log_evidence = log_evidence.fold(rows, tid=full_tid)
                fresh_payload = load_latest_taskspec_payload(
                    ctx, full_tid, broker=broker, task_events=task_events
                )
                if monitor_lookup is None:
                    monitor_lookup = read_monitor_task_record(
                        ctx, full_tid, queue=log_queue
                    )
                retained_record = monitor_lookup[0]
                if (
                    fresh_payload is None
                    and monitor_terminal_evidence(retained_record) is not None
                    and retained_record is not None
                ):
                    fresh_payload = dict(retained_record.taskspec_summary)
                routing_payload = (
                    accepted_taskspec_payload
                    if accepted_taskspec_payload is not None
                    else fresh_payload
                )
                new_names = _terminal_observation_queue_names(full_tid, routing_payload)
                if not set(new_names).issubset(queues):
                    new_observation = resources.enter_context(
                        result_observation(
                            ctx, new_names, borrowed_queues=list(queues.values())
                        )
                    )
                    monitor.close()
                    queues, monitor = new_observation
                    # Register newly discovered routes before their first read.
                snapshot = _terminal_observation_snapshot(
                    ctx,
                    full_tid,
                    routing_payload=routing_payload,
                    task_events=task_events,
                    log_evidence=log_evidence,
                    broker=broker,
                    log_queue=log_queue,
                    monitor_lookup=monitor_lookup,
                )
            if snapshot.terminal or snapshot.status not in {
                "running",
                "pending",
                "unknown",
            }:
                return snapshot
            if deadline is None or time.monotonic() >= deadline:
                return snapshot
            monitor.wait(max(0.0, deadline - time.monotonic()))


def ack_terminal_snapshot(
    snapshot: TaskTerminalSnapshot,
    *,
    context: WeftContext | None = None,
    context_path: str | os.PathLike[str] | None = None,
) -> bool:
    """Delete only exact queue messages returned by `task_terminal_snapshot()`."""

    if not snapshot.ack_targets:
        return False
    ctx = _coerce_context(context=context, context_path=context_path)
    deleted_any = False
    for target in snapshot.ack_targets:
        queue = ctx.queue(target.queue, persistent=True)
        try:
            deleted_any = (
                bool(queue.delete(message_id=target.message_id)) or deleted_any
            )
        finally:
            queue.close()
    return deleted_any


def task_status(
    tid: str,
    *,
    include_terminal: bool = True,
    ping: bool = False,
    probe_timeout: float = CONTROL_SURFACE_WAIT_TIMEOUT,
    context: WeftContext | None = None,
    context_path: str | os.PathLike[str] | None = None,
) -> system_cmd.TaskSnapshot | None:
    return _task_status(
        tid,
        include_terminal=include_terminal,
        ping=ping,
        probe_timeout=probe_timeout,
        context=context,
        context_path=context_path,
    )


def _task_status(
    tid: str,
    *,
    include_terminal: bool = True,
    ping: bool = False,
    probe_timeout: float = CONTROL_SURFACE_WAIT_TIMEOUT,
    context: WeftContext | None = None,
    context_path: str | os.PathLike[str] | None = None,
    broker: Any | None = None,
    observation_queue: Queue | None = None,
) -> system_cmd.TaskSnapshot | None:
    """Project the shared fresh status record [MF-5]."""
    record = _task_status_record(
        tid,
        include_terminal=include_terminal,
        ping=ping,
        probe_timeout=probe_timeout,
        context=context,
        context_path=context_path,
        broker=broker,
        observation_queue=observation_queue,
    )
    return record.snapshot if record is not None else None


def _collect_status_record(
    ctx: WeftContext,
    full_tid: str,
    *,
    include_terminal: bool,
    ping: bool,
    probe_timeout: float,
    broker: Any | None,
    accepted_taskspec_payload: dict[str, Any] | None,
    task_events: Sequence[tuple[dict[str, Any], int]] | None,
    log_evidence: task_evidence.LogFold | None,
) -> tuple[CollectedTaskSnapshot | None, dict[str, Any] | None]:
    """Project status from acquired rows and their shared lifecycle fold [MF-5]."""
    if is_task_tid(full_tid):
        assert task_events is not None
        fresh_payload = load_latest_taskspec_payload(
            ctx, full_tid, broker=broker, task_events=task_events
        )
        routing_payload = (
            accepted_taskspec_payload
            if accepted_taskspec_payload is not None
            else fresh_payload
        )
        if ping:
            mapping_entry = mapping_for_tid(ctx, full_tid, broker=broker)
            evidence = task_evidence.known_tid_evidence(
                ctx,
                tid=full_tid,
                taskspec_payload=routing_payload,
                mapping_entry=mapping_entry,
                ping=True,
                probe_timeout=probe_timeout,
                broker=broker,
                log_evidence=log_evidence,
            )
            if evidence is not None and evidence.classification == "live_pong":
                snapshot = _task_snapshot_from_live_pong(
                    full_tid,
                    evidence,
                    base_snapshot=None,
                    taskspec_payload=fresh_payload,
                )
                return CollectedTaskSnapshot(snapshot, fresh_payload), fresh_payload
        base_record = system_cmd.collect_known_tid_record(
            ctx,
            full_tid,
            include_terminal=include_terminal,
            broker=broker,
            task_events=task_events,
            accepted_taskspec_payload=accepted_taskspec_payload,
        )
        if base_record is not None:
            fresh_payload = base_record.taskspec_payload
    else:
        records = system_cmd._collect_task_snapshot_records(
            ctx,
            include_terminal=include_terminal,
            tid_filters={full_tid},
            broker=broker,
        )
        base_record = records[0] if records else None
        fresh_payload = (
            base_record.taskspec_payload if base_record is not None else None
        )
    return base_record, fresh_payload


def _task_status_record(
    tid: str,
    *,
    include_terminal: bool = True,
    ping: bool = False,
    probe_timeout: float = CONTROL_SURFACE_WAIT_TIMEOUT,
    context: WeftContext | None = None,
    context_path: str | os.PathLike[str] | None = None,
    broker: Any | None = None,
    observation_queue: Queue | None = None,
    accepted_taskspec_payload: dict[str, Any] | None = None,
    task_events: Sequence[tuple[dict[str, Any], int]] | None = None,
    log_evidence: task_evidence.LogFold | None = None,
    monitor_lookup: tuple[MonitorTaskCollationRecord | None, str | None] | None = None,
) -> CollectedTaskSnapshot | None:
    """Select status while retaining fresh payload and observation loans [MF-5].

    Accepted configuration governs routing only. Raw rows serve snapshot
    projection; the lifecycle fold serves classification. An explicit empty
    fold and negative Monitor outcome are authoritative acquisition loans.
    """
    ctx = _coerce_context(context=context, context_path=context_path)
    full_tid = resolve_full_tid(ctx, tid, broker=broker) or tid.strip().lstrip("T")
    known_tid = is_task_tid(full_tid)
    if known_tid and task_events is None:
        task_events, _horizon = task_evidence.read_task_history(
            ctx, full_tid, broker=broker
        )
    if known_tid and log_evidence is None:
        log_evidence = task_evidence.LogFold().fold(task_events or (), tid=full_tid)
    base_record, fresh_payload = _collect_status_record(
        ctx,
        full_tid,
        include_terminal=include_terminal,
        ping=ping,
        probe_timeout=probe_timeout,
        broker=broker,
        accepted_taskspec_payload=accepted_taskspec_payload,
        task_events=task_events,
        log_evidence=log_evidence,
    )
    if ping and base_record is not None and base_record.snapshot.event == "live_pong":
        return base_record
    routing_payload = (
        accepted_taskspec_payload
        if accepted_taskspec_payload is not None
        else fresh_payload
    )
    pipeline_snapshot = _latest_pipeline_status_snapshot(
        ctx, full_tid, taskspec_payload=routing_payload, broker=broker
    )
    base_snapshot = base_record.snapshot if base_record is not None else None
    if pipeline_snapshot is not None and _prefer_pipeline_snapshot(
        pipeline_snapshot, base_snapshot
    ):
        snapshot = _pipeline_task_snapshot(
            ctx,
            full_tid,
            pipeline_snapshot,
            base_snapshot,
            taskspec_payload=fresh_payload,
            broker=broker,
        )
        return CollectedTaskSnapshot(snapshot, fresh_payload)
    if known_tid:
        base_record = _select_monitor_status_record(
            ctx,
            full_tid,
            base_record,
            include_terminal=include_terminal,
            broker=broker,
            queue=observation_queue,
            monitor_lookup=monitor_lookup,
            routing_payload=routing_payload,
            log_evidence=log_evidence,
        )
    if pipeline_snapshot is not None and base_record is not None:
        base_record = replace(
            base_record,
            snapshot=replace(base_record.snapshot, pipeline_status=pipeline_snapshot),
        )
    return base_record


def _select_monitor_status_record(
    ctx: WeftContext,
    tid: str,
    base_record: CollectedTaskSnapshot | None,
    *,
    include_terminal: bool,
    broker: Any | None,
    queue: Queue | None,
    monitor_lookup: tuple[MonitorTaskCollationRecord | None, str | None] | None,
    routing_payload: dict[str, Any] | None,
    log_evidence: task_evidence.LogFold | None,
) -> CollectedTaskSnapshot | None:
    """Let qualified retention replace stale history, with core proof priority [MF-5]."""
    lookup = (
        monitor_lookup
        if monitor_lookup is not None
        else read_monitor_task_record(ctx, tid, queue=queue)
    )
    if monitor_terminal_evidence(lookup[0]) is not None:
        if routing_payload is None and lookup[0] is not None:
            routing_payload = dict(lookup[0].taskspec_summary)
        evidence = task_evidence.known_tid_evidence(
            ctx,
            tid=tid,
            taskspec_payload=routing_payload,
            mapping_entry=mapping_for_tid(ctx, tid, broker=broker),
            broker=broker,
            log_evidence=log_evidence,
        )
        if monitor_terminal_evidence(lookup[0], acquired_evidence=evidence) is None:
            return base_record
    elif base_record is not None:
        return base_record
    return _monitor_store_task_record(
        ctx, tid, include_terminal=include_terminal, monitor_lookup=lookup
    )


def _task_snapshot_from_monitor_store_record(
    record: MonitorTaskCollationRecord,
) -> system_cmd.TaskSnapshot:
    """Build a task snapshot from durable Monitor collation state.

    Terminal rows carry the `terminal_monitor_store` classification; nonterminal
    rows stay diagnostic history and never establish liveness.
    A terminal-looking diagnostic status needs both the collation terminal flag
    and a qualified terminal status before it can yield a terminal answer.

    Spec: [MF-5]
    """

    status_is_terminal = monitor_terminal_evidence(record) is not None
    status = (
        record.terminal_status
        if status_is_terminal
        else record.status
        if record.status not in system_cmd.TERMINAL_TASK_STATUSES
        else "unknown"
    ) or "unknown"
    taskspec_summary = record.taskspec_summary
    metadata = taskspec_summary.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    started_at = record.started_at_ns
    completed_at = record.completed_at_ns
    if isinstance(started_at, int) and isinstance(completed_at, int):
        duration = max(0.0, (completed_at - started_at) / 1_000_000_000)
    else:
        duration = None
    error = record.state.get("error")
    return system_cmd.TaskSnapshot(
        tid=record.tid,
        tid_short=tid_short_form(record.tid),
        name=record.name or str(taskspec_summary.get("name") or record.tid),
        status=status,
        event=record.terminal_event or "monitor_store",
        activity=None,
        waiting_on=None,
        started_at=started_at if isinstance(started_at, int) else None,
        completed_at=completed_at if isinstance(completed_at, int) else None,
        last_timestamp=record.last_message_id,
        duration_seconds=duration,
        runner=record.runner,
        runtime_handle=None,
        runtime=None,
        metadata=dict(metadata),
        return_code=record.return_code,
        error=error if isinstance(error, str) else None,
        reconciliation=(
            {
                "classification": "terminal_monitor_store",
                "reason": "raw_task_log_retired",
            }
            if status_is_terminal
            else None
        ),
    )


def _monitor_store_task_snapshot(
    ctx: WeftContext,
    tid: str,
    *,
    include_terminal: bool,
    queue: Queue | None = None,
    monitor_lookup: tuple[MonitorTaskCollationRecord | None, str | None] | None = None,
) -> system_cmd.TaskSnapshot | None:
    """Return a snapshot from Monitor state after raw task-log retirement.

    This is the last-resort derived read [MF-5] grants the Monitor collation
    store for known full TIDs; it is not lifecycle, result, or control
    authority.

    Spec: [MF-5]
    """

    record, error = (
        monitor_lookup
        if monitor_lookup is not None
        else read_monitor_task_record(ctx, tid, queue=queue)
    )
    if error is not None:
        return system_cmd.TaskSnapshot(
            tid=tid,
            tid_short=tid_short_form(tid),
            name=tid,
            status="unknown",
            event="monitor_store_unavailable",
            activity=None,
            waiting_on=None,
            started_at=None,
            completed_at=None,
            last_timestamp=0,
            duration_seconds=None,
            runner=None,
            runtime_handle=None,
            runtime=None,
            metadata={},
            reconciliation={
                "classification": "monitor_store_unavailable",
                "reason": "store_read_failed",
            },
            error=f"monitor store unavailable: {error}",
        )
    if record is None:
        return None
    snapshot = _task_snapshot_from_monitor_store_record(record)
    if not include_terminal and snapshot.status in system_cmd.TERMINAL_TASK_STATUSES:
        return None
    return snapshot


def _monitor_store_task_record(
    ctx: WeftContext,
    tid: str,
    *,
    include_terminal: bool,
    queue: Queue | None = None,
    monitor_lookup: tuple[MonitorTaskCollationRecord | None, str | None] | None = None,
) -> CollectedTaskSnapshot | None:
    """Keep the Monitor snapshot's fresh summary without a second lookup [MF-5]."""
    lookup = (
        monitor_lookup
        if monitor_lookup is not None
        else read_monitor_task_record(ctx, tid, queue=queue)
    )
    snapshot = _monitor_store_task_snapshot(
        ctx,
        tid,
        include_terminal=include_terminal,
        queue=queue,
        monitor_lookup=lookup,
    )
    if snapshot is None:
        return None
    record, _error = lookup
    payload = dict(record.taskspec_summary) if record is not None else None
    return CollectedTaskSnapshot(snapshot, payload)


def _terminal_snapshot_from_status_snapshot(
    snapshot: system_cmd.TaskSnapshot | None,
) -> TaskTerminalSnapshot | None:
    """Project the shared status path into the compact terminal snapshot shape."""

    if snapshot is None:
        return None
    if snapshot.event == "monitor_store_unavailable":
        return TaskTerminalSnapshot(
            tid=snapshot.tid,
            status="unknown",
            source="monitor_store_unavailable",
            terminal=False,
            error=snapshot.error,
            observed_at=snapshot.last_timestamp,
            metadata={
                "classification": "monitor_store_unavailable",
                "reason": "store_read_failed",
            },
        )
    if snapshot.status not in system_cmd.TERMINAL_TASK_STATUSES:
        return None

    reconciliation = snapshot.reconciliation or {}
    classification = reconciliation.get("classification")
    source = "monitor_store" if classification == "terminal_monitor_store" else "status"
    metadata: dict[str, Any] = {
        "classification": (
            classification
            if isinstance(classification, str) and classification
            else "terminal_status"
        )
    }
    if snapshot.event:
        metadata["event"] = snapshot.event
    if snapshot.name:
        metadata["name"] = snapshot.name
    if snapshot.metadata:
        metadata["task_metadata"] = dict(snapshot.metadata)
    if snapshot.runner is not None:
        metadata["runner"] = snapshot.runner

    return TaskTerminalSnapshot(
        tid=snapshot.tid,
        status=snapshot.status,
        source=source,
        error=snapshot.error,
        return_code=snapshot.return_code,
        terminal=True,
        observed_at=snapshot.last_timestamp,
        metadata=metadata,
    )


def task_ping(
    tid: str,
    *,
    timeout: float = TASK_PING_TIMEOUT_SECONDS,
    context: WeftContext | None = None,
    context_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Send a keyed PING and return the matched extended PONG payload.

    Spec: docs/specifications/10-CLI_Interface.md [CLI-1.3]
    """

    ctx = (
        context
        if context is not None
        else build_context(spec_context=context_path, create_database=False)
    )
    full_tid = resolve_full_tid(ctx, tid) or tid.strip().lstrip("T")
    taskspec_payload = (
        load_latest_taskspec_payload(ctx, full_tid) if is_task_tid(full_tid) else None
    )
    ctrl_in_name, _ctrl_out_name = task_evidence.control_queue_names_for_tid(
        full_tid,
        taskspec_payload,
    )
    result = send_keyed_ping_probe(
        ctx,
        tid=full_tid,
        ctrl_in_name=ctrl_in_name,
        timeout=timeout,
    )
    return {
        "timed_out": result.timed_out,
        "error": result.error,
        "observed_at": result.matched.observed_at if result.matched else None,
        "pong": result.matched.payload if result.matched else None,
    }


def _task_snapshot_from_live_pong(
    tid: str,
    evidence: task_evidence.TaskEvidenceSnapshot,
    *,
    base_snapshot: system_cmd.TaskSnapshot | None,
    taskspec_payload: dict[str, Any] | None,
) -> system_cmd.TaskSnapshot:
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
    if isinstance(task_metadata, dict):
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
    if evidence.status not in system_cmd.TERMINAL_TASK_STATUSES:
        completed_at = None
    elif not isinstance(completed_at, int):
        completed_at = observed_at

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
    return system_cmd.TaskSnapshot(
        tid=tid,
        tid_short=tid_short_form(tid),
        name=name,
        status=evidence.status,
        event="live_pong",
        activity=evidence.activity
        if evidence.status not in system_cmd.TERMINAL_TASK_STATUSES
        else None,
        waiting_on=evidence.waiting_on
        if evidence.status not in system_cmd.TERMINAL_TASK_STATUSES
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


def list_task_snapshots(
    *,
    status_filter: str | None = None,
    include_terminal: bool = False,
    context: WeftContext | None = None,
    context_path: str | os.PathLike[str] | None = None,
) -> list[TaskSnapshot]:
    """Return public task snapshots for the selected context."""

    ctx = _coerce_context(context=context, context_path=context_path)
    records = system_cmd._collect_task_snapshot_records(
        ctx,
        include_terminal=include_terminal,
        tid_filters=None,
    )
    if status_filter:
        records = [
            record for record in records if record.snapshot.status == status_filter
        ]
    return [
        _public_snapshot(
            record.snapshot,
            taskspec_payload=record.taskspec_payload,
        )
        for record in records
    ]


def task_stats(
    *,
    status_filter: str | None = None,
    include_terminal: bool = False,
    context: WeftContext | None = None,
    context_path: str | os.PathLike[str] | None = None,
) -> dict[str, int]:
    """Return status counts for the selected task set."""

    counts: dict[str, int] = {}
    ctx = _coerce_context(context=context, context_path=context_path)
    for record in system_cmd._collect_task_snapshot_records(
        ctx,
        include_terminal=include_terminal,
        tid_filters=None,
    ):
        status = record.snapshot.status
        if status_filter and status != status_filter:
            continue
        counts[status] = counts.get(status, 0) + 1
    return counts


def task_snapshot(
    tid: str,
    *,
    include_terminal: bool = True,
    context: WeftContext | None = None,
    context_path: str | os.PathLike[str] | None = None,
) -> TaskSnapshot | None:
    """Return one public task snapshot or `None` if absent."""

    return _task_snapshot(
        tid,
        include_terminal=include_terminal,
        context=context,
        context_path=context_path,
    )


def _task_snapshot(
    tid: str,
    *,
    include_terminal: bool = True,
    context: WeftContext | None = None,
    context_path: str | os.PathLike[str] | None = None,
    broker: Any | None = None,
    observation_queue: Queue | None = None,
    accepted_taskspec_payload: dict[str, Any] | None = None,
    task_events: Sequence[tuple[dict[str, Any], int]] | None = None,
    monitor_lookup: tuple[MonitorTaskCollationRecord | None, str | None] | None = None,
) -> TaskSnapshot | None:
    """Project fresh public fields using an observation owner's loans [MF-5]."""

    ctx = _coerce_context(context=context, context_path=context_path)
    record = _task_status_record(
        tid,
        include_terminal=include_terminal,
        context=ctx,
        broker=broker,
        observation_queue=observation_queue,
        accepted_taskspec_payload=accepted_taskspec_payload,
        task_events=task_events,
        monitor_lookup=monitor_lookup,
    )
    if record is None:
        return None
    return _public_snapshot(
        record.snapshot,
        taskspec_payload=record.taskspec_payload,
    )


@typed_command_errors
def watch_task_status(
    tid: str,
    *,
    include_terminal: bool = True,
    timeout: float | None = None,
    context: WeftContext | None = None,
    context_path: str | os.PathLike[str] | None = None,
) -> Iterable[TaskSnapshot]:
    """Yield snapshots until terminal state or typed observation failure.

    Spec: [MF-5], [PY-2].
    """

    ctx = _coerce_context(context=context, context_path=context_path)
    full_tid = resolve_full_tid(ctx, tid) or tid.strip().lstrip("T")
    deadline = _deadline_from_timeout(timeout)
    with ExitStack() as stack:
        stack.enter_context(ctx.session())
        log_queue = ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True)
        stack.callback(log_queue.close)
        monitor_queues = [log_queue]
        if is_task_tid(full_tid):
            state_queue = ctx.queue(task_state_queue_name(full_tid), persistent=True)
            stack.callback(state_queue.close)
            monitor_queues.append(state_queue)
        monitor = QueueChangeMonitor(monitor_queues, config=ctx.config)
        stack.callback(monitor.close)
        last_seen: tuple[int | None, str | None] | None = None
        while True:
            with log_queue.get_connection() as broker:
                snapshot = _task_snapshot(
                    full_tid,
                    include_terminal=include_terminal,
                    context=ctx,
                    broker=broker,
                    observation_queue=log_queue,
                )
            current = (
                snapshot.last_timestamp if snapshot is not None else None,
                snapshot.status if snapshot is not None else None,
            )
            if snapshot is not None and current != last_seen:
                last_seen = current
                yield snapshot
                if snapshot.status in system_cmd.TERMINAL_TASK_STATUSES:
                    return
            if _deadline_expired(deadline):
                _raise_watch_timeout(tid=full_tid, timeout=timeout)
            remaining = _remaining_timeout(deadline)
            wait_timeout = (
                system_cmd.STATUS_WATCH_MIN_INTERVAL
                if remaining is None
                else min(system_cmd.STATUS_WATCH_MIN_INTERVAL, remaining)
            )
            monitor.wait(wait_timeout)


def resolve_tid(
    *,
    tid: str | None = None,
    pid: int | None = None,
    reverse: str | None = None,
    context: WeftContext | None = None,
    context_path: str | os.PathLike[str] | None = None,
) -> str | None:
    """Resolve task id variants through the public TID mapping rules."""

    ctx = _coerce_context(context=context, context_path=context_path)
    return task_tid(tid=tid, pid=pid, reverse=reverse, context=ctx)


def _pipeline_snapshot_timestamp(pipeline_status: dict[str, Any]) -> int | None:
    timestamp_raw = pipeline_status.get("timestamp")
    if isinstance(timestamp_raw, int):
        return timestamp_raw
    if isinstance(timestamp_raw, float):
        return int(timestamp_raw)
    if isinstance(timestamp_raw, str) and timestamp_raw.isdigit():
        return int(timestamp_raw)
    return None


def _prefer_pipeline_snapshot(
    pipeline_status: dict[str, Any],
    base_snapshot: system_cmd.TaskSnapshot | None,
) -> bool:
    if base_snapshot is None:
        return True
    pipeline_timestamp = _pipeline_snapshot_timestamp(pipeline_status)
    if pipeline_timestamp is None:
        return False
    return pipeline_timestamp >= base_snapshot.last_timestamp


def _latest_pipeline_status_snapshot(
    ctx: WeftContext,
    tid: str,
    *,
    taskspec_payload: dict[str, Any] | None,
    broker: Any | None = None,
) -> dict[str, Any] | None:
    if not isinstance(taskspec_payload, dict):
        return None
    status_queue = pipeline_status_queue_name(tid, taskspec_payload)
    if not isinstance(status_queue, str) or not status_queue:
        return None

    with queue_broker(ctx, status_queue, broker=broker) as db:
        latest: dict[str, Any] | None = None
        for payload, _timestamp in iter_broker_queue_json_entries(db, status_queue):
            payload_tid = payload.get("pipeline_tid")
            if payload.get("type") != "pipeline_status":
                continue
            if isinstance(payload_tid, str) and payload_tid != tid:
                continue
            latest = payload
        return latest


def _pipeline_task_snapshot(
    ctx: WeftContext,
    tid: str,
    pipeline_status: dict[str, Any],
    base_snapshot: system_cmd.TaskSnapshot | None,
    *,
    taskspec_payload: dict[str, Any] | None,
    broker: Any | None = None,
) -> system_cmd.TaskSnapshot:
    taskspec_payload = taskspec_payload if taskspec_payload is not None else {}
    state = taskspec_payload.get("state") if isinstance(taskspec_payload, dict) else {}
    state = state if isinstance(state, dict) else {}
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
    timestamp_raw = pipeline_status.get("timestamp")
    last_timestamp = (
        int(timestamp_raw)
        if isinstance(timestamp_raw, int | float | str) and str(timestamp_raw).isdigit()
        else base_snapshot.last_timestamp
        if base_snapshot is not None
        else 0
    )
    now_ns = time.time_ns()
    if isinstance(started_at, int) and not isinstance(completed_at, int):
        duration = max(0.0, (now_ns - started_at) / 1_000_000_000)
    elif isinstance(started_at, int) and isinstance(completed_at, int):
        duration = max(0.0, (completed_at - started_at) / 1_000_000_000)
    else:
        duration = None

    status_value = pipeline_status.get("status")
    status_text = status_value if isinstance(status_value, str) else "created"
    runner = base_snapshot.runner if base_snapshot is not None else None
    runtime_handle = base_snapshot.runtime_handle if base_snapshot is not None else None
    runtime = base_snapshot.runtime if base_snapshot is not None else None

    if base_snapshot is None:
        mapping_entry = mapping_for_tid(ctx, tid, broker=broker)
        runner = system_cmd._runner_name_for_snapshot(
            taskspec=taskspec_payload if isinstance(taskspec_payload, dict) else {},
            mapping_entry=mapping_entry,
        )
        runtime_handle_obj = system_cmd._runtime_handle_from_mapping(
            mapping_entry or {}
        )
        runtime_handle = (
            runtime_handle_obj.to_dict() if runtime_handle_obj is not None else None
        )
        runtime = task_evidence.describe_runtime(runtime_handle_obj)

    activity = pipeline_status.get("activity")
    waiting_on = pipeline_status.get("waiting_on")
    if status_text in system_cmd.TERMINAL_TASK_STATUSES:
        activity = None
        waiting_on = None

    metadata: dict[str, Any] = {}
    if base_snapshot is not None:
        metadata.update(base_snapshot.metadata)
    task_metadata = taskspec_payload.get("metadata")
    if isinstance(task_metadata, dict):
        metadata.update(task_metadata)
    pipeline_name = pipeline_status.get("pipeline_name")
    if isinstance(pipeline_name, str) and pipeline_name:
        snapshot_name = pipeline_name
    elif base_snapshot is not None:
        snapshot_name = base_snapshot.name
    else:
        snapshot_name = str(taskspec_payload.get("name") or tid)

    return system_cmd.TaskSnapshot(
        tid=tid,
        tid_short=tid_short_form(tid),
        name=snapshot_name,
        status=status_text,
        event="pipeline_status",
        activity=activity if isinstance(activity, str) and activity else None,
        waiting_on=waiting_on if isinstance(waiting_on, str) and waiting_on else None,
        started_at=started_at if isinstance(started_at, int) else None,
        completed_at=completed_at if isinstance(completed_at, int) else None,
        last_timestamp=last_timestamp,
        duration_seconds=duration,
        runner=runner,
        runtime_handle=runtime_handle,
        runtime=runtime,
        metadata=metadata,
        pipeline_status=pipeline_status,
        runner_diagnostics=base_snapshot.runner_diagnostics
        if base_snapshot is not None
        else None,
    )


def _ctrl_in_for_tid(ctx: WeftContext, tid: str) -> str:
    taskspec = load_latest_taskspec_payload(ctx, tid)
    if taskspec:
        io_section = taskspec.get("io") or {}
        control = io_section.get("control") or {}
        ctrl_in = control.get("ctrl_in")
        if isinstance(ctrl_in, str) and ctrl_in:
            return ctrl_in
    return f"T{tid}.{QUEUE_CTRL_IN_SUFFIX}"


def _ctrl_out_for_tid(
    ctx: WeftContext,
    tid: str,
    *,
    taskspec: dict[str, Any] | None = None,
) -> str:
    if taskspec is None:
        taskspec = load_latest_taskspec_payload(ctx, tid)
    if taskspec:
        io_section = taskspec.get("io") or {}
        control = io_section.get("control") or {}
        ctrl_out = control.get("ctrl_out")
        if isinstance(ctrl_out, str) and ctrl_out:
            return ctrl_out
    return f"T{tid}.ctrl_out"


def _send_control(ctx: WeftContext, tid: str, command: str) -> None:
    """Write a control command to a task's ctrl_in queue.

    Spec: [MF-3]
    """
    ctrl_in = _ctrl_in_for_tid(ctx, tid)
    queue = ctx.queue(ctrl_in, persistent=True)
    try:
        queue.write(encode_control_message(command))
    finally:
        queue.close()


def _host_pids_from_mapping(entry: dict[str, Any]) -> tuple[int, ...]:
    handle = system_cmd._runtime_handle_from_mapping(entry)
    if handle is None or handle.control.get("authority") != "host-pid":
        return ()
    return handle.scoped_host_pids()


def _host_processes_from_mapping(
    entry: dict[str, Any],
) -> tuple[tuple[int, float | None], ...]:
    """Return (pid, create_time) pairs for a mapping entry's host-pid handle.

    Sibling of `_host_pids_from_mapping` that preserves the recorded process
    creation-time identity so signal-sending call sites can verify each PID
    through the same verified process instance (Spec: [CC-3.2]).
    """
    handle = system_cmd._runtime_handle_from_mapping(entry)
    if handle is None or handle.control.get("authority") != "host-pid":
        return ()
    return handle.scoped_host_processes()


def _pid_exists(pid: int | None) -> bool:
    return pid_is_live(pid)


def _snapshot_from_terminal_ctrl_out(
    *,
    tid: str,
    payload: dict[str, Any],
    taskspec_payload: dict[str, Any],
) -> system_cmd.TaskSnapshot | None:
    """Build a terminal snapshot from task-local ctrl_out evidence."""

    if payload.get("type") != TERMINAL_ENVELOPE_TYPE:
        return None
    if payload.get("tid") != tid:
        return None
    status = payload.get("status")
    if not isinstance(status, str) or status not in system_cmd.TERMINAL_TASK_STATUSES:
        return None
    timestamp_raw = payload.get("timestamp")
    timestamp = timestamp_raw if isinstance(timestamp_raw, int) else time.time_ns()
    state_raw = taskspec_payload.get("state")
    state = state_raw if isinstance(state_raw, dict) else {}
    metadata_raw = taskspec_payload.get("metadata")
    metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
    spec_raw = taskspec_payload.get("spec")
    spec = spec_raw if isinstance(spec_raw, dict) else {}
    return_code = payload.get("return_code")
    error = payload.get("error")
    return system_cmd.TaskSnapshot(
        tid=tid,
        tid_short=tid_short_form(tid),
        name=str(taskspec_payload.get("name") or tid),
        status=status,
        event="ctrl_out_terminal",
        activity=None,
        waiting_on=None,
        started_at=state.get("started_at")
        if isinstance(state.get("started_at"), int)
        else None,
        completed_at=state.get("completed_at")
        if isinstance(state.get("completed_at"), int)
        else timestamp,
        last_timestamp=timestamp,
        duration_seconds=None,
        runner=_runner_name_from_taskspec(spec),
        runtime_handle=None,
        runtime=None,
        metadata=dict(metadata),
        return_code=return_code if isinstance(return_code, int) else None,
        error=error if isinstance(error, str) else None,
    )


class _ControlSurfaceResources:
    """Own the current control-observation queues and their change monitor."""

    def __init__(
        self,
        ctx: WeftContext,
        *,
        state_queue_name: str | None,
        ctrl_out_name: str,
        pipeline_status_name: str | None,
    ) -> None:
        self.state_queue_name = state_queue_name
        self.ctrl_out_name = ctrl_out_name
        self.pipeline_status_name = pipeline_status_name
        self._queues: list[Queue] = []
        self._stack = ExitStack()
        self._monitor: QueueChangeMonitor | None = None
        try:
            if state_queue_name is not None:
                self._open_queue(ctx, state_queue_name)
            self.log_queue = self._open_queue(ctx, WEFT_GLOBAL_LOG_QUEUE)
            self.ctrl_out_queue = self._open_queue(ctx, ctrl_out_name)
            if isinstance(pipeline_status_name, str) and pipeline_status_name:
                self._open_queue(ctx, pipeline_status_name)
            self._monitor = QueueChangeMonitor(self._queues, config=ctx.config)
            self._stack.callback(self._monitor.close)
        except BaseException:
            self.close()
            raise

    def _open_queue(self, ctx: WeftContext, name: str) -> Queue:
        queue = ctx.queue(name, persistent=True)
        self._stack.callback(queue.close)
        self._queues.append(queue)
        return queue

    def matches(
        self,
        *,
        state_queue_name: str | None,
        ctrl_out_name: str,
        pipeline_status_name: str | None,
    ) -> bool:
        """Return whether names still describe this observed surface."""

        return (
            self.state_queue_name == state_queue_name
            and self.ctrl_out_name == ctrl_out_name
            and self.pipeline_status_name == pipeline_status_name
        )

    def wait(self, timeout: float | None) -> bool:
        """Wait for activity on any queue in the current surface."""

        monitor = self._monitor
        if monitor is None:
            return False
        return monitor.wait(timeout)

    def close(self) -> None:
        """Close the monitor before its queues, at most once per resource."""

        self._monitor = None
        self._queues = []
        self._stack.close()


@dataclass(frozen=True, slots=True)
class _ControlSurfaceObservation:
    """Facts observed while draining the current ctrl-out queue."""

    terminal_snapshot: system_cmd.TaskSnapshot | None
    public_signal_observed_at: float | None
    kill_ack_observed_at: float | None


def _observe_control_envelopes(
    ctrl_queue: Queue,
    *,
    tid: str,
    taskspec_payload: dict[str, Any],
) -> _ControlSurfaceObservation:
    """Drain current control facts, stopping at the first typed terminal."""

    public_signal_observed_at: float | None = None
    kill_ack_observed_at: float | None = None
    while True:
        ctrl_raw = ctrl_queue.read_one()
        if ctrl_raw is None:
            break
        ctrl_payload = ctrl_raw[0] if isinstance(ctrl_raw, tuple) else ctrl_raw
        try:
            payload = json.loads(str(ctrl_payload))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        terminal_snapshot = _snapshot_from_terminal_ctrl_out(
            tid=tid,
            payload=payload,
            taskspec_payload=taskspec_payload,
        )
        if terminal_snapshot is not None:
            return _ControlSurfaceObservation(
                terminal_snapshot,
                public_signal_observed_at,
                kill_ack_observed_at,
            )
        if ("command" in payload and "status" in payload) or (
            payload.get("type") == "terminal" and isinstance(payload.get("status"), str)
        ):
            public_signal_observed_at = time.monotonic()
        command = str(payload.get("command", "")).strip().upper()
        status = str(payload.get("status", "")).strip().lower()
        if command == CONTROL_KILL and status == "ack":
            kill_ack_observed_at = time.monotonic()
    return _ControlSurfaceObservation(
        None,
        public_signal_observed_at,
        kill_ack_observed_at,
    )


def _control_surface_wait_timeout(
    *,
    overall_deadline: float,
    public_signal_deadline: float | None,
    kill_ack_deadline: float | None,
    now: float,
    interval: float,
) -> float | None:
    """Select the next existing control-observation wait budget."""

    overall_remaining = overall_deadline - now
    if overall_remaining <= 0:
        if public_signal_deadline is None:
            return None
        public_signal_remaining = public_signal_deadline - now
        if public_signal_remaining <= 0:
            return None
        return min(public_signal_remaining, interval)
    wait_timeout = min(overall_remaining, interval)
    if kill_ack_deadline is None:
        return wait_timeout
    kill_ack_remaining = kill_ack_deadline - now
    if kill_ack_remaining <= 0:
        return None
    return min(wait_timeout, kill_ack_remaining)


@typed_command_errors
def _await_control_surface(
    ctx: WeftContext,
    tid: str,
    *,
    timeout: float = CONTROL_SURFACE_WAIT_TIMEOUT,
) -> tuple[dict[str, Any] | None, system_cmd.TaskSnapshot | None]:
    """Observe dynamic task-control endpoints within the bounded wait budget.

    Spec: docs/specifications/05-Message_Flow_and_State.md [MF-3], [MF-5];
    docs/specifications/14-Python_API_Surfaces.md [PY-2].
    """

    state_queue_name = task_state_queue_name(tid) if is_task_tid(tid) else None
    deadline = time.monotonic() + timeout
    latest_entry: dict[str, Any] | None = None
    latest_snapshot: system_cmd.TaskSnapshot | None = None
    initial_taskspec_payload = load_latest_taskspec_payload(ctx, tid) or {}
    watched_pipeline_status_queue = pipeline_status_queue_name(
        tid,
        initial_taskspec_payload,
    )
    watched_ctrl_out_queue = _ctrl_out_for_tid(
        ctx,
        tid,
        taskspec=initial_taskspec_payload
        if isinstance(initial_taskspec_payload, dict)
        else None,
    )
    public_signal_deadline: float | None = None
    with ctx.session():
        resources = _ControlSurfaceResources(
            ctx,
            state_queue_name=state_queue_name,
            ctrl_out_name=watched_ctrl_out_queue,
            pipeline_status_name=watched_pipeline_status_queue,
        )
        try:
            kill_ack_deadline: float | None = None
            while True:
                with resources.log_queue.get_connection() as broker:
                    taskspec_payload = (
                        load_latest_taskspec_payload(ctx, tid, broker=broker) or {}
                    )
                pipeline_status_queue = pipeline_status_queue_name(
                    tid, taskspec_payload
                )
                ctrl_out_queue = _ctrl_out_for_tid(
                    ctx,
                    tid,
                    taskspec=taskspec_payload
                    if isinstance(taskspec_payload, dict)
                    else None,
                )
                if not resources.matches(
                    state_queue_name=state_queue_name,
                    ctrl_out_name=ctrl_out_queue,
                    pipeline_status_name=pipeline_status_queue,
                ):
                    resources.close()
                    resources = _ControlSurfaceResources(
                        ctx,
                        state_queue_name=state_queue_name,
                        ctrl_out_name=ctrl_out_queue,
                        pipeline_status_name=pipeline_status_queue,
                    )

                observation = _observe_control_envelopes(
                    resources.ctrl_out_queue,
                    tid=tid,
                    taskspec_payload=taskspec_payload
                    if isinstance(taskspec_payload, dict)
                    else {},
                )
                if observation.terminal_snapshot is not None:
                    return latest_entry, observation.terminal_snapshot
                if observation.public_signal_observed_at is not None:
                    public_signal_deadline = (
                        observation.public_signal_observed_at
                        + CONTROL_SURFACE_WAIT_INTERVAL
                    )
                if observation.kill_ack_observed_at is not None:
                    kill_ack_deadline = (
                        observation.kill_ack_observed_at + CONTROL_SURFACE_WAIT_INTERVAL
                    )

                with resources.log_queue.get_connection() as broker:
                    latest_entry = (
                        mapping_for_tid(ctx, tid, broker=broker) or latest_entry
                    )
                    snapshot = _task_status(
                        tid,
                        context=ctx,
                        broker=broker,
                        observation_queue=resources.log_queue,
                    )
                if snapshot is not None:
                    latest_snapshot = snapshot
                    if snapshot.status in system_cmd.TERMINAL_TASK_STATUSES:
                        return latest_entry, latest_snapshot
                    if (
                        kill_ack_deadline is not None
                        and time.monotonic() >= kill_ack_deadline
                    ):
                        return latest_entry, latest_snapshot
                wait_timeout = _control_surface_wait_timeout(
                    overall_deadline=deadline,
                    public_signal_deadline=public_signal_deadline,
                    kill_ack_deadline=kill_ack_deadline,
                    now=time.monotonic(),
                    interval=CONTROL_SURFACE_WAIT_INTERVAL,
                )
                if wait_timeout is None:
                    return latest_entry, latest_snapshot
                resources.wait(wait_timeout)
        finally:
            resources.close()


def _latest_task_entry(
    ctx: WeftContext,
    lookup: dict[str, dict[str, Any]],
    tid: str,
    current: dict[str, Any] | None,
) -> dict[str, Any] | None:
    return mapping_for_tid(ctx, tid) or current or lookup.get(tid)


def _stop_via_fallback(task_entry: dict[str, Any] | None) -> bool:
    if task_entry is None:
        return False

    handle = system_cmd._runtime_handle_from_mapping(task_entry)
    if handle is not None:
        if handle.control.get("authority") == "external-supervisor":
            return False
        plugin = require_runner_plugin(handle.runner)
        plugin.stop(handle, timeout=0.2)
        return True

    return False


def _stop_terminal_host_process(task_entry: dict[str, Any] | None) -> bool:
    if task_entry is None:
        return False

    handle = system_cmd._runtime_handle_from_mapping(task_entry)
    if handle is not None and handle.runner not in {"host", "macos-sandbox"}:
        return False

    return _stop_via_fallback(task_entry)


def _kill_via_fallback(task_entry: dict[str, Any] | None) -> bool:
    if task_entry is None:
        return False

    handle = system_cmd._runtime_handle_from_mapping(task_entry)
    if handle is not None:
        if handle.control.get("authority") == "external-supervisor":
            return False
        plugin = require_runner_plugin(handle.runner)
        plugin.kill(handle, timeout=0.2)
        return True

    return False


def _force_kill_task_processes(task_entry: dict[str, Any] | None) -> bool:
    """Attempt a force-kill for each identity-verified mapped host process.

    The return value records whether the host fallback was attempted. Runtime
    death is proven separately by ``_kill_success_is_proven`` because Windows
    may keep a terminated PID observable while process handles remain open.
    """

    if task_entry is None:
        return False

    processes: dict[int, float | None] = dict(_host_processes_from_mapping(task_entry))

    kill_attempted = False
    for pid_value, create_time in processes.items():
        attempted = terminate_verified_process_tree(
            pid_value, create_time, timeout=0.2, kill=True
        )
        kill_attempted = attempted or kill_attempted
    return kill_attempted


def _observable_host_pids_from_mapping(
    task_entry: dict[str, Any] | None,
) -> tuple[int, ...]:
    if task_entry is None:
        return ()

    handle = system_cmd._runtime_handle_from_mapping(task_entry)
    if handle is None:
        return _host_pids_from_mapping(task_entry)
    return handle.scoped_host_pids()


def _observed_host_pids_are_dead(
    task_entry: dict[str, Any] | None,
    *,
    timeout: float = 0.0,
) -> bool | None:
    pids = tuple(dict.fromkeys(_observable_host_pids_from_mapping(task_entry)))
    if not pids:
        return None

    deadline = time.monotonic() + max(timeout, 0.0)
    while True:
        if all(not _pid_exists(pid) for pid in pids):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(
            min(TASK_PID_EXIT_POLL_INTERVAL, max(0.0, deadline - time.monotonic()))
        )


def _kill_success_is_proven(
    task_entry: dict[str, Any] | None,
    *,
    handled_by_runner: bool,
    host_fallback_attempted: bool,
) -> bool:
    observed_dead = _observed_host_pids_are_dead(
        task_entry,
        timeout=0.2 if handled_by_runner or host_fallback_attempted else 0.0,
    )
    if observed_dead is not None:
        return observed_dead
    return handled_by_runner or host_fallback_attempted


def _control_terminal_status(
    snapshot: system_cmd.TaskSnapshot | None,
) -> str | None:
    if snapshot is None or snapshot.status not in system_cmd.TERMINAL_TASK_STATUSES:
        return None
    return snapshot.status


def _require_control_action(
    action: str,
    allowed: set[str],
) -> None:
    if action not in allowed:
        allowed_values = ", ".join(sorted(allowed))
        raise RuntimeError(
            f"Unexpected control convergence action {action!r}; "
            f"expected one of: {allowed_values}"
        )


def stop_tasks(
    tids: Iterable[str],
    *,
    context: WeftContext | None = None,
    context_path: str | os.PathLike[str] | None = None,
) -> int:
    """Gracefully stop one or more tasks by sending STOP control messages.

    Spec: docs/specifications/10-CLI_Interface.md [CLI-1.2.3] (task stop)
    """
    ctx = _coerce_context(context=context, context_path=context_path)
    resolved_tids = [
        resolve_full_tid(ctx, tid) or tid.strip().lstrip("T") for tid in tids
    ]
    entries = _read_tid_state_entries(ctx, tids=resolved_tids)
    lookup: dict[str, dict[str, Any]] = {}
    for mapping_entry in entries:
        full_tid = mapping_entry.get("full")
        if isinstance(full_tid, str):
            lookup[full_tid] = mapping_entry
    count = 0
    for full in resolved_tids:
        if not full:
            continue
        _send_control(ctx, full, CONTROL_STOP)
        task_entry, snapshot = _await_control_surface(ctx, full)
        handled_by_runner = False
        fallback_attempted = False
        decision = reduce_control_convergence(
            "command_sent",
            ControlConvergenceEvidence(
                command=CONTROL_STOP,
                terminal_status=_control_terminal_status(snapshot),
                observation_budget_expired=True,
            ),
        )
        _require_control_action(decision.action, {"accept_terminal", "escalate_runner"})
        if decision.action == "escalate_runner":
            task_entry = _latest_task_entry(ctx, lookup, full, task_entry)
            handled_by_runner = _stop_via_fallback(task_entry)
            fallback_attempted = True
            task_entry, snapshot = _await_control_surface(ctx, full)
        elif snapshot is not None and snapshot.status == "cancelled":
            task_entry = _latest_task_entry(ctx, lookup, full, task_entry)
            handled_by_runner = _stop_terminal_host_process(task_entry)
            fallback_attempted = True

        decision = reduce_control_convergence(
            "escalating_runner" if fallback_attempted else "command_sent",
            ControlConvergenceEvidence(
                command=CONTROL_STOP,
                terminal_status=_control_terminal_status(snapshot),
                runner_fallback_attempted=fallback_attempted,
                observation_budget_expired=True,
            ),
        )
        _require_control_action(
            decision.action,
            {"accept_terminal", "escalate_runner", "report_unknown"},
        )
        if (
            decision.action in {"escalate_runner", "report_unknown"}
            and not handled_by_runner
        ):
            task_entry = _latest_task_entry(ctx, lookup, full, task_entry)
            _stop_via_fallback(task_entry)
        count += 1
    return count


def _require_controllable_task(
    context: WeftContext,
    tid: str,
    full_tid: str,
) -> system_cmd.TaskSnapshot | None:
    """Return known task evidence or reject an unknown task before control."""

    snapshot = task_status(full_tid, context=context)
    if snapshot is None and mapping_for_tid(context, full_tid) is None:
        raise TaskNotFound(f"Task {tid} not found")
    return snapshot


def stop_task(
    tid: str,
    *,
    context: WeftContext | None = None,
    context_path: str | os.PathLike[str] | None = None,
) -> None:
    """Stop one task or raise a typed exception."""

    ctx = _coerce_context(context=context, context_path=context_path)
    full = resolve_full_tid(ctx, tid) or tid.strip().lstrip("T")
    snapshot = _require_controllable_task(ctx, tid, full)
    if snapshot is not None and snapshot.status in system_cmd.TERMINAL_TASK_STATUSES:
        raise ControlRejected(f"Task {tid} already {snapshot.status}")
    if stop_tasks([full], context=ctx) <= 0:
        raise ControlRejected(f"Failed to stop task {tid}")


def kill_tasks(
    tids: Iterable[str],
    *,
    context: WeftContext | None = None,
    context_path: str | os.PathLike[str] | None = None,
) -> int:
    """Force-terminate one or more tasks by sending KILL control messages.

    Spec: docs/specifications/10-CLI_Interface.md [CLI-1.2.3] (task kill)
    """
    ctx = _coerce_context(context=context, context_path=context_path)
    resolved_tids = [
        resolve_full_tid(ctx, tid) or tid.strip().lstrip("T") for tid in tids
    ]
    entries = _read_tid_state_entries(ctx, tids=resolved_tids)
    lookup: dict[str, dict[str, Any]] = {}
    for mapping_entry in entries:
        full_tid = mapping_entry.get("full")
        if isinstance(full_tid, str):
            lookup[full_tid] = mapping_entry
    killed = 0
    for full in resolved_tids:
        if not full:
            continue
        _send_control(ctx, full, CONTROL_KILL)
        task_entry, snapshot = _await_control_surface(ctx, full)

        decision = reduce_control_convergence(
            "command_sent",
            ControlConvergenceEvidence(
                command=CONTROL_KILL,
                terminal_status=_control_terminal_status(snapshot),
                observation_budget_expired=True,
            ),
        )
        _require_control_action(decision.action, {"accept_terminal", "escalate_runner"})
        if decision.action == "accept_terminal":
            killed += 1
            continue

        task_entry = _latest_task_entry(ctx, lookup, full, task_entry)
        handled_by_runner = _kill_via_fallback(task_entry)
        task_entry, snapshot = _await_control_surface(ctx, full)

        decision = reduce_control_convergence(
            "escalating_runner",
            ControlConvergenceEvidence(
                command=CONTROL_KILL,
                terminal_status=_control_terminal_status(snapshot),
                runner_fallback_attempted=True,
                observation_budget_expired=True,
            ),
        )
        _require_control_action(
            decision.action,
            {"accept_terminal", "escalate_host", "report_unknown"},
        )
        if decision.action == "accept_terminal":
            killed += 1
            continue
        if decision.action == "report_unknown":
            continue

        task_entry = _latest_task_entry(ctx, lookup, full, task_entry)
        host_fallback_attempted = False
        observed_dead = _observed_host_pids_are_dead(task_entry)
        if not handled_by_runner or observed_dead is False:
            host_fallback_attempted = _force_kill_task_processes(task_entry)
        success_proven = _kill_success_is_proven(
            task_entry,
            handled_by_runner=handled_by_runner,
            host_fallback_attempted=host_fallback_attempted,
        )
        decision = reduce_control_convergence(
            "escalating_host",
            ControlConvergenceEvidence(
                command=CONTROL_KILL,
                runtime_dead_after_control=success_proven,
                runner_fallback_attempted=True,
                host_fallback_attempted=host_fallback_attempted,
                observation_budget_expired=True,
            ),
        )
        _require_control_action(
            decision.action, {"accept_dead_runtime", "report_unknown"}
        )
        if decision.action == "accept_dead_runtime":
            killed += 1
    return killed


def kill_task(
    tid: str,
    *,
    context: WeftContext | None = None,
    context_path: str | os.PathLike[str] | None = None,
) -> None:
    """Kill one task or raise a typed exception."""

    ctx = _coerce_context(context=context, context_path=context_path)
    full = resolve_full_tid(ctx, tid) or tid.strip().lstrip("T")
    snapshot = _require_controllable_task(ctx, tid, full)
    if kill_tasks([full], context=ctx) <= 0:
        if (
            snapshot is not None
            and snapshot.status in system_cmd.TERMINAL_TASK_STATUSES
        ):
            raise ControlRejected(
                f"Task {tid} already {snapshot.status}; "
                "no live runtime was found to kill"
            )
        raise ControlRejected(f"Failed to kill task {tid}")


def filter_tids_by_pattern(
    snapshots: Iterable[system_cmd.TaskSnapshot | TaskSnapshot],
    pattern: str,
) -> list[str]:
    if not pattern:
        return [snap.tid for snap in snapshots]
    return [snap.tid for snap in snapshots if fnmatchcase(snap.name, pattern)]


def _process_fields_for_tid(
    ctx: WeftContext,
    tid: str,
) -> dict[str, tuple[int, ...]]:
    """Return structured process enrichment for task status."""

    mapping = mapping_for_tid(ctx, tid) or {}
    managed_pids: tuple[int, ...] = ()
    handle = system_cmd._runtime_handle_from_mapping(mapping)
    if handle is not None:
        managed_pids = handle.scoped_host_pids()
    return {
        "host_pids": managed_pids,
        "managed_pids": managed_pids,
        "live_managed_pids": tuple(pid for pid in managed_pids if pid_is_live(pid)),
    }


def _command_tid(
    raw: str,
    *,
    context: WeftContext,
    allow_unknown_full: bool = False,
) -> str:
    """Normalize one public command TID or raise its typed failure."""

    candidate = raw.strip().lstrip("T")
    if not candidate or not (candidate.isascii() and candidate.isdecimal()):
        raise InvalidTID(f"Invalid task ID: {raw!r}")
    if is_task_tid(candidate):
        if allow_unknown_full:
            return candidate
        resolved = resolve_tid(tid=candidate, context=context)
        return resolved or candidate
    resolved = resolve_tid(tid=candidate, context=context)
    if resolved is None or not is_task_tid(resolved):
        raise TaskNotFound(f"Task {raw} not found")
    return resolved


@typed_command_errors
def cmd_task_list(
    *,
    status: str | None = None,
    all: bool = False,
    context: Path | None = None,
) -> tuple[TaskSnapshot, ...]:
    """Return the selected task snapshots.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2]
    """

    return tuple(
        list_task_snapshots(
            status_filter=status,
            include_terminal=all,
            context_path=context,
        )
    )


@typed_command_errors
def cmd_task_status(
    tid: str,
    *,
    process: bool = False,
    watch: bool = False,
    ping: bool = False,
    context: Path | None = None,
) -> TaskSnapshot | CommandStream[TaskEvent]:
    """Return one task snapshot or a structured event stream.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2]
    """

    ctx = _coerce_context(context_path=context)
    full_tid = _command_tid(tid, context=ctx, allow_unknown_full=True)
    if ping:
        internal_snapshot = task_status(
            full_tid,
            include_terminal=True,
            ping=True,
            context=ctx,
        )
        snapshot = (
            _public_snapshot(
                internal_snapshot,
                taskspec_payload=_load_taskspec_payload_bounded(ctx, full_tid),
            )
            if internal_snapshot is not None
            else None
        )
    else:
        snapshot = task_snapshot(full_tid, context=ctx)
    if snapshot is None:
        raise TaskNotFound(f"Task {tid} not found")
    if watch:
        # Late import breaks the intentional tasks <-> events helper cycle.
        from .events import follow_task_events

        return cast(CommandStream[TaskEvent], follow_task_events(ctx, full_tid))
    if process:
        process_fields = _process_fields_for_tid(ctx, full_tid)
        snapshot = replace(
            snapshot,
            host_pids=process_fields["host_pids"],
            managed_pids=process_fields["managed_pids"],
            live_managed_pids=process_fields["live_managed_pids"],
        )
    return snapshot


@typed_command_errors
def cmd_task_ping(
    tid: str,
    *,
    timeout: float = TASK_PING_TIMEOUT_SECONDS,
    context: Path | None = None,
) -> TaskPingResult:
    """Send a keyed task PING and return its structured observation.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2]
    """

    ctx = _coerce_context(context_path=context)
    full_tid = _command_tid(tid, context=ctx, allow_unknown_full=True)
    payload = task_ping(full_tid, timeout=timeout, context=ctx)
    timed_out = bool(payload.get("timed_out"))
    pong = payload.get("pong")
    normalized_pong = dict(pong) if isinstance(pong, Mapping) else None
    return TaskPingResult(
        tid=full_tid,
        acknowledged=not timed_out and normalized_pong is not None,
        timed_out=timed_out,
        error=str(payload["error"]) if payload.get("error") is not None else None,
        observed_at=(
            int(payload["observed_at"])
            if isinstance(payload.get("observed_at"), int)
            else None
        ),
        pong=normalized_pong,
        snapshot=task_snapshot(full_tid, context=ctx),
    )


def _selected_control_tids(
    tid: str | None,
    *,
    all_tasks: bool,
    pattern: str | None,
    context: WeftContext,
) -> tuple[str, ...]:
    if all_tasks or pattern:
        snapshots = list_task_snapshots(include_terminal=False, context=context)
        return tuple(filter_tids_by_pattern(snapshots, pattern or ""))
    if tid is None:
        raise CommandUsageError("Provide a task id or use --all or --pattern")
    return (_command_tid(tid, context=context, allow_unknown_full=True),)


def _task_control_result(
    command: Literal["stop", "kill"],
    tid: str | None,
    *,
    tids: Sequence[str] | None = None,
    all_tasks: bool,
    pattern: str | None,
    context_path: Path | None,
    runtime_context: WeftContext | None = None,
) -> TaskControlResult:
    ctx = _coerce_context(context=runtime_context, context_path=context_path)
    if tids is not None and (all_tasks or pattern is not None):
        raise CommandUsageError(
            "Explicit task ids cannot be combined with --all or --pattern"
        )
    explicit_tids = tids
    if explicit_tids is None:
        selected = _selected_control_tids(
            tid,
            all_tasks=all_tasks,
            pattern=pattern,
            context=ctx,
        )
    else:
        selected = tuple(explicit_tids)
    # Ambiguity is batch-fatal before any control write. Other item errors
    # retain the normal accepted/failures partition below.
    for selected_tid in selected:
        resolve_full_tid(ctx, selected_tid)
    operation = stop_task if command == "stop" else kill_task
    requested: list[str] = []
    accepted: list[str] = []
    failures: list[TaskControlFailure] = []
    for selected_tid in selected:
        failure_tid = selected_tid
        requested.append(selected_tid)
        try:
            full_tid = (
                _command_tid(selected_tid, context=ctx, allow_unknown_full=True)
                if explicit_tids is not None
                else selected_tid
            )
            failure_tid = full_tid
            requested[-1] = full_tid
            operation(full_tid, context=ctx)
        except Exception as exc:
            if explicit_tids is None and not all_tasks and pattern is None:
                raise
            failures.append(
                TaskControlFailure(
                    tid=failure_tid,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
            )
        else:
            accepted.append(full_tid)
    if requested and not accepted:
        first = failures[0]
        raise ControlRejected(
            f"Failed to {command} task {first.tid}: {first.error}",
            failures=tuple(failures),
        )
    snapshots = tuple(
        snapshot
        for full_tid in accepted
        if (snapshot := task_snapshot(full_tid, context=ctx)) is not None
    )
    return TaskControlResult(
        command=command,
        requested=tuple(requested),
        accepted=tuple(accepted),
        failures=tuple(failures),
        snapshots=snapshots,
    )


@typed_command_errors
def cmd_task_stop(
    tid: str | None = None,
    *,
    all: bool = False,
    pattern: str | None = None,
    context: Path | None = None,
) -> TaskControlResult:
    """Gracefully stop the selected tasks.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2]
    """

    return _task_control_result(
        "stop",
        tid,
        tids=None,
        all_tasks=all,
        pattern=pattern,
        context_path=context,
    )


@typed_command_errors
def cmd_task_kill(
    tid: str | None = None,
    *,
    all: bool = False,
    pattern: str | None = None,
    context: Path | None = None,
) -> TaskControlResult:
    """Force-kill the selected tasks.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2]
    """

    return _task_control_result(
        "kill",
        tid,
        tids=None,
        all_tasks=all,
        pattern=pattern,
        context_path=context,
    )


@typed_command_errors
def cmd_task_tid(
    tid: str | None = None,
    *,
    pid: int | None = None,
    reverse: str | None = None,
    context: Path | None = None,
) -> str:
    """Resolve one selector to a canonical full task ID.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2]
    """

    selectors = sum(value is not None for value in (tid, pid, reverse))
    if selectors != 1:
        raise CommandUsageError("Provide exactly one of tid, --pid, or --reverse")
    ctx = _coerce_context(context_path=context)
    if reverse is not None:
        candidate = reverse.strip().lstrip("T")
        if not is_task_tid(candidate):
            raise InvalidTID(f"Invalid full task ID: {reverse!r}")
        return candidate
    resolved = resolve_tid(tid=tid, pid=pid, context=ctx)
    if resolved is None:
        raise TaskNotFound("No matching TID found")
    if not is_task_tid(resolved):
        raise InvalidTID(f"Invalid resolved task ID: {resolved!r}")
    return resolved
