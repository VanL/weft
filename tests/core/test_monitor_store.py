"""Tests for the Monitor-owned durable collation store."""

from __future__ import annotations

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft._constants import (
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_MONITOR_SCHEMA_VERSION,
)
from weft.context import build_context
from weft.core.monitor import store as monitor_store_mod
from weft.core.monitor.collation import MonitorTaskEventUpdate
from weft.core.monitor.store import (
    MonitorStoreUnavailable,
    open_monitor_store,
)

pytestmark = [pytest.mark.shared]


def _context(tmp_path):
    return build_context(spec_context=prepare_project_root(tmp_path))


def _update(
    tid: str,
    message_id: int,
    *,
    event: str = "work_started",
    status: str = "running",
    terminal: bool = False,
    reporting_interval: float | str | None = None,
) -> MonitorTaskEventUpdate:
    terminal_status = status if terminal else None
    taskspec_summary: dict[str, object] = {"tid": tid, "name": "sample"}
    if reporting_interval is not None:
        taskspec_summary["spec"] = {"reporting_interval": reporting_interval}
    return MonitorTaskEventUpdate(
        tid=tid,
        queue_name=WEFT_GLOBAL_LOG_QUEUE,
        message_id=message_id,
        event=event,
        status=status,
        observed_at_ns=message_id,
        name="sample",
        runner="host",
        terminal_seen=terminal,
        terminal_event=event if terminal else None,
        terminal_status=terminal_status,
        first_seen_at_ns=message_id,
        last_seen_at_ns=message_id,
        taskspec_summary=taskspec_summary,
        state={"status": status},
        lifecycle={"event": event, "status": status},
        resources={"peak_memory": 10},
        bookkeeping={"last_message_id": message_id},
        reserved_probe_needed=terminal and status != "completed",
    )


def _monitor_table_count(
    ctx,
    table: str,
    *,
    where: str = "",
    params: tuple[object, ...] = (),
) -> int:
    query = f"SELECT count(*) FROM {table}"
    if where:
        query = f"{query} WHERE {where}"
    with ctx.broker() as broker:
        rows = list(broker._runner.run(query, params, fetch=True))
    return int(rows[0][0])


class _FakeRunner:
    def __init__(self, *, fail_begin: bool = False, fail_inside: bool = False) -> None:
        self.fail_begin = fail_begin
        self.fail_inside = fail_inside
        self.calls: list[str] = []

    def begin_immediate(self) -> None:
        self.calls.append("begin")
        if self.fail_begin:
            raise RuntimeError("begin failed")

    def commit(self) -> None:
        self.calls.append("commit")

    def rollback(self) -> None:
        self.calls.append("rollback")

    def run(
        self,
        sql: str,
        params: tuple[object, ...] = (),
        *,
        fetch: bool = False,
    ) -> list[tuple[object, ...]]:
        del sql, params, fetch
        self.calls.append("run")
        if self.fail_inside:
            raise RuntimeError("write failed")
        return []


def test_monitor_store_write_transaction_commits_after_begin() -> None:
    runner = _FakeRunner()

    with monitor_store_mod._write_transaction(runner):
        runner.run("INSERT")

    assert runner.calls == ["begin", "run", "commit"]


def test_monitor_store_write_transaction_rolls_back_after_write_failure() -> None:
    runner = _FakeRunner(fail_inside=True)

    with pytest.raises(RuntimeError, match="write failed"):
        with monitor_store_mod._write_transaction(runner):
            runner.run("INSERT")

    assert runner.calls == ["begin", "run", "rollback"]


def test_monitor_store_write_transaction_does_not_rollback_begin_failure() -> None:
    runner = _FakeRunner(fail_begin=True)

    with pytest.raises(RuntimeError, match="begin failed"):
        with monitor_store_mod._write_transaction(runner):
            runner.run("INSERT")

    assert runner.calls == ["begin"]


def test_monitor_store_schema_creation_is_idempotent(tmp_path) -> None:
    ctx = _context(tmp_path)
    store = open_monitor_store(ctx)

    store.ensure_schema()
    store.ensure_schema()

    assert store.status().available is True
    assert store.status().schema_version == WEFT_MONITOR_SCHEMA_VERSION


