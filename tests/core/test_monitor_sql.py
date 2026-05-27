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
    assert "NOT EXISTS" in query
    assert "m.deleted_at_ns IS NULL" not in query
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
    assert "NOT EXISTS" in query
    assert "m.deleted_at_ns IS NULL" not in query
    assert "%s" not in query


def test_monitor_sql_deletes_task_messages_physically() -> None:
    query = monitor_sql.delete_task_messages("weft_monitor_task_messages", 2)

    assert "DELETE FROM weft_monitor_task_messages" in query
    assert "message_id IN (?, ?)" in query
    assert "UPDATE" not in query
    assert "%s" not in query


def test_monitor_sql_selects_legacy_deleted_task_message_refs() -> None:
    query = monitor_sql.select_deleted_task_message_refs("weft_monitor_task_messages")

    assert "SELECT tid, message_id" in query
    assert "deleted_at_ns IS NOT NULL" in query
    assert "ORDER BY message_id" in query
    assert "LIMIT ?" in query
    assert "%s" not in query


def test_monitor_sql_retirable_family_query_is_conservative() -> None:
    query = monitor_sql.select_retirable_task_collations(
        "weft_monitor_task_collations",
        "weft_monitor_task_messages",
        apply_retention_cutoff=True,
    )

    assert "COALESCE(completed_at_ns, last_seen_at_ns, last_message_id) <= ?" in query
    assert "raw_deleted_at_ns IS NOT NULL" in query
    assert "summary_emitted_at_ns IS NOT NULL" in query
    assert "disposition_at_ns IS NOT NULL" in query
    assert "task_control_deleted_at_ns IS NOT NULL" in query
    assert "reserved_probe_needed = 0" in query
    assert "reserved_cleanup_checked_at_ns IS NOT NULL" in query
    assert "NOT EXISTS" in query
    assert "LIMIT ?" in query
    assert "%s" not in query


def test_monitor_sql_retirable_family_query_omits_null_cutoff_probe() -> None:
    query = monitor_sql.select_retirable_task_collations(
        "weft_monitor_task_collations",
        "weft_monitor_task_messages",
        apply_retention_cutoff=False,
    )

    assert (
        "COALESCE(completed_at_ns, last_seen_at_ns, last_message_id) <= ?" not in query
    )
    assert "? IS NULL" not in query
    assert "raw_deleted_at_ns IS NOT NULL" in query
    assert "LIMIT ?" in query


def test_monitor_sql_reserved_cleanup_query_selects_unchecked_families() -> None:
    query = monitor_sql.select_reserved_cleanup_pending_tasks(
        "weft_monitor_task_collations",
        ("context_key", "tid", "last_message_id"),
    )

    assert "reserved_probe_needed = 1" in query
    assert "reserved_cleanup_checked_at_ns IS NULL" in query
    assert "raw_deleted_at_ns IS NOT NULL" in query
    assert "OR disposition_at_ns IS NOT NULL" in query
    assert "terminal_seen = 1 AND summary_emitted_at_ns IS NOT NULL" in query
    assert "ORDER BY last_message_id, tid" in query
    assert "LIMIT ?" in query
    assert "%s" not in query


def test_monitor_sql_orphan_recovery_query_excludes_checked_families() -> None:
    query = monitor_sql.select_raw_deleted_task_log_recovery_tids(
        "weft_monitor_task_collations",
        "weft_monitor_task_messages",
    )

    assert "raw_deleted_at_ns IS NOT NULL" in query
    assert "orphan_raw_recovery_checked_at_ns IS NULL" in query
    assert "NOT EXISTS" in query
    assert "LIMIT ?" in query
    assert "%s" not in query


def test_monitor_sql_raw_deleted_child_ref_repair_query_selects_inconsistency() -> None:
    query = monitor_sql.select_raw_deleted_task_message_refs(
        "weft_monitor_task_messages",
        "weft_monitor_task_collations",
    )

    assert "FROM weft_monitor_task_messages AS m" in query
    assert "JOIN weft_monitor_task_collations AS c" in query
    assert "m.deleted_at_ns IS NULL" in query
    assert "c.raw_deleted_at_ns IS NOT NULL" in query
    assert "ORDER BY m.message_id" in query
    assert "LIMIT ?" in query
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


def test_monitor_sql_terminal_control_cleanup_query_requires_summary() -> None:
    query = monitor_sql.select_terminal_control_cleanup_ready_tasks(
        "weft_monitor_task_collations",
        ("context_key", "tid", "last_message_id"),
    )

    assert "summary_emitted_at_ns IS NOT NULL" in query
    assert "task_control_deleted_at_ns IS NULL" in query
    assert "terminal_seen = 1 AND last_message_id <= ?" in query
    assert "OR disposition_at_ns IS NOT NULL" in query
    assert "last_message_id <= ?" in query
    assert "LIMIT ?" in query
    assert "%s" not in query
