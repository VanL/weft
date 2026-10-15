"""Shared result observation, acquisition and consuming settlement.

Spec references:
- docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.3], [IMPL.1]
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from simplebroker import Queue
from weft._constants import (
    TASK_HISTORY_MAX_MESSAGE_ID,
    WEFT_COMPLETED_RESULT_GRACE_SECONDS,
    WEFT_GLOBAL_LOG_QUEUE,
)
from weft._exceptions import CommandTimeoutError
from weft.context import WeftContext
from weft.core import task_evidence
from weft.core.outbox import decode_result_payload
from weft.core.outbox import process_outbox_message as decode_outbox_message
from weft.core.queue_wait import QueueChangeMonitor
from weft.core.task_evidence import coerce_terminal_envelope
from weft.core.task_state import read_task_state_snapshot, task_state_queue_name
from weft.core.taskspec import TaskSpec
from weft.helpers import closing_queue_iterator
from weft.helpers.message_ids import is_task_tid

from ._resources import command_resource_scope
from ._streaming import (
    DecodedOutboxValue,
    aggregate_public_outputs,
    handle_ctrl_stream,
    process_outbox_message,
)
from .types import TaskEvent, TaskResult


def observation_cancelled(cancel_event: Any | None) -> bool:
    """Check cancellation at an observation boundary, without timed probes."""
    return bool(cancel_event is not None and cancel_event.is_set())


@contextmanager
def result_observation(
    context: WeftContext,
    queue_names: Sequence[str],
    *,
    borrowed_queues: Sequence[Queue] = (),
    cancel_event: Any | None = None,
) -> Iterator[tuple[dict[str, Queue], QueueChangeMonitor]]:
    """Register queues before reading, and dispose observers before leases.

    Borrowed queues remain owned by the caller's enclosing session.

    Private paired contract with weft_django.realtime.AsyncIteratorOwner and
    commands.run._LiveRunSession:
    `_register_wakeup` returns idempotent deregistration. Callbacks must be
    thread-safe, non-blocking and perform no broker effects. Deregister before
    monitor disposal; a racing callback only sets its harmless activity Event.

    Spec: docs/specifications/05-Message_Flow_and_State.md [MF-5];
        docs/specifications/04-SimpleBroker_Integration.md [SB-0.4].
    """
    with command_resource_scope() as resources:
        if not borrowed_queues:
            resources.enter_context(context.session())
        queues = {queue.name: queue for queue in borrowed_queues}
        for name in queue_names:
            if name in queues:
                continue
            queue = context.queue(name, persistent=True)
            resources.callback(queue.close)
            queues[name] = queue
        # A watcher mutates its facade's stop-event custody. A borrowed facade
        # can already belong to an outer observer, so give this subscription
        # independent facades while retaining the caller's broker/session loan.
        borrowed_names = {queue.name for queue in borrowed_queues}
        watched: list[Queue] = []
        for queue in queues.values():
            if queue.name in borrowed_names:
                watch_queue = context.queue(queue.name, persistent=True)
                resources.callback(watch_queue.close)
                watched.append(watch_queue)
            else:
                watched.append(queue)
        monitor = QueueChangeMonitor(watched, config=context.config)
        resources.callback(monitor.close)
        register = getattr(cancel_event, "_register_wakeup", None)
        if callable(register):
            resources.callback(register(monitor.wake))
        yield queues, monitor


def append_public_value(
    values: list[Any],
    output: DecodedOutboxValue,
    *,
    show_stderr: bool,
) -> None:
    """Append a caller-facing value after optional stderr selection."""
    if output.emitted:
        return
    value = output.value
    if show_stderr and isinstance(value, dict) and "stderr" in value:
        values.append(value.get("stderr") or "")
        return
    values.append(value)


def _peek_result_rows(
    broker: Any,
    queue_name: str,
    horizon: int | None,
) -> Iterator[tuple[str, int] | str]:
    """Borrow public paging through the captured inclusive result range [MF-5]."""
    if horizon is None:
        return
    before = None if horizon == TASK_HISTORY_MAX_MESSAGE_ID else horizon + 1
    with closing_queue_iterator(
        broker.peek_generator(
            queue_name,
            with_timestamps=True,
            before_timestamp=before,
        )
    ) as rows:
        yield from rows


def drain_ctrl_out_stream_messages(
    broker: Any,
    queue_name: str,
    *,
    tid: str,
) -> list[tuple[dict[str, Any], int]]:
    """Render non-terminal ctrl_out messages while retaining terminal proof.

    Terminal ctrl_out envelopes are a task-local lifecycle evidence surface. The
    result waiter may observe them, but it must not consume them as ordinary
    stream/control output because later status surfaces may need the same proof
    if the global task log races or is unavailable.

    Returns ``(envelope, message_id)`` candidates so callers can apply the shared
    source-precedence selection (Spec: [MF-5]); the broker ``message_id`` is the
    timestamp the selector uses for same-source latest-wins.
    """

    terminal_candidates: list[tuple[dict[str, Any], int]] = []
    horizon: int | None = broker.latest_pending_timestamp(queue_name)
    iterator = _peek_result_rows(broker, queue_name, horizon)
    with closing_queue_iterator(iterator) as rows:
        for entry in rows:
            if not isinstance(entry, tuple) or len(entry) < 2:
                continue
            ctrl_payload, message_id = entry[0], entry[1]
            raw = str(ctrl_payload)
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = None
            if _is_completion_record(payload, raw):
                terminal_envelope = coerce_terminal_envelope(raw, tid=tid)
                if terminal_envelope is not None:
                    terminal_candidates.append((terminal_envelope, int(message_id)))
                continue
            handle_ctrl_stream(raw)
            broker.delete_message_ids(queue_name, [message_id])
    return terminal_candidates


def _is_completion_record(payload: Any, raw: str) -> bool:
    """Recognize retained completion facts for every owner of a shared route [MF-5]."""
    if not isinstance(payload, dict) or not isinstance(payload.get("tid"), str):
        return False
    return coerce_terminal_envelope(raw, tid=payload["tid"]) is not None or (
        payload.get("event") == "work_item_completed"
        and payload.get("status") == "running"
    )


@dataclass(frozen=True, slots=True)
class _ResultSettlement:
    """The collection owner's verdict after its eligible last drain [MF-5]."""

    action: Literal["wait", "result", "timeout"]
    status: str | None = None
    error: str | None = None
    wait_until: float | None = None
    reconciliation: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ResultMaterialization:
    """Retained configuration, bounded history and acquired source clocks [MF-5]."""

    config: task_evidence.TaskObservationConfig | None
    taskspec_payload: dict[str, Any] | None = None
    mapping_entry: dict[str, Any] | None = None

    @property
    def outbox_name(self) -> str:
        """Resolved output route; unavailable configuration has no output route."""
        if self.config is None:
            raise ValueError("observation metadata unavailable")
        return self.config.outbox

    @property
    def ctrl_out_name(self) -> str:
        """Resolved completion route."""
        if self.config is None:
            raise ValueError("observation metadata unavailable")
        return self.config.ctrl_out

    log_last_timestamp: int | None = None
    ctrl_last_timestamp: int | None = None
    ctrl_initial_timestamp: int | None = None
    # Each selected fact carries its own source's initial observation horizon.
    # These containers belong to the single observation owner. Dict membership
    # deduplicates retries and replaces a discovery copy by its local copy.
    batch_boundaries: list[tuple[int, float, int]] = field(default_factory=list)
    boundary_indexes: dict[str, tuple[int, bool]] = field(default_factory=dict)
    result_surface_had_activity: bool = False
    terminal_evidence: task_evidence.TaskEvidenceSnapshot | None = None
    terminal_observed_monotonic: float | None = None
    output_evidence: task_evidence.TaskEvidenceSnapshot | None = None