def test_monitor_store_checkpoint_round_trips(tmp_path) -> None:
    ctx = _context(tmp_path)
    store = open_monitor_store(ctx)
    store.ensure_schema()

    assert store.get_checkpoint(WEFT_GLOBAL_LOG_QUEUE) is None
    store.set_checkpoint(WEFT_GLOBAL_LOG_QUEUE, 1779000000000000001)

    assert store.get_checkpoint(WEFT_GLOBAL_LOG_QUEUE) == 1779000000000000001


def test_monitor_store_upsert_is_replay_safe_and_preserves_terminal(tmp_path) -> None:
    ctx = _context(tmp_path)
    store = open_monitor_store(ctx)
    store.ensure_schema()
    tid = "1779000000000000002"
    start = _update(tid, 1779000000000001000)
    terminal = _update(
        tid,
        1779000000000002000,
        event="work_completed",
        status="completed",
        terminal=True,
    )

    store.upsert_task_event(start)
    store.upsert_task_event(terminal)
    store.upsert_task_event(start)

    record = store.get_task(tid)
    assert record is not None
    assert record.terminal_seen is True
    assert record.terminal_status == "completed"
    assert record.status == "completed"
    assert record.first_message_id == start.message_id
    assert record.last_message_id == terminal.message_id
    refs = store.list_deletable_task_log_messages(limit=10, require_summary=False)
    assert [ref.message_id for ref in refs] == [
        start.message_id,
        terminal.message_id,
    ]


def test_monitor_store_lists_deletable_task_log_messages_for_exact_tids(
    tmp_path,
) -> None:
    ctx = _context(tmp_path)
    store = open_monitor_store(ctx)
    store.ensure_schema()
    first_tid = "1779000000000000123"
    prefixed_tid = "17790000000000001230"
    other_tid = "1779000000000000124"
    updates = (
        _update(
            first_tid,
            1779000000000100001,
            event="work_completed",
            status="completed",
            terminal=True,
        ),
        _update(
            prefixed_tid,
            1779000000000100002,
            event="work_completed",
            status="completed",
            terminal=True,
        ),
        _update(
            other_tid,
            1779000000000100003,
            event="work_completed",
            status="completed",
            terminal=True,
        ),
    )
    store.record_task_log_updates(
        WEFT_GLOBAL_LOG_QUEUE,
        updates,
        checkpoint_message_id=None,
    )
    store.mark_summary_emitted(first_tid, 1779000000000100011)
    store.mark_summary_emitted(prefixed_tid, 1779000000000100012)
    store.mark_summary_emitted(other_tid, 1779000000000100013)

    refs = store.list_deletable_task_log_messages_for_tids(
        (first_tid, other_tid),
        limit=10,
        require_summary=True,
    )
    limited_refs = store.list_deletable_task_log_messages_for_tids(
        (first_tid, other_tid),
        limit=1,
        require_summary=True,
    )

    assert [(ref.tid, ref.message_id) for ref in refs] == [
        (first_tid, 1779000000000100001),
        (other_tid, 1779000000000100003),
    ]
    assert [(ref.tid, ref.message_id) for ref in limited_refs] == [
        (first_tid, 1779000000000100001)
    ]


def test_monitor_store_summary_ready_respects_terminal_retention(tmp_path) -> None:
    ctx = _context(tmp_path)
    store = open_monitor_store(ctx)
    store.ensure_schema()
    tid = "1779000000000000004"
    terminal = _update(
        tid,
        1779000000000009000,
        event="work_completed",
        status="completed",
        terminal=True,
    )
    store.record_task_log_updates(
        WEFT_GLOBAL_LOG_QUEUE,
        (terminal,),
        checkpoint_message_id=None,
    )

    too_soon = store.list_summary_ready_tasks(
        limit=10,
        now_ns=terminal.message_id + 1_000_000_000,
        retention_seconds=2.0,
    )
    retained = store.list_summary_ready_tasks(
        limit=10,
        now_ns=terminal.message_id + 3_000_000_000,
        retention_seconds=2.0,
    )

    assert too_soon == ()
    assert [(item.record.tid, item.close_reason) for item in retained] == [
        (tid, "terminal")
    ]


