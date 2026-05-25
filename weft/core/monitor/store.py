"""Monitor-owned durable collation store.

The tables in this module are derived operational state owned by the
``TaskMonitor``. They live in an already configured Weft broker database,
but they are not queues and they are not lifecycle or result authority.

Spec references:
- docs/specifications/01-Core_Components.md [CC-2.3], [CC-3.4]
- docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.13], [OBS.17]
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from weft._constants import (
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_MONITOR_CHECKPOINT_META_PREFIX,
    WEFT_MONITOR_META_TABLE,
    WEFT_MONITOR_SCHEMA_VERSION,
    WEFT_MONITOR_SCHEMA_VERSION_KEY,
    WEFT_MONITOR_TASK_COLLATIONS_TABLE,
    WEFT_MONITOR_TASK_MESSAGES_TABLE,
    WEFT_TASK_MONITOR_STALE_OPEN_FAMILY_SECONDS_DEFAULT,
    WEFT_TASK_MONITOR_STORE_WRITE_BATCH_SIZE_DEFAULT,
)
from weft.context import WeftContext, service_context_key
from weft.core.monitor import sql as monitor_sql
from weft.core.monitor.collation import MonitorTaskEventUpdate


class MonitorStoreError(RuntimeError):
    """Base error for Monitor collation store failures."""


class MonitorStoreUnavailable(MonitorStoreError):
    """Raised when the Monitor store cannot be used safely."""


class _SQLRunner(Protocol):
    def run(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
        *,
        fetch: bool = False,
    ) -> Iterable[tuple[Any, ...]]: ...

    def begin_immediate(self) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


@contextmanager
def _write_transaction(runner: _SQLRunner) -> Iterator[None]:
    """Run a Monitor store write in one explicit SQL transaction."""

    runner.begin_immediate()
    try:
        yield
        runner.commit()
    except Exception:
        runner.rollback()
        raise


@dataclass(frozen=True, slots=True)
class MonitorStoreConfig:
    """Runtime config for the Monitor-owned durable store."""

    schema_version: int = WEFT_MONITOR_SCHEMA_VERSION
    write_batch_size: int = WEFT_TASK_MONITOR_STORE_WRITE_BATCH_SIZE_DEFAULT

    def __post_init__(self) -> None:
        if self.write_batch_size <= 0:
            raise ValueError("Monitor store write_batch_size must be positive")


@dataclass(frozen=True, slots=True)
class MonitorStoreStatus:
    """Cached Monitor store availability summary."""

    enabled: bool
    available: bool
    schema_version: int | None = None
    checkpoint: int | None = None
    error: str | None = None

    def to_summary(self) -> dict[str, Any]:
        """Return a JSON-safe cached PONG summary."""

        return {
            "enabled": self.enabled,
            "available": self.available,
            "schema_version": self.schema_version,
            "checkpoint": self.checkpoint,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class MonitorStoreIngestResult:
    """Summary of one durable task-log ingest operation."""

    updates_written: int = 0
    tasks_updated: int = 0
    terminal_tasks: int = 0
    checkpoint_message_id: int | None = None
    checkpoint_written: bool = False

    def to_summary(self) -> dict[str, Any]:
        """Return a JSON-safe cached PONG summary."""

        return {
            "updates_written": self.updates_written,
            "tasks_updated": self.tasks_updated,
            "terminal_tasks": self.terminal_tasks,
            "checkpoint_message_id": self.checkpoint_message_id,
            "checkpoint_written": self.checkpoint_written,
        }


@dataclass(frozen=True, slots=True)
class MonitorTaskCollationRecord:
    """One durable task collation record."""

    context_key: str
    tid: str
    name: str | None
    runner: str | None
    parent_tid: str | None
    role: str | None
    status: str | None
    terminal_seen: bool
    terminal_event: str | None
    terminal_status: str | None
    terminal_message_id: int | None
    return_code: int | None
    first_message_id: int
    last_message_id: int
    first_seen_at_ns: int | None
    last_seen_at_ns: int | None
    started_at_ns: int | None
    completed_at_ns: int | None
    taskspec_summary: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    lifecycle: dict[str, Any] = field(default_factory=dict)
    resources: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    bookkeeping: dict[str, Any] = field(default_factory=dict)
    reserved_probe_needed: bool = False
    summary_emitted_at_ns: int | None = None
    raw_deleted_at_ns: int | None = None
    suspect_reason: str | None = None
    suspect_at_ns: int | None = None
    disposition_reason: str | None = None
    disposition_at_ns: int | None = None
    task_control_deleted_at_ns: int | None = None
    reserved_cleanup_checked_at_ns: int | None = None
    orphan_raw_recovery_checked_at_ns: int | None = None
    updated_at_ns: int = 0

    def to_summary(self) -> dict[str, Any]:
        """Return a compact JSON-safe operational summary."""

        return {
            "tid": self.tid,
            "name": self.name,
            "runner": self.runner,
            "parent_tid": self.parent_tid,
            "role": self.role,
            "status": self.status,
            "terminal_seen": self.terminal_seen,
            "terminal_event": self.terminal_event,
            "terminal_status": self.terminal_status,
            "terminal_message_id": self.terminal_message_id,
            "return_code": self.return_code,
            "first_message_id": self.first_message_id,
            "last_message_id": self.last_message_id,
            "first_seen_at_ns": self.first_seen_at_ns,
            "last_seen_at_ns": self.last_seen_at_ns,
            "started_at_ns": self.started_at_ns,
            "completed_at_ns": self.completed_at_ns,
            "taskspec_summary": self.taskspec_summary,
            "state": self.state,
            "resources": self.resources,
            "diagnostics": self.diagnostics,
            "reserved_probe_needed": self.reserved_probe_needed,
            "summary_emitted_at_ns": self.summary_emitted_at_ns,
            "raw_deleted_at_ns": self.raw_deleted_at_ns,
            "suspect_reason": self.suspect_reason,
            "suspect_at_ns": self.suspect_at_ns,
            "disposition_reason": self.disposition_reason,
            "disposition_at_ns": self.disposition_at_ns,
            "task_control_deleted_at_ns": self.task_control_deleted_at_ns,
            "reserved_cleanup_checked_at_ns": (self.reserved_cleanup_checked_at_ns),
            "orphan_raw_recovery_checked_at_ns": (
                self.orphan_raw_recovery_checked_at_ns
            ),
        }


@dataclass(frozen=True, slots=True)
class MonitorRawMessageRef:
    """Exact raw task-log message selected from the Monitor store."""

    queue: str
    message_id: int
    tid: str
    report_only: bool = False


@dataclass(frozen=True, slots=True)
class MonitorStoreRetirementResult:
    """Summary of physical Monitor-store row retirement work."""

    message_rows_deleted: int = 0
    message_tombstones_pruned: int = 0
    families_retired: int = 0
    affected_tids: int = 0

    def to_summary(self) -> dict[str, Any]:
        """Return a JSON-safe cached PONG summary."""

        return {
            "message_rows_deleted": self.message_rows_deleted,
            "message_tombstones_pruned": self.message_tombstones_pruned,
            "families_retired": self.families_retired,
            "affected_tids": self.affected_tids,
        }


@dataclass(frozen=True, slots=True)
class MonitorSummaryReadyTask:
    """One Monitor collation record ready for summary disposition."""

    record: MonitorTaskCollationRecord
    close_reason: str

    def to_summary(self) -> dict[str, Any]:
        """Return a compact JSON-safe operational summary."""

        return {
            "tid": self.record.tid,
            "close_reason": self.close_reason,
            "terminal_seen": self.record.terminal_seen,
            "last_message_id": self.record.last_message_id,
        }


@dataclass(frozen=True, slots=True)
class _MonitorTableNames:
    meta: str = WEFT_MONITOR_META_TABLE
    task_collations: str = WEFT_MONITOR_TASK_COLLATIONS_TABLE
    task_messages: str = WEFT_MONITOR_TASK_MESSAGES_TABLE


@dataclass(frozen=True, slots=True)
class _MonitorIndexSpec:
    name: str
    table: str
    columns: tuple[str, ...]


_monitor_tables = _MonitorTableNames()

_task_columns: tuple[str, ...] = (
    "context_key",
    "tid",
    "name",
    "runner",
    "parent_tid",
    "role",
    "status",
    "terminal_seen",
    "terminal_event",
    "terminal_status",
    "terminal_message_id",
    "return_code",
    "first_message_id",
    "last_message_id",
    "first_seen_at_ns",
    "last_seen_at_ns",
    "started_at_ns",
    "completed_at_ns",
    "taskspec_summary_json",
    "state_json",
    "lifecycle_json",
    "resources_json",
    "diagnostics_json",
    "bookkeeping_json",
    "reserved_probe_needed",
    "summary_emitted_at_ns",
    "raw_deleted_at_ns",
    "suspect_reason",
    "suspect_at_ns",
    "disposition_reason",
    "disposition_at_ns",
    "task_control_deleted_at_ns",
    "reserved_cleanup_checked_at_ns",
    "orphan_raw_recovery_checked_at_ns",
    "updated_at_ns",
)

_task_collation_additive_columns: tuple[tuple[str, str], ...] = (
    ("suspect_at_ns", "BIGINT NULL"),
    ("disposition_reason", "TEXT NULL"),
    ("disposition_at_ns", "BIGINT NULL"),
    ("task_control_deleted_at_ns", "BIGINT NULL"),
    ("reserved_cleanup_checked_at_ns", "BIGINT NULL"),
    ("orphan_raw_recovery_checked_at_ns", "BIGINT NULL"),
)

_monitor_index_specs: tuple[_MonitorIndexSpec, ...] = (
    _MonitorIndexSpec(
        name="idx_weft_monitor_collations_terminal",
        table=_monitor_tables.task_collations,
        columns=(
            "context_key",
            "terminal_seen",
            "raw_deleted_at_ns",
            "completed_at_ns",
        ),
    ),
    _MonitorIndexSpec(
        name="idx_weft_monitor_collations_last_seen",
        table=_monitor_tables.task_collations,
        columns=("context_key", "last_seen_at_ns"),
    ),
    _MonitorIndexSpec(
        name="idx_weft_monitor_collations_reserved_probe",
        table=_monitor_tables.task_collations,
        columns=("context_key", "reserved_probe_needed", "last_seen_at_ns"),
    ),
    _MonitorIndexSpec(
        name="idx_weft_monitor_collations_reserved_cleanup",
        table=_monitor_tables.task_collations,
        columns=(
            "context_key",
            "reserved_probe_needed",
            "reserved_cleanup_checked_at_ns",
            "last_message_id",
        ),
    ),
    _MonitorIndexSpec(
        name="idx_weft_monitor_collations_disposition_terminal",
        table=_monitor_tables.task_collations,
        columns=(
            "context_key",
            "terminal_seen",
            "disposition_at_ns",
            "last_message_id",
        ),
    ),
    _MonitorIndexSpec(
        name="idx_weft_monitor_collations_control_cleanup",
        table=_monitor_tables.task_collations,
        columns=(
            "context_key",
            "terminal_seen",
            "summary_emitted_at_ns",
            "task_control_deleted_at_ns",
            "disposition_at_ns",
            "last_message_id",
        ),
    ),
    _MonitorIndexSpec(
        name="idx_weft_monitor_collations_orphan_recovery",
        table=_monitor_tables.task_collations,
        columns=(
            "context_key",
            "raw_deleted_at_ns",
            "orphan_raw_recovery_checked_at_ns",
            "last_message_id",
        ),
    ),
    _MonitorIndexSpec(
        name="idx_weft_monitor_collations_disposition_open",
        table=_monitor_tables.task_collations,
        columns=("context_key", "disposition_at_ns", "last_message_id"),
    ),
    _MonitorIndexSpec(
        name="idx_weft_monitor_messages_tid",
        table=_monitor_tables.task_messages,
        columns=("context_key", "tid"),
    ),
    _MonitorIndexSpec(
        name="idx_weft_monitor_messages_deleted",
        table=_monitor_tables.task_messages,
        columns=("context_key", "deleted_at_ns", "message_id"),
    ),
)


def open_monitor_store(
    context: WeftContext,
    *,
    config: Mapping[str, Any] | None = None,
) -> MonitorStore:
    """Return a Monitor store bound to an already resolved Weft context."""

    store_config = MonitorStoreConfig(
        write_batch_size=int(
            (config or {}).get(
                "WEFT_TASK_MONITOR_STORE_WRITE_BATCH_SIZE",
                WEFT_TASK_MONITOR_STORE_WRITE_BATCH_SIZE_DEFAULT,
            )
        )
    )
    return MonitorStore(context, store_config=store_config)


class _MonitorTableAccess:
    """Small table access layer for Monitor-owned SQL operations."""

    def __init__(
        self,
        runner: _SQLRunner,
        *,
        context_key: str,
        backend_name: str,
        tables: _MonitorTableNames = _monitor_tables,
    ) -> None:
        self._runner = runner
        self._context_key = context_key
        self._backend_name = backend_name
        self._tables = tables

    def ensure_schema(self) -> None:
        """Create Monitor-owned schema objects idempotently."""

        self._runner.run(monitor_sql.create_meta_table(self._tables.meta))
        self._runner.run(
            monitor_sql.create_task_collations_table(self._tables.task_collations)
        )
        self._runner.run(
            monitor_sql.create_task_messages_table(self._tables.task_messages)
        )
        for column, definition in _task_collation_additive_columns:
            if not self._column_exists(self._tables.task_collations, column):
                self._runner.run(
                    monitor_sql.add_column(
                        self._tables.task_collations,
                        column,
                        definition,
                    )
                )
        for spec in _monitor_index_specs:
            self._runner.run(
                monitor_sql.create_index(spec.name, spec.table, spec.columns)
            )

    def _column_exists(self, table: str, column: str) -> bool:
        if self._backend_name == "postgres":
            rows = list(
                self._runner.run(
                    monitor_sql.postgres_column_exists(),
                    (table, column),
                    fetch=True,
                )
            )
            return bool(rows)
        rows = list(
            self._runner.run(
                monitor_sql.sqlite_table_info(table),
                fetch=True,
            )
        )
        return any(len(row) > 1 and str(row[1]) == column for row in rows)

    def read_meta(self, key: str) -> dict[str, Any] | None:
        """Read one Monitor metadata value."""

        rows = list(
            self._runner.run(
                monitor_sql.select_meta(self._tables.meta),
                (key,),
                fetch=True,
            )
        )
        if not rows:
            return None
        try:
            value = json.loads(str(rows[0][0]))
        except json.JSONDecodeError as exc:
            raise MonitorStoreUnavailable(f"invalid monitor meta value {key}") from exc
        if not isinstance(value, dict):
            raise MonitorStoreUnavailable(f"invalid monitor meta shape {key}")
        return cast(dict[str, Any], value)

    def write_meta(self, key: str, value: Mapping[str, Any]) -> None:
        """Write one Monitor metadata value."""

        self._runner.run(
            monitor_sql.upsert_meta(self._tables.meta),
            (key, _json(value), time.time_ns()),
        )

    def fetch_task(self, tid: str) -> MonitorTaskCollationRecord | None:
        """Read one task collation record."""

        rows = list(
            self._runner.run(
                monitor_sql.select_task(self._tables.task_collations, _task_columns),
                (self._context_key, tid),
                fetch=True,
            )
        )
        if not rows:
            return None
        return _record_from_row(rows[0])

    def list_unemitted_terminal_tasks(
        self,
        *,
        limit: int,
    ) -> tuple[MonitorTaskCollationRecord, ...]:
        """Return terminal task summaries that still need emission."""

        rows = self._runner.run(
            monitor_sql.select_unemitted_terminal_tasks(
                self._tables.task_collations,
                _task_columns,
            ),
            (self._context_key, int(limit)),
            fetch=True,
        )
        return tuple(_record_from_row(row) for row in rows)

    def list_summary_ready_tasks(
        self,
        *,
        limit: int,
        now_ns: int,
        retention_seconds: float,
        terminal_retention_seconds: float | None = None,
        stale_open_family_seconds: float = (
            WEFT_TASK_MONITOR_STALE_OPEN_FAMILY_SECONDS_DEFAULT
        ),
        include_suspected: bool = True,
    ) -> tuple[MonitorSummaryReadyTask, ...]:
        """Return retained task families ready for summary disposition."""

        if limit <= 0:
            return ()
        terminal_cutoff_ns = _retention_cutoff_ns(
            now_ns,
            retention_seconds
            if terminal_retention_seconds is None
            else terminal_retention_seconds,
        )
        open_cutoff_ns = _retention_cutoff_ns(now_ns, retention_seconds)
        terminal_rows = self._runner.run(
            monitor_sql.select_summary_ready_terminal_tasks(
                self._tables.task_collations,
                _task_columns,
            ),
            (self._context_key, terminal_cutoff_ns, int(limit)),
            fetch=True,
        )
        ready: list[MonitorSummaryReadyTask] = [
            MonitorSummaryReadyTask(
                record=_record_from_row(row),
                close_reason="terminal",
            )
            for row in terminal_rows
        ]
        remaining = max(0, int(limit) - len(ready))
        if include_suspected and remaining:
            open_rows = self._runner.run(
                monitor_sql.select_summary_ready_open_tasks(
                    self._tables.task_collations,
                    _task_columns,
                ),
                (self._context_key, open_cutoff_ns, remaining),
                fetch=True,
            )
            for row in open_rows:
                record = _record_from_row(row)
                if _suspected_inactive(record, now_ns=now_ns):
                    ready.append(
                        MonitorSummaryReadyTask(
                            record=record,
                            close_reason="suspected_inactive",
                        )
                    )
                elif _stale_open(
                    record, now_ns=now_ns, max_age_seconds=stale_open_family_seconds
                ):
                    ready.append(
                        MonitorSummaryReadyTask(
                            record=record,
                            close_reason="stale_open",
                        )
                    )
        return tuple(ready)

    def list_terminal_control_cleanup_ready_tasks(
        self,
        *,
        limit: int,
        now_ns: int,
        retention_seconds: float,
    ) -> tuple[MonitorTaskCollationRecord, ...]:
        """Return retained terminal families ready for control cleanup."""

        if limit <= 0:
            return ()
        cutoff_ns = _retention_cutoff_ns(now_ns, retention_seconds)
        rows = self._runner.run(
            monitor_sql.select_terminal_control_cleanup_ready_tasks(
                self._tables.task_collations,
                _task_columns,
            ),
            (self._context_key, cutoff_ns, int(limit)),
            fetch=True,
        )
        return tuple(_record_from_row(row) for row in rows)

    def list_terminal_control_deleted_disposition_backfill_tasks(
        self,
        *,
        limit: int,
    ) -> tuple[str, ...]:
        """Return terminal families cleaned before disposition was marked."""

        if limit <= 0:
            return ()
        rows = self._runner.run(
            monitor_sql.select_terminal_control_deleted_disposition_backfill_tasks(
                self._tables.task_collations,
            ),
            (self._context_key, int(limit)),
            fetch=True,
        )
        return tuple(str(row[0]) for row in rows)

    def list_reserved_cleanup_pending_tasks(
        self,
        *,
        limit: int,
    ) -> tuple[MonitorTaskCollationRecord, ...]:
        """Return retained families needing reserved-queue cleanup proof."""

        if limit <= 0:
            return ()
        rows = self._runner.run(
            monitor_sql.select_reserved_cleanup_pending_tasks(
                self._tables.task_collations,
                _task_columns,
            ),
            (self._context_key, int(limit)),
            fetch=True,
        )
        return tuple(_record_from_row(row) for row in rows)

    def mark_summary_emitted(
        self,
        tid: str,
        emitted_at_ns: int,
        *,
        suspect_reason: str | None = None,
    ) -> None:
        """Mark one terminal summary emitted."""

        self._runner.run(
            monitor_sql.mark_summary_emitted(self._tables.task_collations),
            (
                int(emitted_at_ns),
                suspect_reason,
                int(emitted_at_ns),
                self._context_key,
                tid,
            ),
        )

    def mark_task_control_deleted(self, tid: str, deleted_at_ns: int) -> None:
        """Mark one task family's control queues cleanup complete."""

        self._runner.run(
            monitor_sql.mark_task_control_deleted(self._tables.task_collations),
            (
                int(deleted_at_ns),
                int(deleted_at_ns),
                self._context_key,
                tid,
            ),
        )

    def mark_reserved_cleanup_checked(self, tid: str, checked_at_ns: int) -> None:
        """Mark one reserved-queue cleanup probe complete."""

        self._runner.run(
            monitor_sql.mark_reserved_cleanup_checked(self._tables.task_collations),
            (
                int(checked_at_ns),
                int(checked_at_ns),
                self._context_key,
                tid,
            ),
        )

    def mark_orphan_raw_recovery_checked(self, tid: str, checked_at_ns: int) -> None:
        """Mark one raw-log orphan recovery probe complete."""

        self._runner.run(
            monitor_sql.mark_orphan_raw_recovery_checked(self._tables.task_collations),
            (
                int(checked_at_ns),
                int(checked_at_ns),
                self._context_key,
                tid,
            ),
        )

    def mark_family_disposed(
        self,
        tid: str,
        disposed_at_ns: int,
        *,
        disposition_reason: str,
        suspect_reason: str | None = None,
        suspect_at_ns: int | None = None,
    ) -> None:
        """Mark one Monitor task family disposition complete."""

        self._runner.run(
            monitor_sql.mark_family_disposed(self._tables.task_collations),
            (
                disposition_reason,
                int(disposed_at_ns),
                suspect_reason,
                suspect_at_ns,
                int(disposed_at_ns),
                self._context_key,
                tid,
            ),
        )

    def list_deletable_task_log_messages(
        self,
        *,
        limit: int,
        require_summary: bool,
    ) -> tuple[MonitorRawMessageRef, ...]:
        """Return exact task-log messages proven deletable."""

        rows = self._runner.run(
            monitor_sql.select_deletable_task_log_messages(
                self._tables.task_messages,
                self._tables.task_collations,
                require_summary=require_summary,
            ),
            (self._context_key, int(limit)),
            fetch=True,
        )
        return tuple(
            MonitorRawMessageRef(
                queue=str(row[0]),
                message_id=int(row[1]),
                tid=str(row[2]),
            )
            for row in rows
        )

    def list_deletable_task_log_messages_for_tids(
        self,
        tids: Sequence[str],
        *,
        limit: int,
        require_summary: bool,
    ) -> tuple[MonitorRawMessageRef, ...]:
        """Return exact task-log messages proven deletable for known TIDs."""

        tid_tuple = tuple(str(tid) for tid in tids if tid)
        if not tid_tuple or limit <= 0:
            return ()
        rows = self._runner.run(
            monitor_sql.select_deletable_task_log_messages_for_tids(
                self._tables.task_messages,
                self._tables.task_collations,
                len(tid_tuple),
                require_summary=require_summary,
            ),
            (self._context_key, *tid_tuple, int(limit)),
            fetch=True,
        )
        return tuple(
            MonitorRawMessageRef(
                queue=str(row[0]),
                message_id=int(row[1]),
                tid=str(row[2]),
            )
            for row in rows
        )

    def raw_deleted_task_message_refs(
        self,
        *,
        limit: int,
    ) -> tuple[MonitorRawMessageRef, ...]:
        """Return child refs left after parent raw deletion was recorded."""

        if limit <= 0:
            return ()
        rows = self._runner.run(
            monitor_sql.select_raw_deleted_task_message_refs(
                self._tables.task_messages,
                self._tables.task_collations,
            ),
            (self._context_key, int(limit)),
            fetch=True,
        )
        return tuple(
            MonitorRawMessageRef(
                queue=str(row[0]),
                message_id=int(row[1]),
                tid=str(row[2]),
            )
            for row in rows
        )

    def task_message_refs_for_message_ids(
        self,
        message_ids: Sequence[int],
    ) -> tuple[tuple[str, int], ...]:
        """Return child message refs attached to exact raw message IDs."""

        ids = tuple(int(message_id) for message_id in message_ids)
        if not ids:
            return ()
        rows = self._runner.run(
            monitor_sql.select_task_message_refs_for_message_ids(
                self._tables.task_messages,
                len(ids),
            ),
            (self._context_key, *ids),
            fetch=True,
        )
        return tuple((str(row[0]), int(row[1])) for row in rows)

    def raw_deleted_task_log_recovery_tids(self, *, limit: int) -> tuple[str, ...]:
        """Return terminal families that may have orphan raw broker rows."""

        if limit <= 0:
            return ()
        rows = self._runner.run(
            monitor_sql.select_raw_deleted_task_log_recovery_tids(
                self._tables.task_collations,
                self._tables.task_messages,
            ),
            (self._context_key, int(limit)),
            fetch=True,
        )
        return tuple(str(row[0]) for row in rows)

    def deleted_task_message_refs(self, *, limit: int) -> tuple[tuple[str, int], ...]:
        """Return legacy child message tombstones for physical cleanup."""

        if limit <= 0:
            return ()
        rows = self._runner.run(
            monitor_sql.select_deleted_task_message_refs(self._tables.task_messages),
            (self._context_key, int(limit)),
            fetch=True,
        )
        return tuple((str(row[0]), int(row[1])) for row in rows)

    def delete_task_messages(self, message_ids: Sequence[int]) -> None:
        """Physically delete exact child raw-message references."""

        ids = tuple(int(message_id) for message_id in message_ids)
        if not ids:
            return
        self._runner.run(
            monitor_sql.delete_task_messages(self._tables.task_messages, len(ids)),
            (self._context_key, *ids),
        )

    def tids_for_message_ids(self, message_ids: Sequence[int]) -> set[str]:
        """Return TIDs attached to the given exact raw message IDs."""

        ids = tuple(int(message_id) for message_id in message_ids)
        if not ids:
            return set()
        rows = self._runner.run(
            monitor_sql.select_distinct_tids_for_message_ids(
                self._tables.task_messages,
                len(ids),
            ),
            (self._context_key, *ids),
            fetch=True,
        )
        return {str(row[0]) for row in rows}

    def has_task_messages(self, tid: str) -> bool:
        """Return whether a task still has known child raw-message refs."""

        rows = list(
            self._runner.run(
                monitor_sql.select_remaining_task_message(self._tables.task_messages),
                (self._context_key, tid),
                fetch=True,
            )
        )
        return bool(rows)

    def mark_raw_deleted(self, tid: str, deleted_at_ns: int) -> None:
        """Mark all known raw task-log rows for a task deleted."""

        self._runner.run(
            monitor_sql.mark_raw_deleted(self._tables.task_collations),
            (int(deleted_at_ns), int(deleted_at_ns), self._context_key, tid),
        )

    def reconcile_raw_deleted(self, deleted_at_ns: int) -> None:
        """Mark parent rows whose known child rows are all deleted."""

        self._runner.run(
            monitor_sql.reconcile_raw_deleted_tasks(
                self._tables.task_collations,
                self._tables.task_messages,
            ),
            (int(deleted_at_ns), int(deleted_at_ns), self._context_key),
        )

    def reconcile_raw_deleted_for_tids(
        self,
        tids: Sequence[str],
        deleted_at_ns: int,
    ) -> None:
        """Mark affected parent rows whose known child rows are all deleted."""

        tid_items = tuple(str(tid) for tid in tids)
        if not tid_items:
            return
        self._runner.run(
            monitor_sql.reconcile_raw_deleted_tasks_for_tids(
                self._tables.task_collations,
                self._tables.task_messages,
                len(tid_items),
            ),
            (
                int(deleted_at_ns),
                int(deleted_at_ns),
                self._context_key,
                *tid_items,
            ),
        )

    def list_retirable_task_collations(self, *, limit: int) -> tuple[str, ...]:
        """Return completed Monitor collation families safe to remove."""

        if limit <= 0:
            return ()
        rows = self._runner.run(
            monitor_sql.select_retirable_task_collations(
                self._tables.task_collations,
                self._tables.task_messages,
            ),
            (self._context_key, int(limit)),
            fetch=True,
        )
        return tuple(str(row[0]) for row in rows)

    def delete_task_collations(self, tids: Sequence[str]) -> None:
        """Physically delete completed Monitor collation families."""

        tid_items = tuple(str(tid) for tid in tids)
        if not tid_items:
            return
        self._runner.run(
            monitor_sql.delete_task_collations(
                self._tables.task_collations,
                len(tid_items),
            ),
            (self._context_key, *tid_items),
        )

    def upsert_record(self, record: MonitorTaskCollationRecord) -> None:
        """Upsert one task collation record."""

        self._runner.run(
            monitor_sql.upsert_task_record(
                self._tables.task_collations,
                _task_columns,
                conflict_columns=("context_key", "tid"),
            ),
            _record_values(record),
        )

    def upsert_message(self, update: MonitorTaskEventUpdate) -> None:
        """Upsert one child raw-message reference."""

        self._runner.run(
            monitor_sql.upsert_task_message(self._tables.task_messages),
            (
                self._context_key,
                update.tid,
                update.queue_name,
                update.message_id,
                update.event,
                update.status,
                update.observed_at_ns,
            ),
        )


