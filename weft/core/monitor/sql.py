"""SQL templates and builders for the Monitor-owned collation store.

This module mirrors SimpleBroker's SQL namespace pattern: SQL lives in a
dedicated module, runtime values are always parameters, and dynamic SQL is
assembled only from code-owned identifiers/fragments.

Spec references:
- docs/specifications/04-SimpleBroker_Integration.md [SB-0.4a]
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
"""

from __future__ import annotations

import re
from collections.abc import Sequence

_identifier_pattern = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def identifier(name: str) -> str:
    """Return a trusted SQL identifier or raise for unsafe input."""

    if not _identifier_pattern.fullmatch(name):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return name


def identifier_list(names: Sequence[str]) -> str:
    """Return a comma-separated trusted identifier list."""

    return ", ".join(identifier(name) for name in names)


def placeholders(count: int) -> str:
    """Return positional placeholders for a known-size parameter list."""

    if count <= 0:
        raise ValueError("placeholder count must be positive")
    return ", ".join("?" for _ in range(count))


def create_meta_table(table: str) -> str:
    """Build the Monitor meta table DDL."""

    return f"""
        CREATE TABLE IF NOT EXISTS {identifier(table)} (
          key TEXT PRIMARY KEY,
          value_json TEXT NOT NULL,
          updated_at_ns BIGINT NOT NULL
        )
        """


def create_task_collations_table(table: str) -> str:
    """Build the Monitor task-collation table DDL."""

    return f"""
        CREATE TABLE IF NOT EXISTS {identifier(table)} (
          context_key TEXT NOT NULL,
          tid TEXT NOT NULL,
          name TEXT NULL,
          runner TEXT NULL,
          parent_tid TEXT NULL,
          role TEXT NULL,
          status TEXT NULL,
          terminal_seen INTEGER NOT NULL DEFAULT 0,
          terminal_event TEXT NULL,
          terminal_status TEXT NULL,
          terminal_message_id BIGINT NULL,
          return_code INTEGER NULL,
          first_message_id BIGINT NOT NULL,
          last_message_id BIGINT NOT NULL,
          first_seen_at_ns BIGINT NULL,
          last_seen_at_ns BIGINT NULL,
          started_at_ns BIGINT NULL,
          completed_at_ns BIGINT NULL,
          taskspec_summary_json TEXT NOT NULL,
          state_json TEXT NOT NULL,
          lifecycle_json TEXT NOT NULL,
          resources_json TEXT NOT NULL,
          diagnostics_json TEXT NOT NULL,
          bookkeeping_json TEXT NOT NULL,
          reserved_probe_needed INTEGER NOT NULL DEFAULT 0,
          summary_emitted_at_ns BIGINT NULL,
          raw_deleted_at_ns BIGINT NULL,
          suspect_reason TEXT NULL,
          suspect_at_ns BIGINT NULL,
          disposition_reason TEXT NULL,
          disposition_at_ns BIGINT NULL,
          task_control_deleted_at_ns BIGINT NULL,
          updated_at_ns BIGINT NOT NULL,
          PRIMARY KEY (context_key, tid)
        )
        """


def create_task_messages_table(table: str) -> str:
    """Build the Monitor task-message table DDL."""

    return f"""
        CREATE TABLE IF NOT EXISTS {identifier(table)} (
          context_key TEXT NOT NULL,
          tid TEXT NOT NULL,
          queue_name TEXT NOT NULL,
          message_id BIGINT NOT NULL,
          event TEXT NULL,
          status TEXT NULL,
          observed_at_ns BIGINT NULL,
          selected_for_delete_at_ns BIGINT NULL,
          deleted_at_ns BIGINT NULL,
          PRIMARY KEY (context_key, tid, message_id)
        )
        """


def create_index(
    index_name: str,
    table: str,
    columns: Sequence[str],
) -> str:
    """Build a simple Monitor store index DDL statement."""

    return f"""
        CREATE INDEX IF NOT EXISTS {identifier(index_name)}
        ON {identifier(table)} ({identifier_list(columns)})
        """


def add_column(table: str, column: str, definition: str) -> str:
    """Build an additive column migration statement for trusted fragments."""

    return (
        f"ALTER TABLE {identifier(table)} ADD COLUMN {identifier(column)} {definition}"
    )


def sqlite_table_info(table: str) -> str:
    """Build a SQLite table-info pragma for trusted table names."""

    return f"PRAGMA table_info({identifier(table)})"


def postgres_column_exists() -> str:
    """Build a Postgres information-schema column lookup."""

    return """
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = ?
          AND column_name = ?
        LIMIT 1
        """


def select_meta(meta_table: str) -> str:
    """Build a Monitor meta lookup query."""

    return f"SELECT value_json FROM {identifier(meta_table)} WHERE key = ?"