def test_monitor_store_summary_ready_uses_family_high_water(tmp_path) -> None:
    ctx = _context(tmp_path)
    store = open_monitor_store(ctx)
    store.ensure_schema()
    tid = "1779000000000000094"
    terminal = _update(
        tid,
        1779000000000009000,
        event="work_completed",
        status="completed",
        terminal=True,
    )
    later_activity = _update(
        tid,
        1779000005000009000,
        event="task_activity",
        status="completed",
    )
    store.record_task_log_updates(
        WEFT_GLOBAL_LOG_QUEUE,
        (terminal, later_activity),
        checkpoint_message_id=None,
    )

    ready_before_later_row_ages = store.list_summary_ready_tasks(
        limit=10,
        now_ns=later_activity.message_id + 1_000_000_000,
        retention_seconds=2.0,
    )
    ready_after_family_high_water_ages = store.list_summary_ready_tasks(
        limit=10,
        now_ns=later_activity.message_id + 3_000_000_000,
        retention_seconds=2.0,
    )

    assert ready_before_later_row_ages == ()
    assert [
        (item.record.tid, item.close_reason)
        for item in ready_after_family_high_water_ages
    ] == [(tid, "terminal")]


def test_monitor_store_summary_ready_suspects_only_known_interval(tmp_path) -> None:
    ctx = _context(tmp_path)
    store = open_monitor_store(ctx)
    store.ensure_schema()
    known_tid = "1779000000000000005"
    unknown_tid = "1779000000000000006"
    known = _update(
        known_tid,
        1779000000000010000,
        reporting_interval=1.0,
    )
    unknown = _update(unknown_tid, 1779000000000011000)
    store.record_task_log_updates(
        WEFT_GLOBAL_LOG_QUEUE,
        (known, unknown),
        checkpoint_message_id=None,
    )

    ready = store.list_summary_ready_tasks(
        limit=10,
        now_ns=known.message_id + 4_000_000_000,
        retention_seconds=2.0,
    )

    assert [(item.record.tid, item.close_reason) for item in ready] == [
        (known_tid, "suspected_inactive")
    ]


def test_monitor_store_summary_ready_classifies_stale_open_without_interval(
    tmp_path,
) -> None:
    ctx = _context(tmp_path)
    store = open_monitor_store(ctx)
    store.ensure_schema()
    tid = "1779000000000000095"
    update = _update(tid, 1779000000000012000)
    store.record_task_log_updates(
        WEFT_GLOBAL_LOG_QUEUE,
        (update,),
        checkpoint_message_id=None,
    )

    too_soon = store.list_summary_ready_tasks(
        limit=10,
        now_ns=update.message_id + 3_000_000_000,
        retention_seconds=1.0,
        stale_open_family_seconds=5.0,
    )
    stale = store.list_summary_ready_tasks(
        limit=10,
        now_ns=update.message_id + 6_000_000_000,
        retention_seconds=1.0,
        stale_open_family_seconds=5.0,
    )

    assert too_soon == ()
    assert [(item.record.tid, item.close_reason) for item in stale] == [
        (tid, "stale_open")
    ]


def test_monitor_store_disposition_tombstone_removes_family_from_ready_list(
    tmp_path,
) -> None:
    ctx = _context(tmp_path)
    store = open_monitor_store(ctx)
    store.ensure_schema()
    tid = "1779000000000000096"
    terminal = _update(
        tid,
        1779000000000013000,
        event="work_completed",
        status="completed",
        terminal=True,
    )
    store.record_task_log_updates(
        WEFT_GLOBAL_LOG_QUEUE,
        (terminal,),
        checkpoint_message_id=None,
    )
    store.mark_summary_emitted(tid, terminal.message_id + 3)
    store.mark_task_control_deleted(tid, terminal.message_id + 4)
    store.mark_family_disposed(
        tid,
        terminal.message_id + 5,
        disposition_reason="terminal",
    )

    record = store.get_task(tid)
    ready = store.list_summary_ready_tasks(
        limit=10,
        now_ns=terminal.message_id + 3_000_000_000,
        retention_seconds=1.0,
    )

    assert record is not None
    assert record.summary_emitted_at_ns == terminal.message_id + 3
    assert record.task_control_deleted_at_ns == terminal.message_id + 4
    assert record.disposition_reason == "terminal"
    assert record.disposition_at_ns == terminal.message_id + 5
    assert ready == ()