class MonitorStore:
    """Durable store for Monitor-owned task-log collation state."""

    def __init__(
        self,
        context: WeftContext,
        *,
        store_config: MonitorStoreConfig | None = None,
    ) -> None:
        self._context = context
        self._context_key = service_context_key(context)
        self._config = store_config or MonitorStoreConfig()

    @property
    def context_key(self) -> str:
        """Stable context key stored with collation rows."""

        return self._context_key

    @property
    def schema_version(self) -> int:
        """Supported Monitor store schema version."""

        return self._config.schema_version

    def _access(self, runner: _SQLRunner) -> _MonitorTableAccess:
        return _MonitorTableAccess(
            runner,
            context_key=self._context_key,
            backend_name=self._context.backend_name,
        )

    def close(self) -> None:
        """Close the store.

        The current implementation uses short-lived broker connections per
        method, so there is no store-owned resource to close.
        """

    def ensure_schema(self) -> None:
        """Create or verify Monitor-owned tables idempotently.

        Spec: [SB-0.4a], [MF-5]
        """

        if (
            self._context.database_path is not None
            and not self._context.database_path.exists()
        ):
            raise MonitorStoreUnavailable("broker database does not exist")

        with self._context.broker() as broker:
            runner = _runner_from_broker(broker)
            access = self._access(runner)
            with _write_transaction(runner):
                access.ensure_schema()
                version = self._read_schema_version(access)
                if version is None:
                    access.write_meta(
                        WEFT_MONITOR_SCHEMA_VERSION_KEY,
                        {"version": self._config.schema_version},
                    )
                elif version > self._config.schema_version:
                    raise MonitorStoreUnavailable(
                        "Monitor store schema version "
                        f"{version} is newer than supported "
                        f"{self._config.schema_version}"
                    )
                elif version < self._config.schema_version:
                    access.write_meta(
                        WEFT_MONITOR_SCHEMA_VERSION_KEY,
                        {"version": self._config.schema_version},
                    )

    def get_checkpoint(self, queue_name: str) -> int | None:
        """Return the durable checkpoint for a queue."""

        with self._context.broker() as broker:
            value = self._access(_runner_from_broker(broker)).read_meta(
                f"{WEFT_MONITOR_CHECKPOINT_META_PREFIX}{queue_name}",
            )
        if value is None:
            return None
        message_id = value.get("message_id")
        return message_id if isinstance(message_id, int) else None

    def set_checkpoint(self, queue_name: str, message_id: int) -> None:
        """Set the durable checkpoint for a queue.

        Spec: [MF-5]
        """

        with self._context.broker() as broker:
            runner = _runner_from_broker(broker)
            access = self._access(runner)
            with _write_transaction(runner):
                access.write_meta(
                    f"{WEFT_MONITOR_CHECKPOINT_META_PREFIX}{queue_name}",
                    {"message_id": int(message_id)},
                )

    def upsert_task_event(self, update: MonitorTaskEventUpdate) -> None:
        """Merge one task-log event into the durable collation tables."""

        self.record_task_log_updates(
            update.queue_name,
            (update,),
            checkpoint_message_id=None,
        )

    def record_task_log_updates(
        self,
        queue_name: str,
        updates: Sequence[MonitorTaskEventUpdate],
        *,
        checkpoint_message_id: int | None,
    ) -> MonitorStoreIngestResult:
        """Merge task-log updates and then advance the durable checkpoint.

        Checkpoint advancement happens only after all update chunks commit.

        Spec: [MF-5], [OBS.13]
        """

        update_items = tuple(updates)
        if not update_items:
            if checkpoint_message_id is None:
                return MonitorStoreIngestResult()
            self.set_checkpoint(queue_name, checkpoint_message_id)
            return MonitorStoreIngestResult(
                checkpoint_message_id=int(checkpoint_message_id),
                checkpoint_written=True,
            )

        tasks_updated: set[str] = set()
        terminal_tasks: set[str] = set()
        with self._context.broker() as broker:
            runner = _runner_from_broker(broker)
            access = self._access(runner)
            for chunk in _chunks(update_items, self._config.write_batch_size):
                with _write_transaction(runner):
                    updates_by_tid: dict[str, list[MonitorTaskEventUpdate]] = {}
                    for update in chunk:
                        updates_by_tid.setdefault(update.tid, []).append(update)

                    for tid, tid_updates in updates_by_tid.items():
                        merged = access.fetch_task(tid)
                        for update in tid_updates:
                            merged = _merge_record(
                                self._context_key,
                                merged,
                                update,
                            )
                            tasks_updated.add(update.tid)
                            if update.terminal_seen:
                                terminal_tasks.add(update.tid)
                        if merged is not None:
                            access.upsert_record(merged)

                    for update in chunk:
                        access.upsert_message(update)
            if checkpoint_message_id is not None:
                with _write_transaction(runner):
                    access.write_meta(
                        f"{WEFT_MONITOR_CHECKPOINT_META_PREFIX}{queue_name}",
                        {"message_id": int(checkpoint_message_id)},
                    )

        return MonitorStoreIngestResult(
            updates_written=len(update_items),
            tasks_updated=len(tasks_updated),
            terminal_tasks=len(terminal_tasks),
            checkpoint_message_id=(
                int(checkpoint_message_id)
                if checkpoint_message_id is not None
                else None
            ),
            checkpoint_written=checkpoint_message_id is not None,
        )

    def get_task(
        self,
        tid: str,
        *,
        runner: _SQLRunner | None = None,
    ) -> MonitorTaskCollationRecord | None:
        """Return one durable task collation record."""

        if runner is not None:
            return self._access(runner).fetch_task(tid)
        with self._context.broker() as broker:
            return self._access(_runner_from_broker(broker)).fetch_task(tid)

    def list_unemitted_terminal_tasks(
        self,
        *,
        limit: int,
    ) -> tuple[MonitorTaskCollationRecord, ...]:
        """Return terminal task summaries that have not been emitted."""

        if limit <= 0:
            return ()
        with self._context.broker() as broker:
            return self._access(
                _runner_from_broker(broker)
            ).list_unemitted_terminal_tasks(
                limit=limit,
            )

    def list_summary_ready_tasks(
        self,
        *,
        limit: int,
        now_ns: int,
        retention_seconds: float,
        terminal_retention_seconds: float | None = None,
        stale_open_family_seconds: float = (
            WEFT_TASK_MONITOR_STALE_OPEN_FAMILY_SECONDS_DEFAULT
        ),
        include_suspected: bool = True,
    ) -> tuple[MonitorSummaryReadyTask, ...]:
        """Return retained task families ready for summary disposition."""

        if limit <= 0:
            return ()
        with self._context.broker() as broker:
            return self._access(_runner_from_broker(broker)).list_summary_ready_tasks(
                limit=limit,
                now_ns=now_ns,
                retention_seconds=retention_seconds,
                terminal_retention_seconds=terminal_retention_seconds,
                stale_open_family_seconds=stale_open_family_seconds,
                include_suspected=include_suspected,
            )

    def list_terminal_control_cleanup_ready_tasks(
        self,
        *,
        limit: int,
        now_ns: int,
        retention_seconds: float,
    ) -> tuple[MonitorTaskCollationRecord, ...]:
        """Return retained terminal families ready for control cleanup."""

        if limit <= 0:
            return ()
        with self._context.broker() as broker:
            return self._access(
                _runner_from_broker(broker)
            ).list_terminal_control_cleanup_ready_tasks(
                limit=limit,
                now_ns=now_ns,
                retention_seconds=retention_seconds,
            )

    def list_terminal_control_deleted_disposition_backfill_tasks(
        self,
        *,
        limit: int,
    ) -> tuple[str, ...]:
        """Return terminal families needing disposition repair.

        Spec: [MF-5], [OBS.13]
        """

        if limit <= 0:
            return ()
        with self._context.broker() as broker:
            return self._access(
                _runner_from_broker(broker)
            ).list_terminal_control_deleted_disposition_backfill_tasks(
                limit=limit,
            )

    def list_reserved_cleanup_pending_tasks(
        self,
        *,
        limit: int,
    ) -> tuple[MonitorTaskCollationRecord, ...]:
        """Return families needing reserved-queue cleanup proof.

        Spec: [MF-5], [OBS.13]
        """

        if limit <= 0:
            return ()
        with self._context.broker() as broker:
            return self._access(
                _runner_from_broker(broker)
            ).list_reserved_cleanup_pending_tasks(limit=limit)

    def mark_summary_emitted(
        self,
        tid: str,
        emitted_at_ns: int,
        *,
        suspect_reason: str | None = None,
    ) -> None:
        """Mark one terminal summary disposition complete.

        Spec: [MF-5]
        """

        self.mark_summaries_emitted(
            ((tid, suspect_reason),),
            emitted_at_ns,
        )

    def mark_summaries_emitted(
        self,
        items: Sequence[tuple[str, str | None]],
        emitted_at_ns: int,
    ) -> None:
        """Mark terminal summary emission complete for a batch of families.

        Spec: [MF-5]
        """

        item_tuple = tuple(items)
        if not item_tuple:
            return
        with self._context.broker() as broker:
            runner = _runner_from_broker(broker)
            access = self._access(runner)
            for chunk in _chunks(item_tuple, self._config.write_batch_size):
                with _write_transaction(runner):
                    for tid, suspect_reason in chunk:
                        access.mark_summary_emitted(
                            tid,
                            emitted_at_ns,
                            suspect_reason=suspect_reason,
                        )

    def mark_task_control_deleted(
        self,
        tid: str,
        deleted_at_ns: int,
    ) -> None:
        """Mark terminal task-local control queue cleanup complete."""

        self.mark_task_controls_deleted((tid,), deleted_at_ns)

    def mark_task_controls_deleted(
        self,
        tids: Sequence[str],
        deleted_at_ns: int,
    ) -> None:
        """Mark task-local control queue cleanup complete for a batch."""

        tid_tuple = tuple(str(tid) for tid in tids)
        if not tid_tuple:
            return
        with self._context.broker() as broker:
            runner = _runner_from_broker(broker)
            access = self._access(runner)
            for chunk in _chunks(tid_tuple, self._config.write_batch_size):
                with _write_transaction(runner):
                    for tid in chunk:
                        access.mark_task_control_deleted(tid, deleted_at_ns)

    def mark_reserved_cleanup_checked(
        self,
        tids: Sequence[str],
        checked_at_ns: int,
    ) -> None:
        """Mark reserved-queue cleanup probes complete for a batch.

        Spec: [MF-5], [OBS.13]
        """

        tid_tuple = tuple(str(tid) for tid in tids)
        if not tid_tuple:
            return
        with self._context.broker() as broker:
            runner = _runner_from_broker(broker)
            access = self._access(runner)
            for chunk in _chunks(tid_tuple, self._config.write_batch_size):
                with _write_transaction(runner):
                    for tid in chunk:
                        access.mark_reserved_cleanup_checked(tid, checked_at_ns)

    def mark_orphan_raw_recovery_checked(
        self,
        tids: Sequence[str],
        checked_at_ns: int,
    ) -> None:
        """Mark raw-log orphan recovery complete for a batch of families.

        Spec: [MF-5], [OBS.13], [OBS.17]
        """

        tid_tuple = tuple(str(tid) for tid in tids)
        if not tid_tuple:
            return
        with self._context.broker() as broker:
            runner = _runner_from_broker(broker)
            access = self._access(runner)
            for chunk in _chunks(tid_tuple, self._config.write_batch_size):
                with _write_transaction(runner):
                    for tid in chunk:
                        access.mark_orphan_raw_recovery_checked(tid, checked_at_ns)

    def mark_family_disposed(
        self,
        tid: str,
        disposed_at_ns: int,
        *,
        disposition_reason: str,
        suspect_reason: str | None = None,
        suspect_at_ns: int | None = None,
    ) -> None:
        """Mark one Monitor task family as dispositioned.

        This is a compact tombstone. It does not physically remove Monitor
        rows.

        Spec: [MF-5], [OBS.13]
        """

        self.mark_families_disposed(
            (
                (
                    tid,
                    disposition_reason,
                    suspect_reason,
                    suspect_at_ns,
                ),
            ),
            disposed_at_ns,
        )

    def mark_families_disposed(
        self,
        items: Sequence[tuple[str, str, str | None, int | None]],
        disposed_at_ns: int,
    ) -> None:
        """Mark Monitor families disposed in batches.

        This is a compact tombstone. It does not physically remove Monitor
        rows.

        Spec: [MF-5], [OBS.13]
        """

        item_tuple = tuple(items)
        if not item_tuple:
            return
        with self._context.broker() as broker:
            runner = _runner_from_broker(broker)
            access = self._access(runner)
            for chunk in _chunks(item_tuple, self._config.write_batch_size):
                with _write_transaction(runner):
                    for (
                        tid,
                        disposition_reason,
                        suspect_reason,
                        suspect_at_ns,
                    ) in chunk:
                        access.mark_family_disposed(
                            tid,
                            disposed_at_ns,
                            disposition_reason=disposition_reason,
                            suspect_reason=suspect_reason,
                            suspect_at_ns=suspect_at_ns,
                        )

    def list_deletable_task_log_messages(
        self,
        *,
        limit: int,
        require_summary: bool = True,
    ) -> tuple[MonitorRawMessageRef, ...]:
        """Return exact task-log messages proven deletable by Monitor state."""

        if limit <= 0:
            return ()
        with self._context.broker() as broker:
            return self._access(
                _runner_from_broker(broker)
            ).list_deletable_task_log_messages(
                limit=limit,
                require_summary=require_summary,
            )

    def list_deletable_task_log_messages_for_tids(
        self,
        tids: Sequence[str],
        *,
        limit: int,
        require_summary: bool = True,
    ) -> tuple[MonitorRawMessageRef, ...]:
        """Return exact deletable task-log refs for known TIDs."""

        tid_tuple = tuple(str(tid) for tid in tids if tid)
        if not tid_tuple or limit <= 0:
            return ()
        with self._context.broker() as broker:
            return self._access(
                _runner_from_broker(broker)
            ).list_deletable_task_log_messages_for_tids(
                tid_tuple,
                limit=limit,
                require_summary=require_summary,
            )

    def list_raw_deleted_task_log_recovery_tids(
        self,
        *,
        limit: int,
    ) -> tuple[str, ...]:
        """Return terminal families that may have orphan raw task-log rows."""

        if limit <= 0:
            return ()
        with self._context.broker() as broker:
            return self._access(
                _runner_from_broker(broker)
            ).raw_deleted_task_log_recovery_tids(limit=limit)

    def list_raw_deleted_task_message_refs(
        self,
        *,
        limit: int,
    ) -> tuple[MonitorRawMessageRef, ...]:
        """Return child refs left after parent raw deletion was recorded.

        Spec: [MF-5], [OBS.13], [OBS.17]
        """

        if limit <= 0:
            return ()
        with self._context.broker() as broker:
            return self._access(
                _runner_from_broker(broker)
            ).raw_deleted_task_message_refs(limit=limit)

    def delete_task_messages_after_raw_delete(
        self,
        message_ids: Sequence[int],
        *,
        deleted_at_ns: int | None = None,
    ) -> MonitorStoreRetirementResult:
        """Physically delete child refs after broker raw deletion succeeds.

        Spec: [MF-5], [OBS.17]
        """

        ids = tuple(int(message_id) for message_id in message_ids)
        if not ids:
            return MonitorStoreRetirementResult()
        timestamp = time.time_ns() if deleted_at_ns is None else int(deleted_at_ns)
        deleted_rows = 0
        affected_tids: set[str] = set()
        with self._context.broker() as broker:
            runner = _runner_from_broker(broker)
            access = self._access(runner)
            for chunk in _chunks(ids, self._config.write_batch_size):
                with _write_transaction(runner):
                    refs = access.task_message_refs_for_message_ids(chunk)
                    chunk_tids = {tid for tid, _message_id in refs}
                    chunk_message_ids = tuple(message_id for _tid, message_id in refs)
                    access.delete_task_messages(chunk_message_ids)
                    deleted_rows += len(chunk_message_ids)
                    affected_tids.update(chunk_tids)
                    access.reconcile_raw_deleted_for_tids(
                        sorted(chunk_tids),
                        timestamp,
                    )
        return MonitorStoreRetirementResult(
            message_rows_deleted=deleted_rows,
            affected_tids=len(affected_tids),
        )

    def prune_deleted_task_message_tombstones(
        self,
        *,
        limit: int,
        pruned_at_ns: int | None = None,
    ) -> MonitorStoreRetirementResult:
        """Physically remove legacy child-message tombstones.

        Spec: [MF-5], [OBS.13]
        """

        if limit <= 0:
            return MonitorStoreRetirementResult()
        timestamp = time.time_ns() if pruned_at_ns is None else int(pruned_at_ns)
        pruned_rows = 0
        affected_tids: set[str] = set()
        with self._context.broker() as broker:
            runner = _runner_from_broker(broker)
            access = self._access(runner)
            remaining = int(limit)
            while remaining > 0:
                chunk_limit = min(remaining, self._config.write_batch_size)
                with _write_transaction(runner):
                    chunk = access.deleted_task_message_refs(limit=chunk_limit)
                    if not chunk:
                        break
                    chunk_tids = {tid for tid, _message_id in chunk}
                    chunk_message_ids = tuple(message_id for _tid, message_id in chunk)
                    access.delete_task_messages(chunk_message_ids)
                    access.reconcile_raw_deleted_for_tids(
                        sorted(chunk_tids),
                        timestamp,
                    )
                pruned_rows += len(chunk_message_ids)
                affected_tids.update(chunk_tids)
                remaining -= len(chunk_message_ids)
        return MonitorStoreRetirementResult(
            message_tombstones_pruned=pruned_rows,
            affected_tids=len(affected_tids),
        )

    def retire_completed_collation_families(
        self,
        *,
        limit: int,
        retired_at_ns: int | None = None,
    ) -> MonitorStoreRetirementResult:
        """Physically remove completed Monitor collation families.

        The current retirement rule is intentionally conservative: parent rows
        are removed only after raw deletion, summary emission, disposition, and
        task-local control cleanup are all recorded.

        Spec: [MF-5], [OBS.13]
        """

        del retired_at_ns
        if limit <= 0:
            return MonitorStoreRetirementResult()
        retired = 0
        with self._context.broker() as broker:
            runner = _runner_from_broker(broker)
            access = self._access(runner)
            remaining = int(limit)
            while remaining > 0:
                chunk_limit = min(remaining, self._config.write_batch_size)
                with _write_transaction(runner):
                    chunk = access.list_retirable_task_collations(limit=chunk_limit)
                    if not chunk:
                        break
                    access.delete_task_collations(chunk)
                    retired += len(chunk)
                remaining -= len(chunk)
        return MonitorStoreRetirementResult(families_retired=retired)

    def status(self) -> MonitorStoreStatus:
        """Return a store status snapshot for cached PONG fields."""

        try:
            checkpoint = self.get_checkpoint(WEFT_GLOBAL_LOG_QUEUE)
        except Exception as exc:
            return MonitorStoreStatus(
                enabled=True,
                available=False,
                schema_version=None,
                checkpoint=None,
                error=str(exc),
            )
        return MonitorStoreStatus(
            enabled=True,
            available=True,
            schema_version=self._config.schema_version,
            checkpoint=checkpoint,
        )

    def _read_schema_version(self, access: _MonitorTableAccess) -> int | None:
        value = access.read_meta(WEFT_MONITOR_SCHEMA_VERSION_KEY)
        if value is None:
            return None
        version = value.get("version")
        return version if isinstance(version, int) else None