def upsert_meta(meta_table: str) -> str:
    """Build a Monitor meta upsert query."""

    table = identifier(meta_table)
    return f"""
        INSERT INTO {table} (key, value_json, updated_at_ns)
        VALUES (?, ?, ?)
        ON CONFLICT (key) DO UPDATE SET
          value_json = excluded.value_json,
          updated_at_ns = excluded.updated_at_ns
        """


def select_task(collations_table: str, columns: Sequence[str]) -> str:
    """Build a Monitor task lookup query."""

    return f"""
        SELECT {identifier_list(columns)}
        FROM {identifier(collations_table)}
        WHERE context_key = ? AND tid = ?
        """


def select_unemitted_terminal_tasks(
    collations_table: str,
    columns: Sequence[str],
) -> str:
    """Build a query for terminal task summaries that need emission."""

    return f"""
        SELECT {identifier_list(columns)}
        FROM {identifier(collations_table)}
        WHERE context_key = ?
          AND terminal_seen = 1
          AND summary_emitted_at_ns IS NULL
        ORDER BY terminal_message_id, tid
        LIMIT ?
        """


def select_summary_ready_terminal_tasks(
    collations_table: str,
    columns: Sequence[str],
) -> str:
    """Build a query for retained terminal task summaries."""

    return f"""
        SELECT {identifier_list(columns)}
        FROM {identifier(collations_table)}
        WHERE context_key = ?
          AND terminal_seen = 1
          AND disposition_at_ns IS NULL
          AND last_message_id <= ?
        ORDER BY last_message_id, tid
        LIMIT ?
        """


def select_terminal_control_cleanup_ready_tasks(
    collations_table: str,
    columns: Sequence[str],
) -> str:
    """Build a query for retained task-local runtime-queue cleanup."""

    return f"""
        SELECT {identifier_list(columns)}
        FROM {identifier(collations_table)}
        WHERE context_key = ?
          AND summary_emitted_at_ns IS NOT NULL
          AND task_control_deleted_at_ns IS NULL
          AND (
            (terminal_seen = 1 AND last_message_id <= ?)
            OR disposition_at_ns IS NOT NULL
          )
        ORDER BY last_message_id, tid
        LIMIT ?
        """


def select_summary_ready_open_tasks(
    collations_table: str,
    columns: Sequence[str],
) -> str:
    """Build a query for retained open task summaries."""

    return f"""
        SELECT {identifier_list(columns)}
        FROM {identifier(collations_table)}
        WHERE context_key = ?
          AND terminal_seen = 0
          AND disposition_at_ns IS NULL
          AND last_message_id <= ?
        ORDER BY last_message_id, tid
        LIMIT ?
        """


def mark_summary_emitted(collations_table: str) -> str:
    """Build a summary-emitted update."""

    return f"""
        UPDATE {identifier(collations_table)}
        SET summary_emitted_at_ns = ?,
            suspect_reason = COALESCE(?, suspect_reason),
            updated_at_ns = ?
        WHERE context_key = ? AND tid = ?
        """


def mark_task_control_deleted(collations_table: str) -> str:
    """Build a task-control cleanup marker update."""

    return f"""
        UPDATE {identifier(collations_table)}
        SET task_control_deleted_at_ns = ?,
            updated_at_ns = ?
        WHERE context_key = ? AND tid = ?
        """


def mark_family_disposed(collations_table: str) -> str:
    """Build a Monitor family disposition update."""

    return f"""
        UPDATE {identifier(collations_table)}
        SET disposition_reason = ?,
            disposition_at_ns = ?,
            suspect_reason = COALESCE(?, suspect_reason),
            suspect_at_ns = COALESCE(?, suspect_at_ns),
            updated_at_ns = ?
        WHERE context_key = ? AND tid = ?
        """


def select_deletable_task_log_messages(
    messages_table: str,
    collations_table: str,
    *,
    require_summary: bool,
) -> str:
    """Build a query for exact task-log messages proven deletable."""

    summary_condition = (
        "AND c.summary_emitted_at_ns IS NOT NULL" if require_summary else ""
    )
    state_condition = (
        "AND (c.terminal_seen = 1 OR c.suspect_reason IS NOT NULL)"
        if require_summary
        else "AND c.terminal_seen = 1"
    )
    return f"""
        SELECT m.queue_name, m.message_id, m.tid
        FROM {identifier(messages_table)} AS m
        JOIN {identifier(collations_table)} AS c
          ON c.context_key = m.context_key AND c.tid = m.tid
        WHERE m.context_key = ?
          AND m.deleted_at_ns IS NULL
          AND c.raw_deleted_at_ns IS NULL
          {state_condition}
          {summary_condition}
        ORDER BY m.message_id
        LIMIT ?
        """