def test_monitor_store_lists_control_deleted_terminal_disposition_backfill(
    tmp_path,
) -> None:
    ctx = _context(tmp_path)
    store = open_monitor_store(ctx)
    store.ensure_schema()
    tid = "1779000000000000098"
    terminal = _update(
        tid,
        1779000000000015000,
        event="work_completed",
        status="completed",
        terminal=True,
    )
    store.record_task_log_updates(
        WEFT_GLOBAL_LOG_QUEUE,
        (terminal,),
        checkpoint_message_id=None,
    )
    store.mark_summary_emitted(tid, terminal.message_id + 1)
    store.mark_task_control_deleted(tid, terminal.message_id + 2)

    assert store.list_terminal_control_deleted_disposition_backfill_tasks(
        limit=10,
    ) == (tid,)

    store.mark_family_disposed(
        tid,
        terminal.message_id + 3,
        disposition_reason="terminal",
    )

    assert (
        store.list_terminal_control_deleted_disposition_backfill_tasks(
            limit=10,
        )
        == ()
    )


def test_monitor_store_summary_ready_keeps_summary_emitted_undisposed_family(
    tmp_path,
) -> None:
    ctx = _context(tmp_path)
    store = open_monitor_store(ctx)
    store.ensure_schema()
    tid = "1779000000000000097"
    terminal = _update(
        tid,
        1779000000000014000,
        event="work_completed",
        status="completed",
        terminal=True,
    )
    store.record_task_log_updates(
        WEFT_GLOBAL_LOG_QUEUE,
        (terminal,),
        checkpoint_message_id=None,
    )
    store.mark_summary_emitted(tid, terminal.message_id + 1)

    ready = store.list_summary_ready_tasks(
        limit=10,
        now_ns=terminal.message_id + 3_000_000_000,
        retention_seconds=1.0,
    )

    assert [(item.record.tid, item.close_reason) for item in ready] == [
        (tid, "terminal")
    ]


def test_monitor_store_terminal_control_cleanup_ready_requires_summary_and_age(
    tmp_path,
) -> None:
    ctx = _context(tmp_path)
    store = open_monitor_store(ctx)
    store.ensure_schema()
    ready_tid = "1779000000000000197"
    no_summary_tid = "1779000000000000198"
    disposed_tid = "1779000000000000199"
    too_young_tid = "1779000000000000200"
    ready_terminal = _update(
        ready_tid,
        1779000000000015000,
        event="work_completed",
        status="completed",
        terminal=True,
    )
    no_summary_terminal = _update(
        no_summary_tid,
        1779000000000016000,
        event="work_completed",
        status="completed",
        terminal=True,
    )
    disposed_terminal = _update(
        disposed_tid,
        1779000000000017000,
        event="work_completed",
        status="completed",
        terminal=True,
    )
    too_young_terminal = _update(
        too_young_tid,
        1779000005000018000,
        event="work_completed",
        status="completed",
        terminal=True,
    )
    store.record_task_log_updates(
        WEFT_GLOBAL_LOG_QUEUE,
        (ready_terminal, no_summary_terminal, disposed_terminal, too_young_terminal),
        checkpoint_message_id=None,
    )
    store.mark_summary_emitted(ready_tid, ready_terminal.message_id + 1)
    store.mark_summary_emitted(disposed_tid, disposed_terminal.message_id + 1)
    store.mark_family_disposed(
        disposed_tid,
        disposed_terminal.message_id + 2,
        disposition_reason="terminal",
    )

    ready = store.list_terminal_control_cleanup_ready_tasks(
        limit=10,
        now_ns=ready_terminal.message_id + 3_000_000_000,
        retention_seconds=2.0,
    )

    assert [record.tid for record in ready] == [ready_tid, disposed_tid]