def _record_from_row(row: tuple[Any, ...]) -> MonitorTaskCollationRecord:
    values = dict(zip(_task_columns, row, strict=True))
    return MonitorTaskCollationRecord(
        context_key=str(values["context_key"]),
        tid=str(values["tid"]),
        name=_str_or_none(values["name"]),
        runner=_str_or_none(values["runner"]),
        parent_tid=_str_or_none(values["parent_tid"]),
        role=_str_or_none(values["role"]),
        status=_str_or_none(values["status"]),
        terminal_seen=bool(values["terminal_seen"]),
        terminal_event=_str_or_none(values["terminal_event"]),
        terminal_status=_str_or_none(values["terminal_status"]),
        terminal_message_id=_int_or_none(values["terminal_message_id"]),
        return_code=_int_or_none(values["return_code"]),
        first_message_id=int(values["first_message_id"]),
        last_message_id=int(values["last_message_id"]),
        first_seen_at_ns=_int_or_none(values["first_seen_at_ns"]),
        last_seen_at_ns=_int_or_none(values["last_seen_at_ns"]),
        started_at_ns=_int_or_none(values["started_at_ns"]),
        completed_at_ns=_int_or_none(values["completed_at_ns"]),
        taskspec_summary=_json_dict(values["taskspec_summary_json"]),
        state=_json_dict(values["state_json"]),
        lifecycle=_json_dict(values["lifecycle_json"]),
        resources=_json_dict(values["resources_json"]),
        diagnostics=_json_dict(values["diagnostics_json"]),
        bookkeeping=_json_dict(values["bookkeeping_json"]),
        reserved_probe_needed=bool(values["reserved_probe_needed"]),
        summary_emitted_at_ns=_int_or_none(values["summary_emitted_at_ns"]),
        raw_deleted_at_ns=_int_or_none(values["raw_deleted_at_ns"]),
        suspect_reason=_str_or_none(values["suspect_reason"]),
        suspect_at_ns=_int_or_none(values["suspect_at_ns"]),
        disposition_reason=_str_or_none(values["disposition_reason"]),
        disposition_at_ns=_int_or_none(values["disposition_at_ns"]),
        task_control_deleted_at_ns=_int_or_none(values["task_control_deleted_at_ns"]),
        reserved_cleanup_checked_at_ns=_int_or_none(
            values["reserved_cleanup_checked_at_ns"]
        ),
        orphan_raw_recovery_checked_at_ns=_int_or_none(
            values["orphan_raw_recovery_checked_at_ns"]
        ),
        updated_at_ns=int(values["updated_at_ns"]),
    )


