"""Task-log scan and family selection primitives.

This module owns non-consuming task-log row discovery and completed lifecycle
selection. It does not delete rows and it does not emit lifecycle summaries;
callers decide what action to take with selected message IDs.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.13], [OBS.17]
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

from weft._constants import (
    TASK_LOG_START_EVENTS,
    TASK_MONITOR_TASK_LOG_SCAN_LIMIT_REACHED,
    TASK_MONITOR_TASK_LOG_SELECTION_LIMIT_REACHED,
)
from weft.context import WeftContext
from weft.core.monitor.task_log_collation import (
    CollatedMessageGroup,
    is_terminal_task_log,
)
from weft.core.queue_window import (
    DecodedQueueWindowRow,
    QueueWindowRow,
    is_old_enough,
    payload_string,
)
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
class TaskLogSkippedFamilySummary:
    """Summary of an open task-log family left in place by selection."""

    tid: str
    first_message_id: int
    rows: int
    start_rows: int
    terminal_rows: int
    reason: str

    def to_summary(self) -> dict[str, Any]:
        """Return a JSON-safe operational summary."""

        return {
            "tid": self.tid,
            "first_message_id": self.first_message_id,
            "rows": self.rows,
            "start_rows": self.start_rows,
            "terminal_rows": self.terminal_rows,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class TaskLogFamilySelection:
    """Completed task-log families selected from a scan window."""

    complete_lifecycle_groups: tuple[CollatedMessageGroup, ...] = ()
    terminal_without_start_groups: tuple[CollatedMessageGroup, ...] = ()
    claimed_message_ids: frozenset[int] = frozenset()
    stop_reason: str | None = None
    skipped_open_families: tuple[TaskLogSkippedFamilySummary, ...] = ()

    @property
    def selected_rows(self) -> int:
        """Number of task-log rows selected by family policies."""

        return len(self.claimed_message_ids)

    def to_summary(
        self,
        *,
        skipped_open_family_limit: int | None = None,
    ) -> dict[str, Any]:
        """Return a JSON-safe operational summary."""

        skipped_open_families = self.skipped_open_families
        if skipped_open_family_limit is not None:
            skipped_open_families = skipped_open_families[:skipped_open_family_limit]
        return {
            "selected_rows": self.selected_rows,
            "complete_lifecycle_group_count": len(self.complete_lifecycle_groups),
            "terminal_without_start_group_count": len(
                self.terminal_without_start_groups
            ),
            "skipped_open_family_count": len(self.skipped_open_families),
            "skipped_open_families": [
                family.to_summary() for family in skipped_open_families
            ],
            "selection_stop_reason": self.stop_reason,
        }


class TaskLogScanBackend(Protocol):
    """Non-consuming task-log scan backend.

    Future SimpleBroker-specific range/index scanners can implement this
    protocol while reusing the pure family-selection logic below.
    """

    def scan_window(
        self,
        ctx: WeftContext,
        queue_name: str,
        *,
        scan_limit: int,
        since_timestamp: int | None = None,
        before_timestamp: int | None = None,
    ) -> TaskLogScanWindow:
        """Return a bounded non-consuming task-log scan window."""


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


def select_task_log_family_groups(
    rows: Sequence[DecodedQueueWindowRow],
    *,
    now_ns: int,
    min_age_seconds: float,
    exclude_tids: set[str],
    claimed_ids: Iterable[int] = (),
    selection_limit: int,
) -> TaskLogFamilySelection:
    """Select complete task-log families from a decoded scan window.

    Selection is family-oriented, not FIFO-anchor-oriented: old open starts are
    summarized and left in place, while complete families later in the scan can
    still be selected.
    """

    if selection_limit <= 0:
        return TaskLogFamilySelection(
            skipped_open_families=_summarize_open_families(
                rows,
                claimed_ids=set(claimed_ids),
                exclude_tids=exclude_tids,
            )
        )

    claimed = set(claimed_ids)
    remaining = selection_limit
    stop_reason: str | None = None
    complete_groups: list[CollatedMessageGroup] = []
    terminal_without_start_groups: list[CollatedMessageGroup] = []

    for group in _complete_lifecycle_groups(
        rows,
        now_ns=now_ns,
        min_age_seconds=min_age_seconds,
        exclude_tids=exclude_tids,
        claimed_ids=claimed,
    ):
        if group.rows > remaining:
            stop_reason = TASK_MONITOR_TASK_LOG_SELECTION_LIMIT_REACHED
            remaining = 0
            break
        complete_groups.append(group)
        claimed.update(group.message_ids)
        remaining -= group.rows

    if remaining > 0:
        for group in _terminal_without_start_groups(
            rows,
            now_ns=now_ns,
            min_age_seconds=min_age_seconds,
            exclude_tids=exclude_tids,
            claimed_ids=claimed,
        ):
            if group.rows > remaining:
                stop_reason = TASK_MONITOR_TASK_LOG_SELECTION_LIMIT_REACHED
                remaining = 0
                break
            terminal_without_start_groups.append(group)
            claimed.update(group.message_ids)
            remaining -= group.rows
            if remaining <= 0:
                break

    return TaskLogFamilySelection(
        complete_lifecycle_groups=tuple(complete_groups),
        terminal_without_start_groups=tuple(terminal_without_start_groups),
        claimed_message_ids=frozenset(
            message_id
            for group in (*complete_groups, *terminal_without_start_groups)
            for message_id in group.message_ids
        ),
        stop_reason=stop_reason,
        skipped_open_families=_summarize_open_families(
            rows,
            claimed_ids=claimed,
            exclude_tids=exclude_tids,
        ),
    )


def _complete_lifecycle_groups(
    rows: Sequence[DecodedQueueWindowRow],
    *,
    now_ns: int,
    min_age_seconds: float,
    exclude_tids: set[str],
    claimed_ids: set[int],
) -> tuple[CollatedMessageGroup, ...]:
    rows_by_tid = _available_old_rows_by_tid(
        rows,
        now_ns=now_ns,
        min_age_seconds=min_age_seconds,
        exclude_tids=exclude_tids,
        claimed_ids=claimed_ids,
    )
    groups: list[CollatedMessageGroup] = []
    for tid, family_rows in rows_by_tid.items():
        index = 0
        while index < len(family_rows):
            while index < len(family_rows) and not _row_has_task_log_start(
                family_rows[index]
            ):
                index += 1
            if index >= len(family_rows):
                break

            collected: list[DecodedQueueWindowRow] = []
            terminal_row: DecodedQueueWindowRow | None = None
            while index < len(family_rows):
                row = family_rows[index]
                collected.append(row)
                index += 1
                if is_terminal_task_log(row.payload):
                    terminal_row = row
                    break

            if terminal_row is None:
                break
            groups.append(
                CollatedMessageGroup(
                    tid=tid,
                    message_rows=tuple(collected),
                    first_message_id=collected[0].raw.message_id,
                    terminal_message_id=terminal_row.raw.message_id,
                    terminal_event=payload_string(terminal_row.payload, "event"),
                    terminal_status=payload_string(terminal_row.payload, "status"),
                )
            )
    return tuple(sorted(groups, key=lambda group: group.first_message_id))


def _available_old_rows_by_tid(
    rows: Sequence[DecodedQueueWindowRow],
    *,
    now_ns: int,
    min_age_seconds: float,
    exclude_tids: set[str],
    claimed_ids: set[int],
) -> dict[str, list[DecodedQueueWindowRow]]:
    rows_by_tid: dict[str, list[DecodedQueueWindowRow]] = {}
    for row in rows:
        if not _available_task_log_row(row, claimed_ids, exclude_tids):
            continue
        if not is_old_enough(row.raw.message_id, now_ns, min_age_seconds):
            continue
        tid = row.tid
        if tid is None:
            continue
        rows_by_tid.setdefault(tid, []).append(row)
    return rows_by_tid


def _terminal_without_start_groups(
    rows: Sequence[DecodedQueueWindowRow],
    *,
    now_ns: int,
    min_age_seconds: float,
    exclude_tids: set[str],
    claimed_ids: set[int],
) -> tuple[CollatedMessageGroup, ...]:
    groups: list[CollatedMessageGroup] = []
    open_started_tids: set[str] = set()
    for row in rows:
        if not _available_task_log_row(row, claimed_ids, exclude_tids):
            continue
        if not is_old_enough(row.raw.message_id, now_ns, min_age_seconds):
            continue
        tid = row.tid
        if tid is None:
            continue
        if _row_has_task_log_start(row):
            open_started_tids.add(tid)
        if not is_terminal_task_log(row.payload):
            continue
        if tid in open_started_tids:
            continue
        groups.append(
            CollatedMessageGroup(
                tid=tid,
                message_rows=(row,),
                first_message_id=row.raw.message_id,
                terminal_message_id=row.raw.message_id,
                terminal_event=payload_string(row.payload, "event"),
                terminal_status=payload_string(row.payload, "status"),
            )
        )
    return tuple(groups)


def _summarize_open_families(
    rows: Sequence[DecodedQueueWindowRow],
    *,
    claimed_ids: set[int],
    exclude_tids: set[str],
) -> tuple[TaskLogSkippedFamilySummary, ...]:
    by_tid: dict[str, list[DecodedQueueWindowRow]] = {}
    for row in rows:
        if not _available_task_log_row(row, claimed_ids, exclude_tids):
            continue
        tid = row.tid
        if tid is None:
            continue
        by_tid.setdefault(tid, []).append(row)

    summaries: list[TaskLogSkippedFamilySummary] = []
    for tid, family_rows in by_tid.items():
        start_rows = sum(1 for row in family_rows if _row_has_task_log_start(row))
        terminal_rows = sum(
            1 for row in family_rows if is_terminal_task_log(row.payload)
        )
        if start_rows == 0 or terminal_rows > 0:
            continue
        summaries.append(
            TaskLogSkippedFamilySummary(
                tid=tid,
                first_message_id=family_rows[0].raw.message_id,
                rows=len(family_rows),
                start_rows=start_rows,
                terminal_rows=terminal_rows,
                reason="open_start_without_terminal_in_scan",
            )
        )
    return tuple(summaries)


def _available_task_log_row(
    row: DecodedQueueWindowRow,
    claimed_ids: set[int],
    exclude_tids: set[str],
) -> bool:
    if row.raw.message_id in claimed_ids or row.malformed_reason is not None:
        return False
    tid = row.tid
    return tid is not None and tid not in exclude_tids


def _row_has_task_log_start(row: DecodedQueueWindowRow) -> bool:
    return payload_string(row.payload, "event") in TASK_LOG_START_EVENTS
