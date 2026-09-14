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


def test_monitor_sql_builds_semantic_schema_catalog_queries() -> None:
    sqlite_columns = monitor_sql.sqlite_table_xinfo("weft_monitor_meta")
    sqlite_index = monitor_sql.sqlite_index_xinfo("idx_monitor_probe")
    postgres_columns = monitor_sql.postgres_table_column_info()
    postgres_primary_index = monitor_sql.postgres_primary_key_index_name()
    postgres_index = monitor_sql.postgres_index_shape()
    postgres_unique = monitor_sql.postgres_unique_secondary_index_names()

    assert sqlite_columns == "PRAGMA table_xinfo(weft_monitor_meta)"
    assert sqlite_index == "PRAGMA index_xinfo(idx_monitor_probe)"
    assert "character_maximum_length" in postgres_columns
    assert "is_generated" in postgres_columns
    assert "is_identity" in postgres_columns
    assert "ordinal_position" not in postgres_columns
    assert "index_definition.indisprimary" in postgres_primary_index
    assert "JOIN pg_am AS access_method" in postgres_index
    assert "index_definition.indpred IS NOT NULL" in postgres_index
    assert "index_definition.indisvalid" in postgres_index
    assert "index_definition.indisready" in postgres_index
    assert "index_definition.indclass" in postgres_index
    assert "index_definition.indcollation" in postgres_index
    assert "index_definition.indnkeyatts" in postgres_index
    assert "index_definition.indisunique" in postgres_unique
    assert "NOT index_definition.indisprimary" in postgres_unique