def _merge_record(
    context_key: str,
    existing: MonitorTaskCollationRecord | None,
    update: MonitorTaskEventUpdate,
) -> MonitorTaskCollationRecord:
    now_ns = time.time_ns()
    if existing is None:
        return MonitorTaskCollationRecord(
            context_key=context_key,
            tid=update.tid,
            name=update.name,
            runner=update.runner,
            parent_tid=update.parent_tid,
            role=update.role,
            status=update.terminal_status or update.status,
            terminal_seen=update.terminal_seen,
            terminal_event=update.terminal_event,
            terminal_status=update.terminal_status,
            terminal_message_id=(update.message_id if update.terminal_seen else None),
            return_code=update.return_code,
            first_message_id=update.message_id,
            last_message_id=update.message_id,
            first_seen_at_ns=update.first_seen_at_ns,
            last_seen_at_ns=update.last_seen_at_ns,
            started_at_ns=update.started_at_ns,
            completed_at_ns=update.completed_at_ns,
            taskspec_summary=dict(update.taskspec_summary),
            state=dict(update.state),
            lifecycle=dict(update.lifecycle),
            resources=dict(update.resources),
            diagnostics=dict(update.diagnostics),
            bookkeeping=dict(update.bookkeeping),
            reserved_probe_needed=update.reserved_probe_needed,
            updated_at_ns=now_ns,
        )

    latest_update = update.message_id >= existing.last_message_id
    terminal_seen = existing.terminal_seen or update.terminal_seen
    terminal_message_id = existing.terminal_message_id
    terminal_event = existing.terminal_event
    terminal_status = existing.terminal_status
    if update.terminal_seen and (
        terminal_message_id is None or update.message_id >= terminal_message_id
    ):
        terminal_message_id = update.message_id
        terminal_event = update.terminal_event
        terminal_status = update.terminal_status

    status = existing.status
    if latest_update:
        status = update.terminal_status or update.status or existing.status
    if terminal_status is not None:
        status = terminal_status

    return MonitorTaskCollationRecord(
        context_key=context_key,
        tid=existing.tid,
        name=_prefer_latest(existing.name, update.name, latest_update),
        runner=_prefer_latest(existing.runner, update.runner, latest_update),
        parent_tid=_prefer_latest(
            existing.parent_tid, update.parent_tid, latest_update
        ),
        role=_prefer_latest(existing.role, update.role, latest_update),
        status=status,
        terminal_seen=terminal_seen,
        terminal_event=terminal_event,
        terminal_status=terminal_status,
        terminal_message_id=terminal_message_id,
        return_code=_prefer_latest(
            existing.return_code, update.return_code, latest_update
        ),
        first_message_id=min(existing.first_message_id, update.message_id),
        last_message_id=max(existing.last_message_id, update.message_id),
        first_seen_at_ns=_min_optional(
            existing.first_seen_at_ns, update.first_seen_at_ns
        ),
        last_seen_at_ns=_max_optional(existing.last_seen_at_ns, update.last_seen_at_ns),
        started_at_ns=_min_optional(existing.started_at_ns, update.started_at_ns),
        completed_at_ns=_prefer_latest(
            existing.completed_at_ns,
            update.completed_at_ns,
            update.completed_at_ns is not None,
        ),
        taskspec_summary=_merge_latest_dict(
            existing.taskspec_summary,
            update.taskspec_summary,
            latest_update,
        ),
        state=_merge_latest_dict(existing.state, update.state, latest_update),
        lifecycle=_merge_latest_dict(
            existing.lifecycle,
            update.lifecycle,
            latest_update,
        ),
        resources=_merge_resources(existing.resources, update.resources),
        diagnostics=_merge_latest_dict(
            existing.diagnostics,
            update.diagnostics,
            latest_update,
        ),
        bookkeeping=_merge_latest_dict(
            existing.bookkeeping,
            update.bookkeeping,
            latest_update,
        ),
        reserved_probe_needed=(
            existing.reserved_probe_needed or update.reserved_probe_needed
        ),
        summary_emitted_at_ns=existing.summary_emitted_at_ns,
        raw_deleted_at_ns=None,
        suspect_reason=existing.suspect_reason,
        suspect_at_ns=existing.suspect_at_ns,
        disposition_reason=existing.disposition_reason,
        disposition_at_ns=existing.disposition_at_ns,
        task_control_deleted_at_ns=existing.task_control_deleted_at_ns,
        reserved_cleanup_checked_at_ns=None,
        orphan_raw_recovery_checked_at_ns=None,
        updated_at_ns=now_ns,
    )


