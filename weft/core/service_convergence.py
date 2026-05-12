"""Shared convergence primitives for runtime-owned services.

Spec references:
- docs/specifications/03-Manager_Architecture.md [MA-1], [MA-3]
- docs/specifications/07-System_Invariants.md [MANAGER.3], [MANAGER.8], [MANAGER.12]
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from weft._constants import (
    LIVE_SERVICE_STATUSES,
    SERVICE_OWNER_SCHEMA,
    SERVICE_STATUS_ACTIVE,
    SERVICE_STATUS_DRAINING,
    SERVICE_STATUS_STOPPED,
    SERVICE_STATUS_TERMINAL,
    SERVICE_TYPE_MANAGER,
    WEFT_SPAWN_REQUESTS_QUEUE,
)
from weft.context import WeftContext, service_context_key

logger = logging.getLogger(__name__)

ServiceOwnerStatus = Literal["active", "draining", "stopped", "terminal", "uncertain"]
ServiceRegistryState = Literal["none", "unknown", "known"]
ServiceOwnerRowDisposition = Literal[
    "accepted",
    "ignored_unsupported_schema",
    "malformed",
]


@dataclass(frozen=True, slots=True)
class ServiceOwnerParseResult:
    """Parsed service-owner row plus its disposition."""

    disposition: ServiceOwnerRowDisposition
    record: ServiceOwnerRecord | None = None


@dataclass(frozen=True, slots=True)
class ServiceRegistryRead:
    """Normalized service-owner rows and parse diagnostics."""

    records: tuple[ServiceOwnerRecord, ...]
    ignored_unsupported_schema: int = 0
    malformed: int = 0
    read_failed: bool = False


@dataclass(frozen=True, slots=True)
class ServiceOwnerRecord:
    """Normalized runtime service-owner evidence."""

    service_key: str
    service_type: str
    owner_tid: str
    status: ServiceOwnerStatus
    timestamp: int
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ServiceConvergenceDecision:
    """Pure reduction result for one convergent service key."""

    service_key: str
    state: ServiceRegistryState
    records: tuple[ServiceOwnerRecord, ...]
    canonical_live: ServiceOwnerRecord | None
    expired_message_ids: tuple[int, ...]
    older_self_message_ids: tuple[int, ...]
    duplicate_live: tuple[ServiceOwnerRecord, ...]
    recent_lower_live_owner: bool
    uncertain: tuple[ServiceOwnerRecord, ...] = ()


def manager_service_key(context: WeftContext) -> str:
    """Return the canonical service key for the public spawn manager."""

    return f"manager:{WEFT_SPAWN_REQUESTS_QUEUE}:{service_context_key(context)}"


def service_owner_tid_key(record: ServiceOwnerRecord) -> int:
    """Return numeric TID key for deterministic owner ordering."""

    return int(record.owner_tid)


def timestamp_is_expired(timestamp: int, now_ns: int, ttl_ns: int) -> bool:
    """Return whether a broker timestamp is outside the service-owner TTL."""

    return ttl_ns >= 0 and now_ns - timestamp > ttl_ns


def parse_service_owner_record(
    payload: Mapping[str, Any],
    *,
    timestamp: int,
) -> ServiceOwnerRecord | None:
    """Parse one service-owner row, ignoring unsupported schemas."""

    result = parse_service_owner_row(payload, timestamp=timestamp)
    return result.record


def parse_service_owner_row(
    payload: Mapping[str, Any],
    *,
    timestamp: int,
) -> ServiceOwnerParseResult:
    """Parse one service-owner row and return accepted/ignored diagnostics."""

    if payload.get("schema") != SERVICE_OWNER_SCHEMA:
        logger.debug("Ignoring unsupported service-owner schema")
        return ServiceOwnerParseResult("ignored_unsupported_schema")
    service_key = payload.get("service_key")
    service_type = payload.get("service_type")
    owner_tid = payload.get("owner_tid")
    status = payload.get("status")
    if not (
        isinstance(timestamp, int)
        and timestamp > 0
        and isinstance(service_key, str)
        and service_key
        and isinstance(service_type, str)
        and service_type
        and isinstance(owner_tid, str)
        and owner_tid
        and owner_tid.isdigit()
        and status
        in {
            SERVICE_STATUS_ACTIVE,
            SERVICE_STATUS_DRAINING,
            SERVICE_STATUS_STOPPED,
            SERVICE_STATUS_TERMINAL,
            "uncertain",
        }
    ):
        return ServiceOwnerParseResult("malformed")
    return ServiceOwnerParseResult(
        "accepted",
        ServiceOwnerRecord(
            service_key=service_key,
            service_type=service_type,
            owner_tid=owner_tid,
            status=status,
            timestamp=timestamp,
            payload=payload,
        ),
    )


def collect_service_owner_records(
    entries: Iterable[tuple[Mapping[str, Any], int]],
    *,
    service_key: str | None = None,
    service_type: str | None = None,
    read_failed: bool = False,
) -> ServiceRegistryRead:
    """Collect accepted service-owner records from queue JSON entries."""

    records: list[ServiceOwnerRecord] = []
    ignored = 0
    malformed = 0
    for payload, timestamp in entries:
        result = parse_service_owner_row(payload, timestamp=timestamp)
        if result.disposition == "ignored_unsupported_schema":
            ignored += 1
            continue
        if result.disposition == "malformed":
            malformed += 1
            continue
        assert result.record is not None
        record = result.record
        if service_key is not None and record.service_key != service_key:
            continue
        if service_type is not None and record.service_type != service_type:
            continue
        records.append(record)
    return ServiceRegistryRead(
        records=tuple(records),
        ignored_unsupported_schema=ignored,
        malformed=malformed,
        read_failed=read_failed,
    )


def reduce_latest_by_service_owner(
    records: Sequence[ServiceOwnerRecord],
) -> tuple[ServiceOwnerRecord, ...]:
    """Keep only the latest row per service key and owner TID."""

    latest: dict[tuple[str, str], ServiceOwnerRecord] = {}
    for record in records:
        key = (record.service_key, record.owner_tid)
        existing = latest.get(key)
        if existing is None or existing.timestamp < record.timestamp:
            latest[key] = record
    return tuple(sorted(latest.values(), key=lambda record: record.timestamp))


def select_canonical_live_owner(
    records: Sequence[ServiceOwnerRecord],
) -> ServiceOwnerRecord | None:
    """Return the lowest-TID live owner from normalized service records."""

    live = [record for record in records if record.status in LIVE_SERVICE_STATUSES]
    if not live:
        return None
    return min(
        live, key=lambda record: (service_owner_tid_key(record), record.timestamp)
    )


def plan_service_registry_prune(
    records: Sequence[ServiceOwnerRecord],
    *,
    own_tid: str | None,
    now_ns: int,
    ttl_ns: int,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Return expired IDs and older-self IDs that may be pruned exactly."""

    expired = tuple(
        record.timestamp
        for record in records
        if timestamp_is_expired(record.timestamp, now_ns, ttl_ns)
    )
    older_self: list[int] = []
    if own_tid is not None:
        own_records = [record for record in records if record.owner_tid == own_tid]
        latest_by_key: dict[str, int] = {}
        for record in own_records:
            latest_by_key[record.service_key] = max(
                latest_by_key.get(record.service_key, -1),
                record.timestamp,
            )
        older_self = [
            record.timestamp
            for record in own_records
            if record.timestamp
            < latest_by_key.get(record.service_key, record.timestamp)
        ]
    return expired, tuple(older_self)


