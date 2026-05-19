"""Tests for Monitor SQL builders."""

from __future__ import annotations

import pytest

from weft.core.monitor import sql as monitor_sql

pytestmark = [pytest.mark.shared]


def test_monitor_sql_rejects_unsafe_identifiers() -> None:
    with pytest.raises(ValueError, match="Unsafe SQL identifier"):
        monitor_sql.select_meta("weft_monitor_meta; DROP TABLE messages")


def test_monitor_sql_uses_parameter_placeholders_for_values() -> None:
    query = monitor_sql.select_task(
        "weft_monitor_task_collations",
        ("context_key", "tid", "status"),
    )

    assert "WHERE context_key = ? AND tid = ?" in query
    assert "context_key, tid, status" in query
    assert "%s" not in query


def test_monitor_sql_builds_raw_deleted_reconciliation_query() -> None:
    query = monitor_sql.reconcile_raw_deleted_tasks(
        "weft_monitor_task_collations",
        "weft_monitor_task_messages",
    )

    assert "UPDATE weft_monitor_task_collations" in query
    assert "raw_deleted_at_ns IS NULL" in query
    assert "deleted_at_ns IS NULL" in query
    assert "context_key = ?" in query
    assert "%s" not in query


def test_monitor_sql_builds_affected_tid_reconciliation_query() -> None:
    query = monitor_sql.reconcile_raw_deleted_tasks_for_tids(
        "weft_monitor_task_collations",
        "weft_monitor_task_messages",
        2,
    )

    assert "UPDATE weft_monitor_task_collations" in query
    assert "tid IN (?, ?)" in query
    assert "raw_deleted_at_ns IS NULL" in query
    assert "deleted_at_ns IS NULL" in query
    assert "%s" not in query


def test_monitor_sql_summary_ready_queries_parameterize_cutoffs() -> None:
    terminal = monitor_sql.select_summary_ready_terminal_tasks(
        "weft_monitor_task_collations",
        ("context_key", "tid", "last_message_id"),
    )
    open_query = monitor_sql.select_summary_ready_open_tasks(
        "weft_monitor_task_collations",
        ("context_key", "tid", "last_message_id"),
    )

    assert "last_message_id <= ?" in terminal
    assert "COALESCE(" not in terminal
    assert "last_message_id <= ?" in open_query
    assert "LIMIT ?" in terminal
    assert "LIMIT ?" in open_query
    assert "%s" not in terminal
    assert "%s" not in open_query
