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

from simplebroker.ext import BrokerError
from weft._constants import (
    CONTROL_KILL,
    CONTROL_STOP,
    DEFAULT_FUNCTION_TARGET,
    QUEUE_CTRL_IN_SUFFIX,
    QUEUE_CTRL_OUT_SUFFIX,
    QUEUE_INBOX_SUFFIX,
    QUEUE_OUTBOX_SUFFIX,
    TASK_MONITOR_ACTIVITY_WAIT_CAP_SECONDS,
    TASK_MONITOR_HEARTBEAT_STARTUP_TIMEOUT_SECONDS,
    TASK_MONITOR_LOG_SUBDIR,
    TASK_MONITOR_POLICY_TASK_LOG_EXTERNAL_RAW,
    TASK_MONITOR_PONG_DETAIL_LIMIT,
    TASK_MONITOR_PROCESSOR_WORKER_LANE,
    TASK_MONITOR_SCHEMA_VERSION,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS,
    WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS_DEFAULT,
    WEFT_TASK_MONITOR_PROCESSOR_BUILTINS,
)
from weft.context import WeftContext
from weft.core.heartbeat import cancel_heartbeat, upsert_heartbeat
from weft.core.monitor.cleanup import (
    TaskMonitorCleanupConfig,
    run_task_monitor_cleanup,
)
from weft.core.monitor.collation import update_from_task_log_row
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
    open_monitor_store,
)
from weft.core.monitor.task_log_scanner import GeneratorTaskLogScanner
from weft.core.pruning.apply import apply_exact_prune_candidates
from weft.core.pruning.models import CleanupPolicyStats, CleanupQueueStats
from weft.core.queue_window import is_old_enough
from weft.core.serve_log import (
    build_serve_log_record,
    emit_serve_log_record,
    serve_log_allows,
    serve_log_level,
    truncate_serve_log_value,
)
from weft.core.tasks.base import BaseTask, ControlRequest, TaskWorkerResult
from weft.core.tasks.multiqueue_watcher import QueueMessageContext
from weft.core.taskspec import IOSection, SpecSection, StateSection, TaskSpec
from weft.helpers import iter_queue_entries

TaskMonitorCallback = Callable[[str, str, int], None]


@dataclass(frozen=True, slots=True)
class _TaskMonitorProcessorWork:
    """Custom processor work scanned by the reactor and run broker-free."""

    candidates: tuple[TaskMonitorCandidate, ...]
    last_timestamp: int | None
    events_scanned: int
    request: TaskMonitorProcessorRequest


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


