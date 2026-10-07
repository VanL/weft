"""Tests for Monitor SQL builders."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft.context import build_context
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


def test_sqlite_catalog_queries_report_columns_and_index_comparison(
    tmp_path: Path,
) -> None:
    context = build_context(prepare_project_root(tmp_path))
    if context.backend_name != "sqlite":
        pytest.skip("SQLite catalog contract")
    with context.broker() as broker, broker.sidecar(transaction=True) as session:
        session.run(
            "CREATE TABLE audit_catalog (task_id TEXT PRIMARY KEY, payload TEXT, "
            "payload_copy TEXT GENERATED ALWAYS AS (payload) VIRTUAL)"
        )
        session.run(
            "CREATE INDEX audit_catalog_payload ON audit_catalog (payload COLLATE NOCASE)"
        )
        columns = tuple(
            session.run(monitor_sql.sqlite_table_xinfo("audit_catalog"), fetch=True)
        )
        keys = tuple(
            session.run(
                monitor_sql.sqlite_index_xinfo("audit_catalog_payload"), fetch=True
            )
        )

    assert "payload_copy" in {row[1] for row in columns}
    assert [(row[1], row[2], row[5], row[6]) for row in columns] == [
        ("task_id", "TEXT", 1, 0),
        ("payload", "TEXT", 0, 0),
        ("payload_copy", "TEXT", 0, 2),
    ]
    assert [(row[2], row[4]) for row in keys if row[5]] == [("payload", "NOCASE")]


def test_postgres_catalog_queries_report_column_and_index_semantics(
    tmp_path: Path,
) -> None:
    """Exercise actual catalog projections, independent of SQL aliases [SB-0.4a]."""
    context = build_context(prepare_project_root(tmp_path))
    if context.backend_name != "postgres":
        pytest.skip("PostgreSQL catalog contract")
    with context.broker() as broker, broker.sidecar(transaction=True) as session:
        session.run(
            "CREATE TABLE audit_catalog ("
            "task_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, "
            "code VARCHAR(12) NOT NULL DEFAULT 'ready', "
            "copy VARCHAR(12) GENERATED ALWAYS AS (code) STORED)"
        )
        session.run(
            "CREATE UNIQUE INDEX audit_catalog_unique ON audit_catalog "
            "(code COLLATE \"C\" text_pattern_ops) INCLUDE (copy) WHERE code <> ''"
        )
        session.run("CREATE INDEX audit_catalog_nonunique ON audit_catalog (code)")
        columns = {
            row[0]: row[1:]
            for row in session.run(
                monitor_sql.postgres_table_column_info(), ("audit_catalog",), fetch=True
            )
        }
        primary = tuple(
            session.run(
                monitor_sql.postgres_primary_key_index_name(),
                ("audit_catalog",),
                fetch=True,
            )
        )
        unique = tuple(
            session.run(
                monitor_sql.postgres_unique_secondary_index_names(),
                ("audit_catalog",),
                fetch=True,
            )
        )
        shape = tuple(
            session.run(
                monitor_sql.postgres_index_shape(),
                ("audit_catalog_unique",),
                fetch=True,
            )
        )
        opclass = tuple(
            session.run(
                "SELECT oid::text FROM pg_opclass WHERE opcname = 'text_pattern_ops' "
                "AND opcmethod = (SELECT oid FROM pg_am WHERE amname = 'btree')",
                fetch=True,
            )
        )
        collation = tuple(
            session.run(
                "SELECT oid::text FROM pg_collation WHERE collname = 'C' "
                "AND collnamespace = 'pg_catalog'::regnamespace",
                fetch=True,
            )
        )

    assert columns["task_id"] == ("bigint", "int8", None, "NO", None, "NEVER", "YES")
    assert columns["code"][:4] == ("character varying", "varchar", 12, "NO")
    assert "ready" in str(columns["code"][4])
    assert columns["code"][5:] == ("NEVER", "NO")
    assert columns["copy"][:4] == ("character varying", "varchar", 12, "YES")
    assert columns["copy"][5:] == ("ALWAYS", "NO")
    assert primary == (("audit_catalog_pkey",),)
    assert unique == (("audit_catalog_unique",),)
    # Included payload columns are not index keys; comparison semantics belong
    # only to the explicitly declared code key.
    assert shape == (
        (
            "audit_catalog",
            True,
            "btree",
            True,
            True,
            True,
            "code",
            opclass[0][0],
            collation[0][0],
        ),
    )


@pytest.mark.parametrize(("flag", "slot"), [("indisvalid", 4), ("indisready", 5)])
def test_postgres_catalog_query_reports_unusable_index_flags(
    tmp_path: Path, flag: str, slot: int
) -> None:
    context = build_context(prepare_project_root(tmp_path))
    if context.backend_name != "postgres":
        pytest.skip("PostgreSQL index readiness/validity contract")
    with context.broker() as broker, broker.sidecar(transaction=True) as session:
        superuser = tuple(
            session.run(
                "SELECT rolsuper FROM pg_roles WHERE rolname = current_user", fetch=True
            )
        )
        if not superuser[0][0]:
            pytest.skip(
                "False index flags require the isolated PostgreSQL superuser runner"
            )
        session.run("CREATE TABLE audit_catalog_flags (code TEXT)")
        session.run(
            "CREATE INDEX audit_catalog_flags_index ON audit_catalog_flags (code)"
        )
        # Change only this test-owned index, inside one transaction. No writes
        # through the index occur while it is unusable. Both success and failure
        # restore the catalog before leaving the sidecar.
        index_oid = next(
            iter(
                session.run(
                    "SELECT indexrelid FROM pg_index "
                    "WHERE indexrelid = 'audit_catalog_flags_index'::regclass",
                    fetch=True,
                )
            )
        )[0]
        session.run(
            f"UPDATE pg_index SET {flag} = FALSE WHERE indexrelid = ?", (index_oid,)
        )
        try:
            shape = tuple(
                session.run(
                    monitor_sql.postgres_index_shape(),
                    ("audit_catalog_flags_index",),
                    fetch=True,
                )
            )
            assert len(shape) == 1
            assert shape[0][slot] is False
            assert shape[0][5 if slot == 4 else 4] is True
        finally:
            session.run(
                f"UPDATE pg_index SET {flag} = TRUE WHERE indexrelid = ?", (index_oid,)
            )
