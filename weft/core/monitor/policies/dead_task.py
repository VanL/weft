"""Dead-task cleanup candidate discovery.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.13]
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from weft._constants import (
    QUEUE_CTRL_IN_SUFFIX,
    QUEUE_CTRL_OUT_SUFFIX,
    QUEUE_INBOX_SUFFIX,
    QUEUE_OUTBOX_SUFFIX,
    QUEUE_RESERVED_SUFFIX,
    STANDARD_TASK_QUEUE_SUFFIXES,
    WEFT_GLOBAL_LOG_QUEUE,
)
from weft.context import WeftContext
from weft.core.queue_window import QueueWindowRow, is_old_enough


@dataclass(frozen=True, slots=True)
class DeadTaskTidSelection:
    """Dead-task TID selection result from standard task-local queue names."""

    selected_tids: tuple[str, ...] = ()
    discovered_tids: int = 0
    skipped_live: int = 0
    skipped_too_young: int = 0


@dataclass(frozen=True, slots=True)
class DeadTaskQueueCleanupPlan:
    """Policy-selected queue names for one dead-TID cleanup attempt."""

    queue_names: tuple[str, ...]
    control_queue_names: tuple[str, str]
    inbox_queue_names: tuple[str, ...]
    outbox_queue_names: tuple[str, ...]
    reserved_queue_names: tuple[str, ...]
    retention_eligible: bool


@dataclass(frozen=True, slots=True)
class DeadTaskLogCoalesceGroup:
    """Exact task-log rows found by the dead-TID coalesce lookup."""

    tid: str
    rows: tuple[QueueWindowRow, ...]
    api_matches: int


def standard_task_queue_tid(queue_name: str) -> str | None:
    """Return the TID encoded in a standard task-local queue name."""

    identity = standard_task_queue_identity(queue_name)
    if identity is None:
        return None
    tid, _suffix = identity
    return tid


def standard_task_queue_identity(queue_name: str) -> tuple[str, str] | None:
    """Return ``(tid, suffix)`` for a strict standard task-local queue name."""

    if not queue_name.startswith("T"):
        return None
    tid, separator, suffix = queue_name[1:].partition(".")
    if separator != "." or not tid.isdigit() or not tid:
        return None
    if suffix not in STANDARD_TASK_QUEUE_SUFFIXES:
        return None
    return tid, suffix


def standard_dead_task_stale_queue_names(tid: str) -> tuple[str, str, str]:
    """Return standard task-local queues stale once a TID is dead."""

    return (
        f"T{tid}.{QUEUE_CTRL_IN_SUFFIX}",
        f"T{tid}.{QUEUE_CTRL_OUT_SUFFIX}",
        f"T{tid}.{QUEUE_INBOX_SUFFIX}",
    )


def standard_dead_task_retention_queue_names(tid: str) -> tuple[str, str]:
    """Return dead-task queues eligible only after retention expiry."""

    return (
        f"T{tid}.{QUEUE_OUTBOX_SUFFIX}",
        f"T{tid}.{QUEUE_RESERVED_SUFFIX}",
    )


def dead_task_queue_cleanup_plan(
    tid: str,
    *,
    now_ns: int,
    retention_seconds: float,
) -> DeadTaskQueueCleanupPlan:
    """Return the queue cleanup policy for one proven-dead task TID."""

    stale_queue_names = standard_dead_task_stale_queue_names(tid)
    retention_eligible = is_old_enough(int(tid), now_ns, retention_seconds)
    retention_queue_names = (
        standard_dead_task_retention_queue_names(tid) if retention_eligible else ()
    )
    outbox_queue_names = tuple(
        queue_name
        for queue_name in retention_queue_names
        if queue_name.endswith(f".{QUEUE_OUTBOX_SUFFIX}")
    )
    reserved_queue_names = tuple(
        queue_name
        for queue_name in retention_queue_names
        if queue_name.endswith(f".{QUEUE_RESERVED_SUFFIX}")
    )
    return DeadTaskQueueCleanupPlan(
        queue_names=(*stale_queue_names, *retention_queue_names),
        control_queue_names=stale_queue_names[:2],
        inbox_queue_names=stale_queue_names[2:],
        outbox_queue_names=outbox_queue_names,
        reserved_queue_names=reserved_queue_names,
        retention_eligible=retention_eligible,
    )


def select_dead_task_tids_from_queue_names(
    queue_names: Iterable[str],
    *,
    live_tids: set[str],
    now_ns: int,
    min_age_seconds: float,
) -> DeadTaskTidSelection:
    """Select dead task TIDs from standard task-local queue names."""

    historical_tids = {
        tid
        for queue_name in queue_names
        if (tid := standard_task_queue_tid(queue_name)) is not None
    }
    selected: list[str] = []
    skipped_live = 0
    skipped_too_young = 0
    for tid in sorted(historical_tids, key=int):
        if tid in live_tids:
            skipped_live += 1
            continue
        if not is_old_enough(int(tid), now_ns, min_age_seconds):
            skipped_too_young += 1
            continue
        selected.append(tid)
    return DeadTaskTidSelection(
        selected_tids=tuple(selected),
        discovered_tids=len(historical_tids),
        skipped_live=skipped_live,
        skipped_too_young=skipped_too_young,
    )


def dead_task_tids_from_queue_names(
    queue_names: Iterable[str],
    *,
    live_tids: set[str],
    now_ns: int,
    min_age_seconds: float,
) -> tuple[str, ...]:
    """Return dead task TIDs from standard task-local queue names."""

    return select_dead_task_tids_from_queue_names(
        queue_names,
        live_tids=live_tids,
        now_ns=now_ns,
        min_age_seconds=min_age_seconds,
    ).selected_tids


def fetch_dead_task_log_coalesce_group(
    ctx: WeftContext,
    tid: str,
    *,
    chunk_limit: int,
) -> DeadTaskLogCoalesceGroup:
    """Fetch exact ``weft.log.tasks`` rows whose task family TID matches ``tid``."""

    if chunk_limit <= 0:
        return DeadTaskLogCoalesceGroup(tid=tid, rows=(), api_matches=0)
    effective_limit = min(chunk_limit, 1000)

    rows: list[QueueWindowRow] = []
    api_matches = 0
    after_timestamp: int | None = None
    with ctx.broker() as broker:
        while True:
            message_ids = broker.find_message_ids(
                WEFT_GLOBAL_LOG_QUEUE,
                body_contains=tid,
                limit=effective_limit,
                after_timestamp=after_timestamp,
                include_claimed=True,
            )
            if not message_ids:
                break
            api_matches += len(message_ids)
            after_timestamp = max(int(message_id) for message_id in message_ids)
            for message_id in message_ids:
                row = _task_log_row_for_message_id_including_claimed(
                    broker,
                    int(message_id),
                )
                if row is not None and _row_belongs_to_tid(row, tid):
                    rows.append(row)
            if len(message_ids) < effective_limit:
                break
    return DeadTaskLogCoalesceGroup(
        tid=tid,
        rows=tuple(rows),
        api_matches=api_matches,
    )


def _task_log_row_for_message_id_including_claimed(
    broker: Any,
    message_id: int,
) -> QueueWindowRow | None:
    row = broker.peek_one(
        WEFT_GLOBAL_LOG_QUEUE,
        exact_timestamp=message_id,
        with_timestamps=True,
        include_claimed=True,
    )
    if row is None:
        return None
    body, timestamp = row
    return QueueWindowRow(
        queue=WEFT_GLOBAL_LOG_QUEUE,
        body=body if isinstance(body, str) else str(body),
        message_id=int(timestamp),
    )


def _row_belongs_to_tid(row: QueueWindowRow, tid: str) -> bool:
    try:
        payload = json.loads(row.body)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, Mapping):
        return False
    return payload.get("tid") == tid