def _applied_monitor_raw_message(
    candidate: MonitorRawMessageRef,
    deleted: bool,
    error: str | None,
) -> _AppliedMonitorRawMessage:
    """Adapt exact-delete results for table-backed Monitor cleanup."""

    return _AppliedMonitorRawMessage(candidate=candidate, deleted=deleted, error=error)


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
        self._last_collation_store_error: str | None = None
        self._external_task_log_sink: ExternalTaskLogSink | None = None
        self._external_task_log_status = disabled_external_task_log_status(
            mode="collated",
            path=None,
        )
        self._processor_work_in_flight: _TaskMonitorProcessorWork | None = None
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
            task_log_scan_limit=self._monitor_config.task_log_scan_limit,
            store_write_batch_size=self._monitor_config.store_write_batch_size,
            task_log_retention_period_seconds=(
                self._monitor_config.task_log_retention_period_seconds
            ),
            task_log_external=self._external_task_log_status.to_summary(),
            processor=self._monitor_config.processor,
            log_sink=self._monitor_config.log_sink,
            collation_store_enabled=(
                self._monitor_config.collation_store_enabled
            ),
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
        if self._processor_work_in_flight is not None:
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
                "task_log_scan_limit": self._monitor_config.task_log_scan_limit,
                "store_write_batch_size": (
                    self._monitor_config.store_write_batch_size
                ),
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
                "collation_store_available": self._monitor_store_status.available,
                "collation_schema_version": (
                    self._monitor_store_status.schema_version
                ),
                "collation_checkpoint": self._monitor_store_status.checkpoint,
                "last_collation_rows_processed": (
                    self._last_collation_rows_processed
                ),
                "last_collation_tasks_updated": self._last_collation_tasks_updated,
                "last_collation_terminal_tasks": (
                    self._last_collation_terminal_tasks
                ),
                "last_collation_summaries_emitted": (
                    self._last_collation_summaries_emitted
                ),
                "last_collation_messages_marked_deleted": (
                    self._last_collation_messages_marked_deleted
                ),
                "last_collation_store_error": self._last_collation_store_error,
                "processor_in_flight": self._processor_work_in_flight is not None,
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
                "task_log_scan_limit": self._monitor_config.task_log_scan_limit,
                "store_write_batch_size": (
                    self._monitor_config.store_write_batch_size
                ),
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
                },
                "last_cycle": {
                    "success": self._last_processor_success,
                    "error": self._last_error,
                    "processor_in_flight": (self._processor_work_in_flight is not None),
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
                    "collation_rows_processed": (
                        self._last_collation_rows_processed
                    ),
                    "collation_tasks_updated": self._last_collation_tasks_updated,
                    "collation_terminal_tasks": (
                        self._last_collation_terminal_tasks
                    ),
                    "collation_summaries_emitted": (
                        self._last_collation_summaries_emitted
                    ),
                    "collation_messages_marked_deleted": (
                        self._last_collation_messages_marked_deleted
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
        if self._monitor_config.table_delete_enabled:
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
        store = self._ensure_monitor_store()
        if store is None:
            return

        try:
            checkpoint = store.get_checkpoint(WEFT_GLOBAL_LOG_QUEUE)
            scanner = GeneratorTaskLogScanner()
            window = scanner.scan_window(
                self._monitor_context(),
                WEFT_GLOBAL_LOG_QUEUE,
                scan_limit=self._monitor_config.task_log_scan_limit,
                since_timestamp=checkpoint,
            )
            last_message_id: int | None = None
            updates = []
            for row in window.rows:
                last_message_id = max(last_message_id or 0, row.raw.message_id)
                update = update_from_task_log_row(row)
                if update is None or update.tid == self.tid:
                    continue
                updates.append(update)
            ingest = store.record_task_log_updates(
                WEFT_GLOBAL_LOG_QUEUE,
                updates,
                checkpoint_message_id=last_message_id,
            )
            self._last_collation_rows_processed = window.scanned
            self._last_collation_tasks_updated = ingest.tasks_updated
            self._last_collation_terminal_tasks = ingest.terminal_tasks
            self._last_collation_summaries_emitted = (
                self._emit_monitor_store_summaries(store, now_ns=now_ns)
            )
            if (
                self._monitor_config.processor == "delete"
                and task_log_owner == "collated_store"
            ):
                self._last_collation_messages_marked_deleted = (
                    self._delete_monitor_store_task_log_rows(store)
                )
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

    def _emit_monitor_store_summaries(
        self,
        store: MonitorStore,
        *,
        now_ns: int,
    ) -> int:
        """Emit terminal summary dispositions for Monitor collation rows.

        Spec: [MF-5]
        """

        emitted = 0
        for ready in store.list_summary_ready_tasks(
            limit=self._monitor_config.batch_size,
            now_ns=now_ns,
            retention_seconds=self._monitor_config.task_log_retention_period_seconds,
        ):
            try:
                self._emit_monitor_store_summary(
                    ready,
                    emitted_at_ns=now_ns,
                )
            except ExternalTaskLogError as exc:
                sink = self._external_task_log_sink
                if sink is not None:
                    sink.record_blocked_deletions(1)
                    self._refresh_external_task_log_status()
                self._last_collation_store_error = str(exc)
                continue
            store.mark_summary_emitted(
                ready.record.tid,
                now_ns,
                suspect_reason=(
                    ready.close_reason
                    if ready.close_reason == "suspected_inactive"
                    else None
                ),
            )
            emitted += 1
        self._refresh_external_task_log_status()
        return emitted

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
        deleted_ids = tuple(
            result.candidate.message_id for result in applied if result.error is None
        )
        store.mark_messages_deleted(deleted_ids)
        errors = tuple(result.error for result in applied if result.error is not None)
        if errors:
            self._last_collation_store_error = "; ".join(errors)
        return len(deleted_ids)

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
        self._next_cycle_due_monotonic = (
            time.monotonic() + self._monitor_config.interval_seconds
        )
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
            "collation_summaries_emitted": (
                self._last_collation_summaries_emitted
            ),
            "collation_messages_marked_deleted": (
                self._last_collation_messages_marked_deleted
            ),
            "processed": result.processed,
            "deleted": result.deleted,
            "reported": result.reported,
            "success": result.success,
            "checkpoint_advanced": result.success and last_timestamp is not None,
            "last_checkpoint": self._last_checkpoint,
            "warnings": result.warnings,
            "errors": result.errors,
            "next_interval_seconds": self._monitor_config.interval_seconds,
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
                {"older_than_task_log_retention_period": selected}
                if selected
                else {}
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
                {"older_than_task_log_retention_period": selected}
                if selected
                else {}
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
