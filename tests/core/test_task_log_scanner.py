"""Live non-consuming task-log scanner contracts [MF-5], [OBS.13]."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft._constants import (
    TASK_MONITOR_TASK_LOG_SCAN_LIMIT_REACHED,
    WEFT_GLOBAL_LOG_QUEUE,
)
from weft.context import WeftContext, build_context
from weft.core.monitor.task_log_scanner import (
    GeneratorTaskLogScanner,
    decode_task_log_row,
)
from weft.core.queue_window import QueueWindowRow
from weft.helpers import iter_queue_entries

pytestmark = [pytest.mark.shared]


def _seed(ctx: WeftContext) -> list[tuple[str, int]]:
    queue = ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    try:
        for index in range(4):
            queue.write(json.dumps({"tid": "1780000000000000000", "index": index}))
        return list(iter_queue_entries(queue))
    finally:
        queue.close()


@pytest.mark.parametrize("limit,reached", [(2, True), (4, False), (5, False)])
def test_scan_window_is_bounded_fifo_and_non_consuming(
    tmp_path: Path, limit: int, reached: bool
) -> None:
    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    before = _seed(ctx)
    window = GeneratorTaskLogScanner().scan_window(
        ctx, WEFT_GLOBAL_LOG_QUEUE, scan_limit=limit
    )
    assert [(row.raw.body, row.raw.message_id) for row in window.rows] == before[:limit]
    assert window.scanned == min(limit, len(before))
    assert window.to_summary() == {"scan_limit": limit, "scan_limit_reached": reached}
    assert window.stop_reason == (
        TASK_MONITOR_TASK_LOG_SCAN_LIMIT_REACHED if reached else None
    )
    queue = ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    try:
        assert list(iter_queue_entries(queue)) == before
        assert queue.stats().claimed == 0
    finally:
        queue.close()


def test_scan_window_respects_timestamp_bounds_and_excludes_claimed_rows(
    tmp_path: Path,
) -> None:
    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    before = _seed(ctx)
    queue = ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    try:
        assert queue.read_one(with_timestamps=True) == before[0]
        window = GeneratorTaskLogScanner().scan_window(
            ctx,
            WEFT_GLOBAL_LOG_QUEUE,
            scan_limit=10,
            since_timestamp=before[1][1],
            before_timestamp=before[3][1],
        )
        assert [row.raw.message_id for row in window.rows] == [before[2][1]]
        unbounded = GeneratorTaskLogScanner().scan_window(
            ctx, WEFT_GLOBAL_LOG_QUEUE, scan_limit=10
        )
        assert [row.raw.message_id for row in unbounded.rows] == [
            row[1] for row in before[1:]
        ]
        assert queue.stats().claimed == 1
    finally:
        queue.close()


@pytest.mark.parametrize("limit", [0, -1])
def test_scan_window_rejects_nonpositive_limit(tmp_path: Path, limit: int) -> None:
    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    with pytest.raises(ValueError, match="scan_limit must be positive"):
        GeneratorTaskLogScanner().scan_window(
            ctx, WEFT_GLOBAL_LOG_QUEUE, scan_limit=limit
        )


@pytest.mark.parametrize(
    "body,payload,reason",
    [
        ("{bad", None, "invalid_json"),
        ("[]", None, "json_not_object"),
        ("{}", {}, "invalid_task_log_shape"),
        ('{"tid": ""}', {"tid": ""}, "invalid_task_log_shape"),
        ('{"tid": 123}', {"tid": 123}, "invalid_task_log_shape"),
        ('{"tid": "1780000000000000000"}', {"tid": "1780000000000000000"}, None),
    ],
)
def test_decode_task_log_row_preserves_raw_evidence(
    body: str, payload: dict[str, Any] | None, reason: str | None
) -> None:
    raw = QueueWindowRow(
        queue=WEFT_GLOBAL_LOG_QUEUE, body=body, message_id=1780000000000000001
    )
    row = decode_task_log_row(raw)
    assert row.raw == raw
    assert row.payload == payload
    assert row.malformed_reason == reason
