"""Tests for task-log scan and family selection primitives."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft._constants import WEFT_GLOBAL_LOG_QUEUE
from weft.context import WeftContext, build_context
from weft.core.monitor.task_log_scanner import (
    GeneratorTaskLogScanner,
    select_task_log_family_groups,
)
from weft.helpers import iter_queue_entries

pytestmark = [pytest.mark.shared]


def _context(tmp_path: Path) -> WeftContext:
    root = prepare_project_root(tmp_path)
    return build_context(spec_context=root)


def _write_json(ctx: WeftContext, queue_name: str, payload: dict[str, Any]) -> int:
    body = json.dumps(payload)
    queue = ctx.queue(queue_name, persistent=False)
    try:
        queue.write(body)
        latest: int | None = None
        for row, message_id in iter_queue_entries(queue):
            if row == body:
                latest = int(message_id)
        assert latest is not None
        return latest
    finally:
        queue.close()


def _now_after(message_id: int, seconds: float) -> int:
    return message_id + int(seconds * 1_000_000_000) + 1


def test_task_log_scanner_selects_complete_family_behind_open_prefix(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    open_tid = "1778000000000000001"
    complete_tid = "1778000000000000002"
    _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_started", "tid": open_tid},
    )
    _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "task_activity", "tid": open_tid, "activity": "waiting"},
    )
    _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_started", "tid": complete_tid},
    )
    complete_terminal_id = _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_completed", "status": "completed", "tid": complete_tid},
    )

    scanner = GeneratorTaskLogScanner()
    window = scanner.scan_window(ctx, WEFT_GLOBAL_LOG_QUEUE, scan_limit=10)
    selection = select_task_log_family_groups(
        window.rows,
        now_ns=_now_after(complete_terminal_id, 2.0),
        min_age_seconds=1.0,
        exclude_tids=set(),
        selection_limit=10,
    )

    assert [group.tid for group in selection.complete_lifecycle_groups] == [
        complete_tid
    ]
    assert selection.terminal_without_start_groups == ()
    assert [family.tid for family in selection.skipped_open_families] == [open_tid]
    assert selection.stop_reason is None


def test_task_log_scanner_selects_terminal_without_visible_start(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    terminal_tid = "1778000000000000003"
    terminal_id = _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"event": "work_completed", "status": "completed", "tid": terminal_tid},
    )

    scanner = GeneratorTaskLogScanner()
    window = scanner.scan_window(ctx, WEFT_GLOBAL_LOG_QUEUE, scan_limit=10)
    selection = select_task_log_family_groups(
        window.rows,
        now_ns=_now_after(terminal_id, 2.0),
        min_age_seconds=1.0,
        exclude_tids=set(),
        selection_limit=10,
    )

    assert selection.complete_lifecycle_groups == ()
    assert [group.tid for group in selection.terminal_without_start_groups] == [
        terminal_tid
    ]
