"""Real-broker task-state reader contracts [OBS.6], [LIVENESS.R3]."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft.context import WeftContext, build_context
from weft.core import task_state
from weft.core.task_state import (
    latest_task_state_rows,
    list_task_state_tids,
    read_task_state_snapshot,
    task_state_queue_name,
)

pytestmark = pytest.mark.shared


def test_newest_valid_snapshot_is_local_and_suffix_bound(tmp_path: Path) -> None:
    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    tid = "1760000000123456789"
    other = "1760000000987654321"
    queue = ctx.queue(task_state_queue_name(tid), persistent=True)
    try:
        good = {"full": tid, "short": "display", "terminal": False}
        message_id = queue.write(json.dumps(good))
        queue.write(json.dumps({"full": other, "short": "wrong"}))
        for _ in range(1050):
            queue.write("malformed")
        assert len(queue.peek_many(limit=1100)) == 1052
    finally:
        queue.close()
    assert read_task_state_snapshot(ctx, tid) == (message_id, good)
    assert latest_task_state_rows(ctx, [tid]) == {tid: (message_id, good)}
    assert read_task_state_snapshot(ctx, other) is None


def test_names_include_malformed_only_entries_without_payload_reads(
    tmp_path: Path,
) -> None:
    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    tid = "1760000000123456789"
    for name in (
        task_state_queue_name(tid),
        "weft.state.tasks.bad",
        "weft.state.tasks.123",
    ):
        queue = ctx.queue(name)
        try:
            queue.write("malformed")
        finally:
            queue.close()
    assert list_task_state_tids(ctx) == [tid]
    assert latest_task_state_rows(ctx, []) == {}
    assert latest_task_state_rows(ctx) == {}


def test_invalid_candidates_are_absent_without_opening_broker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = build_context(spec_context=prepare_project_root(tmp_path))

    def forbidden_open(self: object) -> None:
        raise AssertionError("empty candidate selection must not open broker")

    monkeypatch.setattr(type(ctx), "broker", forbidden_open)
    assert latest_task_state_rows(ctx, ["malformed", "123", "１２３"]) == {}
    for tid in ("malformed", "123", "１２３", "１" * 19, "", "1" * 20):
        assert read_task_state_snapshot(ctx, tid) is None
        with pytest.raises(ValueError, match="19-digit"):
            task_state_queue_name(tid)


def test_unchanged_snapshot_reuses_validated_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    tid = "1760000000123456789"
    queue = ctx.queue(task_state_queue_name(tid))
    try:
        queue.write(json.dumps({"full": tid, "short": "display"}))
        previous = read_task_state_snapshot(ctx, tid)
        assert previous is not None

        def forbidden_decode(*args: object, **kwargs: object) -> None:
            raise AssertionError("unchanged raw message ID must not be decoded again")

        monkeypatch.setattr(task_state, "decode_tid_mapping_row", forbidden_decode)
        assert read_task_state_snapshot(ctx, tid, previous=previous) is previous
    finally:
        queue.close()


@pytest.mark.parametrize("operation", ["open", "list", "first", "pagination"])
def test_task_state_backend_failures_remain_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """A failed state observation never becomes evidence that its owner is absent."""
    context = build_context(spec_context=prepare_project_root(tmp_path))
    tid = "1770000000000000300"

    class FailedBroker:
        calls = 0

        def list_queues(self, **kwargs: Any) -> list[str]:
            raise RuntimeError("injected state backend failure")

        def peek_many(self, *args: Any, **kwargs: Any) -> list[tuple[str, int]]:
            self.calls += 1
            if operation == "pagination" and self.calls == 1:
                return [("{broken-json", 100)]
            raise RuntimeError("injected state backend failure")

    @contextmanager
    def failed_broker(self: WeftContext) -> Iterator[FailedBroker]:
        if operation == "open":
            raise RuntimeError("injected state backend failure")
        yield FailedBroker()

    monkeypatch.setattr(WeftContext, "broker", failed_broker)
    with pytest.raises(RuntimeError, match="injected state backend failure"):
        if operation == "list":
            latest_task_state_rows(context)
        else:
            read_task_state_snapshot(context, tid)