def test_monitor_store_batch_ingest_updates_tasks_and_checkpoint(tmp_path) -> None:
    ctx = _context(tmp_path)
    store = open_monitor_store(
        ctx,
        config={"WEFT_TASK_MONITOR_STORE_WRITE_BATCH_SIZE": 1},
    )
    store.ensure_schema()
    first_tid = "1779000000000000010"
    second_tid = "1779000000000000011"
    updates = (
        _update(first_tid, 1779000000000003000),
        _update(
            first_tid,
            1779000000000004000,
            event="work_completed",
            status="completed",
            terminal=True,
        ),
        _update(second_tid, 1779000000000005000),
    )

    result = store.record_task_log_updates(
        WEFT_GLOBAL_LOG_QUEUE,
        updates,
        checkpoint_message_id=1779000000000005000,
    )

    assert result.updates_written == 3
    assert result.tasks_updated == 2
    assert result.terminal_tasks == 1
    assert result.checkpoint_written is True
    assert store.get_checkpoint(WEFT_GLOBAL_LOG_QUEUE) == 1779000000000005000
    first_record = store.get_task(first_tid)
    second_record = store.get_task(second_tid)
    assert first_record is not None
    assert second_record is not None
    assert first_record.terminal_seen is True
    assert second_record.terminal_seen is False


def test_monitor_store_deletes_task_messages_and_reconciles_parent(tmp_path) -> None:
    ctx = _context(tmp_path)
    store = open_monitor_store(ctx)
    store.ensure_schema()
    tid = "1779000000000000020"
    start = _update(tid, 1779000000000006000)
    terminal = _update(
        tid,
        1779000000000007000,
        event="work_completed",
        status="completed",
        terminal=True,
    )
    store.record_task_log_updates(
        WEFT_GLOBAL_LOG_QUEUE,
        (start, terminal),
        checkpoint_message_id=None,
    )

    result = store.delete_task_messages_after_raw_delete(
        (start.message_id,),
        deleted_at_ns=1,
    )
    record = store.get_task(tid)
    assert record is not None
    assert result.message_rows_deleted == 1
    assert record.raw_deleted_at_ns is None
    assert (
        _monitor_table_count(
            ctx,
            "weft_monitor_task_messages",
            where="context_key = ? AND tid = ?",
            params=(store.context_key, tid),
        )
        == 1
    )

    result = store.delete_task_messages_after_raw_delete(
        (terminal.message_id,),
        deleted_at_ns=2,
    )
    record = store.get_task(tid)
    assert record is not None
    assert result.message_rows_deleted == 1
    assert record.raw_deleted_at_ns == 2
    assert (
        _monitor_table_count(
            ctx,
            "weft_monitor_task_messages",
            where="context_key = ? AND tid = ?",
            params=(store.context_key, tid),
        )
        == 0
    )
    assert (
        _monitor_table_count(
            ctx,
            "weft_monitor_task_messages",
            where="deleted_at_ns IS NOT NULL",
        )
        == 0
    )


def test_monitor_store_deletes_messages_and_reconciles_only_affected_tids(
    tmp_path,
) -> None:
    ctx = _context(tmp_path)
    store = open_monitor_store(ctx)
    store.ensure_schema()
    first_tid = "1779000000000000021"
    second_tid = "1779000000000000022"
    first_start = _update(first_tid, 1779000000000006100)
    first_terminal = _update(
        first_tid,
        1779000000000006200,
        event="work_completed",
        status="completed",
        terminal=True,
    )
    second_terminal = _update(
        second_tid,
        1779000000000006300,
        event="work_completed",
        status="completed",
        terminal=True,
    )
    store.record_task_log_updates(
        WEFT_GLOBAL_LOG_QUEUE,
        (first_start, first_terminal, second_terminal),
        checkpoint_message_id=None,
    )

    result = store.delete_task_messages_after_raw_delete(
        (first_start.message_id, first_terminal.message_id),
        deleted_at_ns=5,
    )

    first_record = store.get_task(first_tid)
    second_record = store.get_task(second_tid)
    assert first_record is not None
    assert second_record is not None
    assert result.message_rows_deleted == 2
    assert first_record.raw_deleted_at_ns == 5
    assert second_record.raw_deleted_at_ns is None


