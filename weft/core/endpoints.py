"""Runtime endpoint registry helpers.

These helpers implement Weft-owned runtime endpoint discovery over ordinary
task-local queues. The registry is runtime-only state layered on top of broker
queues rather than a second hidden control plane.

Spec references:
- docs/specifications/01-Core_Components.md [CC-2.4.1]
- docs/specifications/04-SimpleBroker_Integration.md [SB-0.5]
- docs/specifications/05-Message_Flow_and_State.md [MF-3.1]
- docs/specifications/07-System_Invariants.md [OBS.1], [OBS.2], [OBS.3]
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any, cast

from simplebroker import Queue
from simplebroker.ext import BrokerError
from weft._constants import (
    ENDPOINT_NAME_PATTERN,
    INTERNAL_ENDPOINT_NAMESPACE_PREFIX,
    TERMINAL_TASK_STATUSES,
    WEFT_ENDPOINTS_REGISTRY_QUEUE,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
)
from weft.context import WeftContext
from weft.ext import RunnerHandle
from weft.helpers import canonical_owner_tid, iter_queue_json_entries, pid_is_live


def normalize_endpoint_name(name: str) -> str:
    """Validate and normalize a stable runtime endpoint name."""

    candidate = name.strip()
    if not candidate:
        raise ValueError("endpoint name must not be empty")
    if candidate.startswith(INTERNAL_ENDPOINT_NAMESPACE_PREFIX):
        internal_suffix = candidate.removeprefix(INTERNAL_ENDPOINT_NAMESPACE_PREFIX)
        if ENDPOINT_NAME_PATTERN.fullmatch(internal_suffix):
            return candidate
        raise ValueError(
            "internal endpoint name must use the reserved prefix "
            f"'{INTERNAL_ENDPOINT_NAMESPACE_PREFIX}' followed by a valid endpoint "
            "suffix"
        )
    if not ENDPOINT_NAME_PATTERN.fullmatch(candidate):
        raise ValueError(
            "endpoint name must start with an alphanumeric character and use "
            "only letters, digits, '.', '_', or '-'"
        )
    return candidate


def is_reserved_internal_endpoint_name(name: str) -> bool:
    """Return whether a normalized endpoint name is reserved for Weft internals."""

    normalized_name = normalize_endpoint_name(name)
    return normalized_name.startswith(INTERNAL_ENDPOINT_NAMESPACE_PREFIX)


def validate_endpoint_claim_name(
    name: str,
    *,
    allow_reserved_internal: bool = False,
) -> str:
    """Validate a claimable endpoint name for a concrete caller surface."""

    normalized_name = normalize_endpoint_name(name)
    if not allow_reserved_internal and normalized_name.startswith(
        INTERNAL_ENDPOINT_NAMESPACE_PREFIX
    ):
        raise ValueError(
            f"endpoint names under '{INTERNAL_ENDPOINT_NAMESPACE_PREFIX}' are "
            "reserved for internal runtime services"
        )
    return normalized_name


@dataclass(frozen=True, slots=True)
class EndpointRecord:
    """One runtime endpoint registry record."""

    name: str
    tid: str
    status: str
    inbox: str
    outbox: str
    ctrl_in: str
    ctrl_out: str
    registered_at: int | None
    last_seen: int | None
    metadata: dict[str, Any]
    message_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "tid": self.tid,
            "status": self.status,
            "inbox": self.inbox,
            "outbox": self.outbox,
            "ctrl_in": self.ctrl_in,
            "ctrl_out": self.ctrl_out,
            "registered_at": self.registered_at,
            "last_seen": self.last_seen,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ResolvedEndpoint:
    """Canonical live endpoint resolution result."""

    record: EndpointRecord
    live_candidates: int = 1

    def to_dict(self) -> dict[str, Any]:
        payload = self.record.to_dict()
        payload["live_candidates"] = self.live_candidates
        return payload


def build_endpoint_record_payload(
    *,
    name: str,
    tid: str,
    inbox: str,
    outbox: str,
    ctrl_in: str,
    ctrl_out: str,
    metadata: Mapping[str, Any] | None = None,
    registered_at: int | None = None,
    last_seen: int | None = None,
) -> dict[str, Any]:
    """Build a JSON-serializable runtime endpoint record payload."""

    normalized_name = normalize_endpoint_name(name)
    return {
        "name": normalized_name,
        "tid": tid,
        "status": "active",
        "inbox": inbox,
        "outbox": outbox,
        "ctrl_in": ctrl_in,
        "ctrl_out": ctrl_out,
        "registered_at": registered_at,
        "last_seen": last_seen,
        "metadata": dict(metadata or {}),
    }


def endpoint_record_from_payload(
    payload: Mapping[str, Any],
    *,
    message_id: int | None = None,
) -> EndpointRecord | None:
    """Convert one decoded queue payload into an endpoint record."""

    name_raw = payload.get("name")
    tid_raw = payload.get("tid")
    status_raw = payload.get("status")
    inbox_raw = payload.get("inbox")
    outbox_raw = payload.get("outbox")
    ctrl_in_raw = payload.get("ctrl_in")
    ctrl_out_raw = payload.get("ctrl_out")
    if not all(
        isinstance(value, str) and value for value in (name_raw, tid_raw, status_raw)
    ):
        return None
    if not all(
        isinstance(value, str) and value
        for value in (inbox_raw, outbox_raw, ctrl_in_raw, ctrl_out_raw)
    ):
        return None

    try:
        normalized_name = normalize_endpoint_name(cast(str, name_raw))
    except ValueError:
        return None

    tid = cast(str, tid_raw)
    status = cast(str, status_raw)
    inbox = cast(str, inbox_raw)
    outbox = cast(str, outbox_raw)
    ctrl_in = cast(str, ctrl_in_raw)
    ctrl_out = cast(str, ctrl_out_raw)

    registered_at_raw = payload.get("registered_at")
    last_seen_raw = payload.get("last_seen")
    metadata_raw = payload.get("metadata")

    return EndpointRecord(
        name=normalized_name,
        tid=tid,
        status=status,
        inbox=inbox,
        outbox=outbox,
        ctrl_in=ctrl_in,
        ctrl_out=ctrl_out,
        registered_at=registered_at_raw if isinstance(registered_at_raw, int) else None,
        last_seen=last_seen_raw if isinstance(last_seen_raw, int) else None,
        metadata=dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {},
        message_id=message_id,
    )


def find_endpoint_registry_message(queue: Queue, *, name: str, tid: str) -> int | None:
    """Return the latest registry message id for one endpoint owner."""

    latest_message_id: int | None = None
    normalized_name = normalize_endpoint_name(name)
    for payload, message_id in iter_queue_json_entries(queue):
        if payload.get("name") != normalized_name or payload.get("tid") != tid:
            continue
        latest_message_id = int(message_id)
    return latest_message_id


def _latest_task_statuses(ctx: WeftContext) -> dict[str, str]:
    queue = ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    try:
        latest: dict[str, tuple[int, str]] = {}
        for payload, message_id in iter_queue_json_entries(queue):
            tid = payload.get("tid")
            if not isinstance(tid, str) or not tid:
                continue

            status = payload.get("status")
            if not isinstance(status, str) or not status:
                taskspec = payload.get("taskspec")
                if isinstance(taskspec, Mapping):
                    state = taskspec.get("state")
                    if isinstance(state, Mapping):
                        state_status = state.get("status")
                        if isinstance(state_status, str) and state_status:
                            status = state_status
            if not isinstance(status, str) or not status:
                continue

            previous = latest.get(tid)
            if previous is None or previous[0] <= message_id:
                latest[tid] = (int(message_id), status)
        return {tid: status for tid, (_message_id, status) in latest.items()}
    finally:
        queue.close()


def _latest_tid_mapping_entries(ctx: WeftContext) -> dict[str, dict[str, Any]]:
    queue = ctx.queue(WEFT_TID_MAPPINGS_QUEUE, persistent=False)
    try:
        latest: dict[str, tuple[int, dict[str, Any]]] = {}
        for payload, message_id in iter_queue_json_entries(queue):
            full = payload.get("full")
            if not isinstance(full, str) or not full:
                continue
            previous = latest.get(full)
            if previous is None or previous[0] <= message_id:
                latest[full] = (int(message_id), payload)
        return {full: payload for full, (_message_id, payload) in latest.items()}
    finally:
        queue.close()


def _record_owner_is_live(
    record: EndpointRecord,
    *,
    task_statuses: Mapping[str, str],
    tid_mappings: Mapping[str, Mapping[str, Any]],
) -> bool:
    task_status = task_statuses.get(record.tid)
    if task_status in TERMINAL_TASK_STATUSES:
        return False

    mapping = tid_mappings.get(record.tid)
    if mapping is None:
        return False

    handle_payload = mapping.get("runtime_handle")
    if handle_payload is None:
        return True
    if not isinstance(handle_payload, Mapping):
        return False
    try:
        handle = RunnerHandle.from_dict(handle_payload)
    except (TypeError, ValueError):
        return False
    if handle.control.get("authority") != "host-pid":
        return True
    return any(pid_is_live(pid) for pid in handle.scoped_host_pids())


def list_resolved_endpoints(
    ctx: WeftContext,
    *,
    pattern: str | None = None,
) -> list[ResolvedEndpoint]:
    """Return canonical live endpoint records after stale-owner pruning."""

    registry_queue = ctx.queue(WEFT_ENDPOINTS_REGISTRY_QUEUE, persistent=False)
    try:
        latest_by_owner: dict[tuple[str, str], EndpointRecord] = {}
        for payload, message_id in iter_queue_json_entries(registry_queue):
            record = endpoint_record_from_payload(payload, message_id=int(message_id))
            if record is None:
                continue
            if pattern is not None and not fnmatchcase(record.name, pattern):
                continue
            previous = latest_by_owner.get((record.name, record.tid))
            if previous is None or (previous.message_id or -1) <= int(message_id):
                latest_by_owner[(record.name, record.tid)] = record

        task_statuses = _latest_task_statuses(ctx)
        tid_mappings = _latest_tid_mapping_entries(ctx)
        grouped: dict[str, list[EndpointRecord]] = {}
        stale_message_ids: list[int] = []

        for record in latest_by_owner.values():
            if record.status != "active":
                continue
            if _record_owner_is_live(
                record,
                task_statuses=task_statuses,
                tid_mappings=tid_mappings,
            ):
                grouped.setdefault(record.name, []).append(record)
                continue
            if record.message_id is not None:
                stale_message_ids.append(record.message_id)

        for message_id in stale_message_ids:
            try:
                registry_queue.delete(message_id=message_id)
            except (BrokerError, OSError, RuntimeError):
                continue

        resolved: list[ResolvedEndpoint] = []
        for candidates in grouped.values():
            ordered = sorted(candidates, key=lambda item: int(item.tid))
            canonical_tid = canonical_owner_tid(record.tid for record in ordered)
            if canonical_tid is None:
                continue
            canonical_record = next(
                (record for record in ordered if record.tid == canonical_tid),
                ordered[0],
            )
            resolved.append(
                ResolvedEndpoint(record=canonical_record, live_candidates=len(ordered))
            )
        resolved.sort(key=lambda item: (item.record.name, int(item.record.tid)))
        return resolved
    finally:
        registry_queue.close()


def resolve_endpoint(ctx: WeftContext, name: str) -> ResolvedEndpoint | None:
    """Resolve one stable endpoint name to its canonical live owner."""

    normalized_name = normalize_endpoint_name(name)
    for resolved in list_resolved_endpoints(ctx):
        if resolved.record.name == normalized_name:
            return resolved
    return None


__all__ = [
    "ENDPOINT_NAME_PATTERN",
    "EndpointRecord",
    "ResolvedEndpoint",
    "build_endpoint_record_payload",
    "endpoint_record_from_payload",
    "find_endpoint_registry_message",
    "is_reserved_internal_endpoint_name",
    "list_resolved_endpoints",
    "normalize_endpoint_name",
    "validate_endpoint_claim_name",
    "resolve_endpoint",
]
