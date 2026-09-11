"""Broker-backed per-task runtime snapshot readers.

Read policy remains in the broker-free liveness policy module. These helpers
never delete state; LivenessMonitor owns retirement.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.6], [LIVENESS.R3]
- docs/specifications/10-CLI_Interface.md [CLI-1.2.3]
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import Any, cast

from weft._constants import WEFT_TASK_STATE_QUEUE_PREFIX
from weft.context import WeftContext
from weft.core.queue_window import DecodedQueueWindowRow, QueueWindowRow
from weft.helpers import closing_queue_iterator
from weft.helpers.message_ids import is_task_tid
from weft.liveness.policy import decode_tid_mapping_row


def task_state_queue_name(tid: str) -> str:
    """Name a task's state queue without broker I/O (Spec: [OBS.6], [QUEUE.7])."""
    if not is_task_tid(tid):
        raise ValueError("tid must be a 19-digit decimal task ID")
    return f"{WEFT_TASK_STATE_QUEUE_PREFIX}{tid}"


@contextmanager
def _state_broker(ctx: WeftContext, broker: Any | None) -> Iterator[Any]:
    """Share one broker scope and preserve acquisition and read failures."""
    if broker is not None:
        yield broker
        return
    with ctx.broker() as opened:
        yield opened


def list_task_state_tids(ctx: WeftContext, *, broker: Any | None = None) -> list[str]:
    """List syntactically valid retained task names without reading JSON [OBS.6]."""
    with _state_broker(ctx, broker) as db:
        tids = []
        for name in db.list_queues(prefix=WEFT_TASK_STATE_QUEUE_PREFIX):
            tid = name.removeprefix(WEFT_TASK_STATE_QUEUE_PREFIX)
            if is_task_tid(tid):
                tids.append(tid)
        return tids


def read_task_state_snapshot(
    ctx: WeftContext,
    tid: str,
    *,
    broker: Any | None = None,
    previous: tuple[int, dict[str, Any]] | None = None,
) -> tuple[int, dict[str, Any]] | None:
    """Read newest valid suffix-bound state, including behind malformed tails.

    ``previous`` is the caller's already validated snapshot for this TID.
    Immutable broker message IDs let a monitor reuse it without decoding again.
    Non-task selectors have no snapshot and return None without broker I/O.

    Spec: docs/specifications/07-System_Invariants.md [OBS.6]
    """
    if not is_task_tid(tid):
        return None
    name = task_state_queue_name(tid)
    with _state_broker(ctx, broker) as db:
        rows = db.peek_many(name, limit=1, order="newest", with_timestamps=True)
        if rows and previous is not None and int(rows[0][1]) == previous[0]:
            return previous
        while rows:
            for body, message_id in rows:
                decoded = decode_tid_mapping_row(
                    QueueWindowRow(name, body, int(message_id)), expected_tid=tid
                )
                if decoded.malformed_reason is None and decoded.payload is not None:
                    return int(message_id), decoded.payload
            rows = db.peek_many(
                name,
                order="newest",
                with_timestamps=True,
                before_timestamp=int(rows[-1][1]),
            )
    return None


def latest_task_state_rows(
    ctx: WeftContext,
    tids: Iterable[str] | None = None,
    *,
    broker: Any | None = None,
) -> dict[str, tuple[int, dict[str, Any]]]:
    """Read current snapshots; malformed candidate owner IDs have no state [OBS.6]."""
    selected = (
        tuple(dict.fromkeys(tid for tid in tids if is_task_tid(tid)))
        if tids is not None
        else None
    )
    if selected == ():
        return {}
    with _state_broker(ctx, broker) as db:
        if selected is None:
            selected = tuple(list_task_state_tids(ctx, broker=db))
        result = {}
        for tid in selected:
            row = read_task_state_snapshot(ctx, tid, broker=db)
            if row is not None:
                result[tid] = row
        return result


def iter_task_state_rows(
    ctx: WeftContext, tid: str, *, broker: Any | None = None
) -> Iterator[DecodedQueueWindowRow]:
    """Yield strict oldest-first history; close before custodian mutation [MF-5]."""
    name = task_state_queue_name(tid)
    with _state_broker(ctx, broker) as db:
        entries = db.peek_generator(name, with_timestamps=True)
        with closing_queue_iterator(entries) as rows:
            for body, message_id in cast(Iterable[tuple[str, int]], rows):
                yield decode_tid_mapping_row(
                    QueueWindowRow(name, body, int(message_id)), expected_tid=tid
                )