def _accepted_result_materialization(taskspec: TaskSpec) -> ResultMaterialization:
    """Bind committed transport configuration without rediscovery (Spec: [MF-5])."""
    if taskspec.tid is None:
        raise ValueError("accepted TaskSpec requires its committed TID")
    payload = taskspec.model_dump(mode="json")
    config = task_evidence.observation_config_from_taskspec(taskspec)
    return ResultMaterialization(config, taskspec_payload=payload)


def _result_observation_names(
    tid: str,
    materialized: ResultMaterialization,
    *,
    include_history: bool = False,
) -> list[str]:
    """Register discovery or exact bound routes before reading [MF-5]."""
    names = [task_state_queue_name(tid)] if is_task_tid(tid) else []
    config = materialized.config
    if config is None:
        names.append(f"T{tid}.ctrl_out")
    else:
        names.extend((config.outbox, config.ctrl_out))
        if config.pipeline_status is not None:
            names.append(config.pipeline_status)
    if include_history:
        names.append(WEFT_GLOBAL_LOG_QUEUE)
    return list(dict.fromkeys(names))


def _with_terminal_evidence(
    materialized: ResultMaterialization,
    evidence: task_evidence.TaskEvidenceSnapshot | None,
    *,
    now: float,
) -> ResultMaterialization:
    observed = materialized.terminal_observed_monotonic
    if (
        evidence is not None
        and evidence.classification != "claimed_result_without_terminal"
        and observed is None
    ):
        observed = now
    return replace(
        materialized,
        terminal_evidence=evidence,
        terminal_observed_monotonic=observed,
    )


