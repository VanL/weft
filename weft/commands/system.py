"""Status reporting helpers for the Weft CLI.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-1.2.1]
- docs/specifications/01-Core_Components.md [CC-3.2], [CC-3.4]
- docs/specifications/02-TaskSpec.md [TS-1.3]
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/03-Manager_Architecture.md [MA-1.4]
- docs/specifications/14-Python_API_Surfaces.md [PY-2]
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from simplebroker import Queue
from simplebroker.ext import BrokerError
from weft._constants import (
    INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY,
    INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT,
    INTERNAL_RUNTIME_TASK_CLASS_LIVENESS_MONITOR,
    INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR,
    INTERNAL_SERVICE_KEY_HEARTBEAT,
    INTERNAL_SERVICE_KEY_LIVENESS_MONITOR,
    INTERNAL_SERVICE_KEY_TASK_MONITOR,
    LIVE_SERVICE_STATUSES,
    NON_LIVE_RUNTIME_STATES,
    RUNNER_DIAGNOSTICS_FIELD,
    SERVICE_STATUS_STOPPED,
    SERVICE_STATUS_SUPERSEDED,
    SERVICE_STATUS_TERMINAL,
    SERVICE_TYPE_MANAGED,
    STATUS_RUNTIMELESS_STALE_AFTER_SECONDS,
    STATUS_WATCH_MIN_INTERVAL,
    TERMINAL_TASK_STATUSES,
    WEFT_CONTEXT_ENV,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE,
    WEFT_SERVICES_REGISTRY_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
)
from weft._exceptions import CommandError, CommandExecutionError, CommandUsageError
from weft.commands.manager import (
    _manager_snapshot,
)
from weft.commands.types import (
    CommandStream,
    ServiceSnapshot,
    SystemStatusSnapshot,
    TaskEvent,
)
from weft.commands.types import (
    TaskSnapshot as PublicTaskSnapshot,
)
from weft.context import WeftContext, build_context
from weft.core import manager_runtime, task_evidence
from weft.core.queue_wait import QueueChangeMonitor
from weft.core.queue_window import iter_broker_queue_json_entries
from weft.core.service_convergence import (
    ServiceOwnerRecord,
    collect_service_owner_records,
    discard_v1_service_registry_rows,
    reduce_latest_by_service_owner,
)
from weft.core.task_state import latest_task_state_rows, list_task_state_tids
from weft.ext import RunnerHandle
from weft.helpers import (
    closing_queue_iterator,
    handle_has_live_host_process,
    iter_queue_json_entries,
    pid_is_live,
    tid_short_form,
)
from weft.helpers.message_ids import is_task_tid

from ._boundary import typed_command_errors
from ._resources import command_resource_scope
from ._task_snapshot_reducer import (
    CollectedTaskSnapshot,
    FoldedTaskRecord,
    SnapshotEvidence,
    TaskSnapshot,
    order_task_snapshots,
    plan_snapshot_probes,
    prepare_snapshot,
    reduce_task_event,
    reduce_task_snapshot,
    reduce_task_state_snapshot,
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


def _service_evidence_sort_key(candidate: _ServiceEvidence) -> tuple[int, int, str]:
    return (candidate.rank, candidate.updated_at or 0, candidate.tid or "")


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


def _read_tid_states(
    ctx: WeftContext, *, broker: Any | None = None
) -> dict[str, list[str]]:
    """Group retained task-state names by derived short form [CLI-1.2.3]."""
    mapping: dict[str, list[str]] = {}
    for full in list_task_state_tids(ctx, broker=broker):
        short = tid_short_form(full)
        mapping.setdefault(short, []).append(full)
    return {short: sorted(fulls) for short, fulls in mapping.items()}


def _latest_tid_state_entries(
    ctx: WeftContext, tids: Iterable[str] | None = None, *, broker: Any | None = None
) -> dict[str, dict[str, Any]]:
    return {
        full: {**payload, "timestamp": _message_id}
        for full, (_message_id, payload) in latest_task_state_rows(
            ctx, tids=tids, broker=broker
        ).items()
    }


def _resolve_tid_filters(ctx: WeftContext, raw: str | None) -> set[str] | None:
    if raw is None:
        return None
    candidate = raw.strip().lstrip("T")
    if not candidate:
        return None
    if is_task_tid(candidate):
        return {candidate}
    matches = _read_tid_states(ctx).get(candidate, [])
    if len(matches) > 1:
        raise CommandUsageError(
            f"Ambiguous short TID {candidate}: {', '.join(matches)}"
        )
    return {matches[0]} if matches else {candidate}


def _iter_log_events(
    queue: Queue | None,
    *,
    since_timestamp: int | None = None,
    broker: Any | None = None,
) -> Iterable[tuple[dict[str, Any], int]]:
    """Replay all state-change events from the global log queue.

    Spec: [MF-5]
    """
    try:
        iterator_raw = (
            broker.peek_generator(
                WEFT_GLOBAL_LOG_QUEUE,
                with_timestamps=True,
                after_timestamp=since_timestamp,
            )
            if broker is not None
            else cast(Queue, queue).peek_generator(
                with_timestamps=True,
                after_timestamp=since_timestamp,
            )
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


def _runtime_handle_from_mapping(entry: Mapping[str, Any]) -> RunnerHandle | None:
    payload = entry.get("runtime_handle")
    if not isinstance(payload, Mapping):
        return None
    try:
        return RunnerHandle.from_dict(payload)
    except ValueError:
        return None


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
    if role in {"task_monitor", "heartbeat_service", "liveness_monitor"}:
        return True
    service_key = metadata.get("_weft_service_key")
    return isinstance(service_key, str) and service_key.startswith("_weft.service.")


def _service_display_name(key: str) -> str:
    if key == INTERNAL_SERVICE_KEY_HEARTBEAT:
        return "heartbeat-service"
    if key == INTERNAL_SERVICE_KEY_TASK_MONITOR:
        return "task-monitor"
    if key == INTERNAL_SERVICE_KEY_LIVENESS_MONITOR:
        return "liveness-monitor"
    return key.rsplit(".", 1)[-1] or key


def _known_internal_service_keys() -> tuple[str, ...]:
    return (
        INTERNAL_SERVICE_KEY_HEARTBEAT,
        INTERNAL_SERVICE_KEY_TASK_MONITOR,
        INTERNAL_SERVICE_KEY_LIVENESS_MONITOR,
    )


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
    if runtime_class == INTERNAL_RUNTIME_TASK_CLASS_LIVENESS_MONITOR:
        return INTERNAL_SERVICE_KEY_LIVENESS_MONITOR
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


def _stale_liveness_reason(
    status: str,
    *,
    tid: str,
    runner_name: str | None,
    mapping_entry: Mapping[str, Any] | None,
    runtime_description: Mapping[str, Any] | None,
    last_timestamp: int,
    now_ns: int,
) -> str | None:
    """Return why nonterminal liveness evidence needs read-model reconciliation."""

    normalized_runner = (
        runner_name.strip().lower() if isinstance(runner_name, str) else ""
    )
    host_task_pid = _task_process_id(mapping_entry)
    stale_without_runtime = (
        status in {"spawning", "running"}
        and host_task_pid is None
        and runtime_description is None
        and last_timestamp > 0
        and now_ns - last_timestamp
        > int(STATUS_RUNTIMELESS_STALE_AFTER_SECONDS * 1_000_000_000)
    )

    if status in TERMINAL_TASK_STATUSES:
        return None
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


def _merge_raw_task_history(
    current: dict[str, CollectedTaskSnapshot],
    *,
    broker: Any,
    since_timestamp: int | None,
    tid_filters: set[str] | None,
    now_ns: int,
) -> None:
    """Union explicit retained history, without per-task runtime acquisition [MF-5]."""
    records: dict[str, FoldedTaskRecord] = {}
    for payload, timestamp in _iter_log_events(
        None, since_timestamp=since_timestamp, broker=broker
    ):
        tid = payload.get("tid")
        if not isinstance(tid, str):
            continue
        reduced = reduce_task_event(
            records.get(tid), payload, timestamp, tid_filters=tid_filters
        )
        if reduced is not None:
            records[tid] = reduced
    for tid, record in records.items():
        if record.taskspec_payload is None:
            continue
        existing = current.get(tid)
        if (
            record.status not in TERMINAL_TASK_STATUSES
            and existing is not None
            and existing.snapshot.reconciliation is None
        ):
            continue
        reduced_snapshot = reduce_task_snapshot(
            plan_snapshot_probes(
                prepare_snapshot(record, local_evidence=None),
                stale_liveness_reason=None,
            ),
            SnapshotEvidence(
                resolved_runtime_entry=None,
                runtime_handle=None,
                runtime_description=None,
                runtime_observation=None,
                claimed_outbox=None,
                active_service_tid=None,
                selected_active_manager_tid=None,
            ),
            now_ns=now_ns,
        )
        if reduced_snapshot is not None:
            current[tid] = reduced_snapshot


def _collect_task_snapshot_records(
    ctx: WeftContext,
    *,
    include_terminal: bool,
    tid_filters: set[str] | None,
    include_log_history: bool = False,
    since_timestamp: int | None = None,
    now_ns: int | None = None,
    tid_state_entries: Mapping[str, Mapping[str, Any]] | None = None,
    broker: Any | None = None,
) -> list[CollectedTaskSnapshot]:
    """Acquire current inventory, optionally unioning explicit raw history [MF-5]."""
    if broker is None:
        with ctx.broker() as opened:
            return _collect_task_snapshot_records(
                ctx,
                include_terminal=include_terminal,
                tid_filters=tid_filters,
                include_log_history=include_log_history,
                since_timestamp=since_timestamp,
                now_ns=now_ns,
                tid_state_entries=tid_state_entries,
                broker=opened,
            )
    if now_ns is None:
        now_ns = time.time_ns()
    if tid_state_entries is None:
        tid_state_entries = _latest_tid_state_entries(
            ctx, tids=tid_filters, broker=broker
        )
    current = {
        tid: reduce_task_state_snapshot(tid, payload, now_ns=now_ns)
        for tid, payload in tid_state_entries.items()
        if tid_filters is None or tid in tid_filters
    }
    if include_log_history:
        _merge_raw_task_history(
            current,
            broker=broker,
            since_timestamp=since_timestamp,
            tid_filters=tid_filters,
            now_ns=now_ns,
        )
    return order_task_snapshots(
        current.values(), include_terminal=include_terminal or include_log_history
    )


def _service_evidence_from_child_task(
    record: CollectedTaskSnapshot,
    *,
    now_ns: int,
) -> _ServiceEvidence | None:
    key = _service_key_from_taskspec_payload(
        record.taskspec_payload or {"metadata": record.snapshot.metadata}
    )
    if key is None:
        return None
    snapshot = record.snapshot
    if snapshot.status in TERMINAL_TASK_STATUSES:
        return _ServiceEvidence(
            key=key,
            name=snapshot.name or _service_display_name(key),
            status="terminal",
            evidence="child-task-state",
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
            evidence="child-task-state",
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
        evidence="child-task-state",
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
    broker: Any | None = None,
) -> list[_ServiceEvidence]:
    queue = _queue(ctx, WEFT_SERVICES_REGISTRY_QUEUE) if broker is None else None
    try:
        discard_v1_service_registry_rows(queue, broker=broker)
    except (BrokerError, OSError, RuntimeError, ValueError):
        if queue is not None:
            queue.close()
        raise
    try:
        read = collect_service_owner_records(
            iter_broker_queue_json_entries(broker, WEFT_SERVICES_REGISTRY_QUEUE)
            if broker is not None
            else iter_queue_json_entries(cast(Queue, queue)),
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
        if queue is not None:
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
    """Return whether config makes one internal service desired.

    Heartbeat mirrors the manager's desire gate: it is desired only when an
    enabled internal dependent needs it, and ``TaskMonitor`` is the only such
    dependent. ``LivenessMonitor`` schedules from its own due heap.

    Spec: [MA-1] item 7
    """

    task_monitor_enabled = bool(ctx.config.get("TASK_MONITOR_ENABLED", True))
    liveness_monitor_enabled = bool(ctx.config.get("LIVENESS_MONITOR_ENABLED", True))
    if key == INTERNAL_SERVICE_KEY_TASK_MONITOR:
        return task_monitor_enabled
    if key == INTERNAL_SERVICE_KEY_HEARTBEAT:
        return task_monitor_enabled
    if key == INTERNAL_SERVICE_KEY_LIVENESS_MONITOR:
        return liveness_monitor_enabled
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
    tid_state_entries: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any] | None:
    if key != INTERNAL_SERVICE_KEY_TASK_MONITOR or evidence is None:
        return None
    if evidence.tid is None:
        return None
    mapping = tid_state_entries.get(evidence.tid)
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
    service_registry_evidence: Sequence[_ServiceEvidence],
    tid_state_entries: Mapping[str, Mapping[str, Any]],
    include_log_history: bool = False,
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

    if include_log_history:
        with _queue(ctx, WEFT_GLOBAL_LOG_QUEUE) as log_queue:
            for payload, timestamp in _iter_log_events(log_queue):
                candidate = _service_evidence_from_manager_spawned(payload, timestamp)
                if candidate is not None:
                    candidates_by_key.setdefault(candidate.key, []).append(candidate)

    for candidate in service_registry_evidence:
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
                    tid_state_entries=tid_state_entries,
                ),
            )
        )
    return snapshots


def _collect_task_snapshots(
    ctx: WeftContext,
    *,
    include_terminal: bool,
    tid_filters: set[str] | None,
    include_log_history: bool = False,
    broker: Any | None = None,
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
            include_log_history=include_log_history,
            broker=broker,
        )
    ]


def collect_known_tid_record(
    ctx: WeftContext,
    tid: str,
    *,
    include_terminal: bool = True,
    broker: Any | None = None,
) -> CollectedTaskSnapshot | None:
    """Keep a full-TID snapshot and its fresh payload in one acquisition [MF-5]."""

    if not is_task_tid(tid):
        return None
    records = _collect_task_snapshot_records(
        ctx,
        include_terminal=include_terminal,
        tid_filters={tid},
        broker=broker,
    )
    return records[0] if records else None


def collect_known_tid_snapshot(
    ctx: WeftContext,
    tid: str,
    *,
    include_terminal: bool = True,
    broker: Any | None = None,
) -> TaskSnapshot | None:
    """Project one fresh full-TID diagnostic snapshot [MF-5]."""
    record = collect_known_tid_record(
        ctx, tid, include_terminal=include_terminal, broker=broker
    )
    return record.snapshot if record is not None else None


def _public_status_event(
    payload: dict[str, Any],
    timestamp: int,
    *,
    status_filter: str | None,
) -> TaskEvent | None:
    """Project one task-log payload into a filtered public event."""

    tid = payload.get("tid")
    if not isinstance(tid, str):
        return None
    try:
        tid_short_form(tid)
    except ValueError:
        return None
    taskspec = payload.get("taskspec")
    state = taskspec.get("state") if isinstance(taskspec, dict) else None
    status = payload.get("status")
    if not isinstance(status, str) and isinstance(state, dict):
        state_status = state.get("status")
        status = state_status if isinstance(state_status, str) else None
    if status_filter is not None and status != status_filter:
        return None
    event_type = payload.get("event") or status or "task_event"
    return TaskEvent(
        tid=tid,
        event_type=str(event_type),
        timestamp=timestamp,
        payload=dict(payload),
    )


def _iter_public_status_events_owned(
    context: WeftContext,
    *,
    status_filter: str | None,
    interval: float,
) -> Iterator[TaskEvent]:
    """Iterate structured project-wide task events."""

    last_timestamp = 0
    with command_resource_scope() as resources:
        resources.enter_context(context.session())
        queue = _queue(context, WEFT_GLOBAL_LOG_QUEUE)
        resources.callback(queue.close)
        monitor: QueueChangeMonitor | None = None
        monitor = QueueChangeMonitor([queue], config=context.config)
        resources.callback(monitor.close)
        while True:
            emitted = False
            for payload, timestamp in _iter_log_events(
                queue,
                since_timestamp=last_timestamp,
            ):
                if timestamp <= last_timestamp:
                    continue
                last_timestamp = max(last_timestamp, timestamp)
                event = _public_status_event(
                    payload,
                    timestamp,
                    status_filter=status_filter,
                )
                if event is not None:
                    yield event
                    emitted = True
            if not emitted:
                monitor.wait(max(STATUS_WATCH_MIN_INTERVAL, interval))


def _iter_public_status_events(
    context: WeftContext,
    *,
    status_filter: str | None,
    interval: float,
) -> Iterator[TaskEvent]:
    """Map owned status-stream failures to the public command boundary."""

    try:
        yield from _iter_public_status_events_owned(
            context,
            status_filter=status_filter,
            interval=interval,
        )
    except CommandError:
        raise
    except Exception as exc:
        raise CommandExecutionError(f"status watch failed: {exc}") from exc


def _status_event_stream(
    context: WeftContext,
    *,
    status_filter: str | None,
    interval: float,
) -> CommandStream[TaskEvent]:
    """Return the closable structured project-wide task event stream."""

    return cast(
        CommandStream[TaskEvent],
        _iter_public_status_events(
            context,
            status_filter=status_filter,
            interval=interval,
        ),
    )


@typed_command_errors
def cmd_status(
    *,
    all: bool = False,
    include_log_history: bool = False,
    status: str | None = None,
    watch: bool = False,
    interval: float = 1.0,
    context: str | os.PathLike[str] | None = None,
) -> SystemStatusSnapshot | CommandStream[TaskEvent]:
    """Return project status or a structured task-event stream.

    Args:
        all: Include terminal tasks in snapshot mode.
        status: Optional task-status filter.
        watch: Return a live event stream instead of a snapshot.
        interval: Maximum polling interval for watch mode.
        context: Optional project context path.

    Returns:
        A structured project snapshot, or a closable event stream when watching.

    Raises:
        CommandUsageError: If a parsed semantic value is invalid.
        CommandExecutionError: If context resolution or status collection fails.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2]
    """

    if include_log_history and watch:
        raise CommandUsageError("--log cannot be combined with --watch")
    if interval < 0:
        raise CommandUsageError("interval must be non-negative")
    try:
        resolved_context = _resolve_context(context)
    except Exception as exc:
        raise CommandExecutionError(f"failed to resolve status context: {exc}") from exc
    if watch:
        return _status_event_stream(
            resolved_context,
            status_filter=status,
            interval=interval,
        )
    try:
        snapshot = system_status(
            resolved_context,
            include_stopped_managers=all,
            include_terminal=all,
            status=status,
            include_log_history=include_log_history,
        )
    except Exception as exc:
        raise CommandExecutionError(f"failed to collect project status: {exc}") from exc
    tasks = [
        task
        for task in snapshot.tasks
        if (all or include_log_history or task.status not in TERMINAL_TASK_STATUSES)
        and (status is None or task.status == status)
    ]
    return SystemStatusSnapshot(
        broker=dict(snapshot.broker),
        managers=list(snapshot.managers),
        tasks=tasks,
        services=list(snapshot.services),
    )


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
            dict(payload[RUNNER_DIAGNOSTICS_FIELD])
            if isinstance(payload.get(RUNNER_DIAGNOSTICS_FIELD), dict)
            else None
        ),
    )


def system_status(
    context: WeftContext,
    *,
    include_stopped_managers: bool = False,
    include_terminal: bool = False,
    status: str | None = None,
    include_log_history: bool = False,
) -> SystemStatusSnapshot:
    """Return the top-level broker, manager, and task status view."""

    now_ns = time.time_ns()
    service_registry_evidence = tuple(
        _collect_service_registry_evidence(context, now_ns=now_ns)
    )
    managers = _collect_manager_records(
        context,
        include_stopped=include_stopped_managers,
    )
    tid_state_entries = _latest_tid_state_entries(context)
    task_records = _collect_task_snapshot_records(
        context,
        include_terminal=True,
        include_log_history=include_log_history,
        tid_filters=None,
        now_ns=now_ns,
        tid_state_entries=tid_state_entries,
    )
    services = _collect_internal_service_snapshots(
        context,
        managers=managers,
        task_records=task_records,
        now_ns=now_ns,
        service_registry_evidence=service_registry_evidence,
        tid_state_entries=tid_state_entries,
        include_log_history=include_log_history,
    )
    return SystemStatusSnapshot(
        broker=collect_broker_status(context).to_dict(),
        managers=[_manager_snapshot(record) for record in managers],
        tasks=[
            _public_task_snapshot(record.snapshot)
            for record in task_records
            if (
                include_terminal
                or include_log_history
                or record.snapshot.status not in TERMINAL_TASK_STATUSES
            )
            and (status is None or record.snapshot.status == status)
        ],
        services=services,
    )


__all__ = [
    "BrokerStatusSnapshot",
    "TaskSnapshot",
    "cmd_status",
    "collect_broker_status",
    "system_status",
]