def plan_service_owner_history_prune(
    records: Sequence[ServiceOwnerRecord],
    *,
    service_key: str,
    now_ns: int,
    ttl_ns: int,
    keep_recent_per_key: int = 1,
) -> tuple[int, ...]:
    """Return service-owner history rows that are safe to prune exactly."""

    scoped = tuple(record for record in records if record.service_key == service_key)
    if not scoped:
        return ()

    expired = {
        record.timestamp
        for record in scoped
        if timestamp_is_expired(record.timestamp, now_ns, ttl_ns)
    }
    latest_by_owner = reduce_latest_by_service_owner(scoped)
    latest_ids = {record.timestamp for record in latest_by_owner}
    superseded_owner_rows = {
        record.timestamp for record in scoped if record.timestamp not in latest_ids
    }

    retainable = [
        record for record in latest_by_owner if record.timestamp not in expired
    ]
    ordered = sorted(retainable, key=lambda record: record.timestamp, reverse=True)
    keep_count = max(0, keep_recent_per_key)
    protected = {record.timestamp for record in ordered[:keep_count]}
    older_service_rows = {
        record.timestamp
        for record in ordered[keep_count:]
        if record.timestamp not in protected
    }
    return tuple(sorted(expired | superseded_owner_rows | older_service_rows))