def _local_completion_rows(
    broker: Any,
    queue_name: str,
    *,
    tid: str,
    cursor: int | None,
    initial: int | None,
) -> tuple[list[tuple[dict[str, Any], int]], int | None, int]:
    """Inspect only the registered local suffix, retaining negative progress [MF-5]."""
    horizon = broker.latest_pending_timestamp(queue_name)
    if initial is None:
        initial = horizon or 0
    if horizon is None or (cursor is not None and horizon <= cursor):
        return [], cursor, initial
    before = None if horizon == TASK_HISTORY_MAX_MESSAGE_ID else horizon + 1
    local: list[tuple[dict[str, Any], int]] = []
    with closing_queue_iterator(
        broker.peek_generator(
            queue_name,
            with_timestamps=True,
            after_timestamp=cursor,
            before_timestamp=before,
        )
    ) as entries:
        for body, message_id in entries:
            try:
                event = json.loads(body)
            except json.JSONDecodeError:
                continue
            if (
                _is_completion_record(event, body)
                and event["tid"] == tid
                and event.get("source") != "manager"
                and isinstance(event.get("event"), str)
            ):
                local.append((event, int(message_id)))
    return local, horizon, initial


def _retain_batch_boundaries(
    materialized: ResultMaterialization,
    rows: Sequence[tuple[dict[str, Any], int]],
    *,
    initial: int,
    is_local: bool,
    now: float,
) -> None:
    """Select each fact once, preferring its local ID without a list scan [MF-5]."""
    for event, message_id in rows:
        if event.get("event") not in {"work_item_completed", "work_completed"}:
            continue
        fact = json.dumps(event, sort_keys=True)
        indexed = materialized.boundary_indexes.get(fact)
        boundary = (message_id, now, initial)
        if indexed is None:
            materialized.boundary_indexes[fact] = (
                len(materialized.batch_boundaries),
                is_local,
            )
            materialized.batch_boundaries.append(boundary)
        elif is_local and not indexed[1]:
            index = indexed[0]
            # Keep the original grace clock when replacing a discovery copy.
            boundary = (message_id, materialized.batch_boundaries[index][1], initial)
            materialized.batch_boundaries[index] = boundary
            materialized.boundary_indexes[fact] = (index, True)


def _acquire_result_observation(
    context: WeftContext,
    tid: str,
    materialized: ResultMaterialization,
    log_queue: Queue,
    broker: Any,
    *,
    now: float,
    include_history: bool = False,
) -> tuple[ResultMaterialization, tuple[tuple[dict[str, Any], int], ...]]:
    """Acquire task-owned metadata and registered local completion [MF-5]."""
    fresh, cursor = (
        task_evidence.read_task_history(
            context, tid, broker=broker, after_timestamp=materialized.log_last_timestamp
        )
        if include_history
        else ([], materialized.log_last_timestamp)
    )
    mapping = (
        read_task_state_snapshot(context, tid, broker=broker)
        if is_task_tid(tid)
        else None
    )
    projection = (
        task_evidence.task_state_terminal_evidence(tid, mapping[1], mapping[0])
        if mapping is not None
        else None
    )
    config = materialized.config
    if config is None:
        config = task_evidence.discover_task_observation(
            context, tid, broker=broker, mapping_entry=mapping[1] if mapping else None
        )
    if projection is not None and materialized.terminal_evidence is None:
        materialized = _with_terminal_evidence(materialized, projection, now=now)
    ctrl_cursor = materialized.ctrl_last_timestamp
    ctrl_initial = materialized.ctrl_initial_timestamp
    local: list[tuple[dict[str, Any], int]] = []
    # A newly discovered route is read on the next turn, after subscription.
    if config is not None and config == materialized.config:
        local, ctrl_cursor, ctrl_initial = _local_completion_rows(
            broker, config.ctrl_out, tid=tid, cursor=ctrl_cursor, initial=ctrl_initial
        )
    elif config != materialized.config:
        ctrl_cursor = ctrl_initial = None
    _retain_batch_boundaries(
        materialized, local, initial=ctrl_initial or 0, is_local=True, now=now
    )
    acquired = replace(
        materialized,
        config=config,
        mapping_entry={**mapping[1], "timestamp": mapping[0]} if mapping else None,
        log_last_timestamp=cursor,
        ctrl_last_timestamp=ctrl_cursor,
        ctrl_initial_timestamp=ctrl_initial,
    )
    return acquired, tuple(fresh)


