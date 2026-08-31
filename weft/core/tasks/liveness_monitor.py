"""Manager-supervised ephemeral runtime liveness reaper.

The task owns scheduling and exact TID-mapping deletion. Worker threads perform
read-only process/runtime inspection; the reactor alone touches broker state.

Spec references:
- docs/specifications/01-Core_Components.md [CC-2.3], [CC-3.2]
- docs/specifications/05-Message_Flow_and_State.md [MF-5], Cleanup Boundary
- docs/specifications/07-System_Invariants.md [LIVENESS.R1]-[LIVENESS.R10]
"""

from __future__ import annotations

import heapq
import logging
import socket
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from simplebroker.ext import BrokerError
from weft._constants import (
    LIVENESS_FULL_RECONCILE_INTERVAL_SECONDS,
    LIVENESS_MAPPING_MIN_AGE_SECONDS,
    LIVENESS_MONITOR_MAX_IN_FLIGHT_PROBES,
    LIVENESS_PROBE_INTERVAL_SECONDS,
    LIVENESS_PROBE_WORKER_NAME,
    LIVENESS_RUNTIME_PROBE_TIMEOUT_SECONDS,
    LIVENESS_UNKNOWN_TIMEOUT_SECONDS,
    TASK_REACTOR_WAKEUP_MAX_SECONDS,
    WEFT_TID_MAPPINGS_QUEUE,
)
from weft._runner_plugins import get_runner_plugin
from weft.core.queue_window import QueueWindowRow, is_old_enough
from weft.core.taskspec import TaskSpec
from weft.ext import RunnerHandle
from weft.helpers import iter_queue_entries
from weft.liveness.analysis import analyze_liveness, runtime_generation
from weft.liveness.models import LivenessObservation
from weft.liveness.policy import (
    UnknownDeadlineState,
    decode_tid_mapping_row,
    reduce_mapping_history,
    reduce_unknown_deadline,
)
from weft.liveness.registry import liveness_provider_key

