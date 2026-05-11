"""Task monitor task primitive.

This module provides the task-shaped non-consuming scanner used by the
foreground task monitor command and the manager-supervised non-destructive
TaskMonitor service. Classification and destructive cleanup stay outside this
part-1 runtime loop.

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
from dataclasses import replace
from pathlib import Path
from typing import Any

from simplebroker import BrokerTarget
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
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS,
    WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS_DEFAULT,
)
from weft.context import WeftContext, build_context
from weft.core.heartbeat import cancel_heartbeat, upsert_heartbeat
from weft.core.pruning.retention import (
    RetentionPruneConfig,
    run_retention_prune_for_context,
)
from weft.core.pruning.runtime import RuntimePruneConfig, run_runtime_prune_for_context
from weft.core.serve_log import (
    build_serve_log_record,
    emit_serve_log_record,
    serve_log_allows,
    serve_log_level,
    truncate_serve_log_value,
)
from weft.core.task_monitoring import (
    TaskMonitorCandidate,
    TaskMonitorProcessorRequest,
    TaskMonitorProcessorResult,
    TaskMonitorRuntimeConfig,
    build_task_monitor_cycle_snapshot,
    resolve_task_monitor_processor,
    task_monitor_candidate_class_counts,
)
from weft.core.tasks.base import ControlRequest
from weft.core.taskspec import IOSection, SpecSection, StateSection, TaskSpec
from weft.helpers import iter_queue_entries

from .base import BaseTask
from .multiqueue_watcher import QueueMessageContext

TaskMonitorCallback = Callable[[str, str, int], None]


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
    and calls the configured processor without deleting broker evidence.
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
            processor=self._monitor_config.processor,
            log_sink=self._monitor_config.log_sink,
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

    def _has_pending_runtime_input(self) -> bool:
        for queue_name in (self._queue_names["ctrl_in"], self._queue_names["inbox"]):
            try:
                if self._queue(queue_name).has_pending():
                    return True
            except (BrokerError, OSError, RuntimeError):
                continue
        return False

    def next_wait_timeout(self) -> float:
        """Return the launcher wait timeout for the next monitor turn.

        The monitor is reactive to task-local wakeups and its heartbeat-driven
        schedule. It must not rely on the launcher's 50 ms default polling loop
        while it is simply waiting for the next scheduled cycle.
        """

        if self._first_cycle_pending or self._wake_requested:
            return 0.0
        if self._has_pending_runtime_input():
            return 0.0
        if not self._monitor_config.enabled:
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

        if self.should_stop:
            return
        self._emit_task_monitor_config_once()
        if not self._monitor_config.enabled:
            self._set_activity("disabled", waiting_on=None)
            self._maybe_emit_poll_report()
            return

        self._drain_queue()
        if self.should_stop:
            self._maybe_emit_poll_report()
            return
        self._ensure_heartbeat_registered()

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
                "log_sink": self._monitor_config.log_sink,
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
            }
        )
        return payload

    def _monitor_context(self) -> WeftContext:
        spec_context = getattr(self.taskspec.spec, "weft_context", None)
        ctx = build_context(spec_context=spec_context, config=self._config)
        broker_target = self._db_path
        if isinstance(broker_target, BrokerTarget):
            return replace(
                ctx,
                broker_target=broker_target,
                database_path=broker_target.target_path,
                broker_config={
                    key: value
                    for key, value in self._config.items()
                    if key.startswith("BROKER_")
                },
            )
        return ctx

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
        self._set_activity("scanning", waiting_on=WEFT_GLOBAL_LOG_QUEUE)
        candidates, last_timestamp, events_scanned = self._scan_task_log_candidates()
        self._last_cycle_at = now_ns
        self._last_candidates_seen = len(candidates)
        self._last_candidate_class_counts = task_monitor_candidate_class_counts(
            candidates
        )
        self._last_safe_to_delete_candidates = sum(
            1 for candidate in candidates if candidate.safe_to_delete
        )

        result = self._process_monitor_candidates(candidates, now_ns=now_ns)

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
        now_ns: int,
    ) -> TaskMonitorProcessorResult:
        if self._monitor_config.processor in {"delete", "report_only"}:
            return self._run_canonical_prune_cycle(
                apply=self._monitor_config.processor == "delete"
            )

        request = TaskMonitorProcessorRequest(
            context=self._monitor_context(),
            config=self._monitor_config,
            cycle_id=f"{self.tid}:{now_ns}",
            monitor_tid=self.tid,
            candidates=candidates,
            now_ns=now_ns,
        )
        try:
            processor = resolve_task_monitor_processor(self._monitor_config.processor)
            return processor(request)
        except Exception as exc:  # pragma: no cover - custom processor boundary
            self._emit_task_monitor_log(
                "task_monitor_processor_error",
                required_level="info",
                severity="error",
                processor=self._monitor_config.processor,
                errors=(str(exc),),
            )
            return TaskMonitorProcessorResult(success=False, errors=(str(exc),))

    def _run_canonical_prune_cycle(self, *, apply: bool) -> TaskMonitorProcessorResult:
        ctx = self._monitor_context()
        self._set_activity("cleanup_scanning", waiting_on=WEFT_GLOBAL_LOG_QUEUE)
        runtime = run_runtime_prune_for_context(
            ctx,
            RuntimePruneConfig(
                context_path=ctx.root,
                apply=apply,
                limit=self._monitor_config.batch_size,
            ),
        )
        retention = run_retention_prune_for_context(
            ctx,
            RetentionPruneConfig(
                context_path=ctx.root,
                family="retention",
                apply=apply,
                force=False,
                limit=self._monitor_config.batch_size,
                exclude_tids=(self.tid,),
                archive_path=None,
                require_archive=False,
            ),
        )
        self._last_prune_records_scanned = (
            runtime.records_scanned + retention.records_scanned
        )
        errors = (*runtime.errors, *retention.errors)
        warnings = retention.warnings
        processed = len(runtime.candidates) + len(retention.candidates)
        deleted = runtime.deleted + retention.deleted
        reported = 0 if apply else processed
        return TaskMonitorProcessorResult(
            success=not errors and runtime.failed == 0 and retention.failed == 0,
            processed=processed,
            deleted=deleted,
            reported=reported,
            errors=(
                *errors,
                *(
                    candidate.error or "runtime prune candidate failed"
                    for candidate in runtime.applied_candidates
                    if candidate.error
                ),
                *(
                    candidate.error or "retention prune candidate failed"
                    for candidate in retention.applied_candidates
                    if candidate.error
                ),
            ),
            warnings=warnings,
        )

    def _cleanup_reserved_if_needed(self) -> None:
        """Task monitors never create reserved messages."""

        return