def _record_values(record: MonitorTaskCollationRecord) -> tuple[Any, ...]:
    return (
        record.context_key,
        record.tid,
        record.name,
        record.runner,
        record.parent_tid,
        record.role,
        record.status,
        int(record.terminal_seen),
        record.terminal_event,
        record.terminal_status,
        record.terminal_message_id,
        record.return_code,
        record.first_message_id,
        record.last_message_id,
        record.first_seen_at_ns,
        record.last_seen_at_ns,
        record.started_at_ns,
        record.completed_at_ns,
        _json(record.taskspec_summary),
        _json(record.state),
        _json(record.lifecycle),
        _json(record.resources),
        _json(record.diagnostics),
        _json(record.bookkeeping),
        int(record.reserved_probe_needed),
        record.summary_emitted_at_ns,
        record.raw_deleted_at_ns,
        record.suspect_reason,
        record.suspect_at_ns,
        record.disposition_reason,
        record.disposition_at_ns,
        record.task_control_deleted_at_ns,
        record.reserved_cleanup_checked_at_ns,
        record.orphan_raw_recovery_checked_at_ns,
        record.updated_at_ns,
    )


def _retention_cutoff_ns(now_ns: int, retention_seconds: float) -> int:
    retention_ns = int(retention_seconds * 1_000_000_000)
    return int(now_ns) - retention_ns