def _classify_result_observation(
    context: WeftContext,
    tid: str,
    materialized: ResultMaterialization,
    log_queue: Queue,
    broker: Any,
    *,
    now: float,
    allow_outbox_completion: bool = True,
) -> ResultMaterialization:
    """Classify queue-owned evidence using bound configuration [MF-5]."""
    mapping_entry = materialized.mapping_entry
    acquired = task_evidence.known_tid_evidence(
        context,
        tid=tid,
        taskspec_payload=materialized.taskspec_payload,
        observation_config=materialized.config,
        mapping_entry=mapping_entry,
        discover_observation=False,
        broker=broker,
    )
    output = None
    if acquired is not None and acquired.classification == "result_without_terminal":
        if (
            allow_outbox_completion
            and materialized.config is not None
            and materialized.config.mode == "one_shot"
        ):
            output = acquired
        acquired = task_evidence.runtime_evidence(tid=tid, mapping_entry=mapping_entry)
    terminal = acquired if acquired is not None and acquired.terminal else None
    previous = materialized.terminal_evidence
    if previous is not None and (
        terminal is None
        or (
            previous.metadata.get("terminal_source") == "task",
            previous.classification == "terminal_projection",
        )
        > (
            terminal.metadata.get("terminal_source") == "task",
            terminal.classification == "terminal_projection",
        )
    ):
        terminal = previous
    activity = materialized.result_surface_had_activity
    if materialized.config is not None:
        activity = activity or any(
            broker.peek_one(name) is not None
            for name in (materialized.config.outbox, materialized.config.ctrl_out)
        )
    classified = replace(
        materialized, output_evidence=output, result_surface_had_activity=activity
    )
    return _with_terminal_evidence(classified, terminal, now=now)