def mark_message_deleted(messages_table: str) -> str:
    """Build an exact message-deleted update."""

    return f"""
        UPDATE {identifier(messages_table)}
        SET deleted_at_ns = ?
        WHERE context_key = ? AND message_id = ?
        """


def mark_messages_deleted(messages_table: str, message_id_count: int) -> str:
    """Build a batched exact message-deleted update."""

    return f"""
        UPDATE {identifier(messages_table)}
        SET deleted_at_ns = ?
        WHERE context_key = ?
          AND message_id IN ({placeholders(message_id_count)})
        """


def select_distinct_tids_for_message_ids(
    messages_table: str,
    message_id_count: int,
) -> str:
    """Build a query for TIDs attached to exact raw message IDs."""

    return f"""
        SELECT DISTINCT tid
        FROM {identifier(messages_table)}
        WHERE context_key = ?
          AND message_id IN ({placeholders(message_id_count)})
        """


def select_remaining_undeleted_message(messages_table: str) -> str:
    """Build a query checking for any remaining undeleted task message."""

    return f"""
        SELECT 1
        FROM {identifier(messages_table)}
        WHERE context_key = ?
          AND tid = ?
          AND deleted_at_ns IS NULL
        LIMIT 1
        """


def mark_raw_deleted(collations_table: str) -> str:
    """Build a task raw-deleted marker update."""

    return f"""
        UPDATE {identifier(collations_table)}
        SET raw_deleted_at_ns = ?, updated_at_ns = ?
        WHERE context_key = ? AND tid = ?
        """


def reconcile_raw_deleted_tasks(
    collations_table: str,
    messages_table: str,
) -> str:
    """Build a parent raw-deleted reconciliation update.

    Spec: [MF-5], [OBS.17]
    """

    collations = identifier(collations_table)
    messages = identifier(messages_table)
    return f"""
        UPDATE {collations}
        SET raw_deleted_at_ns = ?, updated_at_ns = ?
        WHERE context_key = ?
          AND raw_deleted_at_ns IS NULL
          AND EXISTS (
            SELECT 1
            FROM {messages} AS m
            WHERE m.context_key = {collations}.context_key
              AND m.tid = {collations}.tid
          )
          AND NOT EXISTS (
            SELECT 1
            FROM {messages} AS m
            WHERE m.context_key = {collations}.context_key
              AND m.tid = {collations}.tid
              AND m.deleted_at_ns IS NULL
          )
        """


def reconcile_raw_deleted_tasks_for_tids(
    collations_table: str,
    messages_table: str,
    tid_count: int,
) -> str:
    """Build affected-task parent raw-deleted reconciliation update.

    Spec: [MF-5], [OBS.17]
    """

    collations = identifier(collations_table)
    messages = identifier(messages_table)
    return f"""
        UPDATE {collations}
        SET raw_deleted_at_ns = ?, updated_at_ns = ?
        WHERE context_key = ?
          AND tid IN ({placeholders(tid_count)})
          AND raw_deleted_at_ns IS NULL
          AND EXISTS (
            SELECT 1
            FROM {messages} AS m
            WHERE m.context_key = {collations}.context_key
              AND m.tid = {collations}.tid
          )
          AND NOT EXISTS (
            SELECT 1
            FROM {messages} AS m
            WHERE m.context_key = {collations}.context_key
              AND m.tid = {collations}.tid
              AND m.deleted_at_ns IS NULL
          )
        """


def upsert_task_record(
    collations_table: str,
    columns: Sequence[str],
    *,
    conflict_columns: Sequence[str],
) -> str:
    """Build a Monitor task collation upsert query."""

    insert_columns = tuple(identifier(column) for column in columns)
    conflict = set(conflict_columns)
    assignments = ", ".join(
        f"{identifier(column)} = excluded.{identifier(column)}"
        for column in columns
        if column not in conflict
    )
    return f"""
        INSERT INTO {identifier(collations_table)}
        ({", ".join(insert_columns)})
        VALUES ({placeholders(len(insert_columns))})
        ON CONFLICT ({identifier_list(conflict_columns)}) DO UPDATE SET {assignments}
        """


def upsert_task_message(messages_table: str) -> str:
    """Build a Monitor task-message upsert query."""

    return f"""
        INSERT INTO {identifier(messages_table)}
        (context_key, tid, queue_name, message_id, event, status, observed_at_ns)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (context_key, tid, message_id) DO UPDATE SET
          queue_name = excluded.queue_name,
          event = excluded.event,
          status = excluded.status,
          observed_at_ns = excluded.observed_at_ns
        """
