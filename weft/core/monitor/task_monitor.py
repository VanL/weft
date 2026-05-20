"""Task monitor task primitive.

This module provides the task-shaped non-consuming scanner used by the
foreground task monitor command and the manager-supervised TaskMonitor service.
Built-in destructive cleanup is orchestrated by the TaskMonitor boundary and
uses reusable pruning policies for row eligibility.

Spec references:
- docs/specifications/01-Core_Components.md [CC-2.1], [CC-2.3]
- docs/specifications/03-Manager_Architecture.md [MA-1], [MA-3]
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from simplebroker import QueueStats
from simplebroker.ext import BrokerError
from weft._constants import (
    CONTROL_KILL,
    CONTROL_STOP,
    DEFAULT_FUNCTION_TARGET,
    LIVE_SERVICE_STATUSES,
    QUEUE_CTRL_IN_SUFFIX,
    QUEUE_CTRL_OUT_SUFFIX,
    QUEUE_INBOX_SUFFIX,
    QUEUE_OUTBOX_SUFFIX,
    QUEUE_RESERVED_SUFFIX,
    TASK_MONITOR_ACTIVITY_WAIT_CAP_SECONDS,
    TASK_MONITOR_CONTROL_CLEANUP_WORKER_LANE,
    TASK_MONITOR_HEARTBEAT_STARTUP_TIMEOUT_SECONDS,
    TASK_MONITOR_LOG_SUBDIR,
    TASK_MONITOR_POLICY_TASK_LOG_EXTERNAL_RAW,
    TASK_MONITOR_PONG_DETAIL_LIMIT,
    TASK_MONITOR_PROCESSOR_WORKER_LANE,
    TASK_MONITOR_RUNTIME_CLEANUP_SLICE_FAMILY_LIMIT,
    TASK_MONITOR_RUNTIME_CLEANUP_SLICE_SECONDS,
    TASK_MONITOR_SCHEMA_VERSION,
    TASK_MONITOR_TASK_LOG_SCAN_LIMIT_REACHED,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS,
    WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS_DEFAULT,
    WEFT_SERVICES_REGISTRY_QUEUE,
    WEFT_TASK_MONITOR_PROCESSOR_BUILTINS,
    WEFT_TID_MAPPINGS_QUEUE,
)
from weft.context import WeftContext
from weft.core.heartbeat import cancel_heartbeat, upsert_heartbeat
from weft.core.monitor.cleanup import (
    TaskMonitorCleanupConfig,
    run_task_monitor_cleanup,
)
from weft.core.monitor.collation import MonitorTaskEventUpdate, update_from_task_log_row
from weft.core.monitor.external_log import (
    ExternalTaskLogError,
    ExternalTaskLogSink,
    disabled_external_task_log_status,
)
from weft.core.monitor.runtime import (
    TaskMonitorCandidate,
    TaskMonitorProcessorRequest,
    TaskMonitorProcessorResult,
    TaskMonitorRuntimeConfig,
    build_task_monitor_cycle_snapshot,
    resolve_task_monitor_processor,
    task_monitor_candidate_class_counts,
)
from weft.core.monitor.store import (
    MonitorRawMessageRef,
    MonitorStore,
    MonitorStoreStatus,
    MonitorSummaryReadyTask,
    MonitorTaskCollationRecord,
    open_monitor_store,
)
from weft.core.monitor.task_log_scanner import GeneratorTaskLogScanner
from weft.core.pruning.apply import apply_exact_prune_candidates
from weft.core.pruning.models import CleanupPolicyStats, CleanupQueueStats
from weft.core.queue_window import QueueWindowRow, is_old_enough
from weft.core.serve_log import (
    build_serve_log_record,
    emit_serve_log_record,
    serve_log_allows,
    serve_log_level,
    truncate_serve_log_value,
)
from weft.core.service_convergence import (
    collect_service_owner_records,
    reduce_latest_by_service_owner,
)
from weft.core.tasks.base import BaseTask, ControlRequest, TaskWorkerResult
from weft.core.tasks.multiqueue_watcher import QueueMessageContext
from weft.core.taskspec import IOSection, SpecSection, StateSection, TaskSpec
from weft.ext import RunnerHandle
from weft.helpers import handle_has_live_host_process, iter_queue_entries

TaskMonitorCallback = Callable[[str, str, int], None]


@dataclass(frozen=True, slots=True)
class _TaskMonitorProcessorWork:
    """Custom processor work scanned by the reactor and run broker-free."""

    candidates: tuple[TaskMonitorCandidate, ...]
    last_timestamp: int | None
    events_scanned: int
    request: TaskMonitorProcessorRequest


@dataclass(frozen=True, slots=True)
class _TaskControlCleanupWork:
    """Terminal control cleanup work owned by the TaskMonitor maintenance lane."""

    request_id: str
    now_ns: int


@dataclass(frozen=True, slots=True)
class _TaskControlCleanupWorkerResult:
    """Terminal control cleanup result returned to the TaskMonitor reactor."""

    work: _TaskControlCleanupWork
    cleanup: _TaskControlCleanupResult
    monitor_status: MonitorStoreStatus | None = None


@dataclass(frozen=True, slots=True)
class _AppliedMonitorRawMessage:
    """Result of one table-backed raw task-log delete attempt."""

    candidate: MonitorRawMessageRef
    deleted: bool
    error: str | None


@dataclass(frozen=True, slots=True)
class _RawExternalPruneRef:
    """Exact raw task-log row emitted to the external log."""

    queue: str
    message_id: int
    report_only: bool = False


@dataclass(frozen=True, slots=True)
class _RetainedTaskLogIngestResult:
    """Cached result for one retained task-log FIFO ingestion pass."""

    scanned: int = 0
    selected: int = 0
    malformed_deleted: int = 0
    valid_ingested: int = 0
    raw_deleted: int = 0
    store_update_chunks: int = 0
    exact_delete_chunks: int = 0
    deleted_mark_chunks: int = 0
    checkpoint_message_id: int | None = None
    checkpoint_written: bool = False
    store_write_errors: tuple[str, ...] = ()
    raw_delete_errors: tuple[str, ...] = ()
    stop_reason: str | None = None
    oldest_too_young_age_seconds: float | None = None
    completed_fifo_high_water: bool = False

    @property
    def success(self) -> bool:
        """Return whether the pass finished without write/delete errors."""

        return not self.store_write_errors and not self.raw_delete_errors

    def to_summary(self) -> dict[str, Any]:
        """Return a JSON-safe cached PONG summary."""

        return {
            "scanned": self.scanned,
            "selected": self.selected,
            "malformed_deleted": self.malformed_deleted,
            "valid_ingested": self.valid_ingested,
            "raw_deleted": self.raw_deleted,
            "store_update_chunks": self.store_update_chunks,
            "exact_delete_chunks": self.exact_delete_chunks,
            "deleted_mark_chunks": self.deleted_mark_chunks,
            "checkpoint_message_id": self.checkpoint_message_id,
            "checkpoint_written": self.checkpoint_written,
            "store_write_errors": list(self.store_write_errors),
            "raw_delete_errors": list(self.raw_delete_errors),
            "stop_reason": self.stop_reason,
            "oldest_too_young_age_seconds": self.oldest_too_young_age_seconds,
            "completed_fifo_high_water": self.completed_fifo_high_water,
        }


@dataclass(frozen=True, slots=True)
class _TaskControlCleanupResult:
    """Cached result for terminal task-local control queue cleanup."""

    families_processed: int = 0
    families_disposed: int = 0
    queues_deleted: int = 0
    rows_estimated_deleted: int = 0
    skipped_nonstandard: int = 0
    reserved_families_processed: int = 0
    reserved_queues_deleted: int = 0
    reserved_rows_estimated_deleted: int = 0
    reserved_skipped_active: int = 0
    reserved_skipped_not_ready: int = 0
    pending: bool = False
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    family_limit_hit: bool = False
    deadline_hit: bool = False

    @property
    def success(self) -> bool:
        """Return whether the cleanup finished without delete errors."""

        return not self.errors

    def to_summary(self) -> dict[str, Any]:
        """Return a JSON-safe cached PONG summary."""

        return {
            "families_processed": self.families_processed,
            "families_disposed": self.families_disposed,
            "queues_deleted": self.queues_deleted,
            "rows_estimated_deleted": self.rows_estimated_deleted,
            "skipped_nonstandard": self.skipped_nonstandard,
            "reserved_families_processed": self.reserved_families_processed,
            "reserved_queues_deleted": self.reserved_queues_deleted,
            "reserved_rows_estimated_deleted": self.reserved_rows_estimated_deleted,
            "reserved_skipped_active": self.reserved_skipped_active,
            "reserved_skipped_not_ready": self.reserved_skipped_not_ready,
            "pending": self.pending,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "family_limit_hit": self.family_limit_hit,
            "deadline_hit": self.deadline_hit,
        }


@dataclass(frozen=True, slots=True)
class _ExactTaskLogDeleteResult:
    """Exact task-log delete result with IDs proven deleted by the broker."""

    deleted_ids: tuple[int, ...] = ()
    errors: tuple[str, ...] = ()


def _applied_monitor_raw_message(
    candidate: MonitorRawMessageRef,
    deleted: bool,
    error: str | None,
) -> _AppliedMonitorRawMessage:
    """Adapt exact-delete results for table-backed Monitor cleanup."""

    return _AppliedMonitorRawMessage(candidate=candidate, deleted=deleted, error=error)


def _monitor_monotonic() -> float:
    """Return monotonic time for TaskMonitor-local cleanup scheduling."""

    return time.monotonic()


def _applied_raw_external_message(
    candidate: _RawExternalPruneRef,
    deleted: bool,
    error: str | None,
) -> _AppliedMonitorRawMessage:
    """Adapt exact-delete results for raw external cleanup."""

    return _AppliedMonitorRawMessage(
        candidate=MonitorRawMessageRef(
            queue=candidate.queue,
            message_id=candidate.message_id,
            tid="",
        ),
        deleted=deleted,
        error=error,
    )


def _standard_task_control_queue_names(
    record: MonitorTaskCollationRecord,
) -> tuple[str, str] | None:
    """Return standard task-local control queues eligible for cleanup."""

    if record.role == "manager" or not record.tid.isdigit():
        return None
    expected_ctrl_in = f"T{record.tid}.{QUEUE_CTRL_IN_SUFFIX}"
    expected_ctrl_out = f"T{record.tid}.{QUEUE_CTRL_OUT_SUFFIX}"
    io_summary = record.taskspec_summary.get("io")
    if not isinstance(io_summary, Mapping):
        return None
    control = io_summary.get("control")
    if not isinstance(control, Mapping):
        return None
    if (
        control.get("ctrl_in") != expected_ctrl_in
        or control.get("ctrl_out") != expected_ctrl_out
    ):
        return None
    return expected_ctrl_in, expected_ctrl_out


def _reserved_queue_tid(queue_name: str) -> str | None:
    """Return the TID encoded in a standard task-local reserved queue name."""

    prefix = "T"
    suffix = f".{QUEUE_RESERVED_SUFFIX}"
    if not queue_name.startswith(prefix) or not queue_name.endswith(suffix):
        return None
    tid = queue_name[len(prefix) : -len(suffix)]
    return tid if tid.isdigit() else None


def make_task_monitor_taskspec(tid: str | None = None) -> TaskSpec:
    """Create the private synthetic TaskSpec for a foreground monitor run."""

    monitor_tid = tid or str(time.time_ns())
    prefix = f"T{monitor_tid}"
    return TaskSpec(
        tid=monitor_tid,
        name="task-monitor",
        spec=SpecSection(
            type="function",
            function_target=DEFAULT_FUNCTION_TARGET,
            persistent=True,
            enable_process_title=False,
        ),
        io=IOSection(
            inputs={"inbox": f"{prefix}.{QUEUE_INBOX_SUFFIX}"},
            outputs={"outbox": f"{prefix}.{QUEUE_OUTBOX_SUFFIX}"},
            control={
                "ctrl_in": f"{prefix}.{QUEUE_CTRL_IN_SUFFIX}",
                "ctrl_out": f"{prefix}.{QUEUE_CTRL_OUT_SUFFIX}",
            },
        ),
        state=StateSection(),
        metadata={"internal": True, "weft_runtime": "task_monitor"},
    )


def noop_task_monitor_target() -> None:
    """No-op target used only to satisfy the private synthetic TaskSpec."""


class TaskMonitorTask(BaseTask):
    """Task-shaped non-consuming task-log scanner and supervised monitor.

    The foreground command still owns summary construction and
    log/checkpoint writes via ``scan_once()``. The persistent task path wakes
    on its own inbox, scans task-log history by generator/high-water reads,
    and calls the configured processor. Built-in cleanup processors use the
    bounded exact-delete path; custom processors receive non-consuming
    task-log candidates.
    """

    def __init__(
        self,
        db: Path | str | Any,
        taskspec: TaskSpec,
        observer: TaskMonitorCallback | None = None,
        *,
        stop_event: threading.Event | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        self._persistent_service = observer is None
        self._task_observer = observer or self._ignore_task_log_entry
        self._first_cycle_pending = True
        self._wake_requested = False
        self._last_checkpoint: int | None = None
        self._last_cycle_at: int | None = None
        self._last_candidates_seen = 0
        self._last_candidate_class_counts: dict[str, int] = {}
        self._last_safe_to_delete_candidates = 0
        self._last_processor_success: bool | None = None
        self._last_error: str | None = None
        self._last_processed = 0
        self._last_deleted = 0
        self._last_reported = 0
        self._last_warnings: tuple[str, ...] = ()
        self._last_errors: tuple[str, ...] = ()
        self._last_prune_records_scanned = 0
        self._last_cleanup_queue_stats: tuple[dict[str, Any], ...] = ()
        self._last_cleanup_policy_stats: tuple[dict[str, Any], ...] = ()
        self._last_catchup_pending = False
        self._monitor_store: MonitorStore | None = None
        self._monitor_store_status = MonitorStoreStatus(
            enabled=False,
            available=False,
        )
        self._last_collation_rows_processed = 0
        self._last_collation_tasks_updated = 0
        self._last_collation_terminal_tasks = 0
        self._last_collation_summaries_emitted = 0
        self._last_collation_messages_marked_deleted = 0
        self._last_terminal_families_disposed = 0
        self._last_suspect_families_classified = 0
        self._last_control_families_processed = 0
        self._last_control_families_disposed = 0
        self._last_control_queues_deleted = 0
        self._last_control_rows_estimated_deleted = 0
        self._last_control_nonstandard_skipped = 0
        self._last_control_cleanup_pending = False
        self._last_control_rows_deleted = 0
        self._last_control_cleanup_family_limit_hit = False
        self._last_control_cleanup_deadline_hit = False
        self._last_reserved_families_processed = 0
        self._last_reserved_queues_deleted = 0
        self._last_reserved_rows_estimated_deleted = 0
        self._last_reserved_skipped_active = 0
        self._last_reserved_skipped_not_ready = 0
        self._last_reserved_rows_deleted = 0
        self._last_control_delete_errors: tuple[str, ...] = ()
        self._last_control_delete_warnings: tuple[str, ...] = ()
        self._last_retained_task_log_ingest = _RetainedTaskLogIngestResult()
        self._last_collation_store_error: str | None = None
        self._external_task_log_sink: ExternalTaskLogSink | None = None
        self._external_task_log_status = disabled_external_task_log_status(
            mode="collated",
            path=None,
        )
        self._processor_work_in_flight: _TaskMonitorProcessorWork | None = None
        self._control_cleanup_work_in_flight: _TaskControlCleanupWork | None = None
        self._heartbeat_registered = False
        self._heartbeat_error: str | None = None
        self._heartbeat_id = f"task-monitor:{taskspec.tid}"
        self._next_heartbeat_registration_attempt_monotonic = 0.0
        self._next_cycle_due_monotonic = 0.0
        self._serve_log_config_emitted = False
        self._serve_log_last_emit_ns: dict[str, int] = {}
        self._serve_log_last_state: dict[str, str] = {}
        super().__init__(db=db, taskspec=taskspec, stop_event=stop_event, config=config)
        self._monitor_config = TaskMonitorRuntimeConfig.from_config(self._config)
        self._configure_external_task_log_sink()
        self.register_pong_extension_provider(self._task_monitor_pong_extension)
        if self._persistent_service:
            self._activate_monitor()

    def _manager_tid_for_log(self) -> str:
        parent_tid = self.taskspec.metadata.get("parent_tid")
        return parent_tid if isinstance(parent_tid, str) and parent_tid else self.tid

    def _emit_task_monitor_log(
        self,
        event: str,
        *,
        required_level: str,
        severity: str = "info",
        **fields: Any,
    ) -> None:
        if not serve_log_allows(self._config, required_level):
            return
        manager_tid = self._manager_tid_for_log()
        try:
            record = build_serve_log_record(
                config=self._config,
                event=event,
                component="task_monitor",
                manager_tid=manager_tid,
                manager_tid_short=manager_tid[-10:],
                required_level=required_level,
                severity=severity,
                weft_context=str(self._monitor_context().root),
                runtime_handle_id=self._runtime_handle.id
                if self._runtime_handle is not None
                else None,
                pid=os.getpid(),
                loop_iteration=None,
                fields={"task_tid": self.tid, **fields},
            )
            emit_serve_log_record(record)
        except Exception:  # pragma: no cover - diagnostics must not affect monitor
            return

    def _emit_task_monitor_log_rate_limited(
        self,
        event: str,
        *,
        required_level: str,
        severity: str = "info",
        key: str | None = None,
        state: Mapping[str, Any] | None = None,
        force: bool = False,
        log_fields: Mapping[str, Any] | None = None,
    ) -> None:
        if not serve_log_allows(self._config, required_level):
            return
        fields = dict(log_fields or {})
        log_key = key or event
        now_ns = time.time_ns()
        interval_seconds = float(
            self._config.get(
                WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS,
                WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS_DEFAULT,
            )
        )
        interval_ns = int(interval_seconds * 1_000_000_000)
        state_key = json.dumps(
            truncate_serve_log_value(dict(state or fields)),
            sort_keys=True,
            default=str,
        )
        last_state = self._serve_log_last_state.get(log_key)
        last_emit_ns = self._serve_log_last_emit_ns.get(log_key, 0)
        if (
            not force
            and last_state == state_key
            and now_ns - last_emit_ns < interval_ns
        ):
            return
        self._serve_log_last_state[log_key] = state_key
        self._serve_log_last_emit_ns[log_key] = now_ns
        self._emit_task_monitor_log(
            event,
            required_level=required_level,
            severity=severity,
            **fields,
        )

    def _emit_task_monitor_config_once(self) -> None:
        if self._serve_log_config_emitted or serve_log_level(self._config) == "off":
            return
        self._serve_log_config_emitted = True
        self._emit_task_monitor_log(
            "task_monitor_config",
            required_level="info",
            enabled=self._monitor_config.enabled,
            interval_seconds=self._monitor_config.interval_seconds,
            batch_size=self._monitor_config.batch_size,
            catchup_interval_seconds=self._monitor_config.catchup_interval_seconds,
            task_log_scan_limit=self._monitor_config.task_log_scan_limit,
            store_write_batch_size=self._monitor_config.store_write_batch_size,
            task_log_retention_period_seconds=(
                self._monitor_config.task_log_retention_period_seconds
            ),
            task_log_external=self._external_task_log_status.to_summary(),
            processor=self._monitor_config.processor,
            log_sink=self._monitor_config.log_sink,
            collation_store_enabled=(self._monitor_config.collation_store_enabled),
            table_delete_enabled=self._monitor_config.table_delete_enabled,
        )

    def _activate_monitor(self) -> None:
        """Publish the running lifecycle for the persistent monitor service."""

        if self.taskspec.state.status != "created":
            return
        self.taskspec.mark_started(pid=os.getpid())
        self._report_state_change(event="task_spawning")
        self.taskspec.mark_running(pid=os.getpid())
        self._update_process_title("running")
        self._report_state_change(event="task_started")

    def _build_queue_configs(self) -> dict[str, dict[str, Any]]:
        """Configure task-local wake and control queues.

        The persistent monitor does not watch ``weft.log.tasks`` directly; a
        cycle scans that history by generator so task-log writes do not
        repeatedly wake the monitor on the same row.
        """

        return {
            self._queue_names["inbox"]: self._read_queue_config(
                self._handle_work_message
            ),
            self._queue_names["ctrl_in"]: self._peek_queue_config(
                self._handle_control_message
            ),
        }

    def next_wait_timeout(self) -> float:
        """Return the launcher wait timeout for the next monitor turn.

        The monitor is reactive to task-local wakeups and its heartbeat-driven
        schedule. Queue readiness is owned by the shared MultiQueueWatcher
        wait path; this method only exposes timer and local worker deadlines.
        """

        if self._has_pending_worker_results():
            return 0.0
        if not self._monitor_config.enabled:
            return TASK_MONITOR_ACTIVITY_WAIT_CAP_SECONDS
        if self._first_cycle_pending or self._wake_requested:
            return 0.0
        if (
            self._processor_work_in_flight is not None
            or self._control_cleanup_work_in_flight is not None
        ):
            return TASK_MONITOR_ACTIVITY_WAIT_CAP_SECONDS
        remaining = self._next_cycle_due_monotonic - time.monotonic()
        if remaining <= 0:
            return 0.0
        return min(remaining, TASK_MONITOR_ACTIVITY_WAIT_CAP_SECONDS)

    @staticmethod
    def _ignore_task_log_entry(
        queue_name: str,
        message: str,
        timestamp: int,
    ) -> None:
        del queue_name, message, timestamp

    def scan_once(
        self,
        *,
        since_timestamp: int | None = None,
        limit: int | None = None,
    ) -> int:
        """Invoke the callback for task-log entries without consuming them.

        Spec: [CC-2.3], [MF-5]
        """

        queue = self._queue(WEFT_GLOBAL_LOG_QUEUE)
        count = 0
        for message, timestamp in iter_queue_entries(
            queue,
            since_timestamp=since_timestamp,
        ):
            self._task_observer(WEFT_GLOBAL_LOG_QUEUE, message, timestamp)
            count += 1
            if limit is not None and count >= limit:
                break
        return count

    def process_once(self) -> None:
        """Run one persistent monitor scheduling turn.

        Spec: [CC-2.3], [MF-5]
        """

        self._drain_worker_results()
        if self.should_stop:
            return
        self._emit_task_monitor_config_once()
        self._drain_queue()
        if self.should_stop:
            self._maybe_emit_poll_report()
            return
        if not self._monitor_config.enabled:
            self._first_cycle_pending = False
            self._wake_requested = False
            self._set_activity("disabled", waiting_on=None)
            self._maybe_emit_poll_report()
            return

        self._ensure_heartbeat_registered()
        if self._processor_work_in_flight is not None:
            self._set_activity("processing", waiting_on=None)
            self._drain_worker_results()
            self._maybe_emit_poll_report()
            return
        if self._control_cleanup_work_in_flight is not None:
            self._set_activity("control_cleanup", waiting_on=None)
            self._drain_worker_results()
            self._maybe_emit_poll_report()
            return

        now_monotonic = time.monotonic()
        should_run = (
            self._first_cycle_pending
            or self._wake_requested
            or now_monotonic >= self._next_cycle_due_monotonic
        )
        if should_run:
            self._first_cycle_pending = False
            self._wake_requested = False
            self._run_monitor_cycle()
        else:
            self._set_activity("waiting", waiting_on=self._queue_names["inbox"])
        self._drain_worker_results()
        self._maybe_emit_poll_report()

    def _handle_work_message(
        self,
        message: str,
        timestamp: int,
        context: QueueMessageContext,
    ) -> None:
        del message, timestamp, context
        self._wake_requested = True

    def _handle_control_command(
        self,
        request: ControlRequest,
        context: QueueMessageContext,
    ) -> bool:
        if request.command in {CONTROL_STOP, CONTROL_KILL}:
            self._cancel_heartbeat()
        return super()._handle_control_command(request, context)

    def _control_snapshot_fields(self) -> dict[str, Any]:
        payload = super()._control_snapshot_fields()
        payload.update(
            {
                "role": "task_monitor",
                "processor": self._monitor_config.processor,
                "mode": "persistent",
                "interval_seconds": self._monitor_config.interval_seconds,
                "batch_size": self._monitor_config.batch_size,
                "catchup_interval_seconds": (
                    self._monitor_config.catchup_interval_seconds
                ),
                "task_log_scan_limit": self._monitor_config.task_log_scan_limit,
                "store_write_batch_size": (self._monitor_config.store_write_batch_size),
                "task_log_retention_period_seconds": (
                    self._monitor_config.task_log_retention_period_seconds
                ),
                "task_log_external": self._external_task_log_status.to_summary(),
                "log_sink": self._monitor_config.log_sink,
                "collation_store_enabled": (
                    self._monitor_config.collation_store_enabled
                ),
                "table_delete_enabled": self._monitor_config.table_delete_enabled,
                "last_cycle_at": self._last_cycle_at,
                "last_checkpoint": self._last_checkpoint,
                "last_candidates_seen": self._last_candidates_seen,
                "last_candidate_class_counts": dict(self._last_candidate_class_counts),
                "last_safe_to_delete_candidates": self._last_safe_to_delete_candidates,
                "last_processor_success": self._last_processor_success,
                "last_error": self._last_error,
                "last_processed": self._last_processed,
                "last_deleted": self._last_deleted,
                "last_reported": self._last_reported,
                "last_warnings": list(self._last_warnings),
                "last_errors": list(self._last_errors),
                "last_prune_records_scanned": self._last_prune_records_scanned,
                "last_cleanup_queue_stats": list(self._last_cleanup_queue_stats),
                "last_cleanup_policy_stats": list(self._last_cleanup_policy_stats),
                "last_catchup_pending": self._last_catchup_pending,
                "collation_store_available": self._monitor_store_status.available,
                "collation_schema_version": (self._monitor_store_status.schema_version),
                "collation_checkpoint": self._monitor_store_status.checkpoint,
                "last_collation_rows_processed": (self._last_collation_rows_processed),
                "last_collation_tasks_updated": self._last_collation_tasks_updated,
                "last_collation_terminal_tasks": (self._last_collation_terminal_tasks),
                "last_collation_summaries_emitted": (
                    self._last_collation_summaries_emitted
                ),
                "last_collation_messages_marked_deleted": (
                    self._last_collation_messages_marked_deleted
                ),
                "last_terminal_families_disposed": (
                    self._last_terminal_families_disposed
                ),
                "last_suspect_families_classified": (
                    self._last_suspect_families_classified
                ),
                "last_control_families_processed": (
                    self._last_control_families_processed
                ),
                "last_control_families_disposed": (
                    self._last_control_families_disposed
                ),
                "last_control_queues_deleted": self._last_control_queues_deleted,
                "last_control_rows_estimated_deleted": (
                    self._last_control_rows_estimated_deleted
                ),
                "last_control_nonstandard_skipped": (
                    self._last_control_nonstandard_skipped
                ),
                "last_control_cleanup_pending": self._last_control_cleanup_pending,
                "last_control_rows_deleted": self._last_control_rows_deleted,
                "last_control_cleanup_family_limit_hit": (
                    self._last_control_cleanup_family_limit_hit
                ),
                "last_control_cleanup_deadline_hit": (
                    self._last_control_cleanup_deadline_hit
                ),
                "last_reserved_families_processed": (
                    self._last_reserved_families_processed
                ),
                "last_reserved_queues_deleted": self._last_reserved_queues_deleted,
                "last_reserved_rows_estimated_deleted": (
                    self._last_reserved_rows_estimated_deleted
                ),
                "last_reserved_skipped_active": self._last_reserved_skipped_active,
                "last_reserved_skipped_not_ready": (
                    self._last_reserved_skipped_not_ready
                ),
                "last_reserved_rows_deleted": self._last_reserved_rows_deleted,
                "last_control_delete_errors": list(self._last_control_delete_errors),
                "last_control_delete_warnings": (
                    list(self._last_control_delete_warnings)
                ),
                "last_retained_task_log_ingest": (
                    self._last_retained_task_log_ingest.to_summary()
                ),
                "last_collation_store_error": self._last_collation_store_error,
                "processor_in_flight": self._processor_work_in_flight is not None,
                "control_cleanup_in_flight": (
                    self._control_cleanup_work_in_flight is not None
                ),
            }
        )
        return payload

    def _task_monitor_pong_extension(self) -> Mapping[str, Any]:
        """Return cached TaskMonitor diagnostics for the extended PONG payload."""

        now_monotonic = time.monotonic()
        next_registration_attempt = max(
            0.0,
            self._next_heartbeat_registration_attempt_monotonic - now_monotonic,
        )
        next_cycle_due = max(0.0, self._next_cycle_due_monotonic - now_monotonic)
        if (
            not self._monitor_config.enabled
            or self._first_cycle_pending
            or self._wake_requested
        ):
            next_cycle_due = 0.0

        return {
            "task_monitor": {
                "enabled": self._monitor_config.enabled,
                "mode": "persistent" if self._persistent_service else "observer",
                "processor": self._monitor_config.processor,
                "interval_seconds": self._monitor_config.interval_seconds,
                "batch_size": self._monitor_config.batch_size,
                "catchup_interval_seconds": (
                    self._monitor_config.catchup_interval_seconds
                ),
                "task_log_scan_limit": self._monitor_config.task_log_scan_limit,
                "store_write_batch_size": (self._monitor_config.store_write_batch_size),
                "task_log_retention_period_seconds": (
                    self._monitor_config.task_log_retention_period_seconds
                ),
                "task_log_external": self._external_task_log_status.to_summary(),
                "log_sink": self._monitor_config.log_sink,
                "collation_store": self._monitor_store_status.to_summary(),
                "table_delete_enabled": self._monitor_config.table_delete_enabled,
                "heartbeat": {
                    "registered": self._heartbeat_registered,
                    "id": self._heartbeat_id,
                    "error": self._heartbeat_error,
                    "next_registration_attempt_in_seconds": (next_registration_attempt),
                },
                "schedule": {
                    "first_cycle_pending": self._first_cycle_pending,
                    "wake_requested": self._wake_requested,
                    "last_cycle_at": self._last_cycle_at,
                    "last_checkpoint": self._last_checkpoint,
                    "next_cycle_due_in_seconds": next_cycle_due,
                    "catchup_pending": self._last_catchup_pending,
                },
                "last_cycle": {
                    "success": self._last_processor_success,
                    "error": self._last_error,
                    "processor_in_flight": (self._processor_work_in_flight is not None),
                    "control_cleanup_in_flight": (
                        self._control_cleanup_work_in_flight is not None
                    ),
                    "candidates_seen": self._last_candidates_seen,
                    "candidate_class_counts": dict(self._last_candidate_class_counts),
                    "safe_to_delete_candidates": (self._last_safe_to_delete_candidates),
                    "processed": self._last_processed,
                    "deleted": self._last_deleted,
                    "reported": self._last_reported,
                    "prune_records_scanned": self._last_prune_records_scanned,
                    "cleanup_queue_stats": list(self._last_cleanup_queue_stats)[
                        :TASK_MONITOR_PONG_DETAIL_LIMIT
                    ],
                    "cleanup_policy_stats": list(self._last_cleanup_policy_stats)[
                        :TASK_MONITOR_PONG_DETAIL_LIMIT
                    ],
                    "collation_rows_processed": (self._last_collation_rows_processed),
                    "collation_tasks_updated": self._last_collation_tasks_updated,
                    "collation_terminal_tasks": (self._last_collation_terminal_tasks),
                    "collation_summaries_emitted": (
                        self._last_collation_summaries_emitted
                    ),
                    "collation_messages_marked_deleted": (
                        self._last_collation_messages_marked_deleted
                    ),
                    "terminal_families_disposed": (
                        self._last_terminal_families_disposed
                    ),
                    "suspect_families_classified": (
                        self._last_suspect_families_classified
                    ),
                    "control_families_processed": (
                        self._last_control_families_processed
                    ),
                    "control_families_disposed": (self._last_control_families_disposed),
                    "control_queues_deleted": self._last_control_queues_deleted,
                    "control_rows_estimated_deleted": (
                        self._last_control_rows_estimated_deleted
                    ),
                    "control_nonstandard_skipped": (
                        self._last_control_nonstandard_skipped
                    ),
                    "control_cleanup_pending": self._last_control_cleanup_pending,
                    "control_rows_deleted": self._last_control_rows_deleted,
                    "control_cleanup_family_limit_hit": (
                        self._last_control_cleanup_family_limit_hit
                    ),
                    "control_cleanup_deadline_hit": (
                        self._last_control_cleanup_deadline_hit
                    ),
                    "reserved_families_processed": (
                        self._last_reserved_families_processed
                    ),
                    "reserved_queues_deleted": self._last_reserved_queues_deleted,
                    "reserved_rows_estimated_deleted": (
                        self._last_reserved_rows_estimated_deleted
                    ),
                    "reserved_skipped_active": self._last_reserved_skipped_active,
                    "reserved_skipped_not_ready": (
                        self._last_reserved_skipped_not_ready
                    ),
                    "reserved_rows_deleted": self._last_reserved_rows_deleted,
                    "control_delete_errors": list(self._last_control_delete_errors)[
                        :TASK_MONITOR_PONG_DETAIL_LIMIT
                    ],
                    "control_delete_warnings": list(self._last_control_delete_warnings)[
                        :TASK_MONITOR_PONG_DETAIL_LIMIT
                    ],
                    "retained_task_log_ingest": (
                        self._last_retained_task_log_ingest.to_summary()
                    ),
                    "collation_store_error": self._last_collation_store_error,
                    "warnings": list(self._last_warnings)[
                        :TASK_MONITOR_PONG_DETAIL_LIMIT
                    ],
                    "errors": list(self._last_errors)[:TASK_MONITOR_PONG_DETAIL_LIMIT],
                },
            }
        }

    def _monitor_context(self) -> WeftContext:
        return self._task_context()

    def _configure_external_task_log_sink(self) -> None:
        """Configure the external task-log sink and cache startup health."""

        if not self._monitor_config.task_log_external_enabled:
            self._external_task_log_sink = None
            self._external_task_log_status = disabled_external_task_log_status(
                mode=self._monitor_config.task_log_external_mode,
                path=self._monitor_config.task_log_external_path,
            )
            return
        path = Path(self._monitor_config.task_log_external_path)
        if not path.is_absolute():
            path = self._monitor_context().logs_dir / path
        sink = ExternalTaskLogSink(
            path=path,
            mode=self._monitor_config.task_log_external_mode,
            monitor_tid=self.tid,
        )
        sink.validate()
        self._external_task_log_sink = sink
        self._external_task_log_status = sink.status()

    def _refresh_external_task_log_status(self) -> None:
        sink = self._external_task_log_sink
        if sink is None:
            self._external_task_log_status = disabled_external_task_log_status(
                mode=self._monitor_config.task_log_external_mode,
                path=self._monitor_config.task_log_external_path,
            )
            return
        self._external_task_log_status = sink.status()

    def _task_log_deletion_owner(self) -> str:
        """Return the single task-log deletion owner for the current cycle."""

        if self._monitor_config.task_log_external_enabled:
            if self._monitor_config.task_log_external_mode == "raw":
                return "raw_external"
            return "collated_store"
        if self._monitor_config.collation_store_enabled:
            return "collated_store"
        if self._monitor_config.processor in {"delete", "report_only"}:
            return "cleanup_policy"
        return "none"

    def _ensure_monitor_store(self) -> MonitorStore | None:
        """Return the durable Monitor store when enabled and available."""

        if not self._monitor_config.collation_store_enabled:
            self._monitor_store_status = MonitorStoreStatus(
                enabled=False,
                available=False,
            )
            self._last_collation_store_error = None
            return None
        if self._monitor_store is not None:
            return self._monitor_store
        try:
            store = open_monitor_store(self._monitor_context(), config=self._config)
            store.ensure_schema()
            checkpoint = store.get_checkpoint(WEFT_GLOBAL_LOG_QUEUE)
        except (OSError, RuntimeError, ValueError) as exc:
            self._monitor_store = None
            self._last_collation_store_error = str(exc)
            self._monitor_store_status = MonitorStoreStatus(
                enabled=True,
                available=False,
                error=str(exc),
            )
            return None
        self._monitor_store = store
        self._last_collation_store_error = None
        self._monitor_store_status = MonitorStoreStatus(
            enabled=True,
            available=True,
            schema_version=store.schema_version,
            checkpoint=checkpoint,
        )
        return store

    def _run_monitor_store_cycle(self, *, now_ns: int, task_log_owner: str) -> None:
        """Collate task-log rows into the durable Monitor store.

        Spec: [MF-5], [OBS.13]
        """

        self._last_collation_rows_processed = 0
        self._last_collation_tasks_updated = 0
        self._last_collation_terminal_tasks = 0
        self._last_collation_summaries_emitted = 0
        self._last_collation_messages_marked_deleted = 0
        self._last_terminal_families_disposed = 0
        self._last_suspect_families_classified = 0
        self._last_control_families_processed = 0
        self._last_control_families_disposed = 0
        self._last_control_queues_deleted = 0
        self._last_control_rows_estimated_deleted = 0
        self._last_control_nonstandard_skipped = 0
        self._last_control_cleanup_pending = False
        self._last_control_rows_deleted = 0
        self._last_control_cleanup_family_limit_hit = False
        self._last_control_cleanup_deadline_hit = False
        self._last_reserved_families_processed = 0
        self._last_reserved_queues_deleted = 0
        self._last_reserved_rows_estimated_deleted = 0
        self._last_reserved_skipped_active = 0
        self._last_reserved_skipped_not_ready = 0
        self._last_reserved_rows_deleted = 0
        self._last_control_delete_errors = ()
        self._last_control_delete_warnings = ()
        self._last_retained_task_log_ingest = _RetainedTaskLogIngestResult()
        store = self._ensure_monitor_store()
        if store is None:
            return

        try:
            retained_ingest = self._ingest_retained_task_log_rows(
                store,
                now_ns=now_ns,
                apply=(
                    self._monitor_config.processor == "delete"
                    and task_log_owner == "collated_store"
                ),
            )
            self._last_retained_task_log_ingest = retained_ingest
            self._last_collation_rows_processed = retained_ingest.scanned
            if retained_ingest.completed_fifo_high_water:
                self._last_collation_summaries_emitted = (
                    self._emit_monitor_store_summaries(
                        store,
                        now_ns=now_ns,
                        apply_disposition=(
                            self._monitor_config.processor == "delete"
                            and task_log_owner == "collated_store"
                        ),
                    )
                )
                if (
                    self._monitor_config.processor == "delete"
                    and task_log_owner == "collated_store"
                ):
                    self._last_collation_messages_marked_deleted += (
                        self._delete_monitor_store_task_log_rows(store)
                    )
                    self._maybe_start_terminal_control_cleanup_worker(now_ns=now_ns)
            checkpoint = store.get_checkpoint(WEFT_GLOBAL_LOG_QUEUE)
            self._monitor_store_status = MonitorStoreStatus(
                enabled=True,
                available=True,
                schema_version=store.schema_version,
                checkpoint=checkpoint,
            )
            self._last_collation_store_error = None
        except (OSError, RuntimeError, ValueError) as exc:
            self._last_collation_store_error = str(exc)
            self._monitor_store_status = MonitorStoreStatus(
                enabled=True,
                available=False,
                schema_version=store.schema_version,
                checkpoint=self._monitor_store_status.checkpoint,
                error=str(exc),
            )

    def _ingest_retained_task_log_rows(
        self,
        store: MonitorStore,
        *,
        now_ns: int,
        apply: bool,
    ) -> _RetainedTaskLogIngestResult:
        """Fold retained visible task-log rows into the Monitor table."""

        scanner = GeneratorTaskLogScanner()
        window = scanner.scan_window(
            self._monitor_context(),
            WEFT_GLOBAL_LOG_QUEUE,
            scan_limit=self._monitor_config.task_log_scan_limit,
        )
        scanned = 0
        malformed_deleted = 0
        valid_ingested = 0
        raw_deleted = 0
        store_update_chunks = 0
        exact_delete_chunks = 0
        deleted_mark_chunks = 0
        store_errors: list[str] = []
        delete_errors: list[str] = []
        stop_reason = window.stop_reason
        last_selected_message_id: int | None = None
        terminal_tasks: set[str] = set()
        updated_tasks: set[str] = set()
        selected_rows: list[QueueWindowRow] = []
        valid_updates: list[MonitorTaskEventUpdate] = []
        valid_message_ids: set[int] = set()
        malformed_message_ids: set[int] = set()

        for row in window.rows:
            scanned += 1
            if len(selected_rows) >= self._monitor_config.batch_size:
                stop_reason = "batch_limit"
                break
            if row.malformed_reason is not None:
                selected_rows.append(row.raw)
                malformed_message_ids.add(row.raw.message_id)
                last_selected_message_id = row.raw.message_id
                continue

            update = update_from_task_log_row(row)
            if update is None:
                selected_rows.append(row.raw)
                malformed_message_ids.add(row.raw.message_id)
                last_selected_message_id = row.raw.message_id
                continue

            valid_updates.append(update)
            valid_message_ids.add(row.raw.message_id)
            last_selected_message_id = row.raw.message_id
            updated_tasks.add(update.tid)
            if update.terminal_seen:
                terminal_tasks.add(update.tid)

        if valid_updates:
            try:
                ingest = store.record_task_log_updates(
                    WEFT_GLOBAL_LOG_QUEUE,
                    tuple(valid_updates),
                    checkpoint_message_id=None,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                store_errors.append(str(exc))
                stop_reason = "store_write_error"
            else:
                valid_ingested += ingest.updates_written
                store_update_chunks += 1

        if apply and selected_rows and not store_errors:
            delete_result = self._delete_exact_task_log_rows(
                tuple(selected_rows),
                require_deleted=True,
            )
            exact_delete_chunks += 1
            deleted_ids = set(delete_result.deleted_ids)
            malformed_deleted += len(deleted_ids & malformed_message_ids)
            raw_deleted += len(deleted_ids & valid_message_ids)
            delete_errors.extend(delete_result.errors)
            deleted_valid_ids = tuple(sorted(deleted_ids & valid_message_ids))
            if deleted_valid_ids:
                try:
                    store.mark_messages_deleted(
                        deleted_valid_ids,
                        deleted_at_ns=now_ns,
                    )
                    deleted_mark_chunks += 1
                except (OSError, RuntimeError, ValueError) as exc:
                    store_errors.append(str(exc))
                    stop_reason = "store_deleted_mark_error"
            if delete_errors and stop_reason is None:
                stop_reason = "queue_delete_error"

        checkpoint_written = False
        if (
            last_selected_message_id is not None
            and not store_errors
            and not delete_errors
        ):
            store.set_checkpoint(WEFT_GLOBAL_LOG_QUEUE, last_selected_message_id)
            checkpoint_written = True
        if stop_reason is None and window.scan_limit_reached:
            stop_reason = window.stop_reason
        completed_high_water = (
            not store_errors
            and not delete_errors
            and stop_reason is None
            and not window.scan_limit_reached
        )
        self._last_collation_tasks_updated = len(updated_tasks)
        self._last_collation_terminal_tasks = len(terminal_tasks)
        self._last_collation_messages_marked_deleted = malformed_deleted + raw_deleted
        return _RetainedTaskLogIngestResult(
            scanned=scanned,
            selected=len(selected_rows),
            malformed_deleted=malformed_deleted,
            valid_ingested=valid_ingested,
            raw_deleted=raw_deleted,
            store_update_chunks=store_update_chunks,
            exact_delete_chunks=exact_delete_chunks,
            deleted_mark_chunks=deleted_mark_chunks,
            checkpoint_message_id=last_selected_message_id,
            checkpoint_written=checkpoint_written,
            store_write_errors=tuple(store_errors),
            raw_delete_errors=tuple(delete_errors),
            stop_reason=stop_reason,
            oldest_too_young_age_seconds=None,
            completed_fifo_high_water=completed_high_water,
        )

    def _delete_exact_task_log_rows(
        self,
        rows: tuple[QueueWindowRow, ...],
        *,
        require_deleted: bool = False,
    ) -> _ExactTaskLogDeleteResult:
        """Delete exact task-log rows and return IDs proven deleted."""

        refs = tuple(
            _RawExternalPruneRef(
                queue=row.queue,
                message_id=int(row.message_id),
            )
            for row in rows
        )
        applied = tuple(
            apply_exact_prune_candidates(
                self._monitor_context(),
                refs,
                apply_result=_applied_raw_external_message,
            )
        )
        errors = [result.error for result in applied if result.error is not None]
        if require_deleted:
            errors.extend(
                f"{result.candidate.queue}:{result.candidate.message_id} "
                "was not deleted by exact broker delete"
                for result in applied
                if not result.deleted and result.error is None
            )
        deleted_ids = tuple(
            result.candidate.message_id for result in applied if result.deleted
        )
        return _ExactTaskLogDeleteResult(
            deleted_ids=deleted_ids,
            errors=tuple(errors),
        )

    def _emit_monitor_store_summaries(
        self,
        store: MonitorStore,
        *,
        now_ns: int,
        apply_disposition: bool,
    ) -> int:
        """Emit terminal summary dispositions for Monitor collation rows.

        Spec: [MF-5]
        """

        summary_marks: list[tuple[str, str | None]] = []
        family_disposition_marks: list[tuple[str, str, str | None, int | None]] = []
        for ready in store.list_summary_ready_tasks(
            limit=self._monitor_config.batch_size,
            now_ns=now_ns,
            retention_seconds=self._monitor_config.task_log_retention_period_seconds,
            terminal_retention_seconds=0.0,
            stale_open_family_seconds=(self._monitor_config.stale_open_family_seconds),
        ):
            if ready.record.summary_emitted_at_ns is None:
                try:
                    self._emit_monitor_store_summary(
                        ready,
                        emitted_at_ns=now_ns,
                    )
                except (ExternalTaskLogError, OSError) as exc:
                    sink = self._external_task_log_sink
                    if sink is not None:
                        sink.record_blocked_deletions(1)
                        self._refresh_external_task_log_status()
                    self._last_collation_store_error = str(exc)
                    continue
                summary_marks.append(
                    (
                        ready.record.tid,
                        (
                            ready.close_reason
                            if ready.close_reason != "terminal"
                            else None
                        ),
                    )
                )
            if not apply_disposition:
                continue

            if ready.close_reason == "terminal":
                continue

            suspect_reason = (
                ready.close_reason if ready.close_reason != "terminal" else None
            )
            family_disposition_marks.append(
                (
                    ready.record.tid,
                    ready.close_reason,
                    suspect_reason,
                    now_ns if suspect_reason is not None else None,
                )
            )

        if summary_marks:
            store.mark_summaries_emitted(summary_marks, now_ns)
        if family_disposition_marks:
            store.mark_families_disposed(family_disposition_marks, now_ns)
            for (
                _tid,
                disposition_reason,
                _suspect_reason,
                _suspect_at_ns,
            ) in family_disposition_marks:
                if disposition_reason == "terminal":
                    self._last_terminal_families_disposed += 1
                else:
                    self._last_suspect_families_classified += 1
        self._refresh_external_task_log_status()
        return len(summary_marks)

    def _emit_monitor_store_summary(
        self,
        ready: MonitorSummaryReadyTask,
        *,
        emitted_at_ns: int,
    ) -> None:
        """Emit one terminal task summary to the configured monitor sink."""

        record = ready.record
        if self._monitor_config.task_log_external_enabled:
            sink = self._external_task_log_sink
            if sink is None:
                raise ExternalTaskLogError("external task-log sink is not configured")
            sink.emit_collated(
                task_summary=record.to_summary(),
                emitted_at_ns=emitted_at_ns,
                close_reason=ready.close_reason,
            )

        if self._monitor_config.log_sink == "none":
            return
        payload = {
            "schema_version": TASK_MONITOR_SCHEMA_VERSION,
            "record_type": "task_summary",
            "emitted_at": emitted_at_ns,
            "monitor_tid": self.tid,
            "close_reason": ready.close_reason,
            "task": record.to_summary(),
        }
        if self._monitor_config.log_sink == "disk":
            log_dir = self._monitor_context().logs_dir / TASK_MONITOR_LOG_SUBDIR
            run_date = datetime.now(UTC).date().isoformat()
            log_dir.mkdir(parents=True, exist_ok=True)
            with (log_dir / f"{run_date}.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, sort_keys=True, default=str))
                handle.write("\n")
            return
        print(json.dumps(payload, sort_keys=True, default=str), flush=True)

    def _delete_terminal_control_queues(
        self,
        record: MonitorTaskCollationRecord,
        *,
        queue_totals: Mapping[str, int] | None = None,
    ) -> _TaskControlCleanupResult:
        """Delete whole standard terminal task-local control queues for a task."""

        if record.task_control_deleted_at_ns is not None:
            return _TaskControlCleanupResult(families_processed=1)
        queue_names = _standard_task_control_queue_names(record)
        if queue_names is None:
            return _TaskControlCleanupResult(
                families_processed=1,
                skipped_nonstandard=1,
            )

        queues_deleted = 0
        rows_estimated_deleted = 0
        errors: list[str] = []
        warnings: list[str] = []
        ctx = self._monitor_context()
        for queue_name in queue_names:
            queue = ctx.queue(queue_name, persistent=False)
            try:
                rows_estimated_deleted += int((queue_totals or {}).get(queue_name, 0))
                if queue.delete():
                    queues_deleted += 1
            except (BrokerError, OSError, RuntimeError, ValueError) as exc:
                errors.append(f"{queue_name}: {exc}")
            finally:
                queue.close()

        return _TaskControlCleanupResult(
            families_processed=1,
            queues_deleted=queues_deleted,
            rows_estimated_deleted=0 if errors else rows_estimated_deleted,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

    def _active_runtime_tids(self) -> set[str]:
        """Return TIDs that have current service or live host-process evidence."""

        active_tids: set[str] = set()
        ctx = self._monitor_context()

        services = ctx.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
        service_entries: list[tuple[Mapping[str, Any], int]] = []
        try:
            for body, timestamp in iter_queue_entries(services):
                try:
                    payload = json.loads(body)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, Mapping):
                    service_entries.append((payload, int(timestamp)))
        finally:
            services.close()

        service_read = collect_service_owner_records(service_entries)
        latest_services = reduce_latest_by_service_owner(service_read.records)
        active_tids.update(
            record.owner_tid
            for record in latest_services
            if record.status in LIVE_SERVICE_STATUSES
        )

        mappings = ctx.queue(WEFT_TID_MAPPINGS_QUEUE, persistent=False)
        try:
            for body, _timestamp in iter_queue_entries(mappings):
                try:
                    payload = json.loads(body)
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, Mapping):
                    continue
                tid = payload.get("full")
                handle_payload = payload.get("runtime_handle")
                if not isinstance(tid, str) or not isinstance(handle_payload, Mapping):
                    continue
                try:
                    handle = RunnerHandle.from_dict(handle_payload)
                except ValueError:
                    continue
                if handle_has_live_host_process(handle):
                    active_tids.add(tid)
        finally:
            mappings.close()

        active_tids.add(self.tid)
        return active_tids

    def _queue_total_snapshot(self, *, patterns: tuple[str, ...]) -> dict[str, int]:
        """Return queue totals for runtime cleanup estimates using public broker APIs."""

        totals: dict[str, int] = {}
        if not patterns:
            return totals
        with self._monitor_context().broker() as broker:
            for pattern in patterns:
                for stats in broker.list_queue_stats(pattern=pattern):
                    totals[str(stats.queue)] = int(stats.total)
        return totals

    def _delete_runtime_reserved_queues(
        self,
        store: MonitorStore,
        *,
        now_ns: int,
        limit: int,
        active_tids: set[str],
        queue_stats: tuple[QueueStats, ...] | None = None,
        deadline_monotonic: float | None = None,
    ) -> _TaskControlCleanupResult:
        """Delete stale task-local reserved queues with monitor/store proof."""

        if limit <= 0:
            return _TaskControlCleanupResult()

        ctx = self._monitor_context()
        if queue_stats is None:
            with ctx.broker() as broker:
                queue_stats = tuple(broker.list_queue_stats(pattern="T*.reserved"))

        families_processed = 0
        queues_deleted = 0
        rows_estimated_deleted = 0
        skipped_active = 0
        skipped_not_ready = 0
        errors: list[str] = []
        warnings: list[str] = []
        more_pending = False
        deadline_hit = False

        for stats in queue_stats:
            if families_processed >= limit:
                more_pending = True
                break
            if (
                deadline_monotonic is not None
                and _monitor_monotonic() >= deadline_monotonic
            ):
                more_pending = True
                deadline_hit = True
                break
            queue_name = str(stats.queue)
            tid = _reserved_queue_tid(queue_name)
            if tid is None:
                continue
            if tid in active_tids:
                skipped_active += 1
                continue
            record = store.get_task(tid)
            ready = False
            if record is not None:
                ready = (
                    record.raw_deleted_at_ns is not None
                    or record.disposition_at_ns is not None
                    or (
                        record.terminal_seen
                        and record.summary_emitted_at_ns is not None
                    )
                )
            else:
                ready = is_old_enough(
                    int(tid),
                    now_ns,
                    self._monitor_config.task_log_retention_period_seconds,
                )
            if not ready:
                skipped_not_ready += 1
                continue

            families_processed += 1
            queue = ctx.queue(queue_name, persistent=False)
            try:
                rows_estimated_deleted += int(stats.total)
                if queue.delete():
                    queues_deleted += 1
            except (BrokerError, OSError, RuntimeError, ValueError) as exc:
                errors.append(f"{queue_name}: {exc}")
            finally:
                queue.close()

        return _TaskControlCleanupResult(
            reserved_families_processed=families_processed,
            reserved_queues_deleted=queues_deleted,
            reserved_rows_estimated_deleted=0 if errors else rows_estimated_deleted,
            reserved_skipped_active=skipped_active,
            reserved_skipped_not_ready=skipped_not_ready,
            pending=more_pending or bool(errors),
            errors=tuple(errors),
            warnings=tuple(warnings),
            deadline_hit=deadline_hit,
        )

    def _maybe_start_terminal_control_cleanup_worker(self, *, now_ns: int) -> None:
        """Start the TaskMonitor-owned terminal control cleanup worker if idle."""

        if self._control_cleanup_work_in_flight is not None:
            self._last_control_cleanup_pending = True
            return
        work = _TaskControlCleanupWork(
            request_id=f"{self.tid}:{now_ns}:control_cleanup",
            now_ns=now_ns,
        )
        self._control_cleanup_work_in_flight = work
        self._last_control_cleanup_pending = True
        self._set_activity("control_cleanup", waiting_on=None)
        try:
            self._submit_terminal_control_cleanup_worker(work)
        except RuntimeError as exc:
            self._control_cleanup_work_in_flight = None
            self._last_control_cleanup_pending = False
            self._last_control_delete_errors = (str(exc),)
            self._last_error = str(exc)
            self._last_processor_success = False

    def _submit_terminal_control_cleanup_worker(
        self,
        work: _TaskControlCleanupWork,
    ) -> threading.Thread:
        """Run terminal control cleanup on its dedicated durable-effects lane.

        This lane is a narrow TaskMonitor exception to the generic broker-free
        task-worker rule: it may open fresh broker/store handles, delete
        standard terminal task-local control queues, and mark the corresponding
        Monitor-store records. It must not mutate cached monitor fields
        directly; those are applied from its result on the reactor thread.

        Spec: [CC-2.3], [MF-5]
        """

        if self._worker_stopping.is_set():
            raise RuntimeError("Task worker lanes are stopping")

        def runner() -> None:
            try:
                try:
                    value = self._run_terminal_control_cleanup_worker(work)
                except BaseException as exc:
                    self._publish_worker_result(
                        TASK_MONITOR_CONTROL_CLEANUP_WORKER_LANE,
                        error=exc,
                    )
                else:
                    self._publish_worker_result(
                        TASK_MONITOR_CONTROL_CLEANUP_WORKER_LANE,
                        value=value,
                    )
            finally:
                current = threading.current_thread()
                with self._worker_lock:
                    self._worker_threads.discard(current)

        thread = threading.Thread(
            target=runner,
            name=f"weft-worker-{self.tid_short}-monitor-control-cleanup",
            daemon=True,
        )
        with self._worker_lock:
            self._worker_threads.add(thread)
        thread.start()
        return thread

    def _run_terminal_control_cleanup_worker(
        self,
        work: _TaskControlCleanupWork,
    ) -> _TaskControlCleanupWorkerResult:
        """Run terminal control cleanup with fresh thread-local broker handles."""

        try:
            store = open_monitor_store(self._monitor_context(), config=self._config)
            store.ensure_schema()
            cleanup = self._run_terminal_control_cleanup_slice(
                store,
                now_ns=work.now_ns,
            )
            status = MonitorStoreStatus(
                enabled=True,
                available=True,
                schema_version=store.schema_version,
                checkpoint=store.get_checkpoint(WEFT_GLOBAL_LOG_QUEUE),
            )
        except (BrokerError, OSError, RuntimeError, ValueError) as exc:
            cleanup = _TaskControlCleanupResult(
                pending=True,
                errors=(str(exc),),
            )
            status = MonitorStoreStatus(
                enabled=True,
                available=False,
                error=str(exc),
            )
        return _TaskControlCleanupWorkerResult(
            work=work,
            cleanup=cleanup,
            monitor_status=status,
        )

    def _run_terminal_control_cleanup_slice(
        self,
        store: MonitorStore,
        *,
        now_ns: int,
    ) -> _TaskControlCleanupResult:
        """Run one bounded terminal task-local control cleanup slice."""

        configured_limit = max(0, self._monitor_config.control_queue_delete_limit)
        control_limit = min(
            configured_limit,
            TASK_MONITOR_RUNTIME_CLEANUP_SLICE_FAMILY_LIMIT,
        )
        if control_limit <= 0:
            return _TaskControlCleanupResult()
        deadline_monotonic = (
            _monitor_monotonic() + TASK_MONITOR_RUNTIME_CLEANUP_SLICE_SECONDS
        )
        ready_records = store.list_terminal_control_cleanup_ready_tasks(
            limit=control_limit + 1,
            now_ns=now_ns,
            retention_seconds=self._monitor_config.task_log_retention_period_seconds,
        )
        records = ready_records[:control_limit]
        active_tids = self._active_runtime_tids()
        control_queue_totals = (
            self._queue_total_snapshot(
                patterns=(
                    f"T*.{QUEUE_CTRL_IN_SUFFIX}",
                    f"T*.{QUEUE_CTRL_OUT_SUFFIX}",
                ),
            )
            if records
            else {}
        )
        with self._monitor_context().broker() as broker:
            reserved_queue_stats = tuple(broker.list_queue_stats(pattern="T*.reserved"))
        reserved_has_work = any(
            (tid := _reserved_queue_tid(str(stats.queue))) is not None
            and tid not in active_tids
            for stats in reserved_queue_stats
        )
        families_processed = 0
        families_disposed = 0
        queues_deleted = 0
        rows_estimated_deleted = 0
        skipped_nonstandard = 0
        errors: list[str] = []
        warnings: list[str] = []
        control_delete_marks: list[str] = []
        family_disposition_marks: list[tuple[str, str, str | None, int | None]] = []
        control_index = 0
        family_budget_used = 0
        deadline_hit = False

        def deadline_reached() -> bool:
            return _monitor_monotonic() >= deadline_monotonic

        def run_control_subslice(max_records: int) -> None:
            nonlocal control_index
            nonlocal deadline_hit
            nonlocal families_processed
            nonlocal families_disposed
            nonlocal queues_deleted
            nonlocal rows_estimated_deleted
            nonlocal skipped_nonstandard
            nonlocal family_budget_used
            while control_index < len(records) and max_records > 0:
                if family_budget_used >= control_limit:
                    break
                if deadline_reached():
                    deadline_hit = True
                    break
                record = records[control_index]
                control_index += 1
                max_records -= 1
                family_budget_used += 1
                if record.tid in active_tids:
                    warnings.append(f"{record.tid}: skipped active runtime owner")
                    continue
                result = self._delete_terminal_control_queues(
                    record,
                    queue_totals=control_queue_totals,
                )
                families_processed += result.families_processed
                queues_deleted += result.queues_deleted
                rows_estimated_deleted += result.rows_estimated_deleted
                skipped_nonstandard += result.skipped_nonstandard
                errors.extend(result.errors)
                warnings.extend(result.warnings)
                if not result.success:
                    continue
                control_delete_marks.append(record.tid)
                if record.disposition_at_ns is None:
                    families_disposed += 1
                    family_disposition_marks.append(
                        (record.tid, "terminal", None, None)
                    )

        first_control_budget = control_limit
        if records and reserved_has_work and control_limit > 1:
            first_control_budget = max(1, control_limit // 2)
        run_control_subslice(first_control_budget)

        remaining_limit = max(0, control_limit - family_budget_used)
        if deadline_reached():
            deadline_hit = bool(records[control_index:] or reserved_has_work)

        if deadline_hit:
            reserved_cleanup = _TaskControlCleanupResult(pending=reserved_has_work)
        else:
            reserved_cleanup = self._delete_runtime_reserved_queues(
                store,
                now_ns=now_ns,
                limit=remaining_limit,
                active_tids=active_tids,
                queue_stats=reserved_queue_stats,
                deadline_monotonic=deadline_monotonic,
            )
            family_budget_used += reserved_cleanup.reserved_families_processed
            if reserved_cleanup.deadline_hit:
                deadline_hit = True

        errors.extend(reserved_cleanup.errors)
        warnings.extend(reserved_cleanup.warnings)

        remaining_limit = max(0, control_limit - family_budget_used)
        if remaining_limit > 0 and not deadline_hit:
            run_control_subslice(remaining_limit)

        family_limit_hit = family_budget_used >= control_limit and (
            control_index < len(ready_records) or reserved_cleanup.pending
        )

        if control_delete_marks:
            try:
                store.mark_task_controls_deleted(control_delete_marks, now_ns)
            except (OSError, RuntimeError, ValueError) as exc:
                errors.append(f"mark_task_controls_deleted: {exc}")
                family_disposition_marks = []
                families_disposed = 0
        if family_disposition_marks:
            try:
                store.mark_families_disposed(family_disposition_marks, now_ns)
            except (OSError, RuntimeError, ValueError) as exc:
                errors.append(f"mark_families_disposed: {exc}")
                families_disposed = 0

        pending = (
            control_index < len(ready_records)
            or reserved_cleanup.pending
            or bool(errors)
            or family_limit_hit
            or deadline_hit
        )
        return _TaskControlCleanupResult(
            families_processed=families_processed,
            families_disposed=families_disposed,
            queues_deleted=queues_deleted,
            rows_estimated_deleted=rows_estimated_deleted,
            skipped_nonstandard=skipped_nonstandard,
            reserved_families_processed=(reserved_cleanup.reserved_families_processed),
            reserved_queues_deleted=reserved_cleanup.reserved_queues_deleted,
            reserved_rows_estimated_deleted=(
                reserved_cleanup.reserved_rows_estimated_deleted
            ),
            reserved_skipped_active=reserved_cleanup.reserved_skipped_active,
            reserved_skipped_not_ready=reserved_cleanup.reserved_skipped_not_ready,
            pending=pending,
            errors=tuple(errors),
            warnings=tuple(warnings),
            family_limit_hit=family_limit_hit,
            deadline_hit=deadline_hit or reserved_cleanup.deadline_hit,
        )

    def _delete_monitor_store_task_log_rows(self, store: MonitorStore) -> int:
        """Delete exact task-log rows proven by durable Monitor collation.

        Spec: [MF-5], [OBS.17]
        """

        refs = store.list_deletable_task_log_messages(
            limit=self._monitor_config.batch_size,
            require_summary=True,
        )
        if not refs:
            return 0
        applied = apply_exact_prune_candidates(
            self._monitor_context(),
            refs,
            apply_result=_applied_monitor_raw_message,
        )
        if any(
            result.error is not None and "per-row status unavailable" in result.error
            for result in applied
        ):
            applied = apply_exact_prune_candidates(
                self._monitor_context(),
                refs,
                apply_result=_applied_monitor_raw_message,
                exact_status=True,
            )
        reconciled_ids = tuple(
            result.candidate.message_id
            for result in applied
            if result.deleted
            or (result.error is None and not result.candidate.report_only)
        )
        if reconciled_ids:
            store.mark_messages_deleted(reconciled_ids)
        errors = tuple(result.error for result in applied if result.error is not None)
        if errors:
            self._last_collation_store_error = "; ".join(errors)
        return len(reconciled_ids)

    def _ensure_heartbeat_registered(self) -> None:
        if self._heartbeat_registered:
            return
        now_monotonic = time.monotonic()
        if now_monotonic < self._next_heartbeat_registration_attempt_monotonic:
            return
        try:
            upsert_heartbeat(
                self._monitor_context(),
                heartbeat_id=self._heartbeat_id,
                interval_seconds=self._monitor_config.interval_seconds,
                destination_queue=self._queue_names["inbox"],
                message={
                    "type": "task_monitor_wakeup",
                    "monitor_tid": self.tid,
                },
                startup_timeout=TASK_MONITOR_HEARTBEAT_STARTUP_TIMEOUT_SECONDS,
            )
        except (BrokerError, OSError, RuntimeError, ValueError) as exc:
            self._heartbeat_error = f"heartbeat registration failed: {exc}"
            self._last_error = self._heartbeat_error
            self._heartbeat_registered = False
            self._next_heartbeat_registration_attempt_monotonic = (
                now_monotonic + TASK_MONITOR_ACTIVITY_WAIT_CAP_SECONDS
            )
            return
        self._heartbeat_error = None
        self._heartbeat_registered = True
        self._next_heartbeat_registration_attempt_monotonic = 0.0

    def _cancel_heartbeat(self) -> None:
        if not self._heartbeat_registered:
            return
        try:
            cancel_heartbeat(self._monitor_context(), heartbeat_id=self._heartbeat_id)
        except (BrokerError, OSError, RuntimeError, ValueError):
            return
        finally:
            self._heartbeat_registered = False

    def _scan_task_log_candidates(
        self,
    ) -> tuple[tuple[TaskMonitorCandidate, ...], int | None, int]:
        snapshot = build_task_monitor_cycle_snapshot(
            self._monitor_context(),
            since_timestamp=self._last_checkpoint,
            limit=self._monitor_config.batch_size,
            monitor_tid=self.tid,
            observer=self._task_observer,
        )
        return (
            snapshot.candidates,
            snapshot.last_task_log_timestamp,
            snapshot.events_scanned,
        )

    def _run_monitor_cycle(self) -> None:
        now_ns = time.time_ns()
        self._last_cycle_at = now_ns
        task_log_owner = self._task_log_deletion_owner()
        sink = self._external_task_log_sink
        if sink is not None:
            sink.reset_cycle_counts()
            self._refresh_external_task_log_status()
        if task_log_owner == "raw_external":
            self._last_collation_rows_processed = 0
            self._last_collation_tasks_updated = 0
            self._last_collation_terminal_tasks = 0
            self._last_collation_summaries_emitted = 0
            self._last_collation_messages_marked_deleted = 0
            self._last_terminal_families_disposed = 0
            self._last_suspect_families_classified = 0
            self._last_control_families_processed = 0
            self._last_control_families_disposed = 0
            self._last_control_queues_deleted = 0
            self._last_control_rows_estimated_deleted = 0
            self._last_control_nonstandard_skipped = 0
            self._last_control_cleanup_pending = False
            self._last_control_rows_deleted = 0
            self._last_control_delete_errors = ()
            self._last_control_delete_warnings = ()
        else:
            self._run_monitor_store_cycle(now_ns=now_ns, task_log_owner=task_log_owner)
        if self._monitor_config.processor in WEFT_TASK_MONITOR_PROCESSOR_BUILTINS:
            candidates: tuple[TaskMonitorCandidate, ...] = ()
            last_timestamp = None
            events_scanned = 0
            self._last_candidates_seen = 0
            self._last_candidate_class_counts = {}
            self._last_safe_to_delete_candidates = 0
            self._last_prune_records_scanned = 0
            self._last_cleanup_queue_stats = ()
            self._last_cleanup_policy_stats = ()
        else:
            self._set_activity("scanning", waiting_on=WEFT_GLOBAL_LOG_QUEUE)
            candidates, last_timestamp, events_scanned = (
                self._scan_task_log_candidates()
            )
            self._last_candidates_seen = len(candidates)
            self._last_candidate_class_counts = task_monitor_candidate_class_counts(
                candidates
            )
            self._last_safe_to_delete_candidates = sum(
                1 for candidate in candidates if candidate.safe_to_delete
            )
            self._last_prune_records_scanned = 0
            self._last_cleanup_queue_stats = ()
            self._last_cleanup_policy_stats = ()

        result = self._process_monitor_candidates(
            candidates,
            last_timestamp=last_timestamp,
            events_scanned=events_scanned,
            now_ns=now_ns,
            task_log_owner=task_log_owner,
        )
        if result is None:
            return

        self._finish_monitor_cycle(
            candidates=candidates,
            last_timestamp=last_timestamp,
            events_scanned=events_scanned,
            result=result,
        )

    def _finish_monitor_cycle(
        self,
        *,
        candidates: tuple[TaskMonitorCandidate, ...],
        last_timestamp: int | None,
        events_scanned: int,
        result: TaskMonitorProcessorResult,
    ) -> None:
        """Commit one processor result on the TaskMonitor reactor thread."""

        self._last_processor_success = result.success
        self._last_processed = result.processed
        self._last_deleted = result.deleted
        self._last_reported = result.reported
        self._last_warnings = result.warnings
        self._last_errors = result.errors
        if result.success:
            if last_timestamp is not None:
                self._last_checkpoint = last_timestamp
            self._last_error = self._heartbeat_error
        else:
            self._last_error = "; ".join(result.errors) if result.errors else "failed"
        self._last_catchup_pending = result.success and (
            self._last_retained_task_log_ingest.stop_reason
            in {"batch_limit", TASK_MONITOR_TASK_LOG_SCAN_LIMIT_REACHED}
            or self._last_control_cleanup_pending
        )
        next_interval_seconds = (
            self._monitor_config.catchup_interval_seconds
            if self._last_catchup_pending
            else self._monitor_config.interval_seconds
        )
        self._next_cycle_due_monotonic = time.monotonic() + next_interval_seconds
        self._set_activity(
            "waiting",
            waiting_on=self._queue_names["inbox"],
        )
        if events_scanned == 0 and result.success:
            self._last_error = self._heartbeat_error
        cycle_fields = {
            "processor": self._monitor_config.processor,
            "enabled": self._monitor_config.enabled,
            "interval_seconds": self._monitor_config.interval_seconds,
            "batch_size": self._monitor_config.batch_size,
            "catchup_interval_seconds": self._monitor_config.catchup_interval_seconds,
            "events_scanned": events_scanned,
            "candidate_count": len(candidates),
            "safe_to_delete_count": self._last_safe_to_delete_candidates,
            "cleanup_records_scanned": self._last_prune_records_scanned,
            "cleanup_queue_stats": self._last_cleanup_queue_stats,
            "cleanup_policy_stats": self._last_cleanup_policy_stats,
            "task_log_retention_period_seconds": (
                self._monitor_config.task_log_retention_period_seconds
            ),
            "task_log_external": self._external_task_log_status.to_summary(),
            "collation_store": self._monitor_store_status.to_summary(),
            "collation_rows_processed": self._last_collation_rows_processed,
            "collation_tasks_updated": self._last_collation_tasks_updated,
            "collation_terminal_tasks": self._last_collation_terminal_tasks,
            "collation_summaries_emitted": (self._last_collation_summaries_emitted),
            "collation_messages_marked_deleted": (
                self._last_collation_messages_marked_deleted
            ),
            "terminal_families_disposed": self._last_terminal_families_disposed,
            "suspect_families_classified": self._last_suspect_families_classified,
            "control_families_processed": self._last_control_families_processed,
            "control_families_disposed": self._last_control_families_disposed,
            "control_queues_deleted": self._last_control_queues_deleted,
            "control_rows_estimated_deleted": (
                self._last_control_rows_estimated_deleted
            ),
            "control_nonstandard_skipped": self._last_control_nonstandard_skipped,
            "control_cleanup_pending": self._last_control_cleanup_pending,
            "control_cleanup_in_flight": (
                self._control_cleanup_work_in_flight is not None
            ),
            "control_rows_deleted": self._last_control_rows_deleted,
            "control_cleanup_family_limit_hit": (
                self._last_control_cleanup_family_limit_hit
            ),
            "control_cleanup_deadline_hit": self._last_control_cleanup_deadline_hit,
            "reserved_families_processed": self._last_reserved_families_processed,
            "reserved_queues_deleted": self._last_reserved_queues_deleted,
            "reserved_rows_estimated_deleted": (
                self._last_reserved_rows_estimated_deleted
            ),
            "reserved_skipped_active": self._last_reserved_skipped_active,
            "reserved_skipped_not_ready": self._last_reserved_skipped_not_ready,
            "reserved_rows_deleted": self._last_reserved_rows_deleted,
            "control_delete_errors": self._last_control_delete_errors,
            "control_delete_warnings": self._last_control_delete_warnings,
            "processed": result.processed,
            "deleted": result.deleted,
            "reported": result.reported,
            "success": result.success,
            "checkpoint_advanced": result.success and last_timestamp is not None,
            "last_checkpoint": self._last_checkpoint,
            "warnings": result.warnings,
            "errors": result.errors,
            "catchup_pending": self._last_catchup_pending,
            "next_interval_seconds": next_interval_seconds,
        }
        self._emit_task_monitor_log_rate_limited(
            "task_monitor_cycle",
            required_level="info",
            severity="info" if result.success else "warning",
            key="task_monitor_cycle",
            state=cycle_fields,
            log_fields=cycle_fields,
        )
        if not result.success and result.errors:
            self._emit_task_monitor_log(
                "task_monitor_processor_error",
                required_level="info",
                severity="error",
                processor=self._monitor_config.processor,
                errors=result.errors,
            )

    def _process_monitor_candidates(
        self,
        candidates: tuple[TaskMonitorCandidate, ...],
        *,
        last_timestamp: int | None,
        events_scanned: int,
        now_ns: int,
        task_log_owner: str,
    ) -> TaskMonitorProcessorResult | None:
        if self._monitor_config.processor in {"delete", "report_only"}:
            return self._run_builtin_monitor_processor_cycle(
                apply=self._monitor_config.processor == "delete",
                now_ns=now_ns,
                task_log_owner=task_log_owner,
            )
        if self._monitor_config.processor == "jsonl_then_delete":
            return TaskMonitorProcessorResult(
                success=False,
                errors=(
                    "jsonl_then_delete task-monitor processor is reserved until "
                    "the logging callback is implemented",
                ),
            )

        request = TaskMonitorProcessorRequest(
            context=self._monitor_context(),
            config=self._monitor_config,
            cycle_id=f"{self.tid}:{now_ns}",
            monitor_tid=self.tid,
            candidates=candidates,
            now_ns=now_ns,
        )
        work = _TaskMonitorProcessorWork(
            candidates=candidates,
            last_timestamp=last_timestamp,
            events_scanned=events_scanned,
            request=request,
        )
        self._processor_work_in_flight = work
        self._set_activity("processing", waiting_on=None)
        try:
            self._submit_worker_call(
                TASK_MONITOR_PROCESSOR_WORKER_LANE,
                lambda: self._run_custom_monitor_processor(work.request),
            )
        except RuntimeError as exc:
            self._processor_work_in_flight = None
            return TaskMonitorProcessorResult(success=False, errors=(str(exc),))
        return None

    def _run_custom_monitor_processor(
        self,
        request: TaskMonitorProcessorRequest,
    ) -> TaskMonitorProcessorResult:
        """Run a custom broker-free processor callable on a worker lane."""

        try:
            processor = resolve_task_monitor_processor(self._monitor_config.processor)
            return processor(request)
        except Exception as exc:  # pragma: no cover - custom processor boundary
            return TaskMonitorProcessorResult(success=False, errors=(str(exc),))

    def _handle_worker_result(self, result: TaskWorkerResult) -> None:
        if result.lane == TASK_MONITOR_CONTROL_CLEANUP_WORKER_LANE:
            self._handle_control_cleanup_worker_result(result)
            return
        if result.lane != TASK_MONITOR_PROCESSOR_WORKER_LANE:
            super()._handle_worker_result(result)
            return

        work = self._processor_work_in_flight
        self._processor_work_in_flight = None
        if work is None:
            return
        if result.error is not None:
            processor_result = TaskMonitorProcessorResult(
                success=False,
                errors=(str(result.error),),
            )
        else:
            processor_result = result.value
            if not isinstance(processor_result, TaskMonitorProcessorResult):
                processor_result = TaskMonitorProcessorResult(
                    success=False,
                    errors=("task-monitor processor returned an invalid result",),
                )
        self._finish_monitor_cycle(
            candidates=work.candidates,
            last_timestamp=work.last_timestamp,
            events_scanned=work.events_scanned,
            result=processor_result,
        )

    def _handle_control_cleanup_worker_result(self, result: TaskWorkerResult) -> None:
        """Apply terminal control cleanup worker results on the reactor thread."""

        work = self._control_cleanup_work_in_flight
        self._control_cleanup_work_in_flight = None
        if work is None:
            return
        if result.error is not None:
            cleanup = _TaskControlCleanupResult(
                pending=True,
                errors=(str(result.error),),
            )
            monitor_status = None
        else:
            worker_result = result.value
            if (
                not isinstance(worker_result, _TaskControlCleanupWorkerResult)
                or worker_result.work.request_id != work.request_id
            ):
                cleanup = _TaskControlCleanupResult(
                    pending=True,
                    errors=("task-monitor control cleanup returned an invalid result",),
                )
                monitor_status = None
            else:
                cleanup = worker_result.cleanup
                monitor_status = worker_result.monitor_status

        self._last_control_families_processed = cleanup.families_processed
        self._last_control_families_disposed = cleanup.families_disposed
        self._last_control_queues_deleted = cleanup.queues_deleted
        self._last_control_rows_estimated_deleted = cleanup.rows_estimated_deleted
        self._last_control_rows_deleted = cleanup.rows_estimated_deleted
        self._last_control_nonstandard_skipped = cleanup.skipped_nonstandard
        self._last_control_cleanup_family_limit_hit = cleanup.family_limit_hit
        self._last_control_cleanup_deadline_hit = cleanup.deadline_hit
        self._last_reserved_families_processed = cleanup.reserved_families_processed
        self._last_reserved_queues_deleted = cleanup.reserved_queues_deleted
        self._last_reserved_rows_estimated_deleted = (
            cleanup.reserved_rows_estimated_deleted
        )
        self._last_reserved_skipped_active = cleanup.reserved_skipped_active
        self._last_reserved_skipped_not_ready = cleanup.reserved_skipped_not_ready
        self._last_reserved_rows_deleted = cleanup.reserved_rows_estimated_deleted
        self._last_control_cleanup_pending = cleanup.pending
        self._last_control_delete_errors = cleanup.errors
        self._last_control_delete_warnings = cleanup.warnings
        self._last_terminal_families_disposed += cleanup.families_disposed
        if monitor_status is not None:
            self._monitor_store_status = monitor_status
            self._last_collation_store_error = monitor_status.error

        if cleanup.success:
            self._last_error = self._heartbeat_error
        else:
            self._last_error = "; ".join(cleanup.errors) if cleanup.errors else "failed"
            self._last_processor_success = False
            if self._last_collation_store_error is None:
                self._last_collation_store_error = self._last_error

        self._last_catchup_pending = cleanup.pending or not cleanup.success
        next_interval_seconds = (
            self._monitor_config.catchup_interval_seconds
            if self._last_catchup_pending
            else self._monitor_config.interval_seconds
        )
        self._next_cycle_due_monotonic = time.monotonic() + next_interval_seconds
        self._set_activity("waiting", waiting_on=self._queue_names["inbox"])
        self._emit_task_monitor_log_rate_limited(
            "task_monitor_control_cleanup",
            required_level="info",
            severity="info" if cleanup.success else "warning",
            key="task_monitor_control_cleanup",
            state=cleanup.to_summary(),
            log_fields={
                **cleanup.to_summary(),
                "success": cleanup.success,
                "next_interval_seconds": next_interval_seconds,
            },
        )

    def _run_builtin_monitor_processor_cycle(
        self,
        *,
        apply: bool,
        now_ns: int,
        task_log_owner: str,
    ) -> TaskMonitorProcessorResult:
        """Run built-in cleanup while honoring task-log deletion ownership."""

        cleanup = self._run_task_monitor_cleanup_cycle(
            apply=apply,
            task_log_cleanup_enabled=task_log_owner == "cleanup_policy",
        )
        if task_log_owner == "collated_store" and apply:
            ingest = self._last_retained_task_log_ingest
            errors = (
                *cleanup.errors,
                *ingest.store_write_errors,
                *ingest.raw_delete_errors,
                *self._last_control_delete_errors,
            )
            return TaskMonitorProcessorResult(
                success=(
                    cleanup.success
                    and ingest.success
                    and not self._last_control_delete_errors
                ),
                processed=cleanup.processed
                + ingest.malformed_deleted
                + ingest.valid_ingested,
                deleted=(
                    cleanup.deleted
                    + ingest.malformed_deleted
                    + ingest.raw_deleted
                    + self._last_control_rows_deleted
                ),
                reported=cleanup.reported,
                errors=errors,
                warnings=(*cleanup.warnings, *self._last_control_delete_warnings),
            )
        if task_log_owner != "raw_external" or not apply:
            return cleanup
        raw_result = self._run_raw_external_task_log_cycle(now_ns=now_ns)
        errors = (*cleanup.errors, *raw_result.errors)
        warnings = (*cleanup.warnings, *raw_result.warnings)
        return TaskMonitorProcessorResult(
            success=cleanup.success and raw_result.success,
            processed=cleanup.processed + raw_result.processed,
            deleted=cleanup.deleted + raw_result.deleted,
            reported=cleanup.reported + raw_result.reported,
            errors=errors,
            warnings=warnings,
        )

    def _run_task_monitor_cleanup_cycle(
        self,
        *,
        apply: bool,
        task_log_cleanup_enabled: bool = True,
    ) -> TaskMonitorProcessorResult:
        ctx = self._monitor_context()
        self._set_activity("cleanup_scanning", waiting_on=WEFT_GLOBAL_LOG_QUEUE)
        cleanup = run_task_monitor_cleanup(
            ctx,
            TaskMonitorCleanupConfig(
                batch_size=self._monitor_config.batch_size,
                task_log_scan_limit=self._monitor_config.task_log_scan_limit,
                task_log_min_age_seconds=(
                    self._monitor_config.task_log_retention_period_seconds
                ),
                task_log_cleanup_enabled=task_log_cleanup_enabled,
            ),
            apply=apply,
            exclude_tids=(self.tid,),
        )
        self._last_prune_records_scanned = cleanup.records_scanned
        self._last_cleanup_queue_stats = cleanup.queue_stats_summary()
        self._last_cleanup_policy_stats = cleanup.policy_stats_summary()
        return TaskMonitorProcessorResult(
            success=cleanup.success,
            processed=cleanup.processed,
            deleted=cleanup.deleted,
            reported=cleanup.reported,
            errors=cleanup.errors,
            warnings=cleanup.warnings,
        )

    def _run_raw_external_task_log_cycle(
        self,
        *,
        now_ns: int,
    ) -> TaskMonitorProcessorResult:
        """Emit retained raw task-log rows and then delete exact message IDs."""

        sink = self._external_task_log_sink
        if sink is None:
            return TaskMonitorProcessorResult(
                success=False,
                errors=("external task-log sink is not configured",),
            )
        self._set_activity("raw_external_logging", waiting_on=WEFT_GLOBAL_LOG_QUEUE)
        scanner = GeneratorTaskLogScanner()
        window = scanner.scan_window(
            self._monitor_context(),
            WEFT_GLOBAL_LOG_QUEUE,
            scan_limit=self._monitor_config.task_log_scan_limit,
        )
        selected: list[_RawExternalPruneRef] = []
        errors: list[str] = []
        for row in window.rows:
            if len(selected) >= self._monitor_config.batch_size:
                break
            if row.tid == self.tid:
                continue
            if not is_old_enough(
                row.raw.message_id,
                now_ns,
                self._monitor_config.task_log_retention_period_seconds,
            ):
                continue
            try:
                sink.emit_raw(
                    queue=row.raw.queue,
                    message_id=row.raw.message_id,
                    emitted_at_ns=now_ns,
                    payload=row.payload,
                    raw_body=row.raw.body,
                    malformed_reason=row.malformed_reason,
                )
            except ExternalTaskLogError as exc:
                sink.record_blocked_deletions(1)
                errors.append(str(exc))
                break
            selected.append(
                _RawExternalPruneRef(
                    queue=row.raw.queue,
                    message_id=row.raw.message_id,
                )
            )
        self._refresh_external_task_log_status()
        applied: tuple[_AppliedMonitorRawMessage, ...] = ()
        if selected and not errors:
            applied = tuple(
                apply_exact_prune_candidates(
                    self._monitor_context(),
                    selected,
                    apply_result=_applied_raw_external_message,
                )
            )
        apply_errors = tuple(
            result.error for result in applied if result.error is not None
        )
        deleted = sum(1 for result in applied if result.deleted)
        self._append_raw_external_stats(
            scanned=window.scanned,
            selected=len(selected),
            deleted=deleted,
            reported=0,
            stop_reason=window.stop_reason,
        )
        return TaskMonitorProcessorResult(
            success=not errors and not apply_errors,
            processed=len(selected),
            deleted=deleted,
            errors=(*errors, *apply_errors),
        )

    def _append_raw_external_stats(
        self,
        *,
        scanned: int,
        selected: int,
        deleted: int,
        reported: int,
        stop_reason: str | None,
    ) -> None:
        queue_stat = CleanupQueueStats(
            queue=WEFT_GLOBAL_LOG_QUEUE,
            scanned=scanned,
            selected=selected,
            deleted=deleted,
            reported=reported,
            stop_reason=stop_reason,
            reason_counts=(
                {"older_than_task_log_retention_period": selected} if selected else {}
            ),
        )
        policy_stat = CleanupPolicyStats(
            policy=TASK_MONITOR_POLICY_TASK_LOG_EXTERNAL_RAW,
            queue=WEFT_GLOBAL_LOG_QUEUE,
            scanned=scanned,
            selected=selected,
            deleted=deleted,
            reported=reported,
            stop_reason=stop_reason,
            reason_counts=(
                {"older_than_task_log_retention_period": selected} if selected else {}
            ),
        )
        self._last_prune_records_scanned += scanned
        self._last_cleanup_queue_stats = (
            *self._last_cleanup_queue_stats,
            queue_stat.to_summary(),
        )
        self._last_cleanup_policy_stats = (
            *self._last_cleanup_policy_stats,
            policy_stat.to_summary(),
        )

    def _cleanup_reserved_if_needed(self) -> None:
        """Task monitors never create reserved messages."""

        return
