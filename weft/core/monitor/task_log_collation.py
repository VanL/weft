"""Task-log lifecycle collation.

This module groups task-log rows into completed lifecycle records. It performs
no deletion, archiving, or logging side effects; callers decide what action to
take with the returned exact message rows.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.13], [OBS.17]
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from weft._constants import TERMINAL_TASK_EVENTS, TERMINAL_TASK_STATUSES
from weft.core.queue_window import (
    DecodedQueueWindowRow,
    is_old_enough,
    payload_string,
)


@dataclass(frozen=True, slots=True)
class CollatedMessageGroup:
    """One completed task-log lifecycle group from a bounded window."""

    tid: str
    message_rows: tuple[DecodedQueueWindowRow, ...]
    first_message_id: int
    terminal_message_id: int
    terminal_event: str | None = None
    terminal_status: str | None = None

    @property
    def message_ids(self) -> tuple[int, ...]:
        """Exact message IDs in this lifecycle group."""

        return tuple(row.raw.message_id for row in self.message_rows)

    @property
    def rows(self) -> int:
        """Number of rows in this lifecycle group."""

        return len(self.message_rows)

    def to_summary(self) -> dict[str, Any]:
        """Return a JSON-safe operational summary."""

        return {
            "tid": self.tid,
            "rows": self.rows,
            "first_message_id": self.first_message_id,
            "terminal_message_id": self.terminal_message_id,
            "terminal_event": self.terminal_event,
            "terminal_status": self.terminal_status,
        }


@dataclass(frozen=True, slots=True)
class CollationResult:
    """Result of one task-log collation attempt."""

    group: CollatedMessageGroup | None = None
    stop_reason: str | None = None
    skipped_tid: str | None = None


def collate_next_task_log_group(
    rows: Sequence[DecodedQueueWindowRow],
    *,
    now_ns: int,
    min_age_seconds: float,
    exclude_tids: set[str],
    claimed_ids: set[int],
    skipped_tids: set[str],
    skipped_message_ids: set[int] | None = None,
) -> CollationResult:
    """Return the next completed task-log group from a bounded window."""

    skipped_ids = skipped_message_ids or set()
    anchor_index: int | None = None
    anchor_tid: str | None = None
    for index, row in enumerate(rows):
        if (
            row.raw.message_id in claimed_ids
            or row.raw.message_id in skipped_ids
            or row.malformed_reason is not None
        ):
            continue
        tid = row.tid
        if tid is None or tid in exclude_tids or tid in skipped_tids:
            continue
        anchor_index = index
        anchor_tid = tid
        break

    if anchor_index is None or anchor_tid is None:
        return CollationResult()
    anchor = rows[anchor_index]
    if not is_old_enough(anchor.raw.message_id, now_ns, min_age_seconds):
        return CollationResult(stop_reason="task_log_anchor_too_young_for_collate")

    collected: list[DecodedQueueWindowRow] = []
    terminal_row: DecodedQueueWindowRow | None = None
    for row in rows[anchor_index:]:
        if (
            row.raw.message_id in claimed_ids
            or row.raw.message_id in skipped_ids
            or row.malformed_reason is not None
        ):
            continue
        if row.tid != anchor_tid:
            continue
        collected.append(row)
        if is_terminal_task_log(row.payload):
            terminal_row = row
            break

    if terminal_row is None:
        return CollationResult(
            stop_reason="collate_anchor_without_terminal",
            skipped_tid=anchor_tid,
        )

    return CollationResult(
        group=CollatedMessageGroup(
            tid=anchor_tid,
            message_rows=tuple(collected),
            first_message_id=collected[0].raw.message_id,
            terminal_message_id=terminal_row.raw.message_id,
            terminal_event=payload_string(terminal_row.payload, "event"),
            terminal_status=payload_string(terminal_row.payload, "status"),
        )
    )


def is_terminal_task_log(payload: Mapping[str, Any] | None) -> bool:
    """Return whether a decoded task-log payload is terminal evidence."""

    if payload is None:
        return False
    event = payload.get("event")
    status = payload.get("status")
    if isinstance(event, str):
        return event in TERMINAL_TASK_EVENTS
    return isinstance(status, str) and status in TERMINAL_TASK_STATUSES