def has_recent_lower_live_owner(
    records: Sequence[ServiceOwnerRecord],
    *,
    own_tid: str,
    now_ns: int,
    ttl_ns: int,
) -> bool:
    """Return whether a non-expired lower-TID live owner exists."""

    try:
        own_tid_int = int(own_tid)
    except (TypeError, ValueError):
        return False
    for record in records:
        if record.status not in LIVE_SERVICE_STATUSES:
            continue
        if timestamp_is_expired(record.timestamp, now_ns, ttl_ns):
            continue
        if service_owner_tid_key(record) < own_tid_int:
            return True
    return False


def reduce_service_ownership(
    service_key: str,
    records: Sequence[ServiceOwnerRecord],
    *,
    own_tid: str | None,
    now_ns: int | None = None,
    ttl_ns: int,
    read_failed: bool = False,
) -> ServiceConvergenceDecision:
    """Reduce service-owner evidence for one service key."""

    observed_now_ns = time.time_ns() if now_ns is None else now_ns
    scoped = tuple(record for record in records if record.service_key == service_key)
    expired, older_self = plan_service_registry_prune(
        scoped,
        own_tid=own_tid,
        now_ns=observed_now_ns,
        ttl_ns=ttl_ns,
    )
    usable = [
        record
        for record in reduce_latest_by_service_owner(scoped)
        if not timestamp_is_expired(record.timestamp, observed_now_ns, ttl_ns)
    ]
    canonical = select_canonical_live_owner(usable)
    live = tuple(record for record in usable if record.status in LIVE_SERVICE_STATUSES)
    duplicate_live = tuple(
        record for record in live if canonical is not None and record is not canonical
    )
    uncertain = tuple(record for record in usable if record.status == "uncertain")
    if read_failed:
        state: ServiceRegistryState = "unknown"
    elif usable:
        state = "known"
    else:
        state = "none"
    return ServiceConvergenceDecision(
        service_key=service_key,
        state=state,
        records=tuple(usable),
        canonical_live=canonical,
        expired_message_ids=expired,
        older_self_message_ids=older_self,
        duplicate_live=duplicate_live,
        recent_lower_live_owner=has_recent_lower_live_owner(
            usable,
            own_tid=own_tid or "",
            now_ns=observed_now_ns,
            ttl_ns=ttl_ns,
        )
        if own_tid is not None
        else False,
        uncertain=uncertain,
    )


