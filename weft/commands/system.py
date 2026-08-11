"""Status reporting helpers for the Weft CLI.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-1.2.1]
- docs/specifications/01-Core_Components.md [CC-3.2], [CC-3.4]
- docs/specifications/02-TaskSpec.md [TS-1.3]
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/03-Manager_Architecture.md [MA-1.4]
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from simplebroker import Queue, format_message_id
from simplebroker.ext import BrokerError
from weft._constants import (
    BROKER_BACKED_RECONCILIATION_OBSERVATION_CLASSIFICATIONS,
    INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY,
    INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT,
    INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR,
    INTERNAL_SERVICE_KEY_HEARTBEAT,
    INTERNAL_SERVICE_KEY_TASK_MONITOR,
    LIVE_SERVICE_STATUSES,
    NON_LIVE_RUNTIME_STATES,
    SERVICE_STATUS_STOPPED,
    SERVICE_STATUS_SUPERSEDED,
    SERVICE_STATUS_TERMINAL,
    SERVICE_TYPE_MANAGED,
    STATUS_RUNTIMELESS_STALE_AFTER_SECONDS,
    STATUS_WATCH_MIN_INTERVAL,
    TASKSPEC_TID_SHORT_LENGTH,
    TERMINAL_TASK_STATUSES,
    WALL_CLOCK_TASK_LAST_TIMESTAMP_CLASSIFICATIONS,
    WALL_CLOCK_TASK_LAST_TIMESTAMP_EVENTS,
    WEFT_CONTEXT_ENV,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE,
    WEFT_SERVICES_REGISTRY_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
)
from weft.commands.manager import (
    _manager_record_to_json,
    _manager_snapshot,
)
from weft.commands.types import (
    ServiceSnapshot,
    SystemStatusSnapshot,
)
from weft.commands.types import (
    TaskSnapshot as PublicTaskSnapshot,
)
from weft.context import WeftContext, build_context
from weft.core import manager_runtime, task_evidence
from weft.core.queue_wait import QueueChangeMonitor
from weft.core.service_convergence import (
    ServiceOwnerRecord,
    collect_service_owner_records,
    discard_v1_service_registry_rows,
    reduce_latest_by_service_owner,
)
from weft.ext import RunnerHandle
from weft.helpers import (
    closing_queue_iterator,
    format_byte_size,
    format_timestamp_ns_relative,
    handle_has_live_host_process,
    iter_queue_json_entries,
    pid_is_live,
)

from ._task_snapshot_reducer import (
    CollectedTaskSnapshot,
    FoldedTaskRecord,
    RuntimeObservation,
    SnapshotEvidence,
    SnapshotProbePlan,
    TaskSnapshot,
    order_task_snapshots,
    plan_snapshot_probes,
    prepare_snapshot,
    reduce_task_event,
    reduce_task_snapshot,
    runner_name_for_snapshot,
    service_key_from_taskspec,
)

StatusMapping = Mapping[str, int | float | str | None]
_runner_name_for_snapshot = runner_name_for_snapshot
_service_key_from_taskspec_payload = service_key_from_taskspec


def _to_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


@dataclass(frozen=True)
class BrokerStatusSnapshot:
    """Immutable container for broker status metrics."""

    total_messages: int
    last_timestamp: int
    db_size: int

    @classmethod
    def from_mapping(cls, data: StatusMapping) -> BrokerStatusSnapshot:
        return cls(
            total_messages=_to_int(data.get("total_messages")),
            last_timestamp=_to_int(data.get("last_timestamp")),
            db_size=_to_int(data.get("db_size")),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "total_messages": self.total_messages,
            "last_timestamp": self.last_timestamp,
            "db_size": self.db_size,
        }

    def to_text(self) -> str:
        human_size = format_byte_size(self.db_size)
        relative_ts = format_timestamp_ns_relative(self.last_timestamp)

        timestamp_line = f"last_timestamp: {self.last_timestamp}"
        if relative_ts:
            timestamp_line += f" ({relative_ts})"

        size_line = f"db_size: {self.db_size} bytes ({human_size})"

        return "\n".join(
            (
                f"total_messages: {self.total_messages}",
                timestamp_line,
                size_line,
            )
        )


@dataclass(frozen=True, slots=True)
class _ServiceEvidence:
    """One queue-derived observation for a manager-owned service."""

    key: str
    name: str
    status: str
    evidence: str
    rank: int
    tid: str | None = None
    manager_tid: str | None = None
    queue: str | None = None
    pid: int | None = None
    updated_at: int | None = None
    reconciliation: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class _InternalServiceOwnerEvidenceIndex:
    """Latest service-registry evidence keyed for status reconciliation."""

    by_key: dict[str, tuple[_ServiceEvidence, ...]]
    by_owner: dict[tuple[str, str], _ServiceEvidence]

    @classmethod
    def from_evidence(
        cls,
        evidence: Sequence[_ServiceEvidence],
    ) -> _InternalServiceOwnerEvidenceIndex:
        by_key_lists: dict[str, list[_ServiceEvidence]] = {}
        by_owner: dict[tuple[str, str], _ServiceEvidence] = {}
        for item in evidence:
            by_key_lists.setdefault(item.key, []).append(item)
            if item.tid is not None:
                by_owner[(item.key, item.tid)] = item
        return cls(
            by_key={
                key: tuple(sorted(items, key=_service_evidence_sort_key))
                for key, items in by_key_lists.items()
            },
            by_owner=by_owner,
        )

    def live_owner_for_key(self, service_key: str) -> _ServiceEvidence | None:
        """Return the best live owner evidence for one internal service key."""

        candidates = [
            item
            for item in self.by_key.get(service_key, ())
            if item.status in {"running", "launched"}
        ]
        if not candidates:
            return None
        return max(candidates, key=_service_evidence_sort_key)

    def owner_evidence(
        self,
        service_key: str,
        owner_tid: str,
    ) -> _ServiceEvidence | None:
        """Return service-registry evidence for one service owner TID."""

        return self.by_owner.get((service_key, owner_tid))


def _service_evidence_sort_key(candidate: _ServiceEvidence) -> tuple[int, int, str]:
    return (candidate.rank, candidate.updated_at or 0, candidate.tid or "")


def _service_owner_tid_is_newer(
    *,
    owner_tid: str | None,
    candidate_tid: str,
) -> bool:
    """Return whether service-owner evidence comes from a newer task TID."""

    if not isinstance(owner_tid, str) or not owner_tid.isdigit():
        return False
    if not candidate_tid.isdigit():
        return False
    return int(owner_tid) > int(candidate_tid)


def _resolve_context(
    spec_context: str | os.PathLike[str] | None = None,
) -> WeftContext:
    if spec_context:
        return build_context(spec_context=spec_context)

    env_context = os.environ.get(WEFT_CONTEXT_ENV)
    if env_context:
        return build_context(spec_context=env_context)

    return build_context()


def collect_broker_status(ctx: WeftContext) -> BrokerStatusSnapshot:
    with ctx.broker() as db:
        metrics = db.status()
    return BrokerStatusSnapshot.from_mapping(metrics)


def _queue(
    ctx: WeftContext,
    name: str,
    *,
    persistent: bool = False,
) -> Queue:
    return ctx.queue(name, persistent=persistent)


def _collect_manager_records(
    ctx: WeftContext, *, include_stopped: bool = False
) -> list[dict[str, Any]]:
    return manager_runtime.list_manager_records(
        ctx,
        include_stopped=include_stopped,
        canonical_only=False,
    )


def _format_manager_summary(records: list[dict[str, Any]]) -> str:
    if not records:
        return "Managers: none registered"

    lines = ["Managers:"]
    for record in records:
        tid = record.get("tid", "?")
        status = record.get("status", "unknown")
        role = record.get("role", "manager")
        runtime_handle = record.get("runtime_handle")
        requests = record.get("requests", WEFT_SPAWN_REQUESTS_QUEUE)
        internal_requests = record.get("internal_requests")
        internal_reserved = record.get("internal_reserved")
        outbox = record.get("outbox", "")
        timestamp = _to_int(record.get("timestamp"))
        relative_ts = format_timestamp_ns_relative(timestamp)
        ts_line = f"timestamp: {timestamp}"
        if relative_ts:
            ts_line += f" ({relative_ts})"

        queue_lines = [
            f"    requests: {requests}",
        ]
        if isinstance(internal_requests, str) and internal_requests:
            queue_lines.append(f"    internal_requests: {internal_requests}")
        if isinstance(internal_reserved, str) and internal_reserved:
            queue_lines.append(f"    internal_reserved: {internal_reserved}")
        queue_lines.append(f"    outbox: {outbox}")

        lines.extend(
            [
                f"  - tid: {tid}",
                f"    role: {role}",
                f"    status: {status}",
                f"    runtime: {json.dumps(runtime_handle, sort_keys=True) if isinstance(runtime_handle, dict) else 'n/a'}",
                *queue_lines,
                f"    {ts_line}",
            ]
        )

    return "\n".join(lines)


def _read_tid_mappings(ctx: WeftContext) -> dict[str, str]:
    queue = _queue(ctx, WEFT_TID_MAPPINGS_QUEUE)
    try:
        mapping: dict[str, str] = {}
        for payload, _timestamp in iter_queue_json_entries(queue):
            full = payload.get("full")
            short = payload.get("short")
            if isinstance(full, str) and isinstance(short, str):
                mapping[short] = full
        return mapping
    finally:
        queue.close()


def _latest_tid_mapping_entries(ctx: WeftContext) -> dict[str, dict[str, Any]]:
    queue = _queue(ctx, WEFT_TID_MAPPINGS_QUEUE)
    try:
        latest: dict[str, tuple[int, dict[str, Any]]] = {}
        for payload, timestamp in iter_queue_json_entries(queue):
            full = payload.get("full")
            if not isinstance(full, str):
                continue
            previous = latest.get(full)
            if previous is None or previous[0] <= timestamp:
                latest[full] = (timestamp, payload)
        return {full: payload for full, (_timestamp, payload) in latest.items()}
    finally:
        queue.close()


def _resolve_tid_filters(ctx: WeftContext, raw: str | None) -> set[str] | None:
    if raw is None:
        return None

    candidate = raw.strip()
    if not candidate:
        return None

    if candidate.isdigit() and len(candidate) == 19:
        return {candidate, candidate[-TASKSPEC_TID_SHORT_LENGTH:]}

    mapping = _read_tid_mappings(ctx)
    full = mapping.get(candidate)
    if full:
        return {full, candidate}

    # Fall back to treating the input as a bare identifier
    return {candidate}


def _iter_log_events(
    queue: Queue,
    *,
    since_timestamp: int | None = None,
) -> Iterable[tuple[dict[str, Any], int]]:
    """Replay all state-change events from the global log queue.

    Spec: [MF-5]
    """
    try:
        iterator_raw = queue.peek_generator(
            with_timestamps=True,
            after_timestamp=since_timestamp,
        )
    except (
        BrokerError,
        OSError,
        RuntimeError,
    ):  # pragma: no cover - log replay best effort
        return []

    def _generator() -> Iterable[tuple[dict[str, Any], int]]:
        with closing_queue_iterator(cast(Iterable[Any], iterator_raw)) as rows:
            for entry_raw in rows:
                if isinstance(entry_raw, tuple):
                    if len(entry_raw) != 2:
                        continue
                    body_candidate, timestamp = entry_raw
                    if not isinstance(body_candidate, str):
                        continue
                    body_str = body_candidate
                elif isinstance(entry_raw, str):
                    body_str = entry_raw
                    timestamp = 0
                else:
                    continue
                try:
                    payload = cast(dict[str, Any], json.loads(body_str))
                except (TypeError, json.JSONDecodeError):
                    continue
                yield payload, int(timestamp)

    return _generator()


def _format_timestamp(ts: int | None) -> str:
    if not ts:
        return "-"
    dt = datetime.fromtimestamp(ts / 1_000_000_000, tz=UTC)
    return dt.isoformat().replace("+00:00", "Z")


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    if seconds < 1:
        return f"{seconds:.3f}s"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(seconds, 60.0)
    if minutes < 60:
        return f"{int(minutes)}m{secs:04.1f}s"
    hours, minutes = divmod(minutes, 60.0)
    return f"{int(hours)}h{int(minutes):02}m"


def _runtime_handle_from_mapping(entry: Mapping[str, Any]) -> RunnerHandle | None:
    payload = entry.get("runtime_handle")
    if not isinstance(payload, Mapping):
        return None
    try:
        return RunnerHandle.from_dict(payload)
    except ValueError:
        return None


def _merge_runtime_entry(
    mapping_entry: Mapping[str, Any] | None,
    event_payload: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    """Combine runtime metadata from the mapping queue and the log payload."""

    merged: dict[str, Any] = {}
    if isinstance(event_payload, Mapping):
        merged.update(event_payload)
    if mapping_entry is not None:
        merged.update(mapping_entry)
    return merged or None


def _task_process_alive(mapping_entry: Mapping[str, Any] | None) -> bool:
    handle = _runtime_handle_from_mapping(mapping_entry or {})
    if handle is None or handle.control.get("authority") != "host-pid":
        return False
    return handle_has_live_host_process(handle)


def _task_process_id(mapping_entry: Mapping[str, Any] | None) -> int | None:
    handle = _runtime_handle_from_mapping(mapping_entry or {})
    if handle is None or handle.control.get("authority") != "host-pid":
        return None
    host_pids = handle.scoped_host_pids()
    return host_pids[0] if host_pids else None


def _runtime_description_is_live(
    runtime_description: Mapping[str, Any] | None,
) -> bool:
    if runtime_description is None:
        return False
    state = runtime_description.get("state")
    if not isinstance(state, str):
        return False
    normalized = state.strip().lower()
    if not normalized:
        return False
    return normalized not in NON_LIVE_RUNTIME_STATES


def _runtime_evidence_details(
    *,
    handle: RunnerHandle | None,
    runtime_description: Mapping[str, Any] | None,
) -> tuple[bool, str, str]:
    """Return live/evidence/strength details for reconciliation diagnostics."""

    if handle is None:
        return False, "none", "unknown"

    authority = handle.control.get("authority")
    if authority == "host-pid":
        live = handle_has_live_host_process(handle)
        has_identity = any(
            create_time is not None
            for _pid, create_time in handle.scoped_host_processes()
        )
        return live, "host-pid", "strong" if has_identity else "weak"
    if authority == "runner":
        return _runtime_description_is_live(runtime_description), "runner", "strong"
    if authority == "external-supervisor":
        return (
            _runtime_description_is_live(runtime_description),
            "external-supervisor",
            "unknown",
        )
    return _runtime_description_is_live(runtime_description), "none", "unknown"


def _is_internal_service_record(record: Mapping[str, Any]) -> bool:
    """Return whether a task-log record describes manager-owned service work."""

    metadata = record.get("metadata")
    if not isinstance(metadata, Mapping):
        return False
    if metadata.get("internal") is True:
        return True
    role = metadata.get("role")
    if role in {"task_monitor", "heartbeat_service"}:
        return True
    service_key = metadata.get("_weft_service_key")
    return isinstance(service_key, str) and service_key.startswith("_weft.service.")


def _service_display_name(key: str) -> str:
    if key == INTERNAL_SERVICE_KEY_HEARTBEAT:
        return "heartbeat-service"
    if key == INTERNAL_SERVICE_KEY_TASK_MONITOR:
        return "task-monitor"
    return key.rsplit(".", 1)[-1] or key


def _known_internal_service_keys() -> tuple[str, str]:
    return (INTERNAL_SERVICE_KEY_HEARTBEAT, INTERNAL_SERVICE_KEY_TASK_MONITOR)


def _service_key_from_spawn_payload(payload: Mapping[str, Any]) -> str | None:
    taskspec_payload = payload.get("taskspec")
    if isinstance(taskspec_payload, Mapping):
        key = _service_key_from_taskspec_payload(taskspec_payload)
        if key is not None:
            return key

    runtime_class = payload.get(INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY)
    if runtime_class == INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT:
        return INTERNAL_SERVICE_KEY_HEARTBEAT
    if runtime_class == INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR:
        return INTERNAL_SERVICE_KEY_TASK_MONITOR
    return None


def _iter_queue_json_messages(queue: Queue) -> Iterable[tuple[dict[str, Any], int]]:
    iterator_raw = queue.peek_generator(with_timestamps=True)
    with closing_queue_iterator(cast(Iterable[Any], iterator_raw)) as rows:
        for item in rows:
            if isinstance(item, tuple) and len(item) == 2:
                raw, timestamp = item
            else:
                raw, timestamp = item, 0
            if not isinstance(raw, str):
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                yield payload, int(timestamp)


def _service_runtime_liveness(
    runtime_handle: Mapping[str, Any] | None,
    runtime_description: Mapping[str, Any] | None = None,
) -> tuple[bool, int | None]:
    """Return live-runtime proof and the first live host PID when available."""

    handle = (
        _runtime_handle_from_mapping({"runtime_handle": runtime_handle})
        if isinstance(runtime_handle, Mapping)
        else None
    )
    if handle is None:
        return _runtime_description_is_live(runtime_description), None
    description = (
        runtime_description
        if isinstance(runtime_description, Mapping)
        else task_evidence.describe_runtime(handle)
    )
    live, _evidence, _strength = _runtime_evidence_details(
        handle=handle,
        runtime_description=description,
    )
    pid = None
    if live and handle.control.get("authority") == "host-pid":
        host_pids = handle.scoped_host_pids()
        pid = host_pids[0] if host_pids else None
    return live, pid


def _service_observation_is_stale(*, updated_at: int | None, now_ns: int) -> bool:
    if not isinstance(updated_at, int) or updated_at <= 0:
        return False
    stale_after_ns = int(STATUS_RUNTIMELESS_STALE_AFTER_SECONDS * 1_000_000_000)
    return now_ns - updated_at > stale_after_ns


def _stale_liveness_reason(  # noqa: C901 approved [TS-3.1] [RUFF-SUP-117] exception
    status: str,
    *,
    tid: str,
    runner_name: str | None,
    mapping_entry: Mapping[str, Any] | None,
    runtime_description: Mapping[str, Any] | None,
    last_timestamp: int,
    now_ns: int,
    has_live_manager_record: bool = False,
    internal_service: bool = False,
    internal_service_key: str | None = None,
    service_owner_index: _InternalServiceOwnerEvidenceIndex | None = None,
) -> str | None:
    """Return why nonterminal liveness evidence needs read-model reconciliation."""

    normalized_runner = (
        runner_name.strip().lower() if isinstance(runner_name, str) else ""
    )
    host_task_pid = _task_process_id(mapping_entry)
    stale_without_runtime = (
        status in {"spawning", "running"}
        and not has_live_manager_record
        and host_task_pid is None
        and runtime_description is None
        and last_timestamp > 0
        and now_ns - last_timestamp
        > int(STATUS_RUNTIMELESS_STALE_AFTER_SECONDS * 1_000_000_000)
    )

    if status in TERMINAL_TASK_STATUSES:
        return None
    if internal_service and status in {"spawning", "running"}:
        host_runtime_absent = host_task_pid is None and runtime_description is None
        host_runtime_not_live = (
            host_task_pid is not None
            and not _task_process_alive(mapping_entry)
            and not _runtime_description_is_live(runtime_description)
        )
        runtime_proof_missing = stale_without_runtime or host_runtime_not_live
        if internal_service_key is None:
            return None
        live_owner = (
            service_owner_index.live_owner_for_key(internal_service_key)
            if service_owner_index is not None
            else None
        )
        if (
            live_owner is not None
            and live_owner.tid != tid
            and _service_owner_tid_is_newer(
                owner_tid=live_owner.tid,
                candidate_tid=tid,
            )
            and (host_runtime_absent or host_runtime_not_live)
        ):
            return "superseded_internal_service_record"
        if not runtime_proof_missing:
            return None
        same_owner = (
            service_owner_index.owner_evidence(internal_service_key, tid)
            if service_owner_index is not None
            else None
        )
        if same_owner is not None and same_owner.status in {"running", "launched"}:
            return None
        if live_owner is not None and live_owner.tid != tid:
            return "superseded_internal_service_record"
        if host_runtime_not_live and not stale_without_runtime:
            return "host_process_not_live"
        return "internal_service_runtime_missing_after_stale_window"
    if (
        status in {"spawning", "running"}
        and (not normalized_runner or normalized_runner == "host")
        and host_task_pid is not None
        and not _task_process_alive(mapping_entry)
    ):
        return "host_process_not_live"
    if (
        status in {"spawning", "running"}
        and (not normalized_runner or normalized_runner == "host")
        and stale_without_runtime
    ):
        return "runtime_missing_after_stale_window"
    return None


def _collect_snapshot_evidence(
    ctx: WeftContext,
    record: FoldedTaskRecord,
    *,
    mapping_entry: Mapping[str, Any] | None,
    selected_active_manager_tid: str | None,
    service_owner_index: _InternalServiceOwnerEvidenceIndex,
    now_ns: int,
) -> tuple[SnapshotProbePlan, SnapshotEvidence]:
    """Acquire only the runtime and queue observations requested by policy."""

    taskspec = record.taskspec_payload
    if taskspec is None:
        raise ValueError("snapshot evidence requires a TaskSpec-bearing record")
    runtime_entry = _merge_runtime_entry(mapping_entry, record.event_payload)
    runtime_handle = _runtime_handle_from_mapping(runtime_entry or {})
    runner = runner_name_for_snapshot(
        taskspec=taskspec,
        mapping_entry=runtime_entry,
    )
    runtime_description = task_evidence.describe_runtime(runtime_handle)

    local_evidence: task_evidence.TaskEvidenceSnapshot | None = None
    if record.status not in TERMINAL_TASK_STATUSES:
        local_evidence = task_evidence.task_local_terminal_evidence(
            ctx,
            tid=record.tid,
            taskspec_payload=taskspec,
        )
    draft = prepare_snapshot(record, local_evidence=local_evidence)

    stale_liveness_reason = None
    active_service_tid = None
    if local_evidence is None or not local_evidence.terminal:
        internal_service_key = _service_key_from_taskspec_payload(taskspec)
        internal_service = (
            internal_service_key is not None
            or _is_internal_service_record({"metadata": record.metadata})
        )
        stale_liveness_reason = _stale_liveness_reason(
            record.status,
            tid=record.tid,
            runner_name=runner,
            mapping_entry=runtime_entry,
            runtime_description=runtime_description,
            last_timestamp=record.last_timestamp,
            now_ns=now_ns,
            has_live_manager_record=record.tid == selected_active_manager_tid,
            internal_service=internal_service,
            internal_service_key=internal_service_key,
            service_owner_index=service_owner_index,
        )
        active_service = (
            service_owner_index.live_owner_for_key(internal_service_key)
            if internal_service_key is not None
            else None
        )
        active_service_tid = active_service.tid if active_service is not None else None
    probe_plan = plan_snapshot_probes(
        draft,
        stale_liveness_reason=stale_liveness_reason,
    )

    runtime_observation = None
    if probe_plan.acquire_runtime_observation:
        live, source, strength = _runtime_evidence_details(
            handle=runtime_handle,
            runtime_description=runtime_description,
        )
        runtime_observation = RuntimeObservation(
            live=live,
            evidence=source,
            strength=strength,
        )

    claimed_outbox = None
    if probe_plan.acquire_claimed_outbox:
        outbox_name, _ctrl_out_name = task_evidence.queue_names_for_tid(
            record.tid,
            taskspec,
        )
        claimed_outbox = task_evidence.claimed_outbox_result_evidence(
            ctx,
            tid=record.tid,
            outbox_name=outbox_name,
            taskspec_payload=taskspec,
        )

    return probe_plan, SnapshotEvidence(
        resolved_runtime_entry=runtime_entry,
        runtime_handle=runtime_handle,
        runtime_description=runtime_description,
        runtime_observation=runtime_observation,
        claimed_outbox=claimed_outbox,
        active_service_tid=active_service_tid,
        selected_active_manager_tid=selected_active_manager_tid,
    )


def _collect_task_snapshot_records(
    ctx: WeftContext,
    *,
    include_terminal: bool,
    tid_filters: set[str] | None,
    since_timestamp: int | None = None,
    now_ns: int | None = None,
    service_registry_evidence: Sequence[_ServiceEvidence] | None = None,
) -> list[CollectedTaskSnapshot]:
    """Reconstruct current task state from event-sourced log replay.

    Spec: [MF-5]
    """
    if now_ns is None:
        now_ns = time.time_ns()
    registry_evidence = (
        tuple(service_registry_evidence)
        if service_registry_evidence is not None
        else tuple(_collect_service_registry_evidence(ctx, now_ns=now_ns))
    )
    service_owner_index = _InternalServiceOwnerEvidenceIndex.from_evidence(
        registry_evidence
    )
    records: dict[str, FoldedTaskRecord] = {}
    tid_mapping_entries = _latest_tid_mapping_entries(ctx)
    try:
        selected_manager = manager_runtime.select_active_manager(ctx)
        selected_active_manager_tid = (
            str(selected_manager["tid"])
            if isinstance(selected_manager, Mapping)
            and isinstance(selected_manager.get("tid"), str)
            and selected_manager.get("status") == "active"
            else None
        )
    except Exception:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-335] exception
        selected_active_manager_tid = None
    log_queue = _queue(ctx, WEFT_GLOBAL_LOG_QUEUE)
    try:
        for payload, timestamp in _iter_log_events(
            log_queue,
            since_timestamp=since_timestamp,
        ):
            tid = payload.get("tid")
            if not isinstance(tid, str):
                continue
            reduced = reduce_task_event(
                records.get(tid),
                payload,
                timestamp,
                tid_filters=tid_filters,
            )
            if reduced is not None:
                records[tid] = reduced
    finally:
        log_queue.close()

    records_out: list[CollectedTaskSnapshot] = []
    for tid, record in records.items():
        if record.taskspec_payload is None:
            continue
        probe_plan, evidence = _collect_snapshot_evidence(
            ctx,
            record,
            mapping_entry=tid_mapping_entries.get(tid),
            selected_active_manager_tid=selected_active_manager_tid,
            service_owner_index=service_owner_index,
            now_ns=now_ns,
        )
        snapshot = reduce_task_snapshot(
            probe_plan,
            evidence,
            now_ns=now_ns,
        )
        if snapshot is not None:
            records_out.append(snapshot)
    return order_task_snapshots(records_out, include_terminal=include_terminal)


def _service_evidence_from_child_task(
    record: CollectedTaskSnapshot,
    *,
    now_ns: int,
) -> _ServiceEvidence | None:
    if record.taskspec_payload is None:
        return None
    key = _service_key_from_taskspec_payload(record.taskspec_payload)
    if key is None:
        return None
    snapshot = record.snapshot
    if snapshot.status in TERMINAL_TASK_STATUSES:
        return _ServiceEvidence(
            key=key,
            name=snapshot.name or _service_display_name(key),
            status="terminal",
            evidence="child-task-log",
            rank=100,
            tid=snapshot.tid,
            updated_at=snapshot.last_timestamp,
            reconciliation={"lifecycle_status": snapshot.status},
        )
    runtime_live, pid = _service_runtime_liveness(
        snapshot.runtime_handle,
        snapshot.runtime,
    )
    if not runtime_live and _service_observation_is_stale(
        updated_at=snapshot.last_timestamp,
        now_ns=now_ns,
    ):
        return _ServiceEvidence(
            key=key,
            name=snapshot.name or _service_display_name(key),
            status="uncertain",
            evidence="child-task-log",
            rank=20,
            tid=snapshot.tid,
            pid=pid,
            updated_at=snapshot.last_timestamp,
            reconciliation={
                "classification": "service_liveness_uncertain",
                "reason": "child_task_log_without_live_runtime",
                "lifecycle_status": snapshot.status,
            },
        )
    return _ServiceEvidence(
        key=key,
        name=snapshot.name or _service_display_name(key),
        status="running",
        evidence="child-task-log",
        rank=90,
        tid=snapshot.tid,
        pid=pid,
        updated_at=snapshot.last_timestamp,
        reconciliation=snapshot.reconciliation,
    )


def _service_evidence_from_manager_spawned(
    payload: Mapping[str, Any],
    timestamp: int,
) -> _ServiceEvidence | None:
    if payload.get("event") != "task_spawned":
        return None
    child_tid = payload.get("child_tid")
    child_taskspec = payload.get("child_taskspec")
    if not isinstance(child_tid, str) or not isinstance(child_taskspec, Mapping):
        return None
    key = _service_key_from_taskspec_payload(child_taskspec)
    if key is None:
        payload_key = payload.get("service_key")
        if (
            isinstance(payload_key, str)
            and payload_key in _known_internal_service_keys()
        ):
            key = payload_key
        else:
            return None

    child_pid = payload.get("child_pid")
    pid = (
        child_pid
        if isinstance(child_pid, int) and not isinstance(child_pid, bool)
        else None
    )
    pid_live = pid is not None and pid_is_live(pid)
    manager_tid = payload.get("tid")
    return _ServiceEvidence(
        key=key,
        name=str(child_taskspec.get("name") or _service_display_name(key)),
        status="launched" if pid_live else "uncertain",
        evidence="manager-task-spawned",
        rank=80 if pid_live else 50,
        tid=child_tid,
        manager_tid=manager_tid if isinstance(manager_tid, str) else None,
        pid=pid,
        updated_at=timestamp,
        reconciliation=None
        if pid_live
        else {
            "classification": "service_liveness_uncertain",
            "reason": "manager_spawned_pid_not_live",
        },
    )


def _service_evidence_from_service_owner_record(
    record: ServiceOwnerRecord,
    *,
    now_ns: int,
) -> _ServiceEvidence | None:
    if (
        record.service_type != SERVICE_TYPE_MANAGED
        or record.service_key not in _known_internal_service_keys()
    ):
        return None

    payload = record.payload
    raw_name = payload.get("name")
    name = raw_name if isinstance(raw_name, str) and raw_name else None
    metadata = payload.get("metadata")
    manager_tid = metadata.get("manager_tid") if isinstance(metadata, Mapping) else None
    runtime_handle = payload.get("runtime_handle")
    runtime_live, pid = _service_runtime_liveness(
        runtime_handle if isinstance(runtime_handle, Mapping) else None
    )

    if record.status == SERVICE_STATUS_TERMINAL:
        return _ServiceEvidence(
            key=record.service_key,
            name=name or _service_display_name(record.service_key),
            status="terminal",
            evidence="service-registry",
            rank=100,
            tid=record.owner_tid,
            manager_tid=manager_tid if isinstance(manager_tid, str) else None,
            pid=pid,
            updated_at=record.timestamp,
            reconciliation={"lifecycle_status": "terminal"},
        )

    if record.status in LIVE_SERVICE_STATUSES:
        if runtime_live or not _service_observation_is_stale(
            updated_at=record.timestamp,
            now_ns=now_ns,
        ):
            return _ServiceEvidence(
                key=record.service_key,
                name=name or _service_display_name(record.service_key),
                status="running",
                evidence="service-registry",
                rank=95 if runtime_live else 85,
                tid=record.owner_tid,
                manager_tid=manager_tid if isinstance(manager_tid, str) else None,
                pid=pid,
                updated_at=record.timestamp,
            )
        return _ServiceEvidence(
            key=record.service_key,
            name=name or _service_display_name(record.service_key),
            status="uncertain",
            evidence="service-registry",
            rank=45,
            tid=record.owner_tid,
            manager_tid=manager_tid if isinstance(manager_tid, str) else None,
            updated_at=record.timestamp,
            reconciliation={
                "classification": "service_liveness_uncertain",
                "reason": "service_registry_runtime_not_live",
                "lifecycle_status": record.status,
            },
        )

    terminal_like = {SERVICE_STATUS_STOPPED, SERVICE_STATUS_SUPERSEDED}
    return _ServiceEvidence(
        key=record.service_key,
        name=name or _service_display_name(record.service_key),
        status="terminal" if record.status in terminal_like else "uncertain",
        evidence="service-registry",
        rank=100 if record.status in terminal_like else 45,
        tid=record.owner_tid,
        manager_tid=manager_tid if isinstance(manager_tid, str) else None,
        pid=pid,
        updated_at=record.timestamp,
        reconciliation={"lifecycle_status": record.status},
    )


def _collect_service_registry_evidence(
    ctx: WeftContext,
    *,
    now_ns: int,
) -> list[_ServiceEvidence]:
    queue = _queue(ctx, WEFT_SERVICES_REGISTRY_QUEUE)
    try:
        discard_v1_service_registry_rows(queue)
    except (BrokerError, OSError, RuntimeError, ValueError):
        queue.close()
        raise
    try:
        read = collect_service_owner_records(
            iter_queue_json_entries(queue),
            service_type=SERVICE_TYPE_MANAGED,
        )
        return [
            candidate
            for record in reduce_latest_by_service_owner(read.records)
            if (
                candidate := _service_evidence_from_service_owner_record(
                    record,
                    now_ns=now_ns,
                )
            )
            is not None
        ]
    except (BrokerError, OSError, RuntimeError):
        return []
    finally:
        queue.close()


def _service_evidence_from_spawn_payload(
    payload: Mapping[str, Any],
    *,
    timestamp: int,
    queue_name: str,
    status: str,
    evidence: str,
    rank: int,
) -> _ServiceEvidence | None:
    key = _service_key_from_spawn_payload(payload)
    if key is None:
        return None
    taskspec_payload = payload.get("taskspec")
    raw_name = (
        taskspec_payload.get("name") if isinstance(taskspec_payload, Mapping) else None
    )
    name = raw_name if isinstance(raw_name, str) else _service_display_name(key)
    tid = payload.get("tid")
    return _ServiceEvidence(
        key=key,
        name=name,
        status=status,
        evidence=evidence,
        rank=rank,
        tid=tid if isinstance(tid, str) else str(timestamp) if timestamp else None,
        queue=queue_name,
        updated_at=timestamp,
    )


def _collect_internal_spawn_queue_evidence(
    ctx: WeftContext,
    *,
    queue_name: str,
    status: str,
    evidence: str,
    rank: int,
) -> list[_ServiceEvidence]:
    queue = _queue(ctx, queue_name)
    try:
        return [
            candidate
            for payload, timestamp in _iter_queue_json_messages(queue)
            if (
                candidate := _service_evidence_from_spawn_payload(
                    payload,
                    timestamp=timestamp,
                    queue_name=queue_name,
                    status=status,
                    evidence=evidence,
                    rank=rank,
                )
            )
            is not None
        ]
    except (BrokerError, OSError, RuntimeError):
        return []
    finally:
        queue.close()


def _service_enabled(ctx: WeftContext, key: str) -> bool:
    task_monitor_enabled = bool(ctx.config.get("WEFT_TASK_MONITOR_ENABLED", True))
    if key == INTERNAL_SERVICE_KEY_TASK_MONITOR:
        return task_monitor_enabled
    if key == INTERNAL_SERVICE_KEY_HEARTBEAT:
        return task_monitor_enabled
    return False


def _active_canonical_manager_records(
    managers: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    return [
        manager
        for manager in managers
        if manager.get("status") == "active"
        and manager.get("requests", WEFT_SPAWN_REQUESTS_QUEUE)
        == WEFT_SPAWN_REQUESTS_QUEUE
    ]


def _best_service_evidence(
    candidates: Sequence[_ServiceEvidence],
) -> _ServiceEvidence | None:
    if not candidates:
        return None
    terminal_tids = {
        candidate.tid
        for candidate in candidates
        if candidate.status == "terminal" and candidate.tid is not None
    }
    eligible = [
        candidate
        for candidate in candidates
        if not (
            candidate.tid in terminal_tids
            and candidate.status != "terminal"
            and candidate.tid is not None
        )
    ]
    live_candidates = [
        candidate
        for candidate in eligible
        if candidate.status in {"running", "launched"}
    ]
    if live_candidates:
        return max(live_candidates, key=_service_evidence_sort_key)
    return max(eligible, key=_service_evidence_sort_key)


def _service_snapshot_from_evidence(
    *,
    ctx: WeftContext,
    key: str,
    desired: bool,
    evidence: _ServiceEvidence | None,
    diagnostics: Mapping[str, Any] | None = None,
) -> ServiceSnapshot:
    enabled = _service_enabled(ctx, key)
    if not enabled:
        return ServiceSnapshot(
            key=key,
            name=_service_display_name(key),
            desired=False,
            enabled=False,
            status="disabled",
            evidence="config-disabled",
            diagnostics=dict(diagnostics) if diagnostics is not None else None,
        )
    if evidence is None:
        return ServiceSnapshot(
            key=key,
            name=_service_display_name(key),
            desired=desired,
            enabled=True,
            status="unknown",
            evidence="none",
            diagnostics=dict(diagnostics) if diagnostics is not None else None,
        )
    return ServiceSnapshot(
        key=key,
        name=evidence.name,
        desired=desired,
        enabled=True,
        status=evidence.status,
        evidence=evidence.evidence,
        tid=evidence.tid,
        manager_tid=evidence.manager_tid,
        queue=evidence.queue,
        pid=evidence.pid,
        updated_at=evidence.updated_at,
        reconciliation=evidence.reconciliation,
        diagnostics=dict(diagnostics) if diagnostics is not None else None,
    )


def _service_diagnostics_from_mapping(
    *,
    key: str,
    evidence: _ServiceEvidence | None,
    tid_mapping_entries: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any] | None:
    if key != INTERNAL_SERVICE_KEY_TASK_MONITOR or evidence is None:
        return None
    if evidence.tid is None:
        return None
    mapping = tid_mapping_entries.get(evidence.tid)
    if not isinstance(mapping, Mapping):
        return None
    task_monitor = mapping.get("task_monitor")
    if not isinstance(task_monitor, Mapping):
        return None
    return {"task_monitor": dict(task_monitor)}


def _collect_internal_service_snapshots(  # noqa: C901 approved [TS-3.1] [RUFF-SUP-118] exception
    ctx: WeftContext,
    *,
    managers: Sequence[Mapping[str, Any]],
    task_records: Sequence[CollectedTaskSnapshot],
    now_ns: int | None = None,
    service_registry_evidence: Sequence[_ServiceEvidence] | None = None,
) -> list[ServiceSnapshot]:
    """Return queue-derived status for manager-owned internal services."""

    if now_ns is None:
        now_ns = time.time_ns()
    candidates_by_key: dict[str, list[_ServiceEvidence]] = {
        key: [] for key in _known_internal_service_keys()
    }
    for record in task_records:
        candidate = _service_evidence_from_child_task(record, now_ns=now_ns)
        if candidate is not None:
            candidates_by_key.setdefault(candidate.key, []).append(candidate)

    log_queue = _queue(ctx, WEFT_GLOBAL_LOG_QUEUE)
    try:
        for payload, timestamp in _iter_log_events(log_queue):
            candidate = _service_evidence_from_manager_spawned(payload, timestamp)
            if candidate is not None:
                candidates_by_key.setdefault(candidate.key, []).append(candidate)
    finally:
        log_queue.close()

    registry_evidence = (
        tuple(service_registry_evidence)
        if service_registry_evidence is not None
        else tuple(_collect_service_registry_evidence(ctx, now_ns=now_ns))
    )
    for candidate in registry_evidence:
        candidates_by_key.setdefault(candidate.key, []).append(candidate)

    for candidate in _collect_internal_spawn_queue_evidence(
        ctx,
        queue_name=WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE,
        status="pending",
        evidence="internal-spawn-pending",
        rank=30,
    ):
        candidates_by_key.setdefault(candidate.key, []).append(candidate)

    for manager in managers:
        reserved_queue = manager.get("internal_reserved")
        if not isinstance(reserved_queue, str) or not reserved_queue:
            continue
        for candidate in _collect_internal_spawn_queue_evidence(
            ctx,
            queue_name=reserved_queue,
            status="reserved",
            evidence="internal-spawn-reserved",
            rank=40,
        ):
            manager_tid = manager.get("tid")
            candidates_by_key.setdefault(candidate.key, []).append(
                _ServiceEvidence(
                    key=candidate.key,
                    name=candidate.name,
                    status=candidate.status,
                    evidence=candidate.evidence,
                    rank=candidate.rank,
                    tid=candidate.tid,
                    manager_tid=manager_tid if isinstance(manager_tid, str) else None,
                    queue=candidate.queue,
                    pid=candidate.pid,
                    updated_at=candidate.updated_at,
                    reconciliation=candidate.reconciliation,
                )
            )

    active_managers = _active_canonical_manager_records(managers)
    desired = bool(active_managers)
    tid_mapping_entries = _latest_tid_mapping_entries(ctx)
    snapshots: list[ServiceSnapshot] = []
    for key in _known_internal_service_keys():
        evidence = _best_service_evidence(candidates_by_key.get(key, ()))
        snapshots.append(
            _service_snapshot_from_evidence(
                ctx=ctx,
                key=key,
                desired=desired,
                evidence=evidence,
                diagnostics=_service_diagnostics_from_mapping(
                    key=key,
                    evidence=evidence,
                    tid_mapping_entries=tid_mapping_entries,
                ),
            )
        )
    return snapshots


def _collect_task_snapshots(
    ctx: WeftContext,
    *,
    include_terminal: bool,
    tid_filters: set[str] | None,
) -> list[TaskSnapshot]:
    """Reconstruct current task state from one event-sourced log replay.

    Spec: [MF-5]
    """

    return [
        record.snapshot
        for record in _collect_task_snapshot_records(
            ctx,
            include_terminal=include_terminal,
            tid_filters=tid_filters,
        )
    ]


def collect_known_tid_snapshot(
    ctx: WeftContext,
    tid: str,
    *,
    include_terminal: bool = True,
) -> TaskSnapshot | None:
    """Return one full-TID diagnostic snapshot using bounded task-log replay."""

    if not tid.isdigit() or len(tid) != 19:
        return None
    records = _collect_task_snapshot_records(
        ctx,
        include_terminal=include_terminal,
        tid_filters={tid, tid[-TASKSPEC_TID_SHORT_LENGTH:]},
        since_timestamp=int(tid) - 1,
    )
    if not records and int(tid) > time.time_ns():
        records = _collect_task_snapshot_records(
            ctx,
            include_terminal=include_terminal,
            tid_filters={tid, tid[-TASKSPEC_TID_SHORT_LENGTH:]},
        )
    return records[0].snapshot if records else None


def _format_task_summary(snapshots: Sequence[TaskSnapshot]) -> str:
    if not snapshots:
        return "Tasks: none"

    headers = (
        "TID",
        "STATUS",
        "ACTIVITY",
        "RUNNER",
        "NAME",
        "STARTED",
        "DURATION",
        "EVENT",
    )
    lines = [
        "Tasks:",
        "  {:<19} {:<10} {:<12} {:<14} {:<20} {:<20} {:<10} {}".format(*headers),
    ]
    for snap in snapshots:
        lines.append(
            f"  {snap.tid:<19} {snap.status:<10} {(snap.activity or '-'): <12} {(snap.runner or '-'):<14} {snap.name[:20]:<20} {_format_timestamp(snap.started_at):<20} {_format_duration(snap.duration_seconds):<10} {snap.event}"
        )
    return "\n".join(lines)


def _format_service_summary(snapshots: Sequence[ServiceSnapshot]) -> str:
    if not snapshots:
        return "Services: none"

    lines = ["Services:"]
    for snap in snapshots:
        parts = [f"  {snap.name:<18}", f"{snap.status:<10}"]
        if snap.tid is not None:
            parts.append(f"tid={snap.tid}")
        parts.append(f"evidence={snap.evidence}")
        diagnostics = snap.diagnostics or {}
        task_monitor = diagnostics.get("task_monitor")
        if isinstance(task_monitor, Mapping):
            external = task_monitor.get("task_log_external")
            if isinstance(external, Mapping):
                if external.get("healthy") is False:
                    parts.append("warning=external-log-unhealthy")
                pending = external.get("deferred_pending")
                if isinstance(pending, int) and pending > 0:
                    parts.append("warning=deferred-writes-pending")
                    parts.append(f"deferred_writes={pending}")
        if snap.queue is not None:
            parts.append(f"queue={snap.queue}")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def _service_snapshot_to_dict(snapshot: ServiceSnapshot) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "key": snapshot.key,
        "name": snapshot.name,
        "desired": snapshot.desired,
        "enabled": snapshot.enabled,
        "status": snapshot.status,
        "evidence": snapshot.evidence,
        "tid": snapshot.tid,
        "manager_tid": snapshot.manager_tid,
        "queue": snapshot.queue,
        "pid": snapshot.pid,
        "updated_at": (
            format_message_id(snapshot.updated_at)
            if snapshot.updated_at is not None
            else None
        ),
    }
    if snapshot.reconciliation is not None:
        payload["reconciliation"] = snapshot.reconciliation
    if snapshot.diagnostics is not None:
        payload["diagnostics"] = snapshot.diagnostics
    return payload


def _task_snapshot_to_json_dict(snapshot: TaskSnapshot) -> dict[str, Any]:
    """Project broker-backed task identity fields for external JSON."""

    payload = snapshot.to_dict()
    reconciliation = payload.get("reconciliation")
    classification = (
        reconciliation.get("classification")
        if isinstance(reconciliation, dict)
        else None
    )
    last_timestamp_is_broker_backed = (
        classification not in WALL_CLOCK_TASK_LAST_TIMESTAMP_CLASSIFICATIONS
        and snapshot.event not in WALL_CLOCK_TASK_LAST_TIMESTAMP_EVENTS
    )
    last_timestamp = payload.get("last_timestamp")
    if (
        last_timestamp_is_broker_backed
        and isinstance(last_timestamp, int)
        and not isinstance(last_timestamp, bool)
        and last_timestamp > 0
    ):
        payload["last_timestamp"] = format_message_id(last_timestamp)

    if (
        classification in BROKER_BACKED_RECONCILIATION_OBSERVATION_CLASSIFICATIONS
        and isinstance(reconciliation, dict)
    ):
        projected_reconciliation = dict(reconciliation)
        observed_at = projected_reconciliation.get("observed_at")
        if isinstance(observed_at, int) and not isinstance(observed_at, bool):
            projected_reconciliation["observed_at"] = format_message_id(observed_at)
        payload["reconciliation"] = projected_reconciliation
    return payload


def _render_json_payload(
    broker: BrokerStatusSnapshot,
    managers: list[dict[str, Any]],
    services: Sequence[ServiceSnapshot],
    tasks: Sequence[TaskSnapshot],
) -> str:
    broker_payload: dict[str, Any] = broker.to_dict()
    if broker.last_timestamp is not None:
        broker_payload["last_timestamp"] = format_message_id(broker.last_timestamp)
    payload = {
        "broker": broker_payload,
        "managers": [_manager_record_to_json(record) for record in managers],
        "services": [_service_snapshot_to_dict(snap) for snap in services],
        "tasks": [_task_snapshot_to_json_dict(snap) for snap in tasks],
    }
    return json.dumps(payload, ensure_ascii=False)


def _watch_task_events(  # noqa: C901 approved [TS-3.1] [RUFF-SUP-119] exception
    ctx: WeftContext,
    *,
    tid_filters: set[str] | None,
    status_filter: str | None,
    json_output: bool,
    interval: float,
) -> int:
    """Tail the global log queue for live state-change events.

    Spec: [MF-5]
    """
    last_timestamp = 0
    queue = _queue(ctx, WEFT_GLOBAL_LOG_QUEUE)
    monitor: QueueChangeMonitor | None = None
    try:
        monitor = QueueChangeMonitor([queue], config=ctx.config)
        while True:
            emitted = False
            for payload, timestamp in _iter_log_events(
                queue,
                since_timestamp=last_timestamp,
            ):
                if timestamp <= last_timestamp:
                    continue
                tid = payload.get("tid")
                if not isinstance(tid, str):
                    continue
                short_tid = tid[-TASKSPEC_TID_SHORT_LENGTH:]
                if (
                    tid_filters is not None
                    and tid not in tid_filters
                    and short_tid not in tid_filters
                ):
                    continue

                taskspec = payload.get("taskspec") or {}
                name = taskspec.get("name") or payload.get("name") or tid
                status = payload.get("status") or taskspec.get("state", {}).get(
                    "status"
                )
                if status_filter and status != status_filter:
                    continue
                event = payload.get("event") or "event"
                record = {
                    "timestamp": format_message_id(timestamp),
                    "tid": tid,
                    "tid_short": short_tid,
                    "status": status,
                    "event": event,
                    "name": name,
                }
                if json_output:
                    print(json.dumps(record, ensure_ascii=False))
                else:
                    ts_text = _format_timestamp(timestamp)
                    print(
                        f"{ts_text} {tid:<19} {status or 'unknown':<10} {event:<16} {name}",
                        flush=True,
                    )
                emitted = True
                last_timestamp = max(last_timestamp, timestamp)

            if json_output and emitted:
                sys.stdout.flush()
            monitor.wait(max(STATUS_WATCH_MIN_INTERVAL, interval))
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-336] exception
        print(f"weft: status watch failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if monitor is not None:
            monitor.close()
        queue.close()


def cmd_status(
    *,
    tid: str | None = None,
    include_terminal: bool = False,
    status_filter: str | None = None,
    json_output: bool = False,
    watch: bool = False,
    watch_interval: float = 1.0,
    spec_context: str | os.PathLike[str] | None = None,
) -> tuple[int, str | None]:
    """Broker status snapshot with optional task filtering.

    Spec: [CLI-1.2.1]
    """
    try:
        context = _resolve_context(spec_context)
        tid_filters = _resolve_tid_filters(context, tid)
        broker_snapshot = collect_broker_status(context)
        managers = _collect_manager_records(context, include_stopped=include_terminal)
    except Exception as exc:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-337] exception
        return 1, f"weft: failed to retrieve status: {exc}"

    if watch:
        exit_code = _watch_task_events(
            context,
            tid_filters=tid_filters,
            status_filter=status_filter,
            json_output=json_output,
            interval=watch_interval,
        )
        return exit_code, None

    now_ns = time.time_ns()
    service_registry_evidence = tuple(
        _collect_service_registry_evidence(context, now_ns=now_ns)
    )
    all_task_records = _collect_task_snapshot_records(
        context,
        include_terminal=True,
        tid_filters=tid_filters,
        now_ns=now_ns,
        service_registry_evidence=service_registry_evidence,
    )
    task_records = (
        all_task_records
        if include_terminal
        else [
            record
            for record in all_task_records
            if record.snapshot.status not in TERMINAL_TASK_STATUSES
        ]
    )
    tasks = [record.snapshot for record in task_records]
    services = _collect_internal_service_snapshots(
        context,
        managers=managers,
        task_records=all_task_records,
        now_ns=now_ns,
        service_registry_evidence=service_registry_evidence,
    )
    if status_filter:
        tasks = [snap for snap in tasks if snap.status == status_filter]

    if tid and not tasks:
        return 2, f"weft: task {tid} not found"

    if json_output:
        payload = _render_json_payload(broker_snapshot, managers, services, tasks)
    else:
        payload = "\n".join(
            (
                broker_snapshot.to_text(),
                _format_manager_summary(managers),
                _format_service_summary(services),
                _format_task_summary(tasks),
            )
        )

    return 0, payload


def _public_task_snapshot(snapshot: TaskSnapshot) -> PublicTaskSnapshot:
    payload = snapshot.to_dict()
    return PublicTaskSnapshot(
        tid=str(payload["tid"]),
        tid_short=str(payload["tid_short"]),
        name=str(payload["name"]),
        status=str(payload["status"]),
        event=payload["event"] if isinstance(payload.get("event"), str) else None,
        activity=(
            payload["activity"] if isinstance(payload.get("activity"), str) else None
        ),
        waiting_on=(
            payload["waiting_on"]
            if isinstance(payload.get("waiting_on"), str)
            else None
        ),
        started_at=payload["started_at"]
        if isinstance(payload.get("started_at"), int)
        else None,
        completed_at=(
            payload["completed_at"]
            if isinstance(payload.get("completed_at"), int)
            else None
        ),
        return_code=(
            payload["return_code"]
            if isinstance(payload.get("return_code"), int)
            else None
        ),
        error=payload["error"] if isinstance(payload.get("error"), str) else None,
        last_timestamp=(
            payload["last_timestamp"]
            if isinstance(payload.get("last_timestamp"), int)
            else None
        ),
        duration_seconds=(
            float(payload["duration_seconds"])
            if isinstance(payload.get("duration_seconds"), int | float)
            else None
        ),
        runner=payload["runner"] if isinstance(payload.get("runner"), str) else None,
        runtime_handle=(
            dict(payload["runtime_handle"])
            if isinstance(payload.get("runtime_handle"), dict)
            else None
        ),
        runtime=(
            dict(payload["runtime"])
            if isinstance(payload.get("runtime"), dict)
            else None
        ),
        metadata=(
            dict(payload["metadata"])
            if isinstance(payload.get("metadata"), dict)
            else {}
        ),
        pipeline_status=(
            dict(payload["pipeline_status"])
            if isinstance(payload.get("pipeline_status"), dict)
            else None
        ),
        reconciliation=(
            dict(payload["reconciliation"])
            if isinstance(payload.get("reconciliation"), dict)
            else None
        ),
        runner_diagnostics=(
            dict(payload["runner_diagnostics"])
            if isinstance(payload.get("runner_diagnostics"), dict)
            else None
        ),
    )


def system_status(context: WeftContext) -> SystemStatusSnapshot:
    """Return the top-level broker, manager, and task status view."""

    now_ns = time.time_ns()
    service_registry_evidence = tuple(
        _collect_service_registry_evidence(context, now_ns=now_ns)
    )
    managers = _collect_manager_records(context)
    task_records = _collect_task_snapshot_records(
        context,
        include_terminal=True,
        tid_filters=None,
        now_ns=now_ns,
        service_registry_evidence=service_registry_evidence,
    )
    services = _collect_internal_service_snapshots(
        context,
        managers=managers,
        task_records=task_records,
        now_ns=now_ns,
        service_registry_evidence=service_registry_evidence,
    )
    return SystemStatusSnapshot(
        broker=collect_broker_status(context).to_dict(),
        managers=[_manager_snapshot(record) for record in managers],
        tasks=[_public_task_snapshot(record.snapshot) for record in task_records],
        services=services,
    )


__all__ = [
    "BrokerStatusSnapshot",
    "TaskSnapshot",
    "cmd_status",
    "collect_broker_status",
    "system_status",
]