def test_monitor_store_reconciles_existing_raw_deleted_child_refs(tmp_path) -> None:
    ctx = _context(tmp_path)
    store = open_monitor_store(ctx)
    store.ensure_schema()
    tid = "1779000000000000030"
    terminal = _update(
        tid,
        1779000000000008000,
        event="work_completed",
        status="completed",
        terminal=True,
    )
    store.record_task_log_updates(
        WEFT_GLOBAL_LOG_QUEUE,
        (terminal,),
        checkpoint_message_id=None,
    )

    result = store.delete_task_messages_after_raw_delete(
        (terminal.message_id,),
        deleted_at_ns=4,
    )

    record = store.get_task(tid)
    assert record is not None
    assert result.message_rows_deleted == 1
    assert record.raw_deleted_at_ns == 4
    assert (
        _monitor_table_count(
            ctx,
            "weft_monitor_task_messages",
            where="context_key = ? AND tid = ?",
            params=(store.context_key, tid),
        )
        == 0
    )


def test_monitor_store_lists_raw_deleted_task_log_recovery_tids(tmp_path) -> None:
    ctx = _context(tmp_path)
    store = open_monitor_store(ctx)
    store.ensure_schema()
    tid = "1779000000000000032"
    terminal = _update(
        tid,
        1779000000000008300,
        event="work_completed",
        status="completed",
        terminal=True,
    )
    store.record_task_log_updates(
        WEFT_GLOBAL_LOG_QUEUE,
        (terminal,),
        checkpoint_message_id=None,
    )

    assert store.list_raw_deleted_task_log_recovery_tids(limit=10) == ()
    store.delete_task_messages_after_raw_delete(
        (terminal.message_id,),
        deleted_at_ns=terminal.message_id + 1,
    )

    assert store.list_raw_deleted_task_log_recovery_tids(limit=10) == (tid,)
    store.mark_orphan_raw_recovery_checked((tid,), terminal.message_id + 2)
    record = store.get_task(tid)
    assert record is not None
    assert record.orphan_raw_recovery_checked_at_ns == terminal.message_id + 2
    assert store.list_raw_deleted_task_log_recovery_tids(limit=10) == ()
    later = _update(tid, terminal.message_id + 3, event="task_activity")
    store.record_task_log_updates(
        WEFT_GLOBAL_LOG_QUEUE,
        (later,),
        checkpoint_message_id=None,
    )
    record = store.get_task(tid)
    assert record is not None
    assert record.raw_deleted_at_ns is None
    assert record.orphan_raw_recovery_checked_at_ns is None


def test_monitor_store_lists_reserved_cleanup_pending_tasks(tmp_path) -> None:
    ctx = _context(tmp_path)
    store = open_monitor_store(ctx)
    store.ensure_schema()
    tid = "1779000000000000033"
    terminal = _update(
        tid,
        1779000000000008310,
        event="work_failed",
        status="failed",
        terminal=True,
    )
    store.record_task_log_updates(
        WEFT_GLOBAL_LOG_QUEUE,
        (terminal,),
        checkpoint_message_id=None,
    )

    assert store.list_reserved_cleanup_pending_tasks(limit=10) == ()
    store.mark_summary_emitted(tid, terminal.message_id + 1)

    pending = store.list_reserved_cleanup_pending_tasks(limit=10)
    assert [record.tid for record in pending] == [tid]
    store.mark_reserved_cleanup_checked((tid,), terminal.message_id + 2)
    record = store.get_task(tid)
    assert record is not None
    assert record.reserved_cleanup_checked_at_ns == terminal.message_id + 2
    assert store.list_reserved_cleanup_pending_tasks(limit=10) == ()
    later = _update(tid, terminal.message_id + 3, event="task_activity")
    store.record_task_log_updates(
        WEFT_GLOBAL_LOG_QUEUE,
        (later,),
        checkpoint_message_id=None,
    )
    record = store.get_task(tid)
    assert record is not None
    assert record.reserved_cleanup_checked_at_ns is None