from .service import (
    ServiceTask,
    ServiceWorkerContext,
    ServiceWorkerEvent,
    ServiceWorkerSpec,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MappingRow:
    """Latest valid mapping row retained in monitor memory."""

    tid: str
    message_id: int
    payload: dict[str, Any]
    generation: str


@dataclass(frozen=True, slots=True)
class ProbeWork:
    """Broker-free probe work sent to a bounded worker lane."""

    tid: str
    message_id: int
    payload: dict[str, Any]
    generation: str
    token: str


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """Read-only probe result returned to the owning reactor."""

    work: ProbeWork
    observation: LivenessObservation | None
    attempted: bool
    diagnostic: str | None = None


class LivenessMonitor(ServiceTask):
    """Periodically inspect runtimes and reap stale TID-mapping rows."""

    def __init__(
        self,
        db: Path | str | Any,
        taskspec: TaskSpec,
        *,
        stop_event: threading.Event | None = None,
        config: Mapping[str, Any] | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
        unknown_timeout_seconds: float = LIVENESS_UNKNOWN_TIMEOUT_SECONDS,
        mapping_min_age_seconds: float = LIVENESS_MAPPING_MIN_AGE_SECONDS,
    ) -> None:
        self._monotonic = monotonic_clock
        self._unknown_timeout_seconds = float(unknown_timeout_seconds)
        self._mapping_min_age_seconds = float(mapping_min_age_seconds)
        self._latest_rows: dict[str, MappingRow] = {}
        self._mapping_cursor: int | None = None
        self._deadlines: dict[str, UnknownDeadlineState] = {}
        self._in_flight: dict[str, ProbeWork] = {}
        self._due_heap: list[tuple[float, str, str]] = []
        self._due_tids: set[str] = set()
        self._next_full_reconcile_at = self._monotonic()
        super().__init__(db, taskspec, stop_event=stop_event, config=config)
        self._register_service_worker(
            ServiceWorkerSpec(
                name=LIVENESS_PROBE_WORKER_NAME,
                target=self._run_probe_worker,
                worker_count=LIVENESS_MONITOR_MAX_IN_FLIGHT_PROBES,
                input_queue_maxsize=LIVENESS_MONITOR_MAX_IN_FLIGHT_PROBES,
            )
        )
        self._start_service_worker(LIVENESS_PROBE_WORKER_NAME)
        self._activate_service_task()
        self._set_activity("waiting", waiting_on=WEFT_TID_MAPPINGS_QUEUE)

    def _build_queue_configs(self) -> dict[str, dict[str, Any]]:
        return {
            self._queue_names["ctrl_in"]: self._peek_queue_config(
                self._handle_control_message
            )
        }

    def _handle_work_message(self, message: str, timestamp: int, context: Any) -> None:
        """Reject accidental work input; this internal service has no inbox API."""

        del message, timestamp, context

    def _process_reactor_turn(self) -> None:
        self._drain_worker_results()
        if not self.should_stop:
            self._drain_queue()
        if self.should_stop or self._paused:
            self._maybe_emit_poll_report()
            return
        now = self._monotonic()
        full = now >= self._next_full_reconcile_at
        self._reconcile_mapping_rows(full=full)
        if full:
            self._next_full_reconcile_at = (
                now + LIVENESS_FULL_RECONCILE_INTERVAL_SECONDS
            )
        self._schedule_due_probes(now=now)
        self._maybe_emit_poll_report()

    def _reconcile_mapping_rows(self, *, full: bool) -> None:
        """Replay new mapping rows and exact-delete old malformed/history rows."""

        queue = self._queue(WEFT_TID_MAPPINGS_QUEUE)
        since = None if full else self._mapping_cursor
        original_latest = self._latest_rows
        latest_rows: dict[str, MappingRow] = {} if full else self._latest_rows
        newest_cursor = self._mapping_cursor
        retire_message_ids: set[int] = set()
        for body, message_id in iter_queue_entries(queue, since_timestamp=since):
            newest_cursor = (
                message_id if newest_cursor is None else max(newest_cursor, message_id)
            )
            decoded = decode_tid_mapping_row(
                QueueWindowRow(
                    queue=WEFT_TID_MAPPINGS_QUEUE,
                    body=body,
                    message_id=message_id,
                )
            )
            payload = dict(decoded.payload or {})
            tid_value = payload.get("full")
            tid = tid_value if isinstance(tid_value, str) and tid_value else None
            previous = latest_rows.get(tid) if tid is not None else None
            history = reduce_mapping_history(
                current_message_id=(
                    previous.message_id if previous is not None else None
                ),
                candidate_message_id=message_id,
                malformed_reason=decoded.malformed_reason,
                now_ns=time.time_ns(),
                min_age_seconds=self._mapping_min_age_seconds,
            )
            retire_message_ids.update(history.retire_message_ids)
            if not history.adopt_candidate or tid is None:
                continue
            row = MappingRow(
                tid=tid,
                message_id=message_id,
                payload=payload,
                generation=runtime_generation(payload),
            )
            latest_rows[tid] = row
            if not full:
                self._activate_mapping_row(row, previous=previous)
        for retire_message_id in sorted(retire_message_ids):
            self._delete_mapping_message(retire_message_id)
        if full:
            self._latest_rows = latest_rows
            for removed_tid in original_latest.keys() - latest_rows.keys():
                self._deadlines.pop(removed_tid, None)
                self._in_flight.pop(removed_tid, None)
                self._drop_due(removed_tid)
            for tid, row in latest_rows.items():
                self._activate_mapping_row(row, previous=original_latest.get(tid))
        self._mapping_cursor = newest_cursor

    def _activate_mapping_row(
        self,
        row: MappingRow,
        *,
        previous: MappingRow | None,
    ) -> None:
        """Schedule a changed latest row while preserving generation deadlines."""

        if previous is not None and (
            previous.message_id == row.message_id
            and previous.generation == row.generation
        ):
            return
        if previous is None or previous.generation != row.generation:
            self._deadlines.pop(row.tid, None)
        self._in_flight.pop(row.tid, None)
        self._push_due(row, due_at=self._monotonic())

    def _push_due(self, row: MappingRow, *, due_at: float) -> None:
        if row.tid in self._due_tids:
            self._drop_due(row.tid)
        heapq.heappush(self._due_heap, (due_at, row.tid, row.generation))
        self._due_tids.add(row.tid)

    def _drop_due(self, tid: str) -> None:
        """Remove the one scheduled due entry for ``tid``, if present."""

        if tid not in self._due_tids:
            return
        retained = [entry for entry in self._due_heap if entry[1] != tid]
        self._due_heap[:] = retained
        heapq.heapify(self._due_heap)
        self._due_tids.discard(tid)

    def _schedule_due_probes(self, *, now: float) -> None:
        while self._due_heap and self._due_heap[0][0] <= now:
            _due_at, tid, generation = heapq.heappop(self._due_heap)
            self._due_tids.discard(tid)
            row = self._latest_rows.get(tid)
            if row is None or row.generation != generation:
                continue
            if tid in self._in_flight:
                continue
            if len(self._in_flight) >= LIVENESS_MONITOR_MAX_IN_FLIGHT_PROBES:
                self._apply_not_attempted(row, now=now, diagnostic="probe_lanes_full")
                self._push_due(row, due_at=now + LIVENESS_PROBE_INTERVAL_SECONDS)
                continue
            work = ProbeWork(
                tid=tid,
                message_id=row.message_id,
                payload=row.payload,
                generation=row.generation,
                token=uuid.uuid4().hex,
            )
            if not self._enqueue_service_work(
                LIVENESS_PROBE_WORKER_NAME,
                work,
                block=False,
            ):
                self._apply_not_attempted(row, now=now, diagnostic="probe_queue_full")
                self._push_due(row, due_at=now + LIVENESS_PROBE_INTERVAL_SECONDS)
                continue
            self._in_flight[tid] = work

    @staticmethod
    def _run_probe_worker(context: ServiceWorkerContext) -> None:
        for item in context.iter_items():
            if not isinstance(item, ProbeWork):
                continue
            started = time.monotonic()
            attempted = True
            diagnostic: str | None = None
            observation: LivenessObservation | None = None
            try:
                payload_handle = item.payload.get("runtime_handle")
                try:
                    handle = (
                        RunnerHandle.from_dict(payload_handle)
                        if isinstance(payload_handle, Mapping)
                        else None
                    )
                except (TypeError, ValueError):
                    handle = None
                hostname = item.payload.get("hostname")
                if (
                    isinstance(hostname, str)
                    and hostname
                    and hostname != socket.gethostname()
                ):
                    observation = LivenessObservation(
                        item.tid,
                        "unknown",
                        f"foreign_hostname:{hostname}",
                        True,
                        item.generation,
                    )
                else:
                    if handle is not None and handle.control.get("authority") in {
                        "runner",
                        "external-supervisor",
                    }:
                        provider = liveness_provider_key(handle)
                        try:
                            get_runner_plugin(provider)
                        except (RuntimeError, OSError):
                            attempted = False
                            diagnostic = f"provider_load_failed:{provider}"
                    if attempted:
                        observation = analyze_liveness(
                            item.tid,
                            item.payload,
                            timeout_seconds=LIVENESS_RUNTIME_PROBE_TIMEOUT_SECONDS,
                        )
                        attempted = observation.attempted
                        diagnostic = observation.reason if not attempted else diagnostic
            except Exception as exc:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-372] exception
                attempted = False
                diagnostic = f"probe_internal_error:{type(exc).__name__}"
            if time.monotonic() - started > LIVENESS_RUNTIME_PROBE_TIMEOUT_SECONDS:
                attempted = False
                observation = None
                diagnostic = "probe_budget_expired"
            context.publish_event(
                "probe_result",
                ProbeResult(item, observation, attempted, diagnostic),
                item_id=item.token,
            )

    def _handle_service_worker_event(self, event: ServiceWorkerEvent) -> None:
        if event.name != LIVENESS_PROBE_WORKER_NAME:
            return
        if event.kind == "error" and event.error is not None:
            logger.critical(
                "Liveness probe worker failed",
                extra={
                    "worker_index": event.worker_index,
                    "error_type": type(event.error).__name__,
                },
                exc_info=(
                    type(event.error),
                    event.error,
                    event.error.__traceback__,
                ),
            )
            raise event.error
        if event.kind != "probe_result":
            return
        if isinstance(event.value, ProbeResult):
            self._apply_probe_result(event.value)

    def _apply_not_attempted(
        self,
        row: MappingRow,
        *,
        now: float,
        diagnostic: str,
    ) -> None:
        state, _retire = reduce_unknown_deadline(
            self._deadlines.get(row.tid),
            generation=row.generation,
            outcome="not_attempted",
            now_monotonic=now,
            timeout_seconds=self._unknown_timeout_seconds,
        )
        if state is None:
            self._deadlines.pop(row.tid, None)
        else:
            self._deadlines[row.tid] = state
        logger.debug("Liveness probe not attempted for %s: %s", row.tid, diagnostic)

    def _apply_probe_result(self, result: ProbeResult) -> None:
        """Commit one token/generation-guarded probe result on the reactor."""

        current = self._latest_rows.get(result.work.tid)
        if (
            current is None
            or current.message_id != result.work.message_id
            or current.generation != result.work.generation
        ):
            return
        in_flight = self._in_flight.get(result.work.tid)
        if in_flight is None or in_flight.token != result.work.token:
            return
        self._in_flight.pop(result.work.tid, None)
        now = self._monotonic()
        if not result.attempted or result.observation is None:
            self._apply_not_attempted(
                current,
                now=now,
                diagnostic=result.diagnostic or "not_attempted",
            )
            self._push_due(current, due_at=now + LIVENESS_PROBE_INTERVAL_SECONDS)
            return
        state, retire = reduce_unknown_deadline(
            self._deadlines.get(current.tid),
            generation=current.generation,
            outcome=result.observation.evidence,
            now_monotonic=now,
            timeout_seconds=self._unknown_timeout_seconds,
        )
        if state is None:
            self._deadlines.pop(current.tid, None)
        else:
            self._deadlines[current.tid] = state
        if (
            retire
            and is_old_enough(
                current.message_id,
                time.time_ns(),
                self._mapping_min_age_seconds,
            )
            and self._delete_mapping_message(current.message_id)
        ):
            hostname = current.payload.get("hostname")
            logger.info(
                "Retired TID mapping after liveness probe",
                extra={
                    "tid": current.tid,
                    "hostname": (hostname if isinstance(hostname, str) else "unknown"),
                    "reason": (
                        result.observation.reason
                        if result.observation.evidence == "stale"
                        else "unknown_timeout"
                    ),
                    "probe_reason": result.observation.reason,
                    "evidence": result.observation.evidence,
                    "message_id": current.message_id,
                },
            )
            self._latest_rows.pop(current.tid, None)
            self._deadlines.pop(current.tid, None)
            return
        self._push_due(current, due_at=now + LIVENESS_PROBE_INTERVAL_SECONDS)

    def _delete_mapping_message(self, message_id: int) -> bool:
        try:
            return bool(
                self._queue(WEFT_TID_MAPPINGS_QUEUE).delete(message_id=message_id)
            )
        except (BrokerError, OSError, RuntimeError):
            logger.debug(
                "Failed to exact-delete TID mapping %s",
                message_id,
                exc_info=True,
            )
            return False

    def next_wait_timeout(self) -> float:
        """Return the next local liveness scheduling deadline."""

        if self._has_pending_worker_results():
            return 0.0
        now = self._monotonic()
        due = self._due_heap[0][0] - now if self._due_heap else None
        full = self._next_full_reconcile_at - now
        values = [TASK_REACTOR_WAKEUP_MAX_SECONDS, full]
        if due is not None:
            values.append(due)
        return max(0.0, min(values))


__all__ = ["LivenessMonitor", "ProbeResult", "ProbeWork"]