def _result_stream_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Retain the existing public stream frame shape for both observers [MF-5]."""
    normalized: dict[str, Any] = {
        "data": str(payload.get("data", "")),
        "final": bool(payload.get("final")),
    }
    chunk = payload.get("chunk")
    if isinstance(chunk, int):
        normalized["chunk"] = chunk
    encoding = payload.get("encoding")
    if isinstance(encoding, str) and encoding:
        normalized["encoding"] = encoding
    return normalized


def _claim_result_rows(
    broker: Any,
    queue_name: str,
    rows: Sequence[tuple[str, int]],
    *,
    window_rows: Sequence[tuple[str, int]],
) -> Sequence[tuple[str, int]]:
    """Claim selected rows, batching when the selection is the visible prefix."""
    selected_ids = [row[1] for row in rows]
    window_prefix_ids = [row[1] for row in window_rows[: len(rows)]]
    if rows and selected_ids == window_prefix_ids:
        last_selected_id = rows[-1][1]
        claimed = broker.claim_many(
            queue_name,
            len(rows),
            before_timestamp=(
                None
                if last_selected_id == TASK_HISTORY_MAX_MESSAGE_ID
                else last_selected_id + 1
            ),
            with_timestamps=True,
        )
        return [row for row in claimed if isinstance(row, tuple)]

    claimed_rows: list[tuple[str, int]] = []
    for _peeked, message_id in rows:
        raw = broker.claim_one(
            queue_name, exact_timestamp=message_id, with_timestamps=False
        )
        if raw is not None:
            claimed_rows.append((str(raw), message_id))
    return claimed_rows


def _drain_result_rows(
    connection_owner: Queue,
    queue_name: str,
    rows: Sequence[tuple[str, int]],
    *,
    collected: _CollectedResult,
    emit_stream: bool,
    include_stream_events: bool,
    tid: str,
    show_stderr: bool,
    window_rows: Sequence[tuple[str, int]] = (),
) -> Iterator[TaskEvent]:
    """Claim one selected row at a time and release operations before delivery."""
    decode = decode_outbox_message if include_stream_events else process_outbox_message
    pending_events: list[TaskEvent] = []
    with connection_owner.get_connection() as broker:
        claimed_rows = _claim_result_rows(
            broker, queue_name, rows, window_rows=window_rows
        )
        for raw, message_id in claimed_rows:
            collected.claimed_output = True
            if collected.first_claimed_id is None:
                collected.first_claimed_id = message_id
            final, decoded = decode(
                str(raw), collected.stream_buffer, emit_stream=emit_stream
            )
            stop = False
            if final and decoded is not None:
                stop = collected.record_output(
                    decoded, now=time.monotonic(), show_stderr=show_stderr
                )
            if include_stream_events:
                payload = decode_result_payload(str(raw))
                if isinstance(payload, dict) and payload.get("type") == "stream":
                    stream = str(payload.get("stream") or "stdout")
                    if stream in {"stdout", "stderr"}:
                        pending_events.append(
                            TaskEvent(
                                tid,
                                stream,
                                message_id,
                                _result_stream_payload(payload),
                            )
                        )
            if stop:
                break
    yield from pending_events


@dataclass(slots=True)
class _CollectedResult:
    """One collection state machine for eligible delivery and settlement [MF-5].

    Task-scope realtime borrows the same clocks and completion rules without
    claiming. Batch-scope collection keeps its first actual claim as the batch
    anchor, even when the caller suspends before the boundary is published.
    """

    deadline: float | None = None
    scope: Literal["task", "batch"] = "batch"
    live_delivery: bool = False
    values: list[Any] = field(default_factory=list)
    stream_buffer: list[str] = field(default_factory=list)
    final_count: int = 0
    first_final_seen: float | None = None
    latest_pending: int | None = None
    pending_changed_at: float | None = None
    pending_ordinary_count: int = 0
    boundary: tuple[int, float] | None = None
    stream_output: bool = False
    claimed_output: bool = False
    first_claimed_id: int | None = None
    mode: Literal["one_shot", "persistent", "pipeline"] | None = None

    def _observe_configuration(self, materialized: ResultMaterialization) -> None:
        config = materialized.config
        self.mode = config.mode if config is not None else None
        if config is not None:
            self.stream_output = self.stream_output or config.stream_output

    def should_read_output(self, materialized: ResultMaterialization) -> bool:
        """Avoid output scans when neither observation nor delivery is eligible."""
        self._observe_configuration(materialized)
        if materialized.config is None:
            return False
        if self.scope == "task" or materialized.terminal_evidence is not None:
            return True
        return self.mode is not None and (self.mode != "pipeline" or self.live_delivery)

    def observe_readonly(
        self,
        materialized: ResultMaterialization,
        *,
        final_output_seen: bool,
        stream_framed: bool,
        now: float,
    ) -> None:
        """Record assembled presence independently of the value, including null."""
        self._observe_configuration(materialized)
        self.stream_output = self.stream_output or stream_framed
        self.final_count = int(final_output_seen)
        output = materialized.output_evidence
        if output is None:
            self.first_final_seen = None
            self.latest_pending = None
        else:
            if output.observed_at != self.latest_pending:
                self.first_final_seen = None
            self.latest_pending = output.observed_at
            if self.first_final_seen is None:
                self.first_final_seen = now

    def _observe_window(self, rows: Sequence[tuple[str, int]], *, now: float) -> None:
        """Inspect framing only; peeked rows never mutate the decoder's buffer."""
        ordinary = 0
        for raw, _message_id in rows:
            payload = decode_result_payload(raw)
            if isinstance(payload, dict) and payload.get("type") == "stream":
                self.stream_output = True
            else:
                ordinary += 1
        self.pending_ordinary_count = ordinary
        current_pending = rows[-1][1] if rows else None
        if self.final_count == 0 and (
            ordinary != 1
            or self.stream_output
            or current_pending != self.latest_pending
        ):
            self.first_final_seen = None
        if rows and rows[-1][1] != self.latest_pending:
            self.latest_pending = rows[-1][1]
            self.pending_changed_at = now
        if ordinary == 1 and not self.stream_output and self.first_final_seen is None:
            self.first_final_seen = now

    def _select_batch(
        self, rows: Sequence[tuple[str, int]], materialized: ResultMaterialization
    ) -> None:
        if (
            self.mode != "persistent"
            or self.scope != "batch"
            or self.boundary is not None
        ):
            return
        first_id = self.first_claimed_id or (rows[0][1] if rows else None)
        self.boundary = next(
            (
                (message_id, observed_at)
                for message_id, observed_at, initial in materialized.batch_boundaries
                if (first_id is not None and message_id >= first_id)
                or (first_id is None and message_id > initial)
            ),
            None,
        )

    def _grace_deadline(self, materialized: ResultMaterialization) -> float | None:
        clocks = [materialized.terminal_observed_monotonic]
        if self.boundary is not None:
            clocks.append(self.boundary[1])
        elif (
            self.mode == "persistent"
            and not self.stream_output
            and self._quiet_eligible(materialized)
        ):
            clocks.append(self.pending_changed_at)
        elif self.final_count > 0 or self._quiet_eligible(materialized):
            clocks.append(self.first_final_seen)
        observed = [clock for clock in clocks if clock is not None]
        return min(observed) + WEFT_COMPLETED_RESULT_GRACE_SECONDS if observed else None

    def _expired(self, deadline: float | None, now: float) -> bool:
        return deadline is not None and now >= deadline

    def select_claims(
        self,
        rows: Sequence[tuple[str, int]],
        materialized: ResultMaterialization,
        *,
        now: float,
    ) -> tuple[tuple[str, int], ...]:
        """Select exact deliverable rows using the same facts as settlement."""
        self._observe_configuration(materialized)
        self._observe_window(rows, now=now)
        self._select_batch(rows, materialized)
        terminal = materialized.terminal_evidence
        if terminal is not None or self.boundary is not None:
            end = self.boundary[0] if self.boundary is not None else None
            return tuple(row for row in rows if end is None or row[1] <= end)
        if self.mode is None:
            return ()
        if self.stream_output or self.mode == "pipeline":
            if not self.live_delivery or (
                self.mode == "persistent" and self.final_count
            ):
                return ()
            return tuple(
                row
                for row in rows
                if isinstance(payload := decode_result_payload(row[0]), dict)
                and payload.get("type") == "stream"
                and str(payload.get("stream") or "stdout") in {"stdout", "stderr"}
            )
        if not self._ordinary_count_eligible(self.pending_ordinary_count):
            return ()
        due = self._expired(self.deadline, now) or self._expired(
            self._grace_deadline(materialized), now
        )
        return tuple(rows) if due else ()

    def record_output(
        self, output: DecodedOutboxValue, *, now: float, show_stderr: bool
    ) -> bool:
        """Fold only the value decoded after a successful actual claim."""
        self.final_count += 1
        self.stream_output = self.stream_output or output.streamed
        append_public_value(self.values, output, show_stderr=show_stderr)
        if self.first_final_seen is None:
            self.first_final_seen = now
        return (
            self.mode == "persistent"
            and self.scope == "batch"
            and self.boundary is None
            and output.streamed
        )

    def _diagnostic(
        self, materialized: ResultMaterialization, *, now: float
    ) -> _ResultSettlement | None:
        terminal = materialized.terminal_evidence
        if (
            terminal is not None
            and terminal.classification == "claimed_result_without_terminal"
            and not self.claimed_output
        ):
            return _ResultSettlement(
                "result",
                terminal.status,
                terminal.error,
                reconciliation=terminal.reconciliation,
            )
        if self.mode is not None:
            return None
        if self.deadline is not None:
            return None
        reconciliation: dict[str, Any] = {
            "classification": "observation_metadata_unavailable"
        }
        if terminal is not None:
            reconciliation["observed_terminal_status"] = terminal.status
        return _ResultSettlement(
            "result",
            "unknown",
            "observation metadata unavailable",
            reconciliation=reconciliation,
        )

    def _ordinary_count_eligible(self, count: int) -> bool:
        """Qualify an ordinary window or decoded batch using its actual count."""
        if self.stream_output or self.stream_buffer:
            return False
        return (self.mode == "one_shot" and count == 1) or (
            self.mode == "persistent" and self.scope == "batch" and count > 0
        )

    def _quiet_eligible(self, materialized: ResultMaterialization) -> bool:
        """Schedule quiet grace only for a currently eligible ordinary value."""
        if self.scope == "task":
            return (
                self._ordinary_count_eligible(1)
                and materialized.output_evidence is not None
            )
        return self._ordinary_count_eligible(
            self.pending_ordinary_count
        ) or self._ordinary_count_eligible(self.final_count)

    def _output_fallback(self, materialized: ResultMaterialization) -> bool:
        return self._ordinary_count_eligible(self.final_count) and (
            self.scope == "batch" or materialized.output_evidence is not None
        )

    def _completion(
        self,
        materialized: ResultMaterialization,
        *,
        expired: bool,
        grace_expired: bool,
    ) -> _ResultSettlement | None:
        if materialized.config is None:
            return None
        terminal = materialized.terminal_evidence
        if (
            terminal is not None
            and self.claimed_output
            and terminal.classification == "claimed_result_without_terminal"
        ):
            terminal = None
        if terminal is not None:
            if terminal.status != "completed":
                if (
                    terminal.classification != "wrapper_lost"
                    or expired
                    or grace_expired
                ):
                    return _ResultSettlement("result", terminal.status, terminal.error)
            elif self.final_count > 0 or grace_expired:
                return _ResultSettlement("result", "completed", terminal.error)
            return None
        batch = (
            self.mode == "persistent"
            and self.scope == "batch"
            and self.boundary is not None
        )
        if (batch and (self.final_count > 0 or grace_expired)) or (
            self._output_fallback(materialized) and (expired or grace_expired)
        ):
            return _ResultSettlement("result", "completed")
        return None

    def settle(
        self, materialized: ResultMaterialization, *, now: float
    ) -> _ResultSettlement:
        """Choose outcome and next real deadline after the eligible last drain."""
        self._observe_configuration(materialized)
        diagnostic = self._diagnostic(materialized, now=now)
        if diagnostic is not None:
            return diagnostic
        expired = self._expired(self.deadline, now)
        grace = self._grace_deadline(materialized)
        completion = self._completion(
            materialized, expired=expired, grace_expired=self._expired(grace, now)
        )
        if completion is not None:
            return completion
        if expired:
            return _ResultSettlement("timeout")
        deadlines = [self.deadline]
        if (
            materialized.terminal_evidence is not None
            or self.boundary is not None
            or self._quiet_eligible(materialized)
        ):
            deadlines.append(grace)
        remaining = [
            deadline
            for deadline in deadlines
            if deadline is not None and deadline > now
        ]
        return _ResultSettlement(
            "wait", wait_until=min(remaining) if remaining else None
        )


