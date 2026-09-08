"""Non-consuming task-log discovery for store ingestion and raw emission.

Rows are decoded without claiming or deleting them. The live monitor paths
own checkpointing, emission, and deletion after scanning.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.13], [OBS.17]
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

from weft._constants import TASK_MONITOR_TASK_LOG_SCAN_LIMIT_REACHED
from weft.context import WeftContext
from weft.core.queue_window import DecodedQueueWindowRow, QueueWindowRow
from weft.helpers import iter_queue_entries


@dataclass(frozen=True, slots=True)
class TaskLogScanWindow:
    """Decoded rows from one non-consuming task-log scan."""

    rows: tuple[DecodedQueueWindowRow, ...]
    scanned: int
    scan_limit: int
    scan_limit_reached: bool

    @property
    def stop_reason(self) -> str | None:
        """Return the scan stop reason when the configured limit was reached."""

        if self.scan_limit_reached:
            return TASK_MONITOR_TASK_LOG_SCAN_LIMIT_REACHED
        return None

    def to_summary(self) -> dict[str, Any]:
        """Return a JSON-safe operational summary."""

        return {
            "scan_limit": self.scan_limit,
            "scan_limit_reached": self.scan_limit_reached,
        }


@dataclass(frozen=True, slots=True)
class GeneratorTaskLogScanner:
    """Task-log scanner backed by the public SimpleBroker generator API."""

    persistent: bool = False

    def scan_window(
        self,
        ctx: WeftContext,
        queue_name: str,
        *,
        scan_limit: int,
        since_timestamp: int | None = None,
        before_timestamp: int | None = None,
    ) -> TaskLogScanWindow:
        """Return a bounded decoded task-log window from FIFO queue iteration."""

        if scan_limit <= 0:
            raise ValueError("task-log scan_limit must be positive")

        queue = ctx.queue(queue_name, persistent=self.persistent)
        rows: list[DecodedQueueWindowRow] = []
        scan_limit_reached = False
        try:
            for body, message_id in iter_queue_entries(
                queue,
                since_timestamp=since_timestamp,
                before_timestamp=before_timestamp,
            ):
                if len(rows) >= scan_limit:
                    scan_limit_reached = True
                    break
                raw = QueueWindowRow(
                    queue=queue_name,
                    body=body,
                    message_id=int(message_id),
                )
                rows.append(decode_task_log_row(raw))
        finally:
            queue.close()

        return TaskLogScanWindow(
            rows=tuple(rows),
            scanned=len(rows),
            scan_limit=scan_limit,
            scan_limit_reached=scan_limit_reached,
        )


def decode_task_log_row(row: QueueWindowRow) -> DecodedQueueWindowRow:
    """Decode one task-log row and classify malformed task-log payloads."""

    try:
        payload = json.loads(row.body)
    except json.JSONDecodeError:
        return DecodedQueueWindowRow(
            raw=row,
            payload=None,
            malformed_reason="invalid_json",
        )
    if not isinstance(payload, dict):
        return DecodedQueueWindowRow(
            raw=row,
            payload=None,
            malformed_reason="json_not_object",
        )

    payload_dict = cast(dict[str, Any], payload)
    tid = payload_dict.get("tid")
    if not isinstance(tid, str) or not tid:
        return DecodedQueueWindowRow(
            raw=row,
            payload=payload_dict,
            malformed_reason="invalid_task_log_shape",
        )
    return DecodedQueueWindowRow(raw=row, payload=payload_dict)
