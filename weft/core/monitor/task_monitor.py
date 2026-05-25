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
from typing import Any, cast

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
    TASK_MONITOR_BUILTIN_CYCLE_WORKER_LANE,
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
    TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS,
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
from weft.core.monitor.collation import (
    MonitorTaskEventUpdate,
    update_from_task_log_payload,
    update_from_task_log_row,
)
from weft.core.monitor.external_log import (
    ExternalTaskLogError,
    ExternalTaskLogSink,
    disabled_external_task_log_status,
)
from weft.core.monitor.policies.dead_task import (
    dead_task_queue_cleanup_plan as _dead_task_queue_cleanup_plan,
)
from weft.core.monitor.policies.dead_task import (
    fetch_dead_task_log_coalesce_group as _fetch_dead_task_log_coalesce_group,
)
from weft.core.monitor.policies.runtime_control import RuntimeCleanupSliceKind
from weft.core.monitor.policies.runtime_control import (
    RuntimeReservedCleanupSelection as _RuntimeReservedCleanupSelection,
)
from weft.core.monitor.policies.runtime_control import (
    TaskControlCleanupResult as _TaskControlCleanupResult,
)
from weft.core.monitor.policies.runtime_control import (
    next_runtime_cleanup_slice_kind as _next_runtime_cleanup_slice_kind,
)
from weft.core.monitor.policies.runtime_control import (
    reserved_queue_tid as _reserved_queue_tid,
)
from weft.core.monitor.policies.runtime_control import (
    reserved_queue_tids as _reserved_queue_tids,
)
from weft.core.monitor.policies.runtime_control import (
    runtime_cleanup_queue_discovery_due as _runtime_cleanup_queue_discovery_due,
)
from weft.core.monitor.policies.runtime_control import (
    runtime_dead_task_record_probe_tids as _runtime_dead_task_record_probe_tids,
)
from weft.core.monitor.policies.runtime_control import (
    select_runtime_dead_task_cleanup_candidates as _select_runtime_dead_task_cleanup_candidates,
)
from weft.core.monitor.policies.runtime_control import (
    select_runtime_reserved_cleanup_candidates as _select_runtime_reserved_cleanup_candidates,
)
from weft.core.monitor.policies.runtime_control import (
    standard_task_control_queue_names as _standard_task_control_queue_names,
)
from weft.core.monitor.policies.runtime_control import (
    terminal_task_runtime_queue_cleanup_plan as _terminal_task_runtime_queue_cleanup_plan,
)
from weft.core.monitor.progress import (
    PolicyProgress,
    progress_requires_catchup,
    progress_summaries,
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
    MonitorStoreRetirementResult,
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
from weft.core.tasks.base import ControlRequest, TaskWorkerResult
from weft.core.tasks.multiqueue_watcher import QueueMessageContext
from weft.core.tasks.service import ServiceTask
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
class _TaskMonitorBuiltinCycleWork:
    """Built-in monitor cycle work owned by the TaskMonitor maintenance lane."""

    request_id: str
    now_ns: int
    task_log_owner: str


@dataclass(frozen=True, slots=True)
class _TaskMonitorBuiltinCycleWorkerResult:
    """Built-in monitor cycle result returned to the TaskMonitor reactor."""

    work: _TaskMonitorBuiltinCycleWork
    result: TaskMonitorProcessorResult
    runtime_cleanup_ready: bool = False


@dataclass(frozen=True, slots=True)
class _TaskControlCleanupWork:
    """Runtime cleanup work owned by the TaskMonitor maintenance lane."""

    request_id: str
    now_ns: int
    slice_kind: RuntimeCleanupSliceKind = "terminal_control"
    previous_queue_cleanup_pending: bool = False
    queue_discovery_due_monotonic: float = 0.0


@dataclass(frozen=True, slots=True)
class _TaskControlCleanupWorkerResult:
    """Runtime cleanup result returned to the TaskMonitor reactor."""

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
    monitor_store_delete_chunks: int = 0
    monitor_store_message_rows_deleted: int = 0
    monitor_store_message_tombstones_pruned: int = 0
    monitor_store_families_retired: int = 0
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
            "monitor_store_delete_chunks": self.monitor_store_delete_chunks,
            "monitor_store_message_rows_deleted": (
                self.monitor_store_message_rows_deleted
            ),
            "monitor_store_message_tombstones_pruned": (
                self.monitor_store_message_tombstones_pruned
            ),
            "monitor_store_families_retired": self.monitor_store_families_retired,
            "checkpoint_message_id": self.checkpoint_message_id,
            "checkpoint_written": self.checkpoint_written,
            "store_write_errors": list(self.store_write_errors),
            "raw_delete_errors": list(self.raw_delete_errors),
            "stop_reason": self.stop_reason,
            "oldest_too_young_age_seconds": self.oldest_too_young_age_seconds,
            "completed_fifo_high_water": self.completed_fifo_high_water,
        }