def test_monitor_store_prunes_legacy_message_tombstones(tmp_path) -> None:
    ctx = _context(tmp_path)
    store = open_monitor_store(ctx)
    store.ensure_schema()
    tid = "1779000000000000031"
    start = _update(tid, 1779000000000008100)
    terminal = _update(
        tid,
        1779000000000008200,
        event="work_completed",
        status="completed",
        terminal=True,
    )
    store.record_task_log_updates(
        WEFT_GLOBAL_LOG_QUEUE,
        (start, terminal),
        checkpoint_message_id=None,
    )
    with ctx.broker() as broker:
        runner = broker._runner
        runner.begin_immediate()
        try:
            runner.run(
                "UPDATE weft_monitor_task_messages "
                "SET deleted_at_ns = ? WHERE context_key = ? AND tid = ?",
                (terminal.message_id + 1, store.context_key, tid),
            )
            runner.commit()
        except Exception:
            runner.rollback()
            raise

    result = store.prune_deleted_task_message_tombstones(
        limit=10,
        pruned_at_ns=terminal.message_id + 2,
    )

    record = store.get_task(tid)
    assert record is not None
    assert result.message_tombstones_pruned == 2
    assert record.raw_deleted_at_ns == terminal.message_id + 2
    assert (
        _monitor_table_count(
            ctx,
            "weft_monitor_task_messages",
            where="context_key = ? AND tid = ?",
            params=(store.context_key, tid),
        )
        == 0
    )


def test_monitor_store_retires_completed_collation_families(tmp_path) -> None:
    ctx = _context(tmp_path)
    store = open_monitor_store(ctx)
    store.ensure_schema()
    tid = "1779000000000000032"
    terminal = _update(
        tid,
        1779000000000008300,
        event="work_completed",
        status="completed",
        terminal=True,
    )
    store.record_task_log_updates(
        WEFT_GLOBAL_LOG_QUEUE,
        (terminal,),
        checkpoint_message_id=None,
    )
    store.delete_task_messages_after_raw_delete(
        (terminal.message_id,),
        deleted_at_ns=terminal.message_id + 1,
    )
    store.mark_summary_emitted(tid, terminal.message_id + 2)
    store.mark_family_disposed(
        tid,
        terminal.message_id + 3,
        disposition_reason="terminal",
    )
    assert (
        store.retire_completed_collation_families(
            limit=10,
            retired_at_ns=terminal.message_id + 4,
        ).families_retired
        == 0
    )
    store.mark_task_control_deleted(tid, terminal.message_id + 5)

    result = store.retire_completed_collation_families(
        limit=10,
        retired_at_ns=terminal.message_id + 6,
    )

    assert result.families_retired == 1
    assert store.get_task(tid) is None


def test_monitor_store_retirement_requires_reserved_cleanup_when_probe_needed(
    tmp_path,
) -> None:
    ctx = _context(tmp_path)
    store = open_monitor_store(ctx)
    store.ensure_schema()
    tid = "1779000000000000034"
    terminal = _update(
        tid,
        1779000000000008400,
        event="work_failed",
        status="failed",
        terminal=True,
    )
    store.record_task_log_updates(
        WEFT_GLOBAL_LOG_QUEUE,
        (terminal,),
        checkpoint_message_id=None,
    )
    store.delete_task_messages_after_raw_delete(
        (terminal.message_id,),
        deleted_at_ns=terminal.message_id + 1,
    )
    store.mark_summary_emitted(tid, terminal.message_id + 2)
    store.mark_family_disposed(
        tid,
        terminal.message_id + 3,
        disposition_reason="terminal",
    )
    store.mark_task_control_deleted(tid, terminal.message_id + 4)

    assert (
        store.retire_completed_collation_families(
            limit=10,
            retired_at_ns=terminal.message_id + 5,
        ).families_retired
        == 0
    )
    store.mark_reserved_cleanup_checked((tid,), terminal.message_id + 6)

    result = store.retire_completed_collation_families(
        limit=10,
        retired_at_ns=terminal.message_id + 7,
    )

    assert result.families_retired == 1
    assert store.get_task(tid) is None


def test_monitor_store_rejects_newer_schema_version(tmp_path) -> None:
    ctx = _context(tmp_path)
    store = open_monitor_store(ctx)
    store.ensure_schema()
    with ctx.broker() as broker:
        runner = broker._runner
        runner.run(
            "UPDATE weft_monitor_meta SET value_json = ? WHERE key = ?",
            ('{"version": 999}', "schema_version"),
        )
        runner.commit()

    with pytest.raises(MonitorStoreUnavailable):
        store.ensure_schema()
