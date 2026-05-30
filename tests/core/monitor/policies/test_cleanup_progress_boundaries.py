"""Tests for cleanup policy progress at FIFO age boundaries."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import cast

import pytest

from weft._constants import (
    QUEUE_RESERVED_SUFFIX,
    TASK_MONITOR_POLICY_RUNTIME_STATE_RETENTION,
    TASK_MONITOR_POLICY_TASK_LOCAL_TERMINAL_RUNTIME,
    WEFT_TID_MAPPINGS_QUEUE,
)
from weft.context import WeftContext
from weft.core.monitor.policies.reserved import terminal_reserved_candidates
from weft.core.monitor.policies.tid_mapping import (
    decode_tid_mapping_row,
    tid_mapping_candidates,
)
from weft.core.monitor.task_log_collation import CollatedMessageGroup
from weft.core.queue_window import QueueWindowRow

pytestmark = [pytest.mark.shared]


def _json_row(queue: str, message_id: int, payload: object) -> QueueWindowRow:
    return QueueWindowRow(
        queue=queue,
        message_id=message_id,
        body=json.dumps(payload),
    )


def test_tid_mapping_progress_reaches_base_after_too_young_boundary() -> None:
    """Old selected rows followed by a too-young mapping are base-for-now."""

    old_id = 1_778_000_000_000_000_000
    second_old_id = old_id + 1
    young_id = old_id + 10_000_000_000
    rows = tuple(
        decode_tid_mapping_row(row)
        for row in (
            _json_row(
                WEFT_TID_MAPPINGS_QUEUE,
                old_id,
                {"short": "0000000001", "full": str(old_id)},
            ),
            _json_row(
                WEFT_TID_MAPPINGS_QUEUE,
                second_old_id,
                {"short": "0000000002", "full": str(second_old_id)},
            ),
            _json_row(
                WEFT_TID_MAPPINGS_QUEUE,
                young_id,
                {"short": "0000000003", "full": str(young_id)},
            ),
        )
    )

    candidates, queue_stats, policy_stats, progress = tid_mapping_candidates(
        rows,
        now_ns=young_id + 1_000_000_000,
        min_age_seconds=2.0,
        exclude_tids=set(),
        scan_limit_reached=True,
    )

    assert [candidate.message_id for candidate in candidates] == [
        old_id,
        second_old_id,
    ]
    assert queue_stats.stop_reason == "first_tid_mapping_too_young"
    assert policy_stats[0].stop_reason == "first_tid_mapping_too_young"
    assert progress[0].policy == TASK_MONITOR_POLICY_RUNTIME_STATE_RETENTION
    assert progress[0].selected == 2
    assert progress[0].waypoint_reached is False
    assert progress[0].base_reached is True


def test_reserved_progress_reaches_base_after_too_young_boundary() -> None:
    """Selected reserved rows followed by a too-young row are base-for-now."""

    tid = "1778000000000000001"
    queue_name = f"T{tid}.{QUEUE_RESERVED_SUFFIX}"
    old_id = 1_778_000_000_000_000_000
    second_old_id = old_id + 1
    young_id = old_id + 10_000_000_000
    reserved_rows = (
        _json_row(queue_name, old_id, {"payload": "old"}),
        _json_row(queue_name, second_old_id, {"payload": "also-old"}),
        _json_row(queue_name, young_id, {"payload": "young"}),
    )

    def scan_queue_window(
        _ctx: WeftContext,
        requested_queue_name: str,
        *,
        limit: int,
    ) -> Sequence[QueueWindowRow]:
        assert requested_queue_name == queue_name
        return reserved_rows[:limit]

    group = CollatedMessageGroup(
        tid=tid,
        message_rows=(),
        first_message_id=old_id,
        terminal_message_id=old_id,
        terminal_event="work_failed",
        terminal_status="failed",
    )

    candidates, queue_stats, policy_stats, progress = terminal_reserved_candidates(
        cast(WeftContext, object()),
        (group,),
        now_ns=young_id + 1_000_000_000,
        min_age_seconds=2.0,
        selection_limit=10,
        scan_queue_window_fn=scan_queue_window,
    )

    assert [candidate.message_id for candidate in candidates] == [
        old_id,
        second_old_id,
    ]
    assert queue_stats[0].stop_reason == "first_reserved_row_too_young"
    assert policy_stats[0].stop_reason == "first_reserved_row_too_young"
    assert progress[0].policy == TASK_MONITOR_POLICY_TASK_LOCAL_TERMINAL_RUNTIME
    assert progress[0].selected == 2
    assert progress[0].waypoint_reached is False
    assert progress[0].base_reached is True
