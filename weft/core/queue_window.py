"""Bounded queue-window primitives.

These types describe rows read from broker queues without assigning cleanup,
logging, or task-monitor semantics to them. They are intentionally neutral so
future logging actions do not need to depend on pruning modules.

Spec references:
- docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.13], [OBS.16]
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from simplebroker.ext import BrokerError
from weft.context import WeftContext
from weft.helpers import closing_queue_iterator

logger = logging.getLogger(__name__)


@contextmanager
def queue_broker(
    ctx: WeftContext,
    queue_name: str,
    *,
    broker: Any | None = None,
    persistent: bool = True,
) -> Iterator[Any]:
    """Borrow an active caller scope or own one bounded queue connection [SB-0.4].

    Borrowed brokers must match the context's target/configuration and remain
    on their owning thread. The caller keeps ownership; this helper opens no
    transaction and never closes a borrowed connection.
    """

    if broker is not None:
        yield broker
        return
    with (
        ctx.queue(queue_name, persistent=persistent) as queue,
        queue.get_connection() as db,
    ):
        yield db


def iter_broker_queue_entries(
    broker: Any,
    queue_name: str,
    *,
    since_timestamp: int | None = None,
    before_timestamp: int | None = None,
    strict: bool = False,
) -> Iterator[tuple[str, int]]:
    """Read named history without a facade, preserving queue-entry filtering."""

    try:
        entries = broker.peek_generator(
            queue_name,
            with_timestamps=True,
            after_timestamp=since_timestamp,
            before_timestamp=before_timestamp,
        )
    except (BrokerError, OSError, RuntimeError):
        if strict:
            raise
        logger.debug("Failed to open queue generator for %s", queue_name, exc_info=True)
        return
    with closing_queue_iterator(entries) as rows:
        for entry in rows:
            if not isinstance(entry, tuple) or len(entry) != 2:
                continue
            body, timestamp = entry
            try:
                message_id = int(timestamp)
            except (TypeError, ValueError):
                continue
            if since_timestamp is not None and message_id <= since_timestamp:
                continue
            if before_timestamp is not None and message_id >= before_timestamp:
                continue
            yield str(body), message_id


def iter_broker_queue_json_entries(
    broker: Any,
    queue_name: str,
    *,
    since_timestamp: int | None = None,
    before_timestamp: int | None = None,
    strict: bool = False,
) -> Iterator[tuple[dict[str, Any], int]]:
    """Decode named history, skipping malformed JSON and non-object payloads."""

    with closing_queue_iterator(
        iter_broker_queue_entries(
            broker,
            queue_name,
            since_timestamp=since_timestamp,
            before_timestamp=before_timestamp,
            strict=strict,
        )
    ) as rows:
        for body, message_id in rows:
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                yield payload, message_id


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
    persistent: bool = True,
    broker: Any | None = None,
) -> tuple[QueueWindowRow, ...]:
    """Read at most ``limit`` rows from a queue without consuming them."""

    rows: list[QueueWindowRow] = []
    with (
        queue_broker(ctx, queue_name, broker=broker, persistent=persistent) as db,
        closing_queue_iterator(iter_broker_queue_entries(db, queue_name)) as entries,
    ):
        for body, message_id in entries:
            rows.append(
                QueueWindowRow(
                    queue=queue_name,
                    body=body,
                    message_id=int(message_id),
                )
            )
            if len(rows) >= limit:
                break
    return tuple(rows)


def message_age_seconds(message_id: int, now_ns: int) -> float:
    """Return a broker message ID's non-negative age in seconds."""

    return max(0.0, (now_ns - int(message_id)) / 1_000_000_000)


def is_old_enough(message_id: int, now_ns: int, min_age_seconds: float) -> bool:
    """Return whether a broker message ID is at least ``min_age_seconds`` old."""

    if min_age_seconds <= 0:
        return True
    return message_age_seconds(message_id, now_ns) >= min_age_seconds


def payload_string(payload: Mapping[str, Any] | None, key: str) -> str | None:
    """Return a non-empty string value from a decoded payload."""

    if payload is None:
        return None
    value = payload.get(key)
    return value if isinstance(value, str) and value else None