def project_manager_service_record(
    payload: Mapping[str, Any],
    *,
    timestamp: int,
    service_key: str | None = None,
) -> dict[str, Any] | None:
    """Project a manager service-owner row into the manager command shape."""

    if payload.get("schema") == SERVICE_OWNER_SCHEMA:
        service_type = payload.get("service_type")
        if service_type != SERVICE_TYPE_MANAGER:
            return None
    record = parse_service_owner_record(payload, timestamp=timestamp)
    if record is None or record.service_type != SERVICE_TYPE_MANAGER:
        return None
    if service_key is not None and record.service_key != service_key:
        return None
    queues = payload.get("queues")
    if not isinstance(queues, Mapping):
        queues = {}
    projected: dict[str, Any] = {
        "tid": record.owner_tid,
        "name": payload.get("name", "manager"),
        "capabilities": payload.get("capabilities", []),
        "status": record.status,
        "timestamp": timestamp,
        "requests": queues.get("requests", WEFT_SPAWN_REQUESTS_QUEUE),
        "inbox": queues.get("requests", WEFT_SPAWN_REQUESTS_QUEUE),
        "ctrl_in": queues.get("ctrl_in"),
        "ctrl_out": queues.get("ctrl_out"),
        "outbox": queues.get("outbox"),
        "internal_requests": queues.get("internal_requests"),
        "internal_reserved": queues.get("internal_reserved"),
        "role": payload.get("role", "manager"),
        "runtime_handle": payload.get("runtime_handle", {}),
        "service_key": record.service_key,
        "service_type": record.service_type,
        "_service_owner_payload": dict(payload),
    }
    metadata = payload.get("metadata")
    if isinstance(metadata, Mapping):
        projected["metadata"] = dict(metadata)
    return projected


def build_manager_service_payload(
    *,
    context: WeftContext,
    tid: str,
    name: str,
    status: Literal["active", "draining", "stopped"],
    queues: Mapping[str, str | None],
    runtime_handle: Mapping[str, Any],
    capabilities: Sequence[Any] = (),
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one canonical manager service-owner payload."""

    clean_queues = {
        key: value for key, value in queues.items() if isinstance(value, str) and value
    }
    payload = {
        "schema": SERVICE_OWNER_SCHEMA,
        "service_key": manager_service_key(context),
        "service_type": SERVICE_TYPE_MANAGER,
        "owner_tid": tid,
        "tid": tid,
        "name": name,
        "capabilities": list(capabilities),
        "status": status,
        "queues": clean_queues,
        "runtime_handle": dict(runtime_handle),
        "role": "manager",
        "metadata": dict(metadata or {}),
    }
    for key, value in clean_queues.items():
        payload[key] = value
    request_queue = clean_queues.get("requests")
    if request_queue is not None:
        payload["inbox"] = request_queue
    return payload


def build_service_owner_payload(
    *,
    service_key: str,
    service_type: str,
    owner_tid: str,
    status: ServiceOwnerStatus,
    name: str,
    queues: Mapping[str, str | None] | None = None,
    runtime_handle: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one canonical service-owner payload for a managed service."""

    clean_queues = {
        key: value
        for key, value in (queues or {}).items()
        if isinstance(value, str) and value
    }
    payload = {
        "schema": SERVICE_OWNER_SCHEMA,
        "service_key": service_key,
        "service_type": service_type,
        "owner_tid": owner_tid,
        "tid": owner_tid,
        "name": name,
        "status": status,
        "queues": clean_queues,
        "runtime_handle": dict(runtime_handle or {}),
        "metadata": dict(metadata or {}),
    }
    for key, value in clean_queues.items():
        payload[key] = value
    return payload


def sync_manager_service_payload_top_level_queues(payload: dict[str, Any]) -> None:
    """Mirror nested manager queue fields to top-level compatibility fields."""

    queues = payload.get("queues")
    if not isinstance(queues, Mapping):
        return
    for key in (
        "requests",
        "reserved",
        "ctrl_in",
        "ctrl_out",
        "outbox",
        "internal_requests",
        "internal_reserved",
    ):
        value = queues.get(key)
        if isinstance(value, str) and value:
            payload[key] = value
    payload["inbox"] = payload.get("requests")
