"""Heartbeat registration helpers.

A registration is one row on the runtime-state queue
``weft.state.heartbeats``. The helpers write or delete rows and return; they
do not look up the heartbeat endpoint, start a manager, or wait for the
service. The canonical manager launches the heartbeat service while the queue
holds a row that is unexpired or not yet rejected.

Spec references:
- docs/specifications/03-Manager_Architecture.md [MA-1] item 7
- docs/specifications/05-Message_Flow_and_State.md [MF-3.2]
"""

from __future__ import annotations

import json
from typing import Any

from weft._constants import WEFT_HEARTBEAT_REGISTRATIONS_QUEUE
from weft.context import WeftContext
from weft.core.queue_window import queue_broker
from weft.core.tasks.heartbeat import HeartbeatRegistrationRow
from weft.helpers import closing_queue_iterator


def _delete_rows_for_id(
    db: Any,
    heartbeat_id: str,
    *,
    before_message_id: int | None = None,
) -> None:
    """Delete, by exact message ID, the rows of one canonical id."""

    matching: list[int] = []
    rows = db.peek_generator(WEFT_HEARTBEAT_REGISTRATIONS_QUEUE, with_timestamps=True)
    with closing_queue_iterator(rows) as iterator:
        for body, message_id in iterator:
            if before_message_id is not None and int(message_id) >= before_message_id:
                continue
            try:
                payload = json.loads(str(body))
            except json.JSONDecodeError:
                continue
            row_id = payload.get("heartbeat_id") if isinstance(payload, dict) else None
            if isinstance(row_id, str) and row_id.strip() == heartbeat_id:
                matching.append(int(message_id))
    if matching:
        db.delete_message_ids(WEFT_HEARTBEAT_REGISTRATIONS_QUEUE, matching)


def upsert_heartbeat(
    context: WeftContext,
    *,
    registrant_tid: str,
    heartbeat_id: str,
    interval_seconds: int,
    lease_seconds: int,
    destination_queue: str,
    message: Any,
    broker: Any | None = None,
) -> None:
    """Register or replace one heartbeat as a leased row, then return.

    The row is validated, written to ``weft.state.heartbeats``, and then the
    older rows with the same ``heartbeat_id`` are deleted (write before
    delete keeps the queue non-empty across a replace). The helper waits for
    nothing: no endpoint lookup, no manager start, no poll.

    The registration lives for its lease and no longer: it expires
    ``lease_seconds`` after the row's own message timestamp, whether the
    registrant is alive, dead, or was never a Weft task. Nothing reads the
    registrant's lifecycle evidence. A registrant that wants the heartbeat to
    continue resubmits with the same ``heartbeat_id`` whenever it likes; there
    is no renewal protocol.

    Raises:
        ValueError: If the row is invalid (interval, lease, destination,
            registrant TID, id, or message).

    Spec: [MF-3.2]
    """

    row = HeartbeatRegistrationRow.model_validate(
        {
            "heartbeat_id": heartbeat_id,
            "registrant_tid": registrant_tid,
            "interval_seconds": interval_seconds,
            "lease_seconds": lease_seconds,
            "destination_queue": destination_queue,
            "message": message,
        }
    )
    body = json.dumps(row.model_dump(mode="python"), ensure_ascii=False)
    with queue_broker(context, WEFT_HEARTBEAT_REGISTRATIONS_QUEUE, broker=broker) as db:
        message_id = int(db.write(WEFT_HEARTBEAT_REGISTRATIONS_QUEUE, body))
        _delete_rows_for_id(db, row.heartbeat_id, before_message_id=message_id)


def cancel_heartbeat(
    context: WeftContext,
    *,
    heartbeat_id: str,
    broker: Any | None = None,
) -> None:
    """Delete every registration row of one canonical ``heartbeat_id``.

    The id is canonicalized exactly as the row model does; cancelling an
    unknown id is a no-op.

    Raises:
        ValueError: If ``heartbeat_id`` is empty after stripping.

    Spec: [MF-3.2]
    """

    canonical = HeartbeatRegistrationRow._validate_heartbeat_id(heartbeat_id)
    with queue_broker(context, WEFT_HEARTBEAT_REGISTRATIONS_QUEUE, broker=broker) as db:
        _delete_rows_for_id(db, canonical)


__all__ = ["cancel_heartbeat", "upsert_heartbeat"]