@dataclass(frozen=True, slots=True)
class _ExactTaskLogDeleteResult:
    """Exact task-log delete result with IDs proven deleted by the broker."""

    deleted_ids: tuple[int, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _DeadTaskLogDeleteResult:
    """Per-TID task-log delete result from indexed Monitor-store refs."""

    api_matches: int = 0
    families_checked: int = 0
    empty_probes: int = 0
    coalesced_rows: int = 0
    summaries_emitted: int = 0
    refs_selected: int = 0
    rows_deleted: int = 0
    errors: tuple[str, ...] = ()

    def to_summary(self) -> dict[str, Any]:
        """Return a JSON-safe cached PONG summary."""

        return {
            "api_matches": self.api_matches,
            "families_checked": self.families_checked,
            "empty_probes": self.empty_probes,
            "coalesced_rows": self.coalesced_rows,
            "summaries_emitted": self.summaries_emitted,
            "refs_selected": self.refs_selected,
            "rows_deleted": self.rows_deleted,
            "errors": list(self.errors),
        }


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


def _retained_task_log_ingest_progress(
    result: _RetainedTaskLogIngestResult,
) -> PolicyProgress:
    """Return cached policy progress for retained FIFO Monitor-store ingestion."""

    blocked_reason = None
    if result.store_write_errors:
        blocked_reason = result.store_write_errors[0]
    elif result.raw_delete_errors:
        blocked_reason = result.raw_delete_errors[0]
    return PolicyProgress(
        policy="task_log.retained_fifo_ingest",
        domain=WEFT_GLOBAL_LOG_QUEUE,
        scanned=result.scanned,
        selected=result.selected,
        applied=result.raw_deleted,
        waypoint_reached=result.stop_reason
        in {"batch_limit", TASK_MONITOR_TASK_LOG_SCAN_LIMIT_REACHED},
        base_reached=result.completed_fifo_high_water,
        blocked_reason=blocked_reason,
        reason_counts={
            "malformed_deleted": result.malformed_deleted,
            "valid_ingested": result.valid_ingested,
            "raw_deleted": result.raw_deleted,
        },
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


class TaskMonitor(ServiceTask):
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
        self._last_policy_progress: tuple[PolicyProgress, ...] = ()
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
        self._last_monitor_store_message_rows_deleted = 0
        self._last_monitor_store_message_tombstones_pruned = 0
        self._last_monitor_store_families_retired = 0
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
        self._last_cleanup_workers_configured = 0
        self._last_cleanup_jobs_started = 0
        self._last_cleanup_jobs_completed = 0
        self._last_cleanup_jobs_pending = 0
        self._last_cleanup_jobs_by_kind: dict[str, int] = {}
        self._last_cleanup_jobs_pending_by_kind: dict[str, int] = {}
        self._runtime_cleanup_queue_discovery_pending = False
        self._next_runtime_cleanup_queue_discovery_due_monotonic = 0.0
        self._last_control_delete_errors: tuple[str, ...] = ()
        self._last_control_delete_warnings: tuple[str, ...] = ()
        self._last_retained_task_log_ingest = _RetainedTaskLogIngestResult()
        self._last_orphan_task_log_recovery = _DeadTaskLogDeleteResult()
        self._last_collation_store_error: str | None = None
        self._external_task_log_sink: ExternalTaskLogSink | None = None
        self._external_task_log_status = disabled_external_task_log_status(
            mode="collated",
            path=None,
        )
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

        self._activate_service_task()

    @property
    def _builtin_cycle_work_in_flight(
        self,
    ) -> _TaskMonitorBuiltinCycleWork | None:
        """Return the current built-in cycle work from shared lane state."""

        return cast(
            _TaskMonitorBuiltinCycleWork | None,
            self._service_lane_work(TASK_MONITOR_BUILTIN_CYCLE_WORKER_LANE),
        )

    @property
    def _processor_work_in_flight(self) -> _TaskMonitorProcessorWork | None:
        """Return the current custom processor work from shared lane state."""

        return cast(
            _TaskMonitorProcessorWork | None,
            self._service_lane_work(TASK_MONITOR_PROCESSOR_WORKER_LANE),
        )

    @property
    def _control_cleanup_work_in_flight(
        self,
    ) -> _TaskControlCleanupWork | None:
        """Return the current control cleanup work from shared lane state."""

        return cast(
            _TaskControlCleanupWork | None,
            self._service_lane_work(TASK_MONITOR_CONTROL_CLEANUP_WORKER_LANE),
        )

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
        if (
            self._builtin_cycle_work_in_flight is not None
            or self._processor_work_in_flight is not None
            or self._control_cleanup_work_in_flight is not None
        ):
            return TASK_MONITOR_ACTIVITY_WAIT_CAP_SECONDS
        if self._first_cycle_pending or self._wake_requested:
            return 0.0
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

        worker_results_handled = self._drain_worker_results()
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
        if worker_results_handled:
            self._maybe_emit_poll_report()
            return
        if self._builtin_cycle_work_in_flight is not None:
            self._set_activity("processing", waiting_on=None)
            self._drain_worker_results()
            self._maybe_emit_poll_report()
            return
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
                "last_policy_progress": list(
                    progress_summaries(self._last_policy_progress)
                ),
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
                "last_monitor_store_message_rows_deleted": (
                    self._last_monitor_store_message_rows_deleted
                ),
                "last_monitor_store_message_tombstones_pruned": (
                    self._last_monitor_store_message_tombstones_pruned
                ),
                "last_monitor_store_families_retired": (
                    self._last_monitor_store_families_retired
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
                "last_cleanup_workers_configured": (
                    self._last_cleanup_workers_configured
                ),
                "last_cleanup_jobs_started": self._last_cleanup_jobs_started,
                "last_cleanup_jobs_completed": self._last_cleanup_jobs_completed,
                "last_cleanup_jobs_pending": self._last_cleanup_jobs_pending,
                "last_cleanup_jobs_by_kind": dict(self._last_cleanup_jobs_by_kind),
                "last_cleanup_jobs_pending_by_kind": dict(
                    self._last_cleanup_jobs_pending_by_kind
                ),
                "last_control_delete_errors": list(self._last_control_delete_errors),
                "last_control_delete_warnings": (
                    list(self._last_control_delete_warnings)
                ),
                "last_retained_task_log_ingest": (
                    self._last_retained_task_log_ingest.to_summary()
                ),
                "last_orphan_task_log_recovery": (
                    self._last_orphan_task_log_recovery.to_summary()
                ),
                "last_collation_store_error": self._last_collation_store_error,
                "processor_in_flight": self._processor_work_in_flight is not None,
                "builtin_cycle_in_flight": (
                    self._builtin_cycle_work_in_flight is not None
                ),
                "monitor_builtin_cycle_in_flight": (
                    self._builtin_cycle_work_in_flight is not None
                ),
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
                    "builtin_cycle_in_flight": (
                        self._builtin_cycle_work_in_flight is not None
                    ),
                    "monitor_builtin_cycle_in_flight": (
                        self._builtin_cycle_work_in_flight is not None
                    ),
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
                    "policy_progress": list(
                        progress_summaries(self._last_policy_progress)
                    )[:TASK_MONITOR_PONG_DETAIL_LIMIT],
                    "collation_rows_processed": (self._last_collation_rows_processed),
                    "collation_tasks_updated": self._last_collation_tasks_updated,
                    "collation_terminal_tasks": (self._last_collation_terminal_tasks),
                    "collation_summaries_emitted": (
                        self._last_collation_summaries_emitted
                    ),
                    "monitor_store_message_rows_deleted": (
                        self._last_monitor_store_message_rows_deleted
                    ),
                    "monitor_store_message_tombstones_pruned": (
                        self._last_monitor_store_message_tombstones_pruned
                    ),
                    "monitor_store_families_retired": (
                        self._last_monitor_store_families_retired
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
                    "cleanup_workers_configured": (
                        self._last_cleanup_workers_configured
                    ),
                    "cleanup_jobs_started": self._last_cleanup_jobs_started,
                    "cleanup_jobs_completed": self._last_cleanup_jobs_completed,
                    "cleanup_jobs_pending": self._last_cleanup_jobs_pending,
                    "cleanup_jobs_by_kind": dict(self._last_cleanup_jobs_by_kind),
                    "cleanup_jobs_pending_by_kind": dict(
                        self._last_cleanup_jobs_pending_by_kind
                    ),
                    "control_delete_errors": list(self._last_control_delete_errors)[
                        :TASK_MONITOR_PONG_DETAIL_LIMIT
                    ],
                    "control_delete_warnings": list(self._last_control_delete_warnings)[
                        :TASK_MONITOR_PONG_DETAIL_LIMIT
                    ],
                    "retained_task_log_ingest": (
                        self._last_retained_task_log_ingest.to_summary()
                    ),
                    "orphan_task_log_recovery": (
                        self._last_orphan_task_log_recovery.to_summary()
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

    def _run_monitor_store_cycle(
        self,
        *,
        now_ns: int,
        task_log_owner: str,
        start_control_cleanup: bool = True,
    ) -> bool:
        """Collate task-log rows into the durable Monitor store.

        Spec: [MF-5], [OBS.13]
        """

        runtime_cleanup_requested = (
            self._monitor_config.processor == "delete"
            and task_log_owner == "collated_store"
        )
        runtime_cleanup_ready = False
        self._last_collation_rows_processed = 0
        self._last_collation_tasks_updated = 0
        self._last_collation_terminal_tasks = 0
        self._last_collation_summaries_emitted = 0
        self._last_monitor_store_message_rows_deleted = 0
        self._last_monitor_store_message_tombstones_pruned = 0
        self._last_monitor_store_families_retired = 0
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
        self._last_cleanup_workers_configured = 0
        self._last_cleanup_jobs_started = 0
        self._last_cleanup_jobs_completed = 0
        self._last_cleanup_jobs_pending = 0
        self._last_cleanup_jobs_by_kind = {}
        self._last_cleanup_jobs_pending_by_kind = {}
        self._runtime_cleanup_queue_discovery_pending = False
        self._next_runtime_cleanup_queue_discovery_due_monotonic = 0.0
        self._last_control_delete_errors = ()
        self._last_control_delete_warnings = ()
        self._last_retained_task_log_ingest = _RetainedTaskLogIngestResult()
        self._last_orphan_task_log_recovery = _DeadTaskLogDeleteResult()
        store = self._ensure_monitor_store()
        if store is None:
            return False

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
            self._last_policy_progress = (
                *self._last_policy_progress,
                _retained_task_log_ingest_progress(retained_ingest),
            )
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
                if runtime_cleanup_requested:
                    self._apply_monitor_store_retirement_result(
                        self._delete_monitor_store_task_log_rows(store)
                    )
                    self._apply_monitor_store_retirement_result(
                        self._repair_raw_deleted_task_message_refs(store)
                    )
                    tombstone_prune = store.prune_deleted_task_message_tombstones(
                        limit=self._monitor_config.batch_size,
                        pruned_at_ns=now_ns,
                    )
                    self._apply_monitor_store_retirement_result(tombstone_prune)
                    self._last_policy_progress = (
                        *self._last_policy_progress,
                        PolicyProgress(
                            policy="monitor_store.child_tombstone_prune",
                            domain="weft_monitor_task_messages",
                            selected=tombstone_prune.message_tombstones_pruned,
                            applied=tombstone_prune.message_tombstones_pruned,
                            waypoint_reached=(
                                tombstone_prune.message_tombstones_pruned
                                >= self._monitor_config.batch_size
                            ),
                            base_reached=(
                                tombstone_prune.message_tombstones_pruned == 0
                            ),
                            reason_counts={
                                "message_tombstones_pruned": (
                                    tombstone_prune.message_tombstones_pruned
                                ),
                                "affected_tids": tombstone_prune.affected_tids,
                            },
                        ),
                    )
                    family_retirement = store.retire_completed_collation_families(
                        limit=self._monitor_config.batch_size,
                        retired_at_ns=now_ns,
                    )
                    self._apply_monitor_store_retirement_result(family_retirement)
                    self._last_policy_progress = (
                        *self._last_policy_progress,
                        PolicyProgress(
                            policy="monitor_store.family_retirement",
                            domain="weft_monitor_task_collations",
                            selected=family_retirement.families_retired,
                            applied=family_retirement.families_retired,
                            waypoint_reached=(
                                family_retirement.families_retired
                                >= self._monitor_config.batch_size
                            ),
                            base_reached=family_retirement.families_retired == 0,
                            reason_counts={
                                "families_retired": (
                                    family_retirement.families_retired
                                ),
                            },
                        ),
                    )
                    orphan_recovery = self._recover_orphan_task_log_rows(
                        store,
                        now_ns=now_ns,
                    )
                    self._last_orphan_task_log_recovery = orphan_recovery
                    self._last_monitor_store_message_rows_deleted += (
                        orphan_recovery.rows_deleted
                    )
            if runtime_cleanup_requested:
                runtime_cleanup_ready = True
                if start_control_cleanup:
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
        return runtime_cleanup_ready

    def _apply_monitor_store_retirement_result(
        self,
        result: MonitorStoreRetirementResult,
    ) -> None:
        """Commit cached Monitor-store physical retirement counters."""

        self._last_monitor_store_message_rows_deleted += result.message_rows_deleted
        self._last_monitor_store_message_tombstones_pruned += (
            result.message_tombstones_pruned
        )
        self._last_monitor_store_families_retired += result.families_retired

    def _ingest_retained_task_log_rows(
        self,
        store: MonitorStore,
        *,
        now_ns: int,
        apply: bool,
    ) -> _RetainedTaskLogIngestResult:
        """Fold retained visible task-log rows into the Monitor table."""

        scanner = GeneratorTaskLogScanner()
        checkpoint_message_id = store.get_checkpoint(WEFT_GLOBAL_LOG_QUEUE)
        window = scanner.scan_window(
            self._monitor_context(),
            WEFT_GLOBAL_LOG_QUEUE,
            scan_limit=self._monitor_config.task_log_scan_limit,
            since_timestamp=checkpoint_message_id,
        )
        scanned = 0
        malformed_deleted = 0
        valid_ingested = 0
        raw_deleted = 0
        store_update_chunks = 0
        exact_delete_chunks = 0
        monitor_store_delete_chunks = 0
        store_errors: list[str] = []
        delete_errors: list[str] = []
        stop_reason = window.stop_reason
        last_selected_message_id: int | None = None
        terminal_tasks: set[str] = set()
        updated_tasks: set[str] = set()
        selected_count = 0
        selected_rows: list[QueueWindowRow] = []
        valid_updates: list[MonitorTaskEventUpdate] = []
        valid_message_ids: set[int] = set()
        malformed_message_ids: set[int] = set()

        for row in window.rows:
            if selected_count >= self._monitor_config.batch_size:
                stop_reason = "batch_limit"
                break
            scanned += 1
            if row.malformed_reason is not None:
                selected_rows.append(row.raw)
                malformed_message_ids.add(row.raw.message_id)
                last_selected_message_id = row.raw.message_id
                selected_count += 1
                continue

            update = update_from_task_log_row(row)
            if update is None:
                selected_rows.append(row.raw)
                malformed_message_ids.add(row.raw.message_id)
                last_selected_message_id = row.raw.message_id
                selected_count += 1
                continue

            valid_updates.append(update)
            selected_rows.append(row.raw)
            valid_message_ids.add(row.raw.message_id)
            last_selected_message_id = row.raw.message_id
            selected_count += 1
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
            delete_errors.extend(delete_result.errors)
            deleted_valid_ids = tuple(sorted(deleted_ids & valid_message_ids))
            if deleted_valid_ids:
                try:
                    retirement = store.delete_task_messages_after_raw_delete(
                        deleted_valid_ids,
                        deleted_at_ns=now_ns,
                    )
                    raw_deleted = retirement.message_rows_deleted
                    monitor_store_delete_chunks += 1
                except (OSError, RuntimeError, ValueError) as exc:
                    store_errors.append(str(exc))
                    stop_reason = "store_child_delete_error"
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
        self._last_monitor_store_message_rows_deleted += raw_deleted
        return _RetainedTaskLogIngestResult(
            scanned=scanned,
            selected=selected_count,
            malformed_deleted=malformed_deleted,
            valid_ingested=valid_ingested,
            raw_deleted=raw_deleted,
            store_update_chunks=store_update_chunks,
            exact_delete_chunks=exact_delete_chunks,
            monitor_store_delete_chunks=monitor_store_delete_chunks,
            monitor_store_message_rows_deleted=raw_deleted,
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
        control_delete_marks: list[str] = []
        summary_errors: list[str] = []
        ready_tasks = store.list_summary_ready_tasks(
            limit=self._monitor_config.batch_size + 1,
            now_ns=now_ns,
            retention_seconds=self._monitor_config.task_log_retention_period_seconds,
            terminal_retention_seconds=0.0,
            stale_open_family_seconds=(self._monitor_config.stale_open_family_seconds),
        )
        selected_ready_tasks = ready_tasks[: self._monitor_config.batch_size]
        more_ready = len(ready_tasks) > len(selected_ready_tasks)
        for ready in selected_ready_tasks:
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
                    summary_errors.append(str(exc))
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
                if _standard_task_control_queue_names(ready.record) is None:
                    control_delete_marks.append(ready.record.tid)
                    family_disposition_marks.append(
                        (ready.record.tid, "terminal", None, None)
                    )
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
        if control_delete_marks:
            store.mark_task_controls_deleted(control_delete_marks, now_ns)
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
        self._last_policy_progress = (
            *self._last_policy_progress,
            PolicyProgress(
                policy="monitor_store.summary_disposition",
                domain="weft_monitor_task_collations",
                scanned=len(ready_tasks),
                selected=len(summary_marks) + len(family_disposition_marks),
                applied=len(summary_marks) + len(family_disposition_marks),
                waypoint_reached=more_ready,
                base_reached=not ready_tasks,
                blocked_reason=summary_errors[0] if summary_errors else None,
                reason_counts={
                    "summaries_marked": len(summary_marks),
                    "families_disposed": len(family_disposition_marks),
                    "control_delete_marks": len(control_delete_marks),
                },
            ),
        )
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
        existing_queue_names: set[str] | None = None,
        now_ns: int,
    ) -> _TaskControlCleanupResult:
        """Delete whole standard stale task-local queues for a terminal task."""

        if record.task_control_deleted_at_ns is not None:
            return _TaskControlCleanupResult(families_processed=1)
        cleanup_plan = _terminal_task_runtime_queue_cleanup_plan(
            record,
            now_ns=now_ns,
            retention_seconds=self._monitor_config.task_log_retention_period_seconds,
        )
        if cleanup_plan is None:
            return _TaskControlCleanupResult(
                families_processed=1,
                skipped_nonstandard=1,
            )

        errors: list[str] = []
        warnings: list[str] = []
        queue_label = ",".join(cleanup_plan.queue_names)
        try:
            with self._monitor_context().broker() as broker:
                rows_deleted = int(broker.delete_from_queues(cleanup_plan.queue_names))
        except (BrokerError, OSError, RuntimeError, ValueError) as exc:
            errors.append(f"{queue_label}: {exc}")
            rows_deleted = 0

        if existing_queue_names is None:
            queues_deleted = len(cleanup_plan.queue_names) if rows_deleted > 0 else 0
            existing_standard_queues: tuple[str, ...] = ()
        else:
            existing_standard_queues = tuple(
                queue_name
                for queue_name in cleanup_plan.queue_names
                if queue_name in existing_queue_names
            )
            queues_deleted = 0 if errors else len(existing_standard_queues)

        if not errors and rows_deleted == 0 and existing_standard_queues:
            warnings.append(
                f"{queue_label}: no rows deleted for queues present in the "
                "pre-delete names snapshot"
            )

        return _TaskControlCleanupResult(
            families_processed=1,
            queues_deleted=queues_deleted,
            rows_estimated_deleted=0 if errors else rows_deleted,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

    def _delete_dead_task_control_queues(
        self,
        tid: str,
        *,
        existing_queue_names: set[str],
        active_tids: set[str],
        now_ns: int,
    ) -> _TaskControlCleanupResult:
        """Delete standard stale task-local queues for one dead TID."""

        if tid in active_tids:
            return _TaskControlCleanupResult(
                dead_tids_skipped_live=1,
                warnings=(f"{tid}: skipped active runtime owner",),
            )

        cleanup_plan = _dead_task_queue_cleanup_plan(
            tid,
            now_ns=now_ns,
            retention_seconds=self._monitor_config.task_log_retention_period_seconds,
        )
        errors: list[str] = []
        warnings: list[str] = []
        rows_deleted = 0
        queue_names_to_delete = tuple(
            queue_name
            for queue_name in cleanup_plan.queue_names
            if queue_name in existing_queue_names
        )

        if queue_names_to_delete:
            try:
                with self._monitor_context().broker() as broker:
                    rows_deleted = int(broker.delete_from_queues(queue_names_to_delete))
            except (BrokerError, OSError, RuntimeError, ValueError) as exc:
                queue_label = ",".join(queue_names_to_delete)
                errors.append(f"{queue_label}: {exc}")
                rows_deleted = 0

        existing_control_queues = tuple(
            queue_name
            for queue_name in cleanup_plan.control_queue_names
            if queue_name in existing_queue_names
        )
        existing_inbox_queues = tuple(
            queue_name
            for queue_name in cleanup_plan.inbox_queue_names
            if queue_name in existing_queue_names
        )
        existing_outbox_queues = tuple(
            queue_name
            for queue_name in cleanup_plan.outbox_queue_names
            if queue_name in existing_queue_names
        )
        existing_reserved_queues = tuple(
            queue_name
            for queue_name in cleanup_plan.reserved_queue_names
            if queue_name in existing_queue_names
        )
        control_queues_deleted = 0 if errors else len(existing_control_queues)
        inbox_queues_deleted = 0 if errors else len(existing_inbox_queues)
        outbox_queues_deleted = 0 if errors else len(existing_outbox_queues)
        reserved_queues_deleted = 0 if errors else len(existing_reserved_queues)
        if (
            not errors
            and rows_deleted == 0
            and (existing_control_queues or existing_inbox_queues)
        ):
            warnings.append(
                f"{','.join(queue_names_to_delete)}: no rows deleted for "
                "queues present in the pre-delete names snapshot"
            )

        return _TaskControlCleanupResult(
            dead_tids_processed=1,
            dead_tid_queues_deleted=(
                control_queues_deleted
                + inbox_queues_deleted
                + outbox_queues_deleted
                + reserved_queues_deleted
            ),
            dead_tid_rows_estimated_deleted=rows_deleted,
            dead_tid_control_queues_deleted=control_queues_deleted,
            dead_tid_control_rows_estimated_deleted=0,
            dead_tid_inbox_queues_deleted=inbox_queues_deleted,
            dead_tid_outbox_queues_deleted=outbox_queues_deleted,
            dead_tid_reserved_queues_deleted=reserved_queues_deleted,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

    def _delete_runtime_reserved_queue(
        self,
        queue_name: str,
    ) -> _TaskControlCleanupResult:
        """Delete one selected stale task-local reserved queue."""

        rows_deleted = 0
        errors: list[str] = []
        try:
            with self._monitor_context().broker() as broker:
                rows_deleted = int(broker.delete_from_queues((queue_name,)))
        except (BrokerError, OSError, RuntimeError, ValueError) as exc:
            errors.append(f"reserved queue delete ({queue_name}): {exc}")
        return _TaskControlCleanupResult(
            reserved_families_processed=0 if errors else 1,
            reserved_queues_deleted=0 if errors else 1,
            reserved_rows_estimated_deleted=0 if errors else rows_deleted,
            errors=tuple(errors),
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

    def _queue_name_snapshot(self, *, patterns: tuple[str, ...]) -> set[str]:
        """Return queue names for runtime cleanup using public broker APIs."""

        names: set[str] = set()
        if not patterns:
            return names
        with self._monitor_context().broker() as broker:
            for pattern in patterns:
                names.update(
                    str(queue_name)
                    for queue_name in broker.list_queues(pattern=pattern)
                )
        return names

    def _maybe_start_terminal_control_cleanup_worker(self, *, now_ns: int) -> None:
        """Start the first TaskMonitor-owned runtime cleanup worker if idle."""

        self._maybe_start_runtime_cleanup_worker(
            now_ns=now_ns,
            slice_kind="terminal_control",
        )

    def _maybe_start_runtime_cleanup_worker(
        self,
        *,
        now_ns: int,
        slice_kind: RuntimeCleanupSliceKind,
    ) -> None:
        """Start one discrete TaskMonitor-owned runtime cleanup worker if idle."""

        if self._control_cleanup_work_in_flight is not None:
            self._last_control_cleanup_pending = True
            return
        work = _TaskControlCleanupWork(
            request_id=f"{self.tid}:{now_ns}:control_cleanup:{slice_kind}",
            now_ns=now_ns,
            slice_kind=slice_kind,
            previous_queue_cleanup_pending=(
                self._runtime_cleanup_queue_discovery_pending
            ),
            queue_discovery_due_monotonic=(
                self._next_runtime_cleanup_queue_discovery_due_monotonic
            ),
        )
        self._last_control_cleanup_pending = True
        self._set_activity("control_cleanup", waiting_on=None)
        try:
            self._submit_terminal_control_cleanup_worker(work)
        except RuntimeError as exc:
            self._last_control_cleanup_pending = False
            self._last_control_delete_errors = (str(exc),)
            self._last_error = str(exc)
            self._last_processor_success = False

    def _submit_terminal_control_cleanup_worker(
        self,
        work: _TaskControlCleanupWork,
    ) -> threading.Thread:
        """Run one runtime cleanup slice on its durable-effects lane.

        This lane is a narrow TaskMonitor exception to the generic broker-free
        task-worker rule: it may open fresh broker/store handles, delete
        standard stale task-local runtime queues, and mark the corresponding
        Monitor-store records. It must not mutate cached monitor fields
        directly; those are applied from its result on the reactor thread.

        Spec: [CC-2.3], [MF-5]
        """

        return self._start_service_lane(
            TASK_MONITOR_CONTROL_CLEANUP_WORKER_LANE,
            work,
            lambda: self._run_terminal_control_cleanup_worker(work),
        )

    def _run_terminal_control_cleanup_worker(
        self,
        work: _TaskControlCleanupWork,
    ) -> _TaskControlCleanupWorkerResult:
        """Run one runtime cleanup slice with fresh thread-local broker handles."""

        try:
            store = open_monitor_store(self._monitor_context(), config=self._config)
            store.ensure_schema()
            if work.slice_kind == "terminal_control":
                cleanup = self._run_terminal_control_cleanup_slice(
                    store,
                    now_ns=work.now_ns,
                    previous_queue_cleanup_pending=(
                        work.previous_queue_cleanup_pending
                    ),
                    queue_discovery_due_monotonic=(work.queue_discovery_due_monotonic),
                )
            elif work.slice_kind == "reserved":
                cleanup = self._run_reserved_cleanup_slice(
                    store,
                    now_ns=work.now_ns,
                )
            else:
                cleanup = self._run_dead_task_cleanup_slice(
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

    def _runtime_cleanup_family_limit(self) -> int:
        """Return the per-worker runtime cleanup family limit."""

        configured_limit = max(0, self._monitor_config.control_queue_delete_limit)
        return min(
            configured_limit,
            TASK_MONITOR_RUNTIME_CLEANUP_SLICE_FAMILY_LIMIT,
        )

    def _run_terminal_control_cleanup_slice(
        self,
        store: MonitorStore,
        *,
        now_ns: int,
        previous_queue_cleanup_pending: bool = True,
        queue_discovery_due_monotonic: float = 0.0,
    ) -> _TaskControlCleanupResult:
        """Run one bounded terminal-control cleanup worker slice."""

        control_limit = self._runtime_cleanup_family_limit()
        if control_limit <= 0:
            return _TaskControlCleanupResult()

        families_disposed = 0
        families_retired = 0
        errors: list[str] = []
        warnings: list[str] = []
        backfill_tids = store.list_terminal_control_deleted_disposition_backfill_tasks(
            limit=control_limit,
        )
        if backfill_tids:
            try:
                store.mark_families_disposed(
                    tuple((tid, "terminal", None, None) for tid in backfill_tids),
                    now_ns,
                )
                families_disposed += len(backfill_tids)
            except (OSError, RuntimeError, ValueError) as exc:
                errors.append(f"mark_terminal_backfill_disposed: {exc}")

        ready_records = store.list_terminal_control_cleanup_ready_tasks(
            limit=control_limit + 1,
            now_ns=now_ns,
            retention_seconds=0.0,
        )
        queue_discovery_due = _runtime_cleanup_queue_discovery_due(
            has_terminal_records=bool(ready_records),
            previous_queue_cleanup_pending=previous_queue_cleanup_pending,
            queue_discovery_due_monotonic=queue_discovery_due_monotonic,
            monotonic_now=_monitor_monotonic(),
        )
        if not queue_discovery_due:
            return _TaskControlCleanupResult(
                families_disposed=families_disposed,
                cleanup_workers_configured=1,
                errors=tuple(errors),
                pending=bool(errors),
                policy_progress=(
                    PolicyProgress(
                        policy="runtime.terminal_control_cleanup",
                        domain="task_runtime_queues",
                        scanned=len(ready_records),
                        selected=0,
                        applied=0,
                        base_reached=not ready_records and not errors,
                        blocked_reason=errors[0] if errors else None,
                    ),
                ),
            )

        records = ready_records[:control_limit]
        family_limit_hit = len(ready_records) > len(records)
        active_tids = self._active_runtime_tids() if records else set()
        task_queue_names = (
            self._queue_name_snapshot(
                patterns=(
                    f"T*.{QUEUE_INBOX_SUFFIX}",
                    f"T*.{QUEUE_OUTBOX_SUFFIX}",
                    f"T*.{QUEUE_CTRL_IN_SUFFIX}",
                    f"T*.{QUEUE_CTRL_OUT_SUFFIX}",
                )
            )
            if records
            else set()
        )
        deadline_monotonic = (
            _monitor_monotonic() + TASK_MONITOR_RUNTIME_CLEANUP_SLICE_SECONDS
        )

        families_processed = 0
        queues_deleted = 0
        rows_estimated_deleted = 0
        skipped_nonstandard = 0
        cleanup_jobs_started = 0
        cleanup_jobs_completed = 0
        cleanup_jobs_pending = 0
        unprocessed_selected = 0
        deadline_hit = False
        control_delete_marks: list[str] = []
        family_disposition_marks: list[tuple[str, str, str | None, int | None]] = []

        for record in records:
            if _monitor_monotonic() >= deadline_monotonic:
                deadline_hit = True
                cleanup_jobs_pending += 1
                unprocessed_selected += 1
                continue
            cleanup_jobs_started += 1
            if record.tid in active_tids:
                cleanup = _TaskControlCleanupResult(
                    warnings=(f"{record.tid}: skipped active runtime owner",),
                )
            else:
                try:
                    cleanup = self._delete_terminal_control_queues(
                        record,
                        existing_queue_names=task_queue_names,
                        now_ns=now_ns,
                    )
                except (BrokerError, OSError, RuntimeError, ValueError) as exc:
                    cleanup = _TaskControlCleanupResult(
                        pending=True,
                        errors=(str(exc),),
                    )
            cleanup_jobs_completed += 1
            families_processed += cleanup.families_processed
            queues_deleted += cleanup.queues_deleted
            rows_estimated_deleted += cleanup.rows_estimated_deleted
            skipped_nonstandard += cleanup.skipped_nonstandard
            errors.extend(cleanup.errors)
            warnings.extend(cleanup.warnings)
            if not cleanup.success:
                continue
            if cleanup.families_processed:
                control_delete_marks.append(record.tid)
                if record.disposition_at_ns is None:
                    family_disposition_marks.append(
                        (record.tid, "terminal", None, None)
                    )
            else:
                unprocessed_selected += 1

        if control_delete_marks:
            try:
                store.mark_task_controls_deleted(control_delete_marks, now_ns)
            except (OSError, RuntimeError, ValueError) as exc:
                errors.append(f"mark_task_controls_deleted: {exc}")
                family_disposition_marks = []
        if family_disposition_marks:
            try:
                store.mark_families_disposed(family_disposition_marks, now_ns)
                families_disposed += len(family_disposition_marks)
            except (OSError, RuntimeError, ValueError) as exc:
                errors.append(f"mark_families_disposed: {exc}")
        if not errors:
            try:
                retirement = store.retire_completed_collation_families(
                    limit=control_limit,
                    retired_at_ns=now_ns,
                )
                families_retired = retirement.families_retired
            except (OSError, RuntimeError, ValueError) as exc:
                errors.append(f"retire_completed_collation_families: {exc}")

        terminal_pending = (
            family_limit_hit or deadline_hit or unprocessed_selected > 0 or bool(errors)
        )
        next_slice_kind = None
        if not terminal_pending and queue_discovery_due:
            next_slice_kind = _next_runtime_cleanup_slice_kind("terminal_control")
        pending = terminal_pending or next_slice_kind is not None
        jobs_by_kind = (
            {"terminal_control": cleanup_jobs_completed}
            if cleanup_jobs_completed
            else {}
        )
        jobs_pending_by_kind = (
            {"terminal_control": cleanup_jobs_pending} if cleanup_jobs_pending else {}
        )
        return _TaskControlCleanupResult(
            families_processed=families_processed,
            families_disposed=families_disposed,
            families_retired=families_retired,
            queues_deleted=queues_deleted,
            rows_estimated_deleted=rows_estimated_deleted,
            skipped_nonstandard=skipped_nonstandard,
            cleanup_workers_configured=1,
            cleanup_jobs_started=cleanup_jobs_started,
            cleanup_jobs_completed=cleanup_jobs_completed,
            cleanup_jobs_pending=cleanup_jobs_pending,
            cleanup_jobs_by_kind=jobs_by_kind,
            cleanup_jobs_pending_by_kind=jobs_pending_by_kind,
            pending=pending,
            errors=tuple(errors),
            warnings=tuple(warnings),
            policy_progress=(
                PolicyProgress(
                    policy="runtime.terminal_control_cleanup",
                    domain="task_runtime_queues",
                    scanned=len(ready_records),
                    selected=len(records),
                    applied=families_processed,
                    deferred=skipped_nonstandard + unprocessed_selected,
                    waypoint_reached=family_limit_hit or deadline_hit,
                    base_reached=not terminal_pending,
                    blocked_reason=errors[0] if errors else None,
                    reason_counts={
                        "families_processed": families_processed,
                        "queues_deleted": queues_deleted,
                        "families_disposed": families_disposed,
                        "families_retired": families_retired,
                    },
                ),
            ),
            family_limit_hit=family_limit_hit,
            deadline_hit=deadline_hit,
            next_slice_kind=next_slice_kind,
        )

    def _run_reserved_cleanup_slice(
        self,
        store: MonitorStore,
        *,
        now_ns: int,
    ) -> _TaskControlCleanupResult:
        """Run one bounded reserved-queue cleanup worker slice."""

        control_limit = self._runtime_cleanup_family_limit()
        if control_limit <= 0:
            return _TaskControlCleanupResult()

        pending_records = store.list_reserved_cleanup_pending_tasks(
            limit=control_limit + 1,
        )
        records = pending_records[:control_limit]
        monitor_family_limit_hit = len(pending_records) > len(records)
        remaining_limit = max(0, control_limit - len(records))
        snapshot_needed = bool(records) or remaining_limit > 0
        active_tids = self._active_runtime_tids() if snapshot_needed else set()
        reserved_queue_names = (
            tuple(
                sorted(
                    (
                        queue_name
                        for queue_name in self._queue_name_snapshot(
                            patterns=(f"T*.{QUEUE_RESERVED_SUFFIX}",)
                        )
                        if _reserved_queue_tid(queue_name) is not None
                    ),
                    key=lambda queue_name: int(_reserved_queue_tid(queue_name) or "0"),
                )
            )
            if snapshot_needed
            else ()
        )
        reserved_queue_name_set = set(reserved_queue_names)
        selected_record_tids = {record.tid for record in records}
        fallback_queue_names = tuple(
            queue_name
            for queue_name in reserved_queue_names
            if (_reserved_queue_tid(queue_name) or "") not in selected_record_tids
        )
        selection_deadline_monotonic = (
            _monitor_monotonic() + TASK_MONITOR_RUNTIME_CLEANUP_SLICE_SECONDS
        )

        def selection_deadline_reached() -> bool:
            return _monitor_monotonic() >= selection_deadline_monotonic

        if remaining_limit > 0:
            fallback_record_tids = _reserved_queue_tids(fallback_queue_names)
            fallback_records_by_tid = {
                record.tid: record for record in store.get_tasks(fallback_record_tids)
            }
            selection = _select_runtime_reserved_cleanup_candidates(
                now_ns=now_ns,
                retention_seconds=(
                    self._monitor_config.task_log_retention_period_seconds
                ),
                limit=remaining_limit,
                active_tids=active_tids,
                queue_names=fallback_queue_names,
                task_record=fallback_records_by_tid.get,
                deadline_reached=selection_deadline_reached,
            )
        else:
            selection = _RuntimeReservedCleanupSelection(
                pending=monitor_family_limit_hit or bool(fallback_queue_names),
            )
        job_deadline_monotonic = (
            _monitor_monotonic() + TASK_MONITOR_RUNTIME_CLEANUP_SLICE_SECONDS
        )

        def job_deadline_reached() -> bool:
            return _monitor_monotonic() >= job_deadline_monotonic

        errors: list[str] = []
        warnings: list[str] = []
        cleanup_jobs_started = 0
        cleanup_jobs_completed = 0
        cleanup_jobs_pending = 0
        reserved_families_processed = 0
        reserved_queues_deleted = 0
        reserved_rows_estimated_deleted = 0
        deadline_hit = selection.deadline_hit
        reserved_checked_tids: list[str] = []
        monitor_selected = 0
        monitor_unprocessed = 0
        monitor_skipped_active = 0

        for record in records:
            monitor_selected += 1
            if job_deadline_reached():
                deadline_hit = True
                cleanup_jobs_pending += 1
                monitor_unprocessed += 1
                continue
            if record.tid in active_tids:
                monitor_skipped_active += 1
                continue
            cleanup_jobs_started += 1
            queue_name = f"T{record.tid}.{QUEUE_RESERVED_SUFFIX}"
            if queue_name in reserved_queue_name_set:
                try:
                    cleanup = self._delete_runtime_reserved_queue(queue_name)
                except (BrokerError, OSError, RuntimeError, ValueError) as exc:
                    cleanup = _TaskControlCleanupResult(
                        pending=True,
                        errors=(str(exc),),
                    )
                cleanup_jobs_completed += 1
                reserved_queues_deleted += cleanup.reserved_queues_deleted
                reserved_rows_estimated_deleted += (
                    cleanup.reserved_rows_estimated_deleted
                )
                errors.extend(cleanup.errors)
                warnings.extend(cleanup.warnings)
                if not cleanup.success:
                    continue
            else:
                cleanup_jobs_completed += 1
            reserved_checked_tids.append(record.tid)

        if reserved_checked_tids:
            try:
                store.mark_reserved_cleanup_checked(reserved_checked_tids, now_ns)
                reserved_families_processed += len(reserved_checked_tids)
            except (OSError, RuntimeError, ValueError) as exc:
                errors.append(f"mark_reserved_cleanup_checked: {exc}")

        for queue_name in selection.queue_names:
            if job_deadline_reached():
                deadline_hit = True
                cleanup_jobs_pending += 1
                continue
            cleanup_jobs_started += 1
            try:
                cleanup = self._delete_runtime_reserved_queue(queue_name)
            except (BrokerError, OSError, RuntimeError, ValueError) as exc:
                cleanup = _TaskControlCleanupResult(
                    pending=True,
                    errors=(str(exc),),
                )
            cleanup_jobs_completed += 1
            reserved_families_processed += cleanup.reserved_families_processed
            reserved_queues_deleted += cleanup.reserved_queues_deleted
            reserved_rows_estimated_deleted += cleanup.reserved_rows_estimated_deleted
            errors.extend(cleanup.errors)
            warnings.extend(cleanup.warnings)

        families_retired = 0
        if not errors:
            try:
                retirement = store.retire_completed_collation_families(
                    limit=control_limit,
                    retired_at_ns=now_ns,
                )
                families_retired = retirement.families_retired
            except (OSError, RuntimeError, ValueError) as exc:
                errors.append(f"retire_completed_collation_families: {exc}")

        deferred_count = (
            selection.skipped_active
            + selection.skipped_not_ready
            + monitor_skipped_active
            + monitor_unprocessed
        )
        reserved_pending = (
            monitor_family_limit_hit
            or selection.pending
            or deadline_hit
            or cleanup_jobs_pending > 0
            or monitor_unprocessed > 0
            or bool(errors)
        )
        next_slice_kind = None
        if not reserved_pending:
            next_slice_kind = _next_runtime_cleanup_slice_kind("reserved")
        pending = reserved_pending or next_slice_kind is not None
        jobs_by_kind = (
            {"reserved": cleanup_jobs_completed} if cleanup_jobs_completed else {}
        )
        jobs_pending_by_kind = (
            {"reserved": cleanup_jobs_pending} if cleanup_jobs_pending else {}
        )
        return _TaskControlCleanupResult(
            reserved_families_processed=reserved_families_processed,
            reserved_queues_deleted=reserved_queues_deleted,
            reserved_rows_estimated_deleted=reserved_rows_estimated_deleted,
            reserved_skipped_active=selection.skipped_active + monitor_skipped_active,
            reserved_skipped_not_ready=selection.skipped_not_ready,
            families_retired=families_retired,
            cleanup_workers_configured=1,
            cleanup_jobs_started=cleanup_jobs_started,
            cleanup_jobs_completed=cleanup_jobs_completed,
            cleanup_jobs_pending=cleanup_jobs_pending,
            cleanup_jobs_by_kind=jobs_by_kind,
            cleanup_jobs_pending_by_kind=jobs_pending_by_kind,
            pending=pending,
            errors=tuple(errors),
            warnings=tuple(warnings),
            policy_progress=(
                PolicyProgress(
                    policy="runtime.reserved_cleanup",
                    domain="task_runtime_queues",
                    scanned=len(pending_records) + len(reserved_queue_names),
                    selected=monitor_selected + len(selection.queue_names),
                    applied=reserved_families_processed,
                    deferred=deferred_count,
                    waypoint_reached=(
                        monitor_family_limit_hit or selection.pending or deadline_hit
                    ),
                    base_reached=not reserved_pending and deferred_count == 0,
                    blocked_reason=errors[0] if errors else None,
                    reason_counts={
                        "reserved_families_checked": reserved_families_processed,
                        "reserved_queues_deleted": reserved_queues_deleted,
                        "reserved_rows_estimated_deleted": (
                            reserved_rows_estimated_deleted
                        ),
                        "families_retired": families_retired,
                    },
                ),
            ),
            family_limit_hit=monitor_family_limit_hit or selection.pending,
            deadline_hit=deadline_hit,
            next_slice_kind=next_slice_kind,
        )

    def _run_dead_task_cleanup_slice(
        self,
        store: MonitorStore,
        *,
        now_ns: int,
    ) -> _TaskControlCleanupResult:
        """Run one bounded dead-task queue cleanup worker slice."""

        control_limit = self._runtime_cleanup_family_limit()
        if control_limit <= 0:
            return _TaskControlCleanupResult()

        active_tids = self._active_runtime_tids()
        task_queue_names = self._queue_name_snapshot(
            patterns=(
                f"T*.{QUEUE_INBOX_SUFFIX}",
                f"T*.{QUEUE_OUTBOX_SUFFIX}",
                f"T*.{QUEUE_CTRL_IN_SUFFIX}",
                f"T*.{QUEUE_CTRL_OUT_SUFFIX}",
                f"T*.{QUEUE_RESERVED_SUFFIX}",
            )
        )
        probe_tids = _runtime_dead_task_record_probe_tids(
            task_queue_names,
            now_ns=now_ns,
            min_age_seconds=TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS,
            active_tids=active_tids,
        )
        records_by_tid = {record.tid: record for record in store.get_tasks(probe_tids)}
        selection_deadline_monotonic = (
            _monitor_monotonic() + TASK_MONITOR_RUNTIME_CLEANUP_SLICE_SECONDS
        )

        def selection_deadline_reached() -> bool:
            return _monitor_monotonic() >= selection_deadline_monotonic

        selection = _select_runtime_dead_task_cleanup_candidates(
            task_queue_names,
            now_ns=now_ns,
            min_age_seconds=TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS,
            retention_seconds=self._monitor_config.task_log_retention_period_seconds,
            limit=control_limit,
            active_tids=active_tids,
            task_record=records_by_tid.get,
            deadline_reached=selection_deadline_reached,
        )
        job_deadline_monotonic = (
            _monitor_monotonic() + TASK_MONITOR_RUNTIME_CLEANUP_SLICE_SECONDS
        )

        def job_deadline_reached() -> bool:
            return _monitor_monotonic() >= job_deadline_monotonic

        errors: list[str] = []
        warnings: list[str] = []
        cleanup_jobs_started = 0
        cleanup_jobs_completed = 0
        cleanup_jobs_pending = 0
        dead_tids_processed = 0
        dead_tid_queues_deleted = 0
        dead_tid_rows_estimated_deleted = 0
        dead_tid_control_queues_deleted = 0
        dead_tid_control_rows_estimated_deleted = 0
        dead_tid_inbox_queues_deleted = 0
        dead_tid_outbox_queues_deleted = 0
        dead_tid_reserved_queues_deleted = 0
        deadline_hit = selection.deadline_hit

        for tid in selection.tids:
            if job_deadline_reached():
                deadline_hit = True
                cleanup_jobs_pending += 1
                continue
            cleanup_jobs_started += 1
            try:
                cleanup = self._delete_dead_task_control_queues(
                    tid,
                    existing_queue_names=task_queue_names,
                    active_tids=active_tids,
                    now_ns=now_ns,
                )
            except (BrokerError, OSError, RuntimeError, ValueError) as exc:
                cleanup = _TaskControlCleanupResult(
                    pending=True,
                    errors=(str(exc),),
                )
            cleanup_jobs_completed += 1
            dead_tids_processed += cleanup.dead_tids_processed
            dead_tid_queues_deleted += cleanup.dead_tid_queues_deleted
            dead_tid_rows_estimated_deleted += cleanup.dead_tid_rows_estimated_deleted
            dead_tid_control_queues_deleted += cleanup.dead_tid_control_queues_deleted
            dead_tid_control_rows_estimated_deleted += (
                cleanup.dead_tid_control_rows_estimated_deleted
            )
            dead_tid_inbox_queues_deleted += cleanup.dead_tid_inbox_queues_deleted
            dead_tid_outbox_queues_deleted += cleanup.dead_tid_outbox_queues_deleted
            dead_tid_reserved_queues_deleted += cleanup.dead_tid_reserved_queues_deleted
            errors.extend(cleanup.errors)
            warnings.extend(cleanup.warnings)

        dead_tids_pending = (
            max(0, len(selection.tids) - cleanup_jobs_completed) + cleanup_jobs_pending
        )
        if selection.pending and dead_tids_pending == 0:
            dead_tids_pending = 1
        dead_pending = (
            selection.pending or deadline_hit or dead_tids_pending > 0 or bool(errors)
        )
        jobs_by_kind = (
            {"dead_tid": cleanup_jobs_completed} if cleanup_jobs_completed else {}
        )
        jobs_pending_by_kind = (
            {"dead_tid": cleanup_jobs_pending} if cleanup_jobs_pending else {}
        )
        return _TaskControlCleanupResult(
            dead_tids_discovered=selection.discovered_tids,
            dead_tids_processed=dead_tids_processed,
            dead_tids_skipped_live=selection.skipped_live,
            dead_tids_skipped_too_young=selection.skipped_too_young,
            dead_tids_deferred_retention=selection.deferred_retention,
            dead_tids_pending=dead_tids_pending,
            dead_tid_queues_deleted=dead_tid_queues_deleted,
            dead_tid_rows_estimated_deleted=dead_tid_rows_estimated_deleted,
            dead_tid_control_queues_deleted=dead_tid_control_queues_deleted,
            dead_tid_control_rows_estimated_deleted=(
                dead_tid_control_rows_estimated_deleted
            ),
            dead_tid_inbox_queues_deleted=dead_tid_inbox_queues_deleted,
            dead_tid_outbox_queues_deleted=dead_tid_outbox_queues_deleted,
            dead_tid_reserved_queues_deleted=dead_tid_reserved_queues_deleted,
            cleanup_workers_configured=1,
            cleanup_jobs_started=cleanup_jobs_started,
            cleanup_jobs_completed=cleanup_jobs_completed,
            cleanup_jobs_pending=cleanup_jobs_pending,
            cleanup_jobs_by_kind=jobs_by_kind,
            cleanup_jobs_pending_by_kind=jobs_pending_by_kind,
            pending=dead_pending,
            errors=tuple(errors),
            warnings=tuple(warnings),
            policy_progress=(
                PolicyProgress(
                    policy="runtime.dead_tid_cleanup",
                    domain="task_runtime_queues",
                    scanned=selection.discovered_tids,
                    selected=len(selection.tids),
                    applied=dead_tids_processed,
                    deferred=(
                        selection.skipped_live
                        + selection.skipped_too_young
                        + selection.deferred_retention
                    ),
                    waypoint_reached=selection.pending or deadline_hit,
                    base_reached=not dead_pending,
                    blocked_reason=errors[0] if errors else None,
                    reason_counts={
                        "dead_tid_queues_deleted": dead_tid_queues_deleted,
                        "dead_tid_control_queues_deleted": (
                            dead_tid_control_queues_deleted
                        ),
                        "dead_tid_inbox_queues_deleted": (
                            dead_tid_inbox_queues_deleted
                        ),
                        "dead_tid_outbox_queues_deleted": (
                            dead_tid_outbox_queues_deleted
                        ),
                        "dead_tid_reserved_queues_deleted": (
                            dead_tid_reserved_queues_deleted
                        ),
                    },
                ),
            ),
            family_limit_hit=selection.pending,
            deadline_hit=deadline_hit,
        )

    def _delete_monitor_store_task_log_rows(
        self,
        store: MonitorStore,
    ) -> MonitorStoreRetirementResult:
        """Delete exact task-log rows proven by durable Monitor collation.

        Spec: [MF-5], [OBS.17]
        """

        refs = store.list_deletable_task_log_messages(
            limit=self._monitor_config.batch_size + 1,
            require_summary=True,
        )
        if not refs:
            self._last_policy_progress = (
                *self._last_policy_progress,
                PolicyProgress(
                    policy="monitor_store.raw_ref_delete",
                    domain="weft_monitor_task_messages",
                    scanned=0,
                    selected=0,
                    base_reached=True,
                ),
            )
            return MonitorStoreRetirementResult()
        selected_refs = refs[: self._monitor_config.batch_size]
        more_refs = len(refs) > len(selected_refs)
        applied = apply_exact_prune_candidates(
            self._monitor_context(),
            selected_refs,
            apply_result=_applied_monitor_raw_message,
            reconcile_missing=True,
        )
        reconciled_ids = tuple(
            result.candidate.message_id
            for result in applied
            if result.deleted
            or (result.error is None and not result.candidate.report_only)
        )
        if reconciled_ids:
            retirement = store.delete_task_messages_after_raw_delete(reconciled_ids)
        else:
            retirement = MonitorStoreRetirementResult()
        errors = tuple(result.error for result in applied if result.error is not None)
        if errors:
            self._last_collation_store_error = "; ".join(errors)
        self._last_policy_progress = (
            *self._last_policy_progress,
            PolicyProgress(
                policy="monitor_store.raw_ref_delete",
                domain="weft_monitor_task_messages",
                scanned=len(refs),
                selected=len(selected_refs),
                applied=retirement.message_rows_deleted,
                waypoint_reached=more_refs,
                base_reached=False,
                blocked_reason=errors[0] if errors else None,
                reason_counts={
                    "message_rows_deleted": retirement.message_rows_deleted,
                    "affected_tids": retirement.affected_tids,
                },
            ),
        )
        return retirement

    def _repair_raw_deleted_task_message_refs(
        self,
        store: MonitorStore,
    ) -> MonitorStoreRetirementResult:
        """Repair child refs left after parent raw deletion was recorded.

        Spec: [MF-5], [OBS.13], [OBS.17]
        """

        refs = store.list_raw_deleted_task_message_refs(
            limit=self._monitor_config.batch_size + 1,
        )
        if not refs:
            self._last_policy_progress = (
                *self._last_policy_progress,
                PolicyProgress(
                    policy="monitor_store.raw_deleted_child_ref_repair",
                    domain="weft_monitor_task_messages",
                    scanned=0,
                    selected=0,
                    base_reached=True,
                ),
            )
            return MonitorStoreRetirementResult()

        selected_refs = refs[: self._monitor_config.batch_size]
        more_refs = len(refs) > len(selected_refs)
        applied = apply_exact_prune_candidates(
            self._monitor_context(),
            selected_refs,
            apply_result=_applied_monitor_raw_message,
            reconcile_missing=True,
        )
        reconciled_ids = tuple(
            result.candidate.message_id
            for result in applied
            if result.deleted
            or (result.error is None and not result.candidate.report_only)
        )
        if reconciled_ids:
            retirement = store.delete_task_messages_after_raw_delete(reconciled_ids)
        else:
            retirement = MonitorStoreRetirementResult()
        errors = tuple(result.error for result in applied if result.error is not None)
        if errors:
            self._last_collation_store_error = "; ".join(errors)
        self._last_policy_progress = (
            *self._last_policy_progress,
            PolicyProgress(
                policy="monitor_store.raw_deleted_child_ref_repair",
                domain="weft_monitor_task_messages",
                scanned=len(refs),
                selected=len(selected_refs),
                applied=retirement.message_rows_deleted,
                waypoint_reached=more_refs,
                base_reached=False,
                blocked_reason=errors[0] if errors else None,
                reason_counts={
                    "message_rows_deleted": retirement.message_rows_deleted,
                    "affected_tids": retirement.affected_tids,
                },
            ),
        )
        return retirement

    def _recover_orphan_task_log_rows(
        self,
        store: MonitorStore,
        *,
        now_ns: int,
    ) -> _DeadTaskLogDeleteResult:
        """Delete raw task-log rows stranded by inconsistent Monitor state.

        This is a bounded recovery path for legacy/inconsistent store rows. It
        is not the ordinary FIFO cleanup authority.

        Spec: [MF-5], [OBS.13], [OBS.17]
        """

        tids = store.list_raw_deleted_task_log_recovery_tids(
            limit=self._monitor_config.batch_size + 1,
        )
        selected_tids = tids[: self._monitor_config.batch_size]
        more_tids = len(tids) > len(selected_tids)
        api_matches = 0
        empty_probes = 0
        coalesced_rows = 0
        refs_selected = 0
        rows_deleted = 0
        checked_tids: list[str] = []
        errors: list[str] = []
        for tid in selected_tids:
            try:
                group = _fetch_dead_task_log_coalesce_group(
                    self._monitor_context(),
                    tid,
                    chunk_limit=max(1, self._monitor_config.batch_size),
                )
            except (OSError, RuntimeError, ValueError) as exc:
                errors.append(f"{tid}: {exc}")
                continue
            api_matches += group.api_matches
            if not group.rows:
                empty_probes += 1
                checked_tids.append(tid)
                continue

            updates: list[MonitorTaskEventUpdate] = []
            for row in group.rows:
                try:
                    payload = json.loads(row.body)
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, Mapping):
                    continue
                update = update_from_task_log_payload(
                    payload,
                    queue_name=row.queue,
                    message_id=row.message_id,
                )
                if update is not None and update.tid == tid:
                    updates.append(update)

            tid_errors: list[str] = []
            try:
                if updates:
                    ingest = store.record_task_log_updates(
                        WEFT_GLOBAL_LOG_QUEUE,
                        tuple(updates),
                        checkpoint_message_id=None,
                    )
                    coalesced_rows += ingest.updates_written
                delete_result = self._delete_exact_task_log_rows(
                    tuple(group.rows),
                    require_deleted=True,
                )
                refs_selected += len(group.rows)
                tid_errors.extend(delete_result.errors)
                if delete_result.deleted_ids:
                    retirement = store.delete_task_messages_after_raw_delete(
                        delete_result.deleted_ids,
                        deleted_at_ns=now_ns,
                    )
                    rows_deleted += retirement.message_rows_deleted
            except (OSError, RuntimeError, ValueError) as exc:
                tid_errors.append(str(exc))
            if tid_errors:
                errors.extend(f"{tid}: {error}" for error in tid_errors)
            else:
                checked_tids.append(tid)

        families_checked = 0
        if checked_tids:
            try:
                store.mark_orphan_raw_recovery_checked(checked_tids, now_ns)
                families_checked = len(checked_tids)
            except (OSError, RuntimeError, ValueError) as exc:
                errors.append(f"mark_orphan_raw_recovery_checked: {exc}")

        result = _DeadTaskLogDeleteResult(
            api_matches=api_matches,
            families_checked=families_checked,
            empty_probes=empty_probes,
            coalesced_rows=coalesced_rows,
            refs_selected=refs_selected,
            rows_deleted=rows_deleted,
            errors=tuple(errors),
        )
        self._last_policy_progress = (
            *self._last_policy_progress,
            PolicyProgress(
                policy="monitor_store.orphan_raw_recovery",
                domain="weft_monitor_task_collations",
                scanned=len(tids),
                selected=len(selected_tids),
                applied=families_checked,
                waypoint_reached=more_tids,
                base_reached=not tids,
                blocked_reason=errors[0] if errors else None,
                reason_counts={
                    "api_matches": api_matches,
                    "families_checked": families_checked,
                    "empty_probes": empty_probes,
                    "coalesced_rows": coalesced_rows,
                    "refs_selected": refs_selected,
                    "rows_deleted": rows_deleted,
                },
            ),
        )
        return result

    def _coalesce_and_delete_dead_task_log_rows_for_tids(
        self,
        store: MonitorStore,
        tids: tuple[str, ...],
        *,
        now_ns: int,
    ) -> _DeadTaskLogDeleteResult:
        """Coalesce and delete exact task-log rows for known dead TIDs.

        Spec: [MF-5], [OBS.17]
        """

        api_matches = 0
        coalesced_rows = 0
        summaries_emitted = 0
        refs_selected = 0
        rows_deleted = 0
        errors: list[str] = []
        for tid in tids:
            try:
                group = _fetch_dead_task_log_coalesce_group(
                    self._monitor_context(),
                    tid,
                    chunk_limit=max(1, self._monitor_config.batch_size),
                )
                api_matches += group.api_matches
                updates: list[MonitorTaskEventUpdate] = []
                for row in group.rows:
                    try:
                        payload = json.loads(row.body)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(payload, Mapping):
                        continue
                    update = update_from_task_log_payload(
                        payload,
                        queue_name=row.queue,
                        message_id=row.message_id,
                    )
                    if update is not None and update.tid == tid:
                        updates.append(update)
                if updates:
                    ingest = store.record_task_log_updates(
                        WEFT_GLOBAL_LOG_QUEUE,
                        tuple(updates),
                        checkpoint_message_id=None,
                    )
                    coalesced_rows += ingest.updates_written
                    record = store.get_task(tid)
                    if record is not None:
                        if record.summary_emitted_at_ns is None:
                            close_reason = (
                                "terminal" if record.terminal_seen else "dead_task"
                            )
                            self._emit_monitor_store_summary(
                                MonitorSummaryReadyTask(
                                    record=record,
                                    close_reason=close_reason,
                                ),
                                emitted_at_ns=now_ns,
                            )
                            store.mark_summary_emitted(
                                tid,
                                now_ns,
                                suspect_reason=(
                                    None if close_reason == "terminal" else close_reason
                                ),
                            )
                            summaries_emitted += 1
                        if record.disposition_at_ns is None:
                            disposition_reason = (
                                "terminal" if record.terminal_seen else "dead_task"
                            )
                            store.mark_family_disposed(
                                tid,
                                now_ns,
                                disposition_reason=disposition_reason,
                                suspect_reason=(
                                    None
                                    if disposition_reason == "terminal"
                                    else disposition_reason
                                ),
                                suspect_at_ns=(
                                    None if disposition_reason == "terminal" else now_ns
                                ),
                            )
                deleted = self._delete_monitor_store_task_log_rows_for_tids(
                    store,
                    (tid,),
                )
            except (ExternalTaskLogError, OSError, RuntimeError, ValueError) as exc:
                errors.append(f"{tid}: {exc}")
                continue
            refs_selected += deleted.refs_selected
            rows_deleted += deleted.rows_deleted
            errors.extend(deleted.errors)
        result = _DeadTaskLogDeleteResult(
            api_matches=api_matches,
            coalesced_rows=coalesced_rows,
            summaries_emitted=summaries_emitted,
            refs_selected=refs_selected,
            rows_deleted=rows_deleted,
            errors=tuple(errors),
        )
        self._last_policy_progress = (
            *self._last_policy_progress,
            PolicyProgress(
                policy="runtime.dead_task_log_coalesce",
                domain=WEFT_GLOBAL_LOG_QUEUE,
                scanned=len(tids),
                selected=refs_selected,
                applied=rows_deleted,
                base_reached=not tids or refs_selected == 0,
                blocked_reason=errors[0] if errors else None,
                reason_counts={
                    "api_matches": api_matches,
                    "coalesced_rows": coalesced_rows,
                    "summaries_emitted": summaries_emitted,
                    "refs_selected": refs_selected,
                    "rows_deleted": rows_deleted,
                },
            ),
        )
        return result

    def _delete_monitor_store_task_log_rows_for_tids(
        self,
        store: MonitorStore,
        tids: tuple[str, ...],
    ) -> _DeadTaskLogDeleteResult:
        """Delete exact task-log rows proven deletable for known TIDs."""

        refs = store.list_deletable_task_log_messages_for_tids(
            tids,
            limit=self._monitor_config.batch_size,
            require_summary=True,
        )
        if not refs:
            return _DeadTaskLogDeleteResult()
        applied = apply_exact_prune_candidates(
            self._monitor_context(),
            refs,
            apply_result=_applied_monitor_raw_message,
            reconcile_missing=True,
        )
        reconciled_ids = tuple(
            result.candidate.message_id
            for result in applied
            if result.deleted
            or (result.error is None and not result.candidate.report_only)
        )
        if reconciled_ids:
            retirement = store.delete_task_messages_after_raw_delete(reconciled_ids)
        else:
            retirement = MonitorStoreRetirementResult()
        errors = tuple(result.error for result in applied if result.error is not None)
        if errors:
            self._last_collation_store_error = "; ".join(errors)
        return _DeadTaskLogDeleteResult(
            refs_selected=len(refs),
            rows_deleted=retirement.message_rows_deleted,
            errors=errors,
        )

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
        self._last_policy_progress = ()
        task_log_owner = self._task_log_deletion_owner()
        if self._monitor_config.processor in WEFT_TASK_MONITOR_PROCESSOR_BUILTINS:
            self._maybe_start_builtin_cycle_worker(
                now_ns=now_ns,
                task_log_owner=task_log_owner,
            )
            return

        self._last_cycle_at = now_ns
        sink = self._external_task_log_sink
        if sink is not None:
            sink.reset_cycle_counts()
            self._refresh_external_task_log_status()
        if task_log_owner == "raw_external":
            self._last_collation_rows_processed = 0
            self._last_collation_tasks_updated = 0
            self._last_collation_terminal_tasks = 0
            self._last_collation_summaries_emitted = 0
            self._last_monitor_store_message_rows_deleted = 0
            self._last_monitor_store_message_tombstones_pruned = 0
            self._last_monitor_store_families_retired = 0
            self._last_terminal_families_disposed = 0
            self._last_suspect_families_classified = 0
            self._last_control_families_processed = 0
            self._last_control_families_disposed = 0
            self._last_control_queues_deleted = 0
            self._last_control_rows_estimated_deleted = 0
            self._last_control_nonstandard_skipped = 0
            self._last_control_cleanup_pending = False
            self._last_control_rows_deleted = 0
            self._last_cleanup_workers_configured = 0
            self._last_cleanup_jobs_started = 0
            self._last_cleanup_jobs_completed = 0
            self._last_cleanup_jobs_pending = 0
            self._last_cleanup_jobs_by_kind = {}
            self._last_cleanup_jobs_pending_by_kind = {}
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

    def _maybe_start_builtin_cycle_worker(
        self,
        *,
        now_ns: int,
        task_log_owner: str,
    ) -> None:
        """Start one built-in monitor cycle worker if no monitor work is active."""

        if self._builtin_cycle_work_in_flight is not None:
            self._last_catchup_pending = True
            return
        work = _TaskMonitorBuiltinCycleWork(
            request_id=f"{self.tid}:{now_ns}:builtin_cycle",
            now_ns=now_ns,
            task_log_owner=task_log_owner,
        )
        self._set_activity("processing", waiting_on=None)
        try:
            self._submit_builtin_cycle_worker(work)
        except RuntimeError as exc:
            result = TaskMonitorProcessorResult(success=False, errors=(str(exc),))
            self._last_cycle_at = now_ns
            self._finish_monitor_cycle(
                candidates=(),
                last_timestamp=None,
                events_scanned=0,
                result=result,
            )

    def _submit_builtin_cycle_worker(
        self,
        work: _TaskMonitorBuiltinCycleWork,
    ) -> threading.Thread:
        """Run built-in monitor work on its dedicated durable-effects lane.

        This lane is a narrow TaskMonitor exception to the generic broker-free
        task-worker rule: it may open fresh broker/store handles, scan
        task-log rows, write Monitor-store rows, and delete exact queue rows.
        Cached monitor fields are committed from the returned result on the
        reactor thread.

        Spec: [CC-2.3], [MF-5]
        """

        return self._start_service_lane(
            TASK_MONITOR_BUILTIN_CYCLE_WORKER_LANE,
            work,
            lambda: self._run_builtin_cycle_worker(work),
        )

    def _run_builtin_cycle_worker(
        self,
        work: _TaskMonitorBuiltinCycleWork,
    ) -> _TaskMonitorBuiltinCycleWorkerResult:
        """Run one bounded built-in monitor cycle with worker-local handles."""

        self._last_cycle_at = work.now_ns
        self._last_policy_progress = ()
        runtime_cleanup_ready = False
        sink = self._external_task_log_sink
        if sink is not None:
            sink.reset_cycle_counts()
            self._refresh_external_task_log_status()
        if work.task_log_owner == "raw_external":
            self._last_collation_rows_processed = 0
            self._last_collation_tasks_updated = 0
            self._last_collation_terminal_tasks = 0
            self._last_collation_summaries_emitted = 0
            self._last_monitor_store_message_rows_deleted = 0
            self._last_monitor_store_message_tombstones_pruned = 0
            self._last_monitor_store_families_retired = 0
            self._last_terminal_families_disposed = 0
            self._last_suspect_families_classified = 0
            self._last_control_families_processed = 0
            self._last_control_families_disposed = 0
            self._last_control_queues_deleted = 0
            self._last_control_rows_estimated_deleted = 0
            self._last_control_nonstandard_skipped = 0
            self._last_control_cleanup_pending = False
            self._last_control_rows_deleted = 0
            self._last_cleanup_workers_configured = 0
            self._last_cleanup_jobs_started = 0
            self._last_cleanup_jobs_completed = 0
            self._last_cleanup_jobs_pending = 0
            self._last_cleanup_jobs_by_kind = {}
            self._last_cleanup_jobs_pending_by_kind = {}
            self._last_control_delete_errors = ()
            self._last_control_delete_warnings = ()
        else:
            runtime_cleanup_ready = self._run_monitor_store_cycle(
                now_ns=work.now_ns,
                task_log_owner=work.task_log_owner,
                start_control_cleanup=False,
            )

        self._last_candidates_seen = 0
        self._last_candidate_class_counts = {}
        self._last_safe_to_delete_candidates = 0
        self._last_prune_records_scanned = 0
        self._last_cleanup_queue_stats = ()
        self._last_cleanup_policy_stats = ()
        result = self._run_builtin_monitor_processor_cycle(
            apply=self._monitor_config.processor == "delete",
            now_ns=work.now_ns,
            task_log_owner=work.task_log_owner,
        )
        return _TaskMonitorBuiltinCycleWorkerResult(
            work=work,
            result=result,
            runtime_cleanup_ready=runtime_cleanup_ready,
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
        self._last_catchup_pending = result.success and progress_requires_catchup(
            self._last_policy_progress
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
            "policy_progress": progress_summaries(self._last_policy_progress),
            "task_log_retention_period_seconds": (
                self._monitor_config.task_log_retention_period_seconds
            ),
            "task_log_external": self._external_task_log_status.to_summary(),
            "collation_store": self._monitor_store_status.to_summary(),
            "collation_rows_processed": self._last_collation_rows_processed,
            "collation_tasks_updated": self._last_collation_tasks_updated,
            "collation_terminal_tasks": self._last_collation_terminal_tasks,
            "collation_summaries_emitted": (self._last_collation_summaries_emitted),
            "monitor_store_message_rows_deleted": (
                self._last_monitor_store_message_rows_deleted
            ),
            "monitor_store_message_tombstones_pruned": (
                self._last_monitor_store_message_tombstones_pruned
            ),
            "monitor_store_families_retired": (
                self._last_monitor_store_families_retired
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
            "cleanup_workers_configured": self._last_cleanup_workers_configured,
            "cleanup_jobs_started": self._last_cleanup_jobs_started,
            "cleanup_jobs_completed": self._last_cleanup_jobs_completed,
            "cleanup_jobs_pending": self._last_cleanup_jobs_pending,
            "cleanup_jobs_by_kind": dict(self._last_cleanup_jobs_by_kind),
            "cleanup_jobs_pending_by_kind": dict(
                self._last_cleanup_jobs_pending_by_kind
            ),
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
        self._set_activity("processing", waiting_on=None)
        try:
            self._start_service_call_lane(
                TASK_MONITOR_PROCESSOR_WORKER_LANE,
                work,
                lambda: self._run_custom_monitor_processor(work.request),
            )
        except RuntimeError as exc:
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
        if result.lane == TASK_MONITOR_BUILTIN_CYCLE_WORKER_LANE:
            self._handle_builtin_cycle_worker_result(result)
            return
        if result.lane == TASK_MONITOR_CONTROL_CLEANUP_WORKER_LANE:
            self._handle_control_cleanup_worker_result(result)
            return
        if result.lane != TASK_MONITOR_PROCESSOR_WORKER_LANE:
            super()._handle_worker_result(result)
            return

        work = cast(
            _TaskMonitorProcessorWork | None,
            self._pop_service_lane_work(TASK_MONITOR_PROCESSOR_WORKER_LANE),
        )
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

    def _handle_builtin_cycle_worker_result(self, result: TaskWorkerResult) -> None:
        """Apply built-in monitor cycle worker results on the reactor thread."""

        work = cast(
            _TaskMonitorBuiltinCycleWork | None,
            self._pop_service_lane_work(TASK_MONITOR_BUILTIN_CYCLE_WORKER_LANE),
        )
        if work is None:
            return
        if result.error is not None:
            worker_result = _TaskMonitorBuiltinCycleWorkerResult(
                work=work,
                result=TaskMonitorProcessorResult(
                    success=False,
                    errors=(str(result.error),),
                ),
            )
        else:
            value = result.value
            if (
                not isinstance(value, _TaskMonitorBuiltinCycleWorkerResult)
                or value.work.request_id != work.request_id
            ):
                worker_result = _TaskMonitorBuiltinCycleWorkerResult(
                    work=work,
                    result=TaskMonitorProcessorResult(
                        success=False,
                        errors=(
                            "task-monitor built-in cycle returned an invalid result",
                        ),
                    ),
                )
            else:
                worker_result = value

        self._finish_monitor_cycle(
            candidates=(),
            last_timestamp=None,
            events_scanned=0,
            result=worker_result.result,
        )
        if (
            worker_result.runtime_cleanup_ready
            and self._control_cleanup_work_in_flight is None
        ):
            self._maybe_start_terminal_control_cleanup_worker(now_ns=work.now_ns)

    def _handle_control_cleanup_worker_result(self, result: TaskWorkerResult) -> None:
        """Apply runtime cleanup worker results on the reactor thread."""

        work = cast(
            _TaskControlCleanupWork | None,
            self._pop_service_lane_work(TASK_MONITOR_CONTROL_CLEANUP_WORKER_LANE),
        )
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
        self._last_monitor_store_families_retired += cleanup.families_retired
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
        self._last_cleanup_workers_configured = cleanup.cleanup_workers_configured
        self._last_cleanup_jobs_started = cleanup.cleanup_jobs_started
        self._last_cleanup_jobs_completed = cleanup.cleanup_jobs_completed
        self._last_cleanup_jobs_pending = cleanup.cleanup_jobs_pending
        self._last_cleanup_jobs_by_kind = dict(cleanup.cleanup_jobs_by_kind or {})
        self._last_cleanup_jobs_pending_by_kind = dict(
            cleanup.cleanup_jobs_pending_by_kind or {}
        )
        self._last_control_cleanup_pending = cleanup.pending
        self._last_control_delete_errors = cleanup.errors
        self._last_control_delete_warnings = cleanup.warnings
        self._last_policy_progress = (
            *self._last_policy_progress,
            *cleanup.policy_progress,
        )
        self._last_terminal_families_disposed += cleanup.families_disposed
        self._runtime_cleanup_queue_discovery_pending = (
            cleanup.pending or not cleanup.success
        )
        self._next_runtime_cleanup_queue_discovery_due_monotonic = time.monotonic() + (
            self._monitor_config.catchup_interval_seconds
            if self._runtime_cleanup_queue_discovery_pending
            else self._monitor_config.interval_seconds
        )
        if monitor_status is not None:
            self._monitor_store_status = monitor_status
            self._last_collation_store_error = monitor_status.error

        if cleanup.success:
            self._last_error = self._heartbeat_error
        else:
            self._last_error = "; ".join(cleanup.errors) if cleanup.errors else "failed"
        if cleanup.success and cleanup.next_slice_kind is not None:
            self._maybe_start_runtime_cleanup_worker(
                now_ns=work.now_ns,
                slice_kind=cleanup.next_slice_kind,
            )

        self._last_catchup_pending = self._last_catchup_pending or (
            cleanup.success and progress_requires_catchup(cleanup.policy_progress)
        )
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

        if self._monitor_config.processor == "jsonl_then_delete":
            return TaskMonitorProcessorResult(
                success=False,
                errors=(
                    "jsonl_then_delete task-monitor processor is reserved until "
                    "the logging callback is implemented",
                ),
            )
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
        self._last_policy_progress = (
            *self._last_policy_progress,
            *cleanup.policy_progress,
        )
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
        raw_waypoint = (
            len(selected) >= self._monitor_config.batch_size
            or window.scan_limit_reached
            or bool(errors)
            or bool(apply_errors)
        )
        self._last_policy_progress = (
            *self._last_policy_progress,
            PolicyProgress(
                policy=TASK_MONITOR_POLICY_TASK_LOG_EXTERNAL_RAW,
                domain=WEFT_GLOBAL_LOG_QUEUE,
                scanned=window.scanned,
                selected=len(selected),
                applied=deleted,
                waypoint_reached=raw_waypoint,
                base_reached=(
                    not raw_waypoint and not selected and not window.scan_limit_reached
                ),
                blocked_reason=(errors[0] if errors else None)
                or (apply_errors[0] if apply_errors else None),
                reason_counts={"older_than_task_log_retention_period": len(selected)}
                if selected
                else {},
            ),
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