def _wait_unconfigured_result(
    tid: str,
    materialized: ResultMaterialization,
    collected: _CollectedResult,
    monitor: QueueChangeMonitor,
    *,
    deadline: float | None,
    timeout: float | None,
) -> Iterator[TaskResult]:
    """Keep unresolved discovery within the caller's original budget [MF-5]."""
    decision = collected.settle(materialized, now=time.monotonic())
    if decision.action == "result":
        yield TaskResult(
            tid,
            "unknown",
            None,
            None,
            None,
            decision.error,
            reconciliation=decision.reconciliation,
        )
        return
    if decision.action == "timeout":
        raise CommandTimeoutError(
            f"Timed out after {timeout} seconds waiting for task {tid}"
        )
    monitor.wait(None if deadline is None else max(0.0, deadline - time.monotonic()))


def _result_window(
    broker: Any,
    materialized: ResultMaterialization,
    collected: _CollectedResult,
) -> list[tuple[str, int]]:
    """Read a bounded window only once collection is eligible [MF-5]."""
    if not collected.should_read_output(materialized):
        return []
    name = materialized.outbox_name
    horizon = broker.latest_pending_timestamp(name)
    return [
        row
        for row in _peek_result_rows(broker, name, horizon)
        if isinstance(row, tuple)
    ]


