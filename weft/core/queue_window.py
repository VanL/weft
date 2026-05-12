"""Bounded queue-window primitives.

These types describe rows read from broker queues without assigning cleanup,
logging, or task-monitor semantics to them. They are intentionally neutral so
future logging actions do not need to depend on pruning modules.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.13], [OBS.16]
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from weft.context import WeftContext
from weft.helpers import iter_queue_entries


@dataclass(frozen=True, slots=True)
class QueueWindowRow:
    """One broker row read from a bounded FIFO queue window."""

    queue: str
    body: str
    message_id: int


@dataclass(frozen=True, slots=True)
class ExactMessageRef:
    """Exact broker message reference usable by cleanup or logging actions."""

    queue: str
    message_id: int


@dataclass(frozen=True, slots=True)
class DecodedQueueWindowRow:
    """One queue-window row with optional decoded JSON object payload."""

    raw: QueueWindowRow
    payload: dict[str, Any] | None
    malformed_reason: str | None = None

    @property
    def tid(self) -> str | None:
        """Return a task ID when the decoded payload carries one."""

        if self.payload is None:
            return None
        value = self.payload.get("tid")
        return value if isinstance(value, str) and value else None


def scan_queue_window(
    ctx: WeftContext,
    queue_name: str,
    *,
    limit: int,
    persistent: bool = False,
) -> tuple[QueueWindowRow, ...]:
    """Read at most ``limit`` rows from a queue without consuming them."""

    queue = ctx.queue(queue_name, persistent=persistent)
    rows: list[QueueWindowRow] = []
    try:
        for body, message_id in iter_queue_entries(queue):
            rows.append(
                QueueWindowRow(
                    queue=queue_name,
                    body=body,
                    message_id=int(message_id),
                )
            )
            if len(rows) >= limit:
                break
    finally:
        queue.close()
    return tuple(rows)


def is_old_enough(message_id: int, now_ns: int, min_age_seconds: float) -> bool:
    """Return whether a broker message ID is at least ``min_age_seconds`` old."""

    if min_age_seconds <= 0:
        return True
    return max(0.0, (now_ns - int(message_id)) / 1_000_000_000) >= min_age_seconds


def payload_string(payload: Mapping[str, Any] | None, key: str) -> str | None:
    """Return a non-empty string value from a decoded payload."""

    if payload is None:
        return None
    value = payload.get(key)
    return value if isinstance(value, str) and value else None