def _suspected_inactive(
    record: MonitorTaskCollationRecord,
    *,
    now_ns: int,
) -> bool:
    interval = _effective_reporting_interval_seconds(record)
    if interval is None:
        return False
    newest_age = max(0.0, (int(now_ns) - int(record.last_message_id)) / 1_000_000_000)
    return newest_age >= interval * 3


def _stale_open(
    record: MonitorTaskCollationRecord,
    *,
    now_ns: int,
    max_age_seconds: float,
) -> bool:
    if _effective_reporting_interval_seconds(record) is not None:
        return False
    newest_age = max(0.0, (int(now_ns) - int(record.last_message_id)) / 1_000_000_000)
    return newest_age >= max_age_seconds


def _effective_reporting_interval_seconds(
    record: MonitorTaskCollationRecord,
) -> float | None:
    spec = record.taskspec_summary.get("spec")
    if not isinstance(spec, Mapping):
        return None
    return _positive_float_or_none(spec.get("reporting_interval"))


def _positive_float_or_none(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        parsed = float(value)
    elif isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed > 0 else None


def _chunks[T](items: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _runner_from_broker(broker: Any) -> _SQLRunner:
    runner = getattr(broker, "_runner", None)
    if runner is None:
        raise MonitorStoreUnavailable("broker does not expose a SQL runner")
    return cast(_SQLRunner, runner)


def _json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, ensure_ascii=False, default=str)


def _json_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _prefer_latest[T](existing: T | None, update: T | None, latest: bool) -> T | None:
    if latest and update is not None:
        return update
    return existing if existing is not None else update


def _min_optional(left: int | None, right: int | None) -> int | None:
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right)


def _max_optional(left: int | None, right: int | None) -> int | None:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)


def _merge_latest_dict(
    existing: Mapping[str, Any],
    update: Mapping[str, Any],
    latest: bool,
) -> dict[str, Any]:
    merged = dict(existing)
    if latest or not merged:
        merged.update(update)
    else:
        for key, value in update.items():
            merged.setdefault(key, value)
    return merged


def _merge_resources(
    existing: Mapping[str, Any],
    update: Mapping[str, Any],
) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in update.items():
        if key.startswith("peak_"):
            previous = merged.get(key)
            if isinstance(previous, int | float) and isinstance(value, int | float):
                merged[key] = max(previous, value)
                continue
        merged[key] = value
    return merged