def _lifecycle_events(
    tid: str, rows: Sequence[tuple[dict[str, Any], int]]
) -> Iterator[TaskEvent]:
    """Adapt explicitly followed raw lifecycle rows to public events [MF-5]."""
    for payload, message_id in rows:
        yield TaskEvent(
            tid,
            str(payload.get("event") or payload.get("status") or "task_event"),
            message_id,
            payload,
        )


def _iter_consuming_result(
    context: WeftContext,
    tid: str,
    *,
    materialized: ResultMaterialization,
    deadline: float | None,
    timeout: float | None,
    show_stderr: bool,
    emit_stream: bool,
    include_stream_events: bool,
    follow_lifecycle: bool = False,
) -> Iterator[TaskEvent | TaskResult]:
    """One consuming observation from unresolved routes to final verdict [MF-5]."""
    collected = _CollectedResult(
        deadline=deadline, live_delivery=emit_stream or include_stream_events
    )
    pending_events: list[tuple[dict[str, Any], int]] = []
    include_history = follow_lifecycle
    with command_resource_scope() as resources:
        resources.enter_context(context.session())
        lease = resources.enter_context(command_resource_scope())
        names = _result_observation_names(
            tid, materialized, include_history=include_history
        )
        observation = lease.enter_context(result_observation(context, names))
        while True:
            queues, monitor = observation
            connection_owner = queues[names[0]]
            with connection_owner.get_connection() as broker:
                materialized, fresh = _acquire_result_observation(
                    context,
                    tid,
                    materialized,
                    connection_owner,
                    broker,
                    now=time.monotonic(),
                    include_history=include_history,
                )
            pending_events.extend(fresh)
            include_history = follow_lifecycle
            wanted = _result_observation_names(
                tid, materialized, include_history=include_history
            )
            if wanted != names:
                replacement = resources.enter_context(command_resource_scope())
                observation = replacement.enter_context(
                    result_observation(context, wanted)
                )
                lease.close()
                lease, names = replacement, wanted
                queues, monitor = observation
                # Read the newly registered local route before classification.
                continue
            with connection_owner.get_connection() as broker:
                materialized = _classify_result_observation(
                    context,
                    tid,
                    materialized,
                    connection_owner,
                    broker,
                    now=time.monotonic(),
                )
            if pending_events:
                yield from _lifecycle_events(tid, pending_events)
                pending_events.clear()
                # The caller may publish while we yield. Reacquire the bounded
                # suffix and local evidence on resume before deciding a verdict.
                continue
            if materialized.config is None:
                yield from _wait_unconfigured_result(
                    tid,
                    materialized,
                    collected,
                    monitor,
                    deadline=deadline,
                    timeout=timeout,
                )
                if deadline is None:
                    return
                continue
            with connection_owner.get_connection() as broker:
                rows = _result_window(broker, materialized, collected)
                selected = collected.select_claims(
                    rows, materialized, now=time.monotonic()
                )
                drain_ctrl_out_stream_messages(
                    broker, materialized.ctrl_out_name, tid=tid
                )
            yielded = False
            for event in _drain_result_rows(
                connection_owner,
                materialized.outbox_name,
                selected,
                collected=collected,
                emit_stream=emit_stream,
                include_stream_events=include_stream_events,
                tid=tid,
                show_stderr=show_stderr,
                window_rows=rows,
            ):
                yielded = True
                yield event
            if yielded:
                continue
            decision = collected.settle(materialized, now=time.monotonic())
            if decision.action == "result":
                value = aggregate_public_outputs(collected.values)
                stdout, stderr = task_evidence.split_stdio(value)
                yield TaskResult(
                    tid,
                    decision.status or "completed",
                    value,
                    stdout,
                    stderr,
                    decision.error
                    or (
                        f"no outbox queue for task {tid}"
                        if decision.status == "missing"
                        else None
                    ),
                    reconciliation=decision.reconciliation,
                )
                return
            if decision.action == "timeout":
                raise CommandTimeoutError(
                    f"Timed out after {timeout} seconds waiting for task {tid}"
                )
            wait = (
                None
                if decision.wait_until is None
                else max(0.0, decision.wait_until - time.monotonic())
            )
            monitor.wait(wait)
