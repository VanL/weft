"""Manager task implementation for spawning child tasks.

Spec references:
- docs/specifications/01-Core_Components.md [CC-2.2], [CC-2.3], [CC-2.5]
- docs/specifications/03-Manager_Architecture.md [MA-0], [MA-1], [MA-2], [MA-3]
"""

from __future__ import annotations

import atexit
import copy
import json
import logging
import multiprocessing
import os
import signal
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import ValidationError

from simplebroker import BrokerTarget, Queue
from simplebroker.ext import BrokerError
from weft._constants import (
    CONTROL_KILL,
    CONTROL_PING,
    CONTROL_STOP,
    DEFAULT_FUNCTION_TARGET,
    INTERNAL_AUTOSTART_ENABLED_METADATA_KEY,
    INTERNAL_AUTOSTART_SOURCE_METADATA_KEY,
    INTERNAL_HEARTBEAT_ENDPOINT_NAME,
    INTERNAL_RUNTIME_ENDPOINT_NAME_KEY,
    INTERNAL_RUNTIME_ENVELOPE_ENDPOINT_NAME_KEY,
    INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY,
    INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT,
    INTERNAL_RUNTIME_TASK_CLASS_KEY,
    INTERNAL_RUNTIME_TASK_CLASS_PIPELINE,
    INTERNAL_RUNTIME_TASK_CLASS_PIPELINE_EDGE,
    INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR,
    INTERNAL_SERVICE_AUTHORITY_MANAGER,
    INTERNAL_SERVICE_AUTHORITY_METADATA_KEY,
    INTERNAL_SERVICE_KEY_HEARTBEAT,
    INTERNAL_SERVICE_KEY_TASK_MONITOR,
    INTERNAL_SERVICE_LIFECYCLE_METADATA_KEY,
    MANAGED_SERVICE_CONVERGENCE_INTERVAL_SECONDS,
    MANAGED_SERVICE_PING_TIMEOUT_SECONDS,
    MANAGED_SERVICE_RECENT_EVIDENCE_GRACE_SECONDS,
    MANAGED_SERVICE_STABLE_AUDIT_INTERVAL_SECONDS,
    MANAGER_CHILD_EXIT_POLL_INTERVAL,
    MANAGER_CHILD_LAUNCH_WORKER_LANE,
    MANAGER_CHILD_STARTUP_LIVENESS_GRACE_SECONDS,
    MANAGER_CONTROL_DRAIN_MAX_MESSAGES,
    MANAGER_DISPATCH_STALL_LOG_INTERVAL_SECONDS,
    MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS,
    MANAGER_INTERNAL_SPAWN_DRAIN_MAX_MESSAGES,
    MANAGER_LEADERSHIP_CHECK_INTERVAL_SECONDS,
    MANAGER_LEADERSHIP_DRAIN_REVALIDATE_SECONDS,
    MANAGER_LEADERSHIP_PING_CACHE_TTL_SECONDS,
    MANAGER_LEADERSHIP_PING_TIMEOUT_SECONDS,
    MANAGER_PID_LIVENESS_RECHECK_INTERVAL,
    MANAGER_PUBLIC_SPAWN_DRAIN_MAX_MESSAGES,
    MANAGER_REGISTRY_HEARTBEAT_INTERVAL_SECONDS,
    MANAGER_SERVE_LOG_CHILD_LIMIT,
    MANAGER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS,
    MANAGER_STALLED_CONTROL_LOG_INTERVAL_SECONDS,
    MANAGER_STALLED_CONTROL_RETRY_SECONDS,
    QUEUE_CTRL_IN_SUFFIX,
    QUEUE_CTRL_OUT_SUFFIX,
    QUEUE_INTERNAL_RESERVED_SUFFIX,
    QUEUE_PRIORITY_INTERNAL,
    QUEUE_PRIORITY_NORMAL,
    SERVICE_OWNER_SCHEMA,
    SERVICE_STATUS_ACTIVE,
    SERVICE_STATUS_DRAINING,
    SERVICE_STATUS_STOPPED,
    SERVICE_STATUS_SUPERSEDED,
    SERVICE_STATUS_TERMINAL,
    SERVICE_TYPE_MANAGED,
    SERVICE_TYPE_MANAGER,
    SPEC_TYPE_PIPELINE,
    SPEC_TYPE_TASK,
    TERMINAL_ENVELOPE_TYPE,
    TERMINAL_TASK_STATUSES,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE,
    WEFT_MANAGER_LIFETIME_TIMEOUT,
    WEFT_MANAGER_RUNTIME_HANDLE_JSON_ENV,
    WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS,
    WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS_DEFAULT,
    WEFT_SERVICES_REGISTRY_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS_DEFAULT,
    WEFT_TID_MAPPINGS_QUEUE,
    WORK_ENVELOPE_START,
    WRAPPER_LOST_ERROR,
    get_weft_directory_name,
)
from weft.context import WeftContext
from weft.ext import RunnerHandle
from weft.helpers import (
    canonical_owner_tid,
    detect_container_runtime,
    handle_has_live_host_process,
    is_canonical_manager_record,
    iter_queue_json_entries,
    kill_process_tree,
    live_host_processes_from_handle,
    pid_is_live,
    process_create_time,
    redact_taskspec_dump,
    terminate_process_tree,
)
from weft.runtime_liveness import runtime_liveness_from_registered_probe

from .control_probe import coerce_pong_response
from .launcher import launch_task_process
from .manager_services import (
    ManagedServiceDecision,
    ManagedServiceEvidence,
    ManagedServiceSpec,
    ManagedServiceState,
    ServiceCandidate,
    apply_service_metadata,
    reduce_managed_service_state,
    select_canonical_live_candidate,
    service_key_from_metadata,
    service_metadata,
)
from .pipelines import (
    PipelineCompilationContext,
    compile_linear_pipeline,
    load_pipeline_spec_payload,
)
from .serve_log import (
    build_serve_log_record,
    emit_serve_log_record,
    serve_log_allows,
    serve_log_level,
    truncate_serve_log_value,
)
from .service_convergence import (
    ServiceOwnerRecord,
    build_manager_service_payload,
    build_service_owner_payload,
    collect_service_owner_records,
    manager_service_key,
    parse_service_owner_record,
    plan_service_owner_history_prune,
    project_manager_service_record,
    reduce_service_ownership,
)
from .spec_store import resolve_named_spec_from_root
from .tasks import Consumer
from .tasks.base import (
    BaseTask,
    ControlRequest,
    QueueMessageContext,
    TaskControlPolicy,
    TaskWorkerResult,
)
from .tasks.multiqueue_watcher import QueueMode
from .taskspec import (
    ReservedPolicy,
    TaskSpec,
    apply_bundle_root_to_taskspec_payload,
    resolve_taskspec_payload,
)

logger = logging.getLogger(__name__)

DispatchOwnershipState = Literal["self", "other", "none", "unknown"]
ManagerLivenessState = Literal["live", "stale", "unknown"]


@dataclass
class ManagedChild:
    """Bookkeeping for spawned child processes."""

    process: BaseProcess
    ctrl_queue: str | None
    persistent: bool = False
    autostart_source: str | None = None
    ctrl_out_queue: str | None = None
    internal_role: str | None = None
    service_key: str | None = None
    launched_ns: int = 0
    last_liveness_probe_ns: int = 0


@dataclass(frozen=True, slots=True)
class DispatchOwnership:
    """Current runtime-owned view of manager dispatch eligibility.

    Spec: [MA-1.4], [MF-6]
    """

    state: DispatchOwnershipState
    leader_tid: str | None = None


@dataclass(frozen=True, slots=True)
class ManagerLeadershipProof:
    """Liveness and dispatch proof for one candidate manager row."""

    liveness: ManagerLivenessState
    dispatch_eligible: bool = False
    source: str = "none"
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class _ManagerChildLaunchRequest:
    """Broker-free child launch request prepared by the manager reactor."""

    child_spec: TaskSpec
    task_cls: type[Any]
    internal_role: str | None
    service_key: str | None
    autostart_source: str | None
    detach_stdio: bool
    source_queue: str | None = None
    reserved_queue: str | None = None
    message_timestamp: int | None = None


@dataclass(frozen=True, slots=True)
class _ManagerChildLaunchResult:
    """Result returned from the child-launch worker lane."""

    request: _ManagerChildLaunchRequest
    process: BaseProcess | None = None
    launched_ns: int = 0
    error: BaseException | None = None


@dataclass(frozen=True, slots=True)
class _ManagerPendingPongProbe:
    """Non-blocking manager-liveness PING awaiting a matching PONG."""

    tid: str
    row_timestamp: int | None
    ctrl_in_name: str
    ctrl_out_name: str
    request_id: str
    deadline_ns: int


@dataclass(frozen=True, slots=True)
class _ServicePendingPongProbe:
    """Non-blocking service-owner PING awaiting a matching PONG."""

    key: str
    service_key: str
    tid: str
    row_timestamp: int | None
    source: Literal["control-pong", "service-registry-pong"]
    ctrl_in_name: str
    ctrl_out_name: str
    request_id: str
    deadline_ns: int


class Manager(BaseTask):
    """Task that listens for spawn requests and runs child tasks.

    Spec: [MA-0], [MA-1], [MF-6], [MF-7]
    """

    control_policy = TaskControlPolicy(
        stop="drain-children",
        kill="immediate",
        reserved_policy="base",
        ack="immediate-draining",
        terminal_state="drain-completion",
    )

    def __init__(
        self,
        db: str | Any,
        taskspec: TaskSpec,
        *,
        stop_event: multiprocessing.synchronize.Event | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        thread_event = cast(threading.Event | None, stop_event)
        super().__init__(db, taskspec, stop_event=thread_event, config=config)
        self._child_processes: dict[str, ManagedChild] = {}
        self._active_child_launches: dict[str, _ManagerChildLaunchRequest] = {}
        self._child_launch_started_this_turn = False
        self._idle_timeout: float = float(
            taskspec.metadata.get(
                "idle_timeout",
                self._config.get(
                    "WEFT_MANAGER_LIFETIME_TIMEOUT",
                    WEFT_MANAGER_LIFETIME_TIMEOUT,
                ),
            )
        )
        self._last_activity_ns = time.time_ns()
        self._idle_shutdown_logged = False
        self._unregistered = False
        self._unregistered_status: (
            Literal["draining", "stopped", "superseded"] | None
        ) = None
        self._registry_message_id: int | None = None
        self._draining = False
        self._drain_reason: str | None = None
        self._drain_completion_event = "manager_stop_drained"
        self._drain_stops_children = True
        self._drain_leader_tid: str | None = None
        self._drain_signaled_children: set[str] = set()
        self._drain_signal_started_ns: dict[str, int] = {}
        self._drain_started_ns: int | None = None
        self._stalled_control_message_id: int | None = None
        self._stalled_control_retry_after_ns = 0
        self._stalled_control_last_log_ns = 0
        self._pending_termination_signal: int | None = None
        self._loop_iteration = 0
        self._serve_log_last_emit_ns: dict[str, int] = {}
        self._serve_log_last_state: dict[str, str] = {}
        self._serve_log_runtime_handle_id = self._resolve_serve_log_runtime_handle_id()
        self._autostart_enabled = bool(self._config.get("WEFT_AUTOSTART_TASKS", True))
        autostart_dir = self._config.get("WEFT_AUTOSTART_DIR")
        self._autostart_dir = Path(autostart_dir) if autostart_dir else None
        self._autostart_launched: set[str] = set()
        self._managed_service_state: dict[str, ManagedServiceState] = {}
        self._managed_service_duplicate_scan_pending: set[str] = set()
        self._managed_internal_spawn_enqueued = False
        self._last_managed_service_convergence_ns = 0
        self._autostart_state: dict[str, dict[str, Any]] = {}
        self._autostart_last_scan_ns = 0
        self._autostart_scan_interval_ns = 1_000_000_000
        self._task_monitor_enabled = bool(
            self._config.get("WEFT_TASK_MONITOR_ENABLED", True)
        )
        self._task_monitor_restart_backoff_ns = int(
            float(
                self._config.get(
                    "WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS",
                    WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS_DEFAULT,
                )
            )
            * 1_000_000_000
        )
        self._leader_check_interval_ns = int(
            MANAGER_LEADERSHIP_CHECK_INTERVAL_SECONDS * 1_000_000_000
        )
        self._last_leader_check_ns = 0
        self._manager_registry_snapshot: dict[str, dict[str, Any]] = {}
        self._manager_registry_last_seen_ts: int | None = None
        self._manager_registry_initialized = False
        self._leader_probe_cache: dict[
            str, tuple[int, int | None, ManagerLeadershipProof]
        ] = {}
        self._leader_probe_pending: dict[str, _ManagerPendingPongProbe] = {}
        self._service_probe_pending: dict[str, _ServicePendingPongProbe] = {}
        self._manager_superseded_observed_ts_by_tid: dict[str, int] = {}
        self._leader_probe_used_this_turn = False
        self._leader_actionable_work_cache: tuple[int, bool] | None = None
        self._leader_check_turn: int | None = None
        self._last_leadership_drain_revalidate_ns = 0
        self._last_public_spawn_drained_ns = 0
        self._last_public_dispatch_stall_log_ns = 0
        self._broker_probe_interval_ns = 1_000_000_000  # probe at most once per second
        self._last_broker_probe_ns = 0
        self._last_registry_heartbeat_ns = 0
        self._last_broker_timestamp = self._read_broker_timestamp(force=True)
        self._manager_service_key = manager_service_key(self._manager_context())
        self._register_manager()
        if self._maybe_yield_leadership(force=True):
            return
        self.taskspec.mark_started(pid=multiprocessing.current_process().pid)
        self._update_process_title("spawning")
        self._report_state_change(event="task_spawning")
        self.taskspec.mark_running(pid=multiprocessing.current_process().pid)
        self._update_process_title("running")
        self._report_state_change(event="task_started")
        self._emit_manager_loop_summary(force=True)
        self._reconcile_managed_services(force=True)
        self._managed_service_duplicate_scan_pending.clear()
        self._managed_internal_spawn_enqueued = False
        atexit.register(self._atexit_unregister)

    def _service_state(self, service_key: str) -> ManagedServiceState:
        """Return Manager-local mutable state for a supervised service key."""

        return self._managed_service_state.setdefault(
            service_key,
            ManagedServiceState(),
        )

    def _manager_log_enabled(self) -> bool:
        """Return whether foreground manager operational logging is enabled."""

        return serve_log_level(self._config) != "off"

    def _manager_log_allows(self, required_level: str) -> bool:
        """Return whether a foreground manager operational-log event is allowed."""

        return serve_log_allows(self._config, required_level)

    def _resolve_serve_log_runtime_handle_id(self) -> str | None:
        """Return a stable runtime handle id for operational-log indexing."""

        try:
            return self._manager_runtime_handle().id
        except (TypeError, ValueError, RuntimeError):
            return None

    def _serve_log_context_path(self) -> str | None:
        value = getattr(self.taskspec.spec, "weft_context", None)
        return str(value) if value is not None else None

    def _emit_serve_log(
        self,
        event: str,
        *,
        component: str,
        required_level: str,
        severity: str = "info",
        **fields: Any,
    ) -> None:
        """Emit one best-effort bounded JSONL operational-log event."""

        if not self._manager_log_allows(required_level):
            return
        try:
            record = build_serve_log_record(
                config=self._config,
                event=event,
                component=component,
                manager_tid=self.tid,
                manager_tid_short=self.tid_short,
                required_level=required_level,
                severity=severity,
                weft_context=self._serve_log_context_path(),
                runtime_handle_id=self._serve_log_runtime_handle_id,
                pid=os.getpid(),
                loop_iteration=self._loop_iteration,
                fields=fields,
            )
            emit_serve_log_record(record)
        except Exception:  # pragma: no cover - diagnostics must not affect runtime
            logger.debug("Failed to emit manager operational log", exc_info=True)

    def _emit_serve_log_rate_limited(
        self,
        event: str,
        *,
        component: str,
        required_level: str,
        severity: str = "info",
        key: str | None = None,
        state: Mapping[str, Any] | None = None,
        force: bool = False,
        log_fields: Mapping[str, Any] | None = None,
    ) -> None:
        """Emit an operational-log event on state change or interval expiry."""

        if not self._manager_log_allows(required_level):
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
        self._emit_serve_log(
            event,
            component=component,
            required_level=required_level,
            severity=severity,
            **fields,
        )

    def _queue_pending_for_log(self, queue_name: str | None) -> bool:
        if not queue_name:
            return False
        try:
            return self._queue(queue_name).has_pending()
        except (BrokerError, OSError, RuntimeError):
            return False

    def _child_summaries_for_log(self) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for tid, child in sorted(self._child_processes.items())[
            :MANAGER_SERVE_LOG_CHILD_LIMIT
        ]:
            summaries.append(
                {
                    "tid": tid,
                    "pid": child.process.pid,
                    "alive": child.process.is_alive(),
                    "persistent": child.persistent,
                    "internal_role": child.internal_role,
                    "service_key": child.service_key,
                }
            )
        if len(self._child_processes) > MANAGER_SERVE_LOG_CHILD_LIMIT:
            summaries.append(
                {
                    "truncated_count": len(self._child_processes)
                    - MANAGER_SERVE_LOG_CHILD_LIMIT
                }
            )
        return summaries

    def _service_state_for_log(self, state: ManagedServiceState) -> dict[str, Any]:
        return {
            "spawn_pending": state.spawn_pending,
            "active_tid": state.active_tid,
            "next_allowed_ns": state.next_allowed_ns,
            "launched_once": state.launched_once,
            "restarts": state.restarts,
            "uncertain_attempts": state.uncertain_attempts,
            "uncertain_since_ns": state.uncertain_since_ns,
            "last_uncertain_reason": state.last_uncertain_reason,
        }

    @staticmethod
    def _service_candidates_for_log(
        candidates: Sequence[ServiceCandidate],
    ) -> list[dict[str, Any]]:
        return [
            {
                "tid": candidate.tid,
                "state": candidate.state,
                "source": candidate.source,
                "reason": candidate.reason,
            }
            for candidate in candidates
        ]

    def _emit_manager_loop_summary(self, *, force: bool = False) -> None:
        if not self._manager_log_allows("info"):
            return
        fields = {
            "child_count": len(self._child_processes),
            "children": self._child_summaries_for_log(),
            "pending_public": self._queue_pending_for_log(
                self._queue_names.get("inbox")
            ),
            "pending_internal": self._queue_pending_for_log(
                self._queue_names.get("internal_inbox")
            ),
            "pending_control": self._queue_pending_for_log(
                self._queue_names.get("ctrl_in")
            ),
            "draining": self._draining,
            "should_stop": self.should_stop,
        }
        self._emit_serve_log_rate_limited(
            "manager_loop_summary",
            component="manager",
            required_level="info",
            key="manager_loop_summary",
            state=fields,
            force=force,
            log_fields=fields,
        )

    @property
    def _task_monitor_tid(self) -> str | None:
        return self._service_state(INTERNAL_SERVICE_KEY_TASK_MONITOR).active_tid

    @_task_monitor_tid.setter
    def _task_monitor_tid(self, value: str | None) -> None:
        self._service_state(INTERNAL_SERVICE_KEY_TASK_MONITOR).active_tid = value

    @property
    def _task_monitor_spawn_pending(self) -> bool:
        return self._service_state(INTERNAL_SERVICE_KEY_TASK_MONITOR).spawn_pending

    @_task_monitor_spawn_pending.setter
    def _task_monitor_spawn_pending(self, value: bool) -> None:
        self._service_state(INTERNAL_SERVICE_KEY_TASK_MONITOR).spawn_pending = value

    @property
    def _task_monitor_next_start_allowed_ns(self) -> int:
        return self._service_state(INTERNAL_SERVICE_KEY_TASK_MONITOR).next_allowed_ns

    @_task_monitor_next_start_allowed_ns.setter
    def _task_monitor_next_start_allowed_ns(self, value: int) -> None:
        self._service_state(INTERNAL_SERVICE_KEY_TASK_MONITOR).next_allowed_ns = value

    # ------------------------------------------------------------------
    # Queue configuration
    # ------------------------------------------------------------------
    def _resolve_queue_names(self) -> dict[str, str]:
        """Map manager queue roles, including canonical internal spawn queues."""

        queue_names = super()._resolve_queue_names()
        if queue_names["inbox"] == WEFT_SPAWN_REQUESTS_QUEUE:
            tid_prefix = f"T{self.taskspec.tid}"
            queue_names["internal_inbox"] = WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE
            queue_names["internal_reserved"] = (
                f"{tid_prefix}.{QUEUE_INTERNAL_RESERVED_SUFFIX}"
            )
        return queue_names

    def _internal_spawn_queue_attached(self) -> bool:
        return self._queue_names.get("internal_inbox") == (
            WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE
        )

    def _spawn_inbox_queue_names(self) -> tuple[str, ...]:
        queue_names = [self._queue_names["inbox"]]
        internal_inbox = self._queue_names.get("internal_inbox")
        if internal_inbox is not None:
            queue_names.insert(0, internal_inbox)
        return tuple(dict.fromkeys(queue_names))

    def _spawn_reserved_queue_pairs(self) -> tuple[tuple[str, str], ...]:
        pairs = [(self._queue_names["reserved"], self._queue_names["inbox"])]
        internal_reserved = self._queue_names.get("internal_reserved")
        internal_inbox = self._queue_names.get("internal_inbox")
        if internal_reserved is not None and internal_inbox is not None:
            pairs.insert(0, (internal_reserved, internal_inbox))
        return tuple(dict.fromkeys(pairs))

    def _idle_activity_queue_names(self) -> tuple[str, ...]:
        """Return manager-owned queues whose pending work should reset idle time."""

        queue_names = list(self._spawn_inbox_queue_names())
        queue_names.extend(
            reserved_queue
            for reserved_queue, _source in self._spawn_reserved_queue_pairs()
        )
        return tuple(dict.fromkeys(queue_names))

    def _manager_owned_work_pending(self) -> bool:
        """Return whether manager-owned inbox or reserved queues still have work."""

        if self._has_active_child_launches():
            return True
        for queue_name in self._idle_activity_queue_names():
            try:
                if self._queue(queue_name).has_pending():
                    return True
            except (BrokerError, OSError, RuntimeError):
                logger.debug(
                    "Failed to inspect manager-owned queue %s for pending work",
                    queue_name,
                    exc_info=True,
                )
        return False

    def _build_queue_configs(self) -> dict[str, dict[str, Any]]:
        """Configure inbox reserve mode and control peek mode for the manager.

        Spec: [CC-2.2], [CC-2.5], [MA-1]
        """
        configs: dict[str, dict[str, Any]] = {}
        internal_inbox = self._queue_names.get("internal_inbox")
        internal_reserved = self._queue_names.get("internal_reserved")
        if internal_inbox is not None and internal_reserved is not None:
            configs[internal_inbox] = self._reserve_queue_config(
                self._handle_work_message,
                reserved_queue=internal_reserved,
                priority=QUEUE_PRIORITY_INTERNAL,
            )

        configs.update(
            {
                self._queue_names["inbox"]: self._reserve_queue_config(
                    self._handle_work_message,
                    reserved_queue=self._queue_names["reserved"],
                    priority=QUEUE_PRIORITY_NORMAL,
                ),
                self._queue_names["ctrl_in"]: self._peek_queue_config(
                    self._handle_control_message
                ),
                self._queue_names["reserved"]: self._peek_queue_config(
                    self._handle_reserved_message
                ),
            }
        )
        if internal_reserved is not None:
            configs[internal_reserved] = self._peek_queue_config(
                self._handle_reserved_message
            )
        return configs

    def _build_tid_mapping_payload(self) -> dict[str, Any]:
        """Publish manager identity in tid mappings even without explicit metadata."""

        payload = super()._build_tid_mapping_payload()
        # Manager role is structural, not advisory metadata.
        payload["role"] = "manager"
        return payload

    def _control_snapshot_fields(self) -> dict[str, Any]:
        """Include manager-selection fields in STATUS/PING control snapshots."""

        payload = super()._control_snapshot_fields()
        payload.update(
            {
                "role": "manager",
                "inbox": self._queue_names["inbox"],
                "requests": self._queue_names["inbox"],
                "ctrl_in": self._queue_names["ctrl_in"],
                "ctrl_out": self._queue_names["ctrl_out"],
                "outbox": self._queue_names["outbox"],
            }
        )
        if self._internal_spawn_queue_attached():
            payload["internal_requests"] = self._queue_names["internal_inbox"]
            payload["internal_reserved"] = self._queue_names["internal_reserved"]
        weft_context = getattr(self.taskspec.spec, "weft_context", None)
        if weft_context is not None:
            payload["weft_context"] = str(weft_context)
        return payload

    def _has_active_child_launches(self) -> bool:
        """Return whether child launch work is waiting for main-thread commit."""

        return bool(self._active_child_launches)

    def _handle_control_command(
        self, request: ControlRequest, context: QueueMessageContext
    ) -> bool:
        """Override STOP to drain active children before manager exit."""

        if request.command == CONTROL_STOP:
            self._begin_graceful_shutdown(message_id=context.timestamp)
            self._send_control_response("STOP", "ack", draining=True)
            return True
        return super()._handle_control_command(request, context)

    def _launch_child_task(
        self,
        child_spec: TaskSpec,
        inbox_message: Any | None,
        *,
        autostart_source: str | None = None,
        service_key: str | None = None,
        source_queue: str | None = None,
        reserved_queue: str | None = None,
        message_timestamp: int | None = None,
    ) -> bool:
        """Seed child inbox and submit broker-free child launch work.

        Spec: [MF-1], [MF-6]
        """
        if self._draining or self.should_stop:
            logger.debug(
                "Skipping child launch for %s while manager %s is draining",
                child_spec.tid,
                self.tid,
            )
            self._emit_serve_log(
                "child_launch_result",
                component="spawn",
                required_level="debug",
                severity="warning",
                child_tid=child_spec.tid,
                child_name=child_spec.name,
                success=False,
                reason="manager_draining",
            )
            return False

        inbox_name = child_spec.io.inputs.get("inbox")
        if (
            inbox_message is not None
            and inbox_name
            and not self._seed_child_inbox(
                child_spec,
                inbox_name=inbox_name,
                inbox_message=inbox_message,
            )
        ):
            return False

        child_spec.metadata.setdefault("parent_tid", self.tid)
        if autostart_source:
            child_spec.metadata.setdefault(
                INTERNAL_AUTOSTART_SOURCE_METADATA_KEY, autostart_source
            )
            child_spec.metadata.setdefault(
                INTERNAL_AUTOSTART_ENABLED_METADATA_KEY, True
            )

        try:
            task_cls = self._resolve_child_task_class(child_spec)
        except ValueError as exc:
            logger.warning("Rejecting child launch for %s: %s", child_spec.tid, exc)
            self._report_state_change(
                event="task_spawn_rejected",
                child_tid=child_spec.tid,
                error=str(exc),
            )
            self._emit_serve_log(
                "child_launch_result",
                component="spawn",
                required_level="debug",
                severity="warning",
                child_tid=child_spec.tid,
                child_name=child_spec.name,
                success=False,
                reason="unknown_internal_runtime_class",
                error=str(exc),
            )
            return False

        internal_role = child_spec.metadata.get(INTERNAL_RUNTIME_TASK_CLASS_KEY)
        if not isinstance(internal_role, str):
            internal_role = None
        if service_key is None:
            service_key = service_key_from_metadata(child_spec.metadata)
        if service_key is None and autostart_source:
            service_key = autostart_source

        assert child_spec.tid is not None
        request = _ManagerChildLaunchRequest(
            child_spec=child_spec,
            task_cls=task_cls,
            internal_role=internal_role,
            service_key=service_key,
            autostart_source=autostart_source,
            detach_stdio=not (
                self._manager_log_enabled()
                and internal_role == INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR
            ),
            source_queue=source_queue,
            reserved_queue=reserved_queue,
            message_timestamp=message_timestamp,
        )
        self._active_child_launches[child_spec.tid] = request
        self._child_launch_started_this_turn = True
        self._submit_worker_call(
            MANAGER_CHILD_LAUNCH_WORKER_LANE,
            lambda: self._run_child_launch_worker(request),
        )
        return True

    def _run_child_launch_worker(
        self,
        request: _ManagerChildLaunchRequest,
    ) -> _ManagerChildLaunchResult:
        """Launch the child process without touching broker queues."""

        try:
            process = launch_task_process(
                request.task_cls,
                self._db_path,
                request.child_spec,
                config=self._config,
                detach_stdio=request.detach_stdio,
            )
        except BaseException as exc:
            return _ManagerChildLaunchResult(request=request, error=exc)
        return _ManagerChildLaunchResult(
            request=request,
            process=process,
            launched_ns=time.time_ns(),
        )

    def _commit_child_launch_success(
        self,
        result: _ManagerChildLaunchResult,
    ) -> None:
        """Record a launched child and publish all durable manager effects."""

        request = result.request
        child_spec = request.child_spec
        process = result.process
        if process is None:
            raise RuntimeError("child launch worker returned no process")
        assert child_spec.tid is not None
        self._child_processes[child_spec.tid] = ManagedChild(
            process=process,
            ctrl_queue=child_spec.io.control.get("ctrl_in"),
            ctrl_out_queue=child_spec.io.control.get("ctrl_out"),
            persistent=bool(getattr(child_spec.spec, "persistent", False)),
            autostart_source=request.autostart_source,
            internal_role=request.internal_role,
            service_key=request.service_key,
            launched_ns=result.launched_ns or time.time_ns(),
        )
        self._invalidate_leadership_work_cache()
        if request.service_key is not None:
            self._register_managed_service_owner(
                child_spec,
                service_key=request.service_key,
                process=process,
                status="active",
            )
            state = self._service_state(request.service_key)
            state.active_tid = child_spec.tid
            state.spawn_pending = False
            state.locally_terminal_tids.discard(child_spec.tid)
            self._managed_service_duplicate_scan_pending.add(request.service_key)
        self._last_activity_ns = time.time_ns()

        child_dump = redact_taskspec_dump(
            child_spec.model_dump(mode="json"), self._taskspec_redaction_paths
        )
        if isinstance(process.pid, int):
            state_dump = child_dump.setdefault("state", {})
            if isinstance(state_dump, dict):
                state_dump["pid"] = process.pid

        event_payload: dict[str, Any] = {
            "child_tid": child_spec.tid,
            "child_taskspec": child_dump,
        }
        if isinstance(process.pid, int):
            event_payload["child_pid"] = process.pid
        if request.autostart_source:
            event_payload["autostart_source"] = request.autostart_source
        if request.service_key:
            event_payload["service_key"] = request.service_key

        self._report_state_change(event="task_spawned", **event_payload)
        self._emit_serve_log(
            "child_launch_result",
            component="spawn",
            required_level="debug",
            child_tid=child_spec.tid,
            child_name=child_spec.name,
            child_pid=process.pid,
            internal_runtime_class=request.internal_role,
            service_key=request.service_key,
            autostart_source=request.autostart_source,
            success=True,
        )

    def _handle_child_launch_failure(
        self,
        result: _ManagerChildLaunchResult,
    ) -> None:
        """Apply reserved policy after broker-free child launch fails."""

        request = result.request
        child_spec = request.child_spec
        error = result.error or RuntimeError("child launch failed")
        logger.warning("Child launch failed for %s: %s", child_spec.tid, error)
        self._emit_serve_log(
            "child_launch_result",
            component="spawn",
            required_level="debug",
            severity="warning",
            child_tid=child_spec.tid,
            child_name=child_spec.name,
            internal_runtime_class=request.internal_role,
            service_key=request.service_key,
            autostart_source=request.autostart_source,
            success=False,
            reason="launch_worker_failed",
            error=str(error),
        )
        if (
            request.source_queue is None
            or request.reserved_queue is None
            or request.message_timestamp is None
        ):
            return
        policy = self.taskspec.spec.reserved_policy_on_error
        self._apply_spawn_reserved_policy(
            policy,
            source_queue=request.source_queue,
            reserved_queue=request.reserved_queue,
            message_timestamp=request.message_timestamp,
        )
        if policy is not ReservedPolicy.KEEP:
            self._ensure_reserved_queue_empty(request.reserved_queue)
            self._cleanup_reserved_queue_if_needed(request.reserved_queue)

    def _handle_child_launch_result(
        self,
        result: _ManagerChildLaunchResult,
    ) -> None:
        """Commit one child-launch worker result on the manager reactor."""

        child_tid = result.request.child_spec.tid
        if child_tid is not None:
            self._active_child_launches.pop(child_tid, None)
        self._child_launch_started_this_turn = True
        if result.error is not None:
            self._handle_child_launch_failure(result)
            return

        self._commit_child_launch_success(result)
        request = result.request
        if request.reserved_queue is None or request.message_timestamp is None:
            return
        try:
            self._queue(request.reserved_queue).delete(
                message_id=request.message_timestamp
            )
            self._emit_serve_log(
                "spawn_ack_result",
                component="spawn",
                required_level="trace",
                source_queue=request.source_queue,
                reserved_queue=request.reserved_queue,
                message_timestamp=request.message_timestamp,
                child_tid=child_tid,
                success=True,
            )
        except (BrokerError, OSError, RuntimeError):
            logger.debug(
                "Failed to acknowledge manager message %s",
                request.message_timestamp,
                exc_info=True,
            )
            self._emit_serve_log(
                "spawn_ack_result",
                component="spawn",
                required_level="debug",
                severity="warning",
                source_queue=request.source_queue,
                reserved_queue=request.reserved_queue,
                message_timestamp=request.message_timestamp,
                child_tid=child_tid,
                success=False,
            )

    def _handle_worker_result(self, result: TaskWorkerResult) -> None:
        if result.lane == MANAGER_CHILD_LAUNCH_WORKER_LANE:
            if result.error is not None:
                raise RuntimeError(
                    "Manager child launch worker failed"
                ) from result.error
            self._handle_child_launch_result(
                cast(_ManagerChildLaunchResult, result.value)
            )
            return
        super()._handle_worker_result(result)

    def _child_runtime_handle(self, process: BaseProcess) -> dict[str, Any]:
        """Return runtime evidence for a locally launched child process."""

        pid = process.pid
        if not isinstance(pid, int) or pid <= 0:
            return {}
        return RunnerHandle(
            runner="host",
            kind="process",
            id=str(pid),
            control={"authority": "host-pid"},
            observations={
                "host_pids": [pid],
                "host_processes": [
                    {"pid": pid, "create_time": process_create_time(pid)}
                ],
            },
            metadata={"manager_tid": self.tid},
        ).to_dict()

    def _register_managed_service_owner(
        self,
        child_spec: TaskSpec,
        *,
        service_key: str,
        process: BaseProcess,
        status: Literal["active", "terminal"],
    ) -> None:
        """Publish manager-owned singleton service ownership."""

        if child_spec.tid is None:
            return
        queues = {
            "ctrl_in": child_spec.io.control.get("ctrl_in"),
            "ctrl_out": child_spec.io.control.get("ctrl_out"),
            "inbox": child_spec.io.inputs.get("inbox"),
            "outbox": child_spec.io.outputs.get("outbox"),
        }
        metadata = {
            "manager_tid": self.tid,
            "internal_role": child_spec.metadata.get(INTERNAL_RUNTIME_TASK_CLASS_KEY),
            "autostart_source": child_spec.metadata.get(
                INTERNAL_AUTOSTART_SOURCE_METADATA_KEY
            ),
        }
        payload = build_service_owner_payload(
            service_key=service_key,
            service_type=SERVICE_TYPE_MANAGED,
            owner_tid=child_spec.tid,
            status=status,
            name=child_spec.name,
            queues=queues,
            runtime_handle=self._child_runtime_handle(process),
            metadata={key: value for key, value in metadata.items() if value},
        )
        try:
            self._queue(WEFT_SERVICES_REGISTRY_QUEUE).write(json.dumps(payload))
        except (BrokerError, OSError, RuntimeError):
            logger.debug(
                "Failed to publish managed service owner %s", service_key, exc_info=True
            )

    def _register_terminal_managed_service_owner(
        self,
        tid: str,
        child: ManagedChild,
        *,
        service_key: str,
    ) -> None:
        """Publish terminal ownership for a reaped manager-owned service."""

        queues = {
            "ctrl_in": child.ctrl_queue,
            "ctrl_out": child.ctrl_out_queue,
        }
        metadata = {
            "manager_tid": self.tid,
            "internal_role": child.internal_role,
            "autostart_source": child.autostart_source,
        }
        payload = build_service_owner_payload(
            service_key=service_key,
            service_type=SERVICE_TYPE_MANAGED,
            owner_tid=tid,
            status="terminal",
            name="managed-service",
            queues=queues,
            runtime_handle=self._child_runtime_handle(child.process),
            metadata={key: value for key, value in metadata.items() if value},
        )
        try:
            self._queue(WEFT_SERVICES_REGISTRY_QUEUE).write(json.dumps(payload))
        except (BrokerError, OSError, RuntimeError):
            logger.debug(
                "Failed to publish terminal managed service owner %s",
                service_key,
                exc_info=True,
            )

    def _seed_child_inbox(
        self,
        child_spec: TaskSpec,
        *,
        inbox_name: str,
        inbox_message: Any,
    ) -> bool:
        """Durably write the first child work item before process launch."""

        payload = (
            json.dumps(inbox_message)
            if not isinstance(inbox_message, str)
            else inbox_message
        )
        last_error: BaseException | None = None
        for attempt in range(3):
            try:
                self._queue(inbox_name).write(payload)
                return True
            except (BrokerError, OSError, RuntimeError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.05 * (attempt + 1))

        error = (
            f"Failed to seed inbox {inbox_name} for child {child_spec.tid}: "
            f"{last_error}"
        )
        logger.warning(error, exc_info=last_error)
        self._report_state_change(
            event="task_spawn_rejected",
            child_tid=child_spec.tid,
            error=error,
        )
        self._emit_serve_log(
            "child_launch_result",
            component="spawn",
            required_level="debug",
            severity="error",
            child_tid=child_spec.tid,
            child_name=child_spec.name,
            success=False,
            reason="seed_child_inbox_failed",
            error=error,
        )
        return False

    @staticmethod
    def _resolve_child_task_class(child_spec: TaskSpec) -> type[BaseTask]:
        runtime_class = child_spec.metadata.get(INTERNAL_RUNTIME_TASK_CLASS_KEY)
        if runtime_class is None:
            return Consumer
        if runtime_class == INTERNAL_RUNTIME_TASK_CLASS_PIPELINE:
            from .tasks.pipeline import PipelineTask

            return PipelineTask
        if runtime_class == INTERNAL_RUNTIME_TASK_CLASS_PIPELINE_EDGE:
            from .tasks.pipeline import PipelineEdgeTask

            return PipelineEdgeTask
        if runtime_class == INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT:
            from .tasks.heartbeat import HeartbeatTask

            return HeartbeatTask
        if runtime_class == INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR:
            from .monitor.task_monitor import TaskMonitorTask

            return TaskMonitorTask
        raise ValueError(f"unknown internal runtime task class '{runtime_class}'")

    # ------------------------------------------------------------------
    # Manager bookkeeping
    # ------------------------------------------------------------------
    @staticmethod
    def _child_is_supervision_only(child: ManagedChild) -> bool:
        """Return whether a child is internal supervision rather than user work."""

        return child.internal_role in {
            INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT,
            INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR,
        }

    def _user_work_children(self) -> dict[str, ManagedChild]:
        """Return children that should block idle shutdown and leadership yield."""

        return {
            tid: child
            for tid, child in self._child_processes.items()
            if not self._child_is_supervision_only(child)
        }

    def _register_manager(self) -> None:
        """Publish active manager service ownership (Spec: [MA-1.4], [MF-7])."""
        registry_queue = self._queue(WEFT_SERVICES_REGISTRY_QUEUE)
        now_ns = time.time_ns()
        self._prune_expired_manager_registry_entries(registry_queue, now_ns=now_ns)
        if (
            self._latest_self_registry_status(registry_queue)
            == SERVICE_STATUS_SUPERSEDED
        ):
            self._begin_superseded_shutdown()
            self._last_registry_heartbeat_ns = time.time_ns()
            return
        if self._recent_lower_canonical_manager_exists(registry_queue, now_ns=now_ns):
            if not self._unregistered:
                self._unregister_manager(status=SERVICE_STATUS_DRAINING)
            else:
                self._prune_older_self_registry_entries(registry_queue)
            self._last_registry_heartbeat_ns = time.time_ns()
            return

        runtime_handle = self._manager_runtime_handle()
        if (
            runtime_handle.control.get("authority") == "external-supervisor"
            and runtime_handle.id.startswith("docker:")
            and runtime_handle.metadata.get("foreground_serve") is True
        ):
            self._emit_serve_log_rate_limited(
                "manager_runtime_identity_warning",
                component="registry",
                required_level="info",
                severity="warning",
                key="manager_runtime_identity_warning",
                state={
                    "runtime_handle_id": runtime_handle.id,
                    "authority": runtime_handle.control.get("authority"),
                },
                log_fields={
                    "reason": "docker_external_supervisor_identity_in_foreground_serve",
                    "runtime_handle_id": runtime_handle.id,
                    "authority": runtime_handle.control.get("authority"),
                },
            )
        previous_message_id = self._registry_message_id
        queues = {
            "requests": self._queue_names["inbox"],
            "reserved": self._queue_names.get("reserved"),
            "ctrl_in": self._queue_names["ctrl_in"],
            "ctrl_out": self._queue_names["ctrl_out"],
            "outbox": self._queue_names["outbox"],
        }
        if self._internal_spawn_queue_attached():
            queues["internal_requests"] = self._queue_names["internal_inbox"]
            queues["internal_reserved"] = self._queue_names["internal_reserved"]
        payload = build_manager_service_payload(
            context=self._manager_context(),
            tid=self.tid,
            name=self.taskspec.name,
            status="active",
            queues=queues,
            runtime_handle=runtime_handle.to_dict(),
            capabilities=self.taskspec.metadata.get("capabilities", []),
            metadata={
                "legacy_role": self.taskspec.metadata.get("role", "manager"),
            },
        )
        serialized_payload = json.dumps(payload)
        try:
            message_id = cast(int | None, registry_queue.write(serialized_payload))
        except (BrokerError, OSError, RuntimeError):
            logger.debug("Failed to register manager", exc_info=True)
        else:
            if message_id is None:
                latest = self._latest_registry_entry(registry_queue, self.tid)
                self._registry_message_id = latest[1] if latest else None
            else:
                self._registry_message_id = message_id
            self._last_registry_heartbeat_ns = time.time_ns()
            superseded_timestamp = self._self_registry_status_timestamp(
                registry_queue,
                status=SERVICE_STATUS_SUPERSEDED,
                since_timestamp=now_ns,
            )
            if superseded_timestamp is not None:
                deleted_active = False
                if self._registry_message_id is not None:
                    try:
                        deleted_active = bool(
                            registry_queue.delete(message_id=self._registry_message_id)
                        )
                    except (BrokerError, OSError, RuntimeError):
                        logger.debug(
                            "Failed to prune active registry entry after supersede",
                            exc_info=True,
                        )
                if (
                    not deleted_active
                    and self._latest_self_registry_status(registry_queue)
                    != SERVICE_STATUS_SUPERSEDED
                ):
                    inactive_payload = dict(payload)
                    inactive_payload["status"] = SERVICE_STATUS_SUPERSEDED
                    try:
                        registry_queue.write(json.dumps(inactive_payload))
                    except (BrokerError, OSError, RuntimeError):
                        logger.debug(
                            "Failed to restore superseded registry entry",
                            exc_info=True,
                        )
                self._registry_message_id = None
                self._begin_superseded_shutdown()
                return
            self._prune_older_self_registry_entries(registry_queue)
            projected = project_manager_service_record(
                payload,
                timestamp=self._registry_message_id or time.time_ns(),
                service_key=self._manager_service_key,
            )
            if projected is not None:
                projected["_timestamp"] = self._registry_message_id or time.time_ns()
                self._manager_registry_snapshot[self.tid] = projected
            if (
                previous_message_id is not None
                and self._registry_message_id is not None
                and previous_message_id != self._registry_message_id
            ):
                try:
                    registry_queue.delete(message_id=previous_message_id)
                except (BrokerError, OSError, RuntimeError):
                    logger.debug(
                        "Failed to prune previous manager registry heartbeat",
                        exc_info=True,
                    )

    @staticmethod
    def _manager_registry_retention_ns() -> int:
        return int(MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS * 1_000_000_000)

    def _registry_entry_is_expired(self, timestamp: int, *, now_ns: int) -> bool:
        retention_ns = self._manager_registry_retention_ns()
        if retention_ns < 0:
            return True
        return now_ns - timestamp > retention_ns

    def _prune_expired_manager_registry_entries(
        self, queue: Queue, *, now_ns: int | None = None
    ) -> None:
        """Delete canonical manager service-owner rows outside the runtime window."""

        observed_now_ns = time.time_ns() if now_ns is None else now_ns
        service_key = self._manager_service_key
        expired_timestamps: list[int] = []
        try:
            entries = queue.peek_generator(with_timestamps=True)
        except (BrokerError, OSError, RuntimeError):
            logger.debug("Failed to inspect manager registry for expiry", exc_info=True)
            return

        try:
            for entry in entries:
                if not isinstance(entry, tuple) or len(entry) != 2:
                    continue
                body, timestamp = entry
                if not isinstance(timestamp, int):
                    continue
                try:
                    payload = json.loads(body)
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                if (
                    payload.get("schema") == SERVICE_OWNER_SCHEMA
                    and parse_service_owner_record(payload, timestamp=timestamp) is None
                ):
                    expired_timestamps.append(timestamp)
                    continue
                projected = project_manager_service_record(
                    payload,
                    timestamp=timestamp,
                    service_key=service_key,
                )
                if projected is None:
                    continue
                if isinstance(timestamp, int) and self._registry_entry_is_expired(
                    timestamp,
                    now_ns=observed_now_ns,
                ):
                    expired_timestamps.append(timestamp)
        except (BrokerError, OSError, RuntimeError):
            logger.debug("Failed to replay manager registry for expiry", exc_info=True)
            return

        for timestamp in expired_timestamps:
            try:
                queue.delete(message_id=timestamp)
            except (BrokerError, OSError, RuntimeError):
                logger.debug(
                    "Failed to prune expired manager registry entry",
                    exc_info=True,
                )

    def _recent_lower_canonical_manager_exists(
        self, queue: Queue, *, now_ns: int | None = None
    ) -> bool:
        """Return whether a lower-TID canonical manager should suppress publish."""
        del queue, now_ns
        active = self._active_dispatch_manager_records()
        if active is None:
            return False
        try:
            own_tid = int(self.tid)
        except ValueError:
            return False
        for tid in active:
            try:
                if int(tid) < own_tid:
                    return True
            except ValueError:
                continue
        return False

    def _latest_self_registry_status(self, queue: Queue) -> str | None:
        """Return this manager's latest service-owner status, if readable."""

        latest_timestamp = -1
        latest_status: str | None = None
        service_key = self._manager_service_key
        try:
            entries = queue.peek_generator(with_timestamps=True)
        except (AttributeError, BrokerError, OSError, RuntimeError):
            logger.debug("Failed to inspect self manager status", exc_info=True)
            return None

        try:
            for entry in entries:
                if not isinstance(entry, tuple) or len(entry) != 2:
                    continue
                body, timestamp = entry
                if not isinstance(timestamp, int):
                    continue
                try:
                    payload = json.loads(body)
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                record = parse_service_owner_record(payload, timestamp=timestamp)
                if (
                    record is None
                    or record.owner_tid != self.tid
                    or record.service_type != SERVICE_TYPE_MANAGER
                    or record.service_key != service_key
                ):
                    continue
                if timestamp > latest_timestamp:
                    latest_timestamp = timestamp
                    latest_status = record.status
        except (BrokerError, OSError, RuntimeError):
            logger.debug("Failed to replay self manager status", exc_info=True)
            return None
        return latest_status

    def _self_registry_status_timestamp(
        self,
        queue: Queue,
        *,
        status: str,
        since_timestamp: int | None = None,
    ) -> int | None:
        """Return latest self-owned row timestamp for a status, if readable."""

        latest_timestamp: int | None = None
        service_key = self._manager_service_key
        try:
            entries = queue.peek_generator(with_timestamps=True)
        except (AttributeError, BrokerError, OSError, RuntimeError):
            logger.debug("Failed to inspect self manager status", exc_info=True)
            return None

        try:
            for entry in entries:
                if not isinstance(entry, tuple) or len(entry) != 2:
                    continue
                body, timestamp = entry
                if not isinstance(timestamp, int):
                    continue
                if since_timestamp is not None and timestamp < since_timestamp:
                    continue
                try:
                    payload = json.loads(body)
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                record = parse_service_owner_record(payload, timestamp=timestamp)
                if (
                    record is None
                    or record.owner_tid != self.tid
                    or record.service_type != SERVICE_TYPE_MANAGER
                    or record.service_key != service_key
                    or record.status != status
                ):
                    continue
                if latest_timestamp is None or timestamp > latest_timestamp:
                    latest_timestamp = timestamp
        except (BrokerError, OSError, RuntimeError):
            logger.debug("Failed to replay self manager status", exc_info=True)
            return None
        return latest_timestamp

    def _prune_older_self_registry_entries(self, queue: Queue) -> None:
        """Keep only this manager's latest registry row."""

        latest_ts: int | None = None
        self_timestamps: list[int] = []
        try:
            entries = queue.peek_generator(with_timestamps=True)
        except (AttributeError, BrokerError, OSError, RuntimeError):
            logger.debug("Failed to inspect self manager registry rows", exc_info=True)
            return

        try:
            for entry in entries:
                if not isinstance(entry, tuple) or len(entry) != 2:
                    continue
                body, timestamp = entry
                if not isinstance(timestamp, int):
                    continue
                try:
                    payload = json.loads(body)
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                record = parse_service_owner_record(payload, timestamp=timestamp)
                if (
                    record is None
                    or record.owner_tid != self.tid
                    or record.service_type != SERVICE_TYPE_MANAGER
                    or record.service_key != self._manager_service_key
                ):
                    continue
                self_timestamps.append(timestamp)
                if latest_ts is None or timestamp > latest_ts:
                    latest_ts = timestamp
        except (BrokerError, OSError, RuntimeError):
            logger.debug("Failed to replay self manager registry rows", exc_info=True)
            return

        for timestamp in self_timestamps:
            if timestamp == latest_ts:
                continue
            try:
                queue.delete(message_id=timestamp)
            except (BrokerError, OSError, RuntimeError):
                logger.debug(
                    "Failed to prune older self manager registry entry",
                    exc_info=True,
                )

    def _refresh_manager_registration(self, *, force: bool = False) -> None:
        if self._unregistered or self.should_stop:
            return
        now_ns = time.time_ns()
        interval_ns = int(MANAGER_REGISTRY_HEARTBEAT_INTERVAL_SECONDS * 1_000_000_000)
        if not force and now_ns - self._last_registry_heartbeat_ns < interval_ns:
            return
        self._register_manager()

    def _unregister_manager(
        self,
        *,
        status: Literal["draining", "stopped"] = "stopped",
    ) -> None:
        """Replace active service-owner record with non-active state (Spec: [MA-1.4])."""
        if self._unregistered and not (
            self._unregistered_status == SERVICE_STATUS_DRAINING
            and status == SERVICE_STATUS_STOPPED
        ):
            return
        registry_queue = self._queue(WEFT_SERVICES_REGISTRY_QUEUE)

        deletion_performed = False
        if self._registry_message_id is not None:
            try:
                registry_queue.delete(message_id=self._registry_message_id)
                deletion_performed = True
            except (BrokerError, OSError, RuntimeError):
                logger.debug("Failed to prune active registry entry", exc_info=True)
            finally:
                self._registry_message_id = None

        if not deletion_performed:
            latest = self._latest_registry_entry(registry_queue, self.tid)
            if latest is not None:
                payload, latest_ts = latest
                if payload.get("status") in {"active", SERVICE_STATUS_DRAINING}:
                    try:
                        registry_queue.delete(message_id=latest_ts)
                    except (BrokerError, OSError, RuntimeError):
                        logger.debug(
                            "Failed to prune latest registry entry for %s",
                            self.tid,
                            exc_info=True,
                        )

        queues = {
            "requests": self._queue_names["inbox"],
            "reserved": self._queue_names.get("reserved"),
            "ctrl_in": self._queue_names["ctrl_in"],
            "ctrl_out": self._queue_names["ctrl_out"],
            "outbox": self._queue_names["outbox"],
        }
        if self._internal_spawn_queue_attached():
            queues["internal_requests"] = self._queue_names["internal_inbox"]
            queues["internal_reserved"] = self._queue_names["internal_reserved"]
        payload = build_manager_service_payload(
            context=self._manager_context(),
            tid=self.tid,
            name=self.taskspec.name,
            status=status,
            queues=queues,
            runtime_handle=self._manager_runtime_handle().to_dict(),
            capabilities=self.taskspec.metadata.get("capabilities", []),
            metadata={
                "legacy_role": self.taskspec.metadata.get("role", "manager"),
            },
        )
        latest_after_prune = self._latest_registry_entry(registry_queue, self.tid)
        if latest_after_prune is not None:
            payload_existing, _ = latest_after_prune
            if self._registry_entries_match(payload_existing, payload):
                self._unregistered = True
                return

        serialized_payload = json.dumps(payload)
        try:
            registry_queue.write(serialized_payload)
        except (BrokerError, OSError, RuntimeError):
            logger.debug("Failed to record stopped manager state", exc_info=True)

        self._prune_older_self_registry_entries(registry_queue)
        self._registry_message_id = None
        self._unregistered = True
        self._unregistered_status = status
        latest = self._latest_registry_entry(registry_queue, self.tid)
        if latest is not None:
            latest_payload, latest_ts = latest
            projected = project_manager_service_record(
                latest_payload,
                timestamp=latest_ts,
                service_key=self._manager_service_key,
            )
            if projected is not None:
                projected["_timestamp"] = latest_ts
                self._manager_registry_snapshot[self.tid] = projected

    def _manager_runtime_handle(self) -> RunnerHandle:
        config = getattr(self, "_config", {})
        raw_handle = config.get(WEFT_MANAGER_RUNTIME_HANDLE_JSON_ENV)
        if isinstance(raw_handle, str) and raw_handle.strip():
            try:
                payload = json.loads(raw_handle)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{WEFT_MANAGER_RUNTIME_HANDLE_JSON_ENV} must be JSON"
                ) from exc
            if not isinstance(payload, Mapping):
                raise ValueError(
                    f"{WEFT_MANAGER_RUNTIME_HANDLE_JSON_ENV} must be a JSON object"
                )
            return RunnerHandle.from_dict(payload)

        pid = multiprocessing.current_process().pid
        if pid is None:
            raise RuntimeError("Manager process has no PID")
        container = detect_container_runtime()
        if container is not None:
            return RunnerHandle(
                runner="manager-supervisor",
                kind="supervised-process",
                id=f"{container.runtime}:{container.identifier or 'unknown'}",
                control={"authority": "external-supervisor"},
                observations=container.observations(container_pid=pid),
                metadata={
                    "foreground_serve": True,
                }
                if self.taskspec.metadata.get("foreground_serve") is True
                else {},
            )
        return RunnerHandle(
            runner="host",
            kind="process",
            id=str(pid),
            control={"authority": "host-pid"},
            observations={
                "host_pids": [pid],
                "host_processes": [
                    {"pid": pid, "create_time": process_create_time(pid)}
                ],
            },
            metadata={
                "foreground_serve": True,
            }
            if self.taskspec.metadata.get("foreground_serve") is True
            else {},
        )

    def _latest_registry_entry(
        self, queue: Queue, tid: str
    ) -> tuple[dict[str, Any], int] | None:
        latest: tuple[dict[str, Any], int] | None = None
        try:
            generator = queue.peek_generator(with_timestamps=True)
        except (BrokerError, OSError, RuntimeError):
            return None

        for entry in generator:
            if not isinstance(entry, tuple) or len(entry) != 2:
                continue
            body, timestamp = entry
            if not isinstance(timestamp, int):
                continue
            try:
                payload = json.loads(body)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            projected = project_manager_service_record(
                payload,
                timestamp=timestamp,
                service_key=self._manager_service_key,
            )
            if projected is not None and projected.get("tid") == tid:
                latest = (payload, timestamp)
        return latest

    @staticmethod
    def _registry_entries_match(
        existing: Mapping[str, Any], candidate: Mapping[str, Any]
    ) -> bool:
        keys = {
            "owner_tid",
            "service_key",
            "service_type",
            "status",
            "runtime_handle",
            "name",
            "capabilities",
            "role",
            "queues",
        }
        for key in keys:
            if existing.get(key) != candidate.get(key):
                return False
        return True

    @staticmethod
    def _pid_alive(pid: int | None) -> bool:
        return pid_is_live(pid)

    @staticmethod
    def _manager_record_liveness(
        record: Mapping[str, Any],
    ) -> Literal["live", "stale", "unknown"]:
        payload = record.get("runtime_handle")
        if not isinstance(payload, Mapping):
            return "stale"
        try:
            handle = RunnerHandle.from_dict(payload)
        except ValueError:
            return "stale"
        if handle.control.get("authority") == "external-supervisor":
            runtime_liveness = runtime_liveness_from_registered_probe(handle)
            if runtime_liveness == "live":
                return "live"
            if runtime_liveness == "stale":
                return "stale"
            timestamp = record.get("_timestamp")
            if not isinstance(timestamp, int):
                return "unknown"
            stale_after_ns = int(
                MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS * 1_000_000_000
            )
            if stale_after_ns < 0 or time.time_ns() - timestamp > stale_after_ns:
                return "stale"
            return "unknown"
        if handle.control.get("authority") == "host-pid":
            if handle_has_live_host_process(handle):
                return "live"
            if detect_container_runtime() is not None:
                return "unknown"
            return "stale"
        return "unknown"

    @staticmethod
    def _manager_record_is_live(record: Mapping[str, Any]) -> bool:
        liveness = Manager._manager_record_liveness(record)
        if liveness == "live":
            return True
        return False

    @staticmethod
    def _manager_ctrl_queue_name(tid: str, record: Mapping[str, Any]) -> str:
        value = record.get("ctrl_in")
        return (
            value
            if isinstance(value, str) and value
            else f"T{tid}.{QUEUE_CTRL_IN_SUFFIX}"
        )

    @staticmethod
    def _manager_ctrl_out_queue_name(tid: str, record: Mapping[str, Any]) -> str:
        value = record.get("ctrl_out")
        return (
            value
            if isinstance(value, str) and value
            else f"T{tid}.{QUEUE_CTRL_OUT_SUFFIX}"
        )

    def _pong_dispatch_eligible(
        self,
        payload: Mapping[str, Any],
        *,
        record: Mapping[str, Any],
        ctrl_in_name: str,
        ctrl_out_name: str,
    ) -> bool:
        task_status = payload.get("task_status")
        if task_status in {
            SERVICE_STATUS_DRAINING,
            "stopping",
            "cancelled",
            "completed",
            "failed",
            "timeout",
            "killed",
        }:
            return False
        if payload.get("should_stop") is True:
            return False
        role = payload.get("role")
        if role is not None and role != "manager":
            return False
        requests = payload.get("requests")
        if requests is not None and requests != WEFT_SPAWN_REQUESTS_QUEUE:
            return False
        ctrl_in = payload.get("ctrl_in")
        if ctrl_in is not None and ctrl_in != ctrl_in_name:
            return False
        ctrl_out = payload.get("ctrl_out")
        if ctrl_out is not None and ctrl_out != ctrl_out_name:
            return False
        weft_context = payload.get("weft_context")
        expected_context = str(self._manager_context().root)
        record_context = record.get("weft_context")
        if isinstance(record_context, str) and record_context:
            expected_context = record_context
        return weft_context is None or weft_context == expected_context

    def _manager_pong_dispatch_proof(
        self,
        record: Mapping[str, Any],
        *,
        now_ns: int,
    ) -> ManagerLeadershipProof:
        tid = record.get("tid")
        timestamp = record.get("_timestamp", record.get("timestamp"))
        if not isinstance(tid, str) or not tid:
            return ManagerLeadershipProof("unknown", reason="missing_tid")
        row_timestamp = timestamp if isinstance(timestamp, int) else None
        cache_key = tid
        cached = self._leader_probe_cache.get(cache_key)
        cache_ttl_ns = int(MANAGER_LEADERSHIP_PING_CACHE_TTL_SECONDS * 1_000_000_000)
        if cached is not None:
            cached_at_ns, cached_row_ts, proof = cached
            if cached_row_ts == row_timestamp and now_ns - cached_at_ns < cache_ttl_ns:
                return proof
        pending = self._leader_probe_pending.get(cache_key)
        if pending is not None:
            if pending.row_timestamp == row_timestamp:
                return self._advance_manager_pong_probe(
                    pending,
                    record=record,
                    now_ns=now_ns,
                )
            self._leader_probe_pending.pop(cache_key, None)
        if self._leader_probe_used_this_turn:
            return ManagerLeadershipProof(
                "unknown",
                source="control-pong",
                reason="leadership_ping_budget_exhausted",
            )
        self._leader_probe_used_this_turn = True
        ctrl_in_name = self._manager_ctrl_queue_name(tid, record)
        ctrl_out_name = self._manager_ctrl_out_queue_name(tid, record)
        request_id = uuid.uuid4().hex
        try:
            ctrl_in = self._manager_context().queue(ctrl_in_name, persistent=True)
            try:
                ctrl_in.write(
                    json.dumps({"command": CONTROL_PING, "request_id": request_id})
                )
            finally:
                ctrl_in.close()
        except (BrokerError, OSError, RuntimeError) as exc:
            proof = ManagerLeadershipProof(
                "unknown",
                source="control-pong",
                reason=str(exc),
            )
            self._leader_probe_cache[cache_key] = (now_ns, row_timestamp, proof)
            return proof

        timeout_ns = int(MANAGER_LEADERSHIP_PING_TIMEOUT_SECONDS * 1_000_000_000)
        self._leader_probe_pending[cache_key] = _ManagerPendingPongProbe(
            tid=tid,
            row_timestamp=row_timestamp,
            ctrl_in_name=ctrl_in_name,
            ctrl_out_name=ctrl_out_name,
            request_id=request_id,
            deadline_ns=now_ns + max(0, timeout_ns),
        )
        return ManagerLeadershipProof(
            "unknown",
            source="control-pong",
            reason="ping_pending",
        )

    def _advance_manager_pong_probe(
        self,
        probe: _ManagerPendingPongProbe,
        *,
        record: Mapping[str, Any],
        now_ns: int,
    ) -> ManagerLeadershipProof:
        """Advance one non-blocking manager PING probe without sleeping."""

        try:
            ctrl_out = self._manager_context().queue(
                probe.ctrl_out_name,
                persistent=False,
            )
            try:
                for item in ctrl_out.peek_generator(with_timestamps=True):
                    if not isinstance(item, tuple) or len(item) != 2:
                        continue
                    body, _timestamp = item
                    payload = coerce_pong_response(
                        str(body),
                        tid=probe.tid,
                        request_id=probe.request_id,
                    )
                    if payload is None:
                        continue
                    proof = self._manager_pong_payload_proof(
                        payload,
                        record=record,
                        ctrl_in_name=probe.ctrl_in_name,
                        ctrl_out_name=probe.ctrl_out_name,
                    )
                    self._complete_manager_pong_probe(probe, proof, now_ns=now_ns)
                    return proof
            finally:
                ctrl_out.close()
        except (BrokerError, OSError, RuntimeError) as exc:
            proof = ManagerLeadershipProof(
                "unknown",
                source="control-pong",
                reason=str(exc),
            )
            self._complete_manager_pong_probe(probe, proof, now_ns=now_ns)
            return proof

        if now_ns >= probe.deadline_ns:
            proof = ManagerLeadershipProof(
                "unknown",
                source="control-pong",
                reason="ping_timeout",
            )
            self._complete_manager_pong_probe(probe, proof, now_ns=now_ns)
            return proof

        return ManagerLeadershipProof(
            "unknown",
            source="control-pong",
            reason="ping_pending",
        )

    def _manager_pong_payload_proof(
        self,
        payload: Mapping[str, Any],
        *,
        record: Mapping[str, Any],
        ctrl_in_name: str,
        ctrl_out_name: str,
    ) -> ManagerLeadershipProof:
        if self._pong_dispatch_eligible(
            payload,
            record=record,
            ctrl_in_name=ctrl_in_name,
            ctrl_out_name=ctrl_out_name,
        ):
            return ManagerLeadershipProof(
                "live",
                dispatch_eligible=True,
                source="control-pong",
            )
        return ManagerLeadershipProof(
            "live",
            dispatch_eligible=False,
            source="control-pong",
            reason="pong_not_dispatch_eligible",
        )

    def _complete_manager_pong_probe(
        self,
        probe: _ManagerPendingPongProbe,
        proof: ManagerLeadershipProof,
        *,
        now_ns: int,
    ) -> None:
        self._leader_probe_pending.pop(probe.tid, None)
        self._leader_probe_cache[probe.tid] = (
            now_ns,
            probe.row_timestamp,
            proof,
        )

    def _manager_leadership_proof(
        self,
        record: Mapping[str, Any],
        *,
        now_ns: int,
        allow_ping: bool,
    ) -> ManagerLeadershipProof:
        if record.get("status") != "active":
            return ManagerLeadershipProof("stale", reason="not_active")
        if not is_canonical_manager_record(record):
            return ManagerLeadershipProof("stale", reason="not_canonical")
        liveness = self._manager_record_liveness(record)
        if liveness == "live":
            return ManagerLeadershipProof(
                "live",
                dispatch_eligible=True,
                source="runtime-handle",
            )
        if liveness == "stale":
            if allow_ping:
                proof = self._manager_pong_dispatch_proof(record, now_ns=now_ns)
                if proof.liveness == "unknown" and proof.reason == "ping_pending":
                    return proof
                if proof.liveness == "unknown":
                    return ManagerLeadershipProof(
                        "stale",
                        source=proof.source,
                        reason=proof.reason,
                    )
                return proof
            return ManagerLeadershipProof("stale", source="runtime-handle")
        if allow_ping:
            return self._manager_pong_dispatch_proof(record, now_ns=now_ns)
        return ManagerLeadershipProof("unknown", source="runtime-handle")

    def _update_manager_registry_snapshot(
        self,
        *,
        force_full: bool = False,
    ) -> bool:
        """Refresh this manager's scoped registry view without broad status reads."""

        queue = self._queue(WEFT_SERVICES_REGISTRY_QUEUE)
        service_key = self._manager_service_key
        previous_last_seen_ts = self._manager_registry_last_seen_ts
        since_timestamp = (
            None
            if force_full or not self._manager_registry_initialized
            else previous_last_seen_ts
        )
        fresh_higher_active: list[tuple[dict[str, Any], int]] = []
        try:
            entries = iter_queue_json_entries(queue, since_timestamp=since_timestamp)
            saw_entry = False
            for payload, timestamp in entries:
                saw_entry = True
                if (
                    self._manager_registry_last_seen_ts is None
                    or timestamp > self._manager_registry_last_seen_ts
                ):
                    self._manager_registry_last_seen_ts = timestamp
                projected = project_manager_service_record(
                    payload,
                    timestamp=timestamp,
                    service_key=service_key,
                )
                if projected is None:
                    continue
                tid = projected.get("tid")
                if not isinstance(tid, str) or not tid:
                    continue
                projected["_timestamp"] = timestamp
                existing = self._manager_registry_snapshot.get(tid)
                existing_ts = (
                    int(existing.get("_timestamp", -1))
                    if isinstance(existing, dict)
                    else -1
                )
                if timestamp >= existing_ts:
                    self._manager_registry_snapshot[tid] = projected
                if (
                    since_timestamp is not None
                    and previous_last_seen_ts is not None
                    and timestamp > previous_last_seen_ts
                    and self._manager_record_should_be_superseded(
                        projected,
                        observed_timestamp=timestamp,
                    )
                ):
                    fresh_higher_active.append((projected, timestamp))
            self._manager_registry_initialized = True
            if force_full and not saw_entry:
                self._manager_registry_snapshot.clear()
            for record, timestamp in fresh_higher_active:
                self._publish_superseded_manager_record(
                    queue,
                    record=record,
                    observed_timestamp=timestamp,
                )
            return True
        except (BrokerError, OSError, RuntimeError):
            logger.debug("Failed to refresh manager registry view", exc_info=True)
            return False

    def _manager_record_should_be_superseded(
        self,
        record: Mapping[str, Any],
        *,
        observed_timestamp: int,
    ) -> bool:
        """Return whether a fresh higher active row is supersession proof."""

        if self.should_stop or self._draining or self._unregistered:
            return False
        if record.get("status") != SERVICE_STATUS_ACTIVE:
            return False
        if not is_canonical_manager_record(record):
            return False
        tid = record.get("tid")
        if not isinstance(tid, str) or not tid:
            return False
        try:
            is_higher_tid = int(tid) > int(self.tid)
        except ValueError:
            return False
        if not is_higher_tid:
            return False
        last_superseded = self._manager_superseded_observed_ts_by_tid.get(tid)
        return last_superseded is None or observed_timestamp > last_superseded

    @staticmethod
    def _manager_record_queues(record: Mapping[str, Any]) -> dict[str, str]:
        raw_queues = record.get("queues")
        queues = {
            str(key): value
            for key, value in (
                raw_queues.items() if isinstance(raw_queues, Mapping) else ()
            )
            if isinstance(value, str) and value
        }
        for key in (
            "requests",
            "reserved",
            "ctrl_in",
            "ctrl_out",
            "outbox",
            "internal_requests",
            "internal_reserved",
        ):
            value = record.get(key)
            if isinstance(value, str) and value:
                queues.setdefault(key, value)
        inbox = record.get("inbox")
        if "requests" not in queues and isinstance(inbox, str) and inbox:
            queues["requests"] = inbox
        return queues

    def _publish_superseded_manager_record(
        self,
        queue: Queue,
        *,
        record: Mapping[str, Any],
        observed_timestamp: int,
    ) -> None:
        """Publish a superseded row for a fresh higher-TID active manager row."""

        tid = record.get("tid")
        if not isinstance(tid, str) or not tid:
            return
        raw_runtime_handle = record.get("runtime_handle")
        runtime_handle = (
            dict(raw_runtime_handle) if isinstance(raw_runtime_handle, Mapping) else {}
        )
        raw_metadata = record.get("metadata")
        metadata = dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}
        metadata.update(
            {
                "superseded_by": self.tid,
                "supersession_reason": "higher_tid_active_refresh_seen",
                "supersession_observed_timestamp": observed_timestamp,
                "supersession_proof": "fresh_active_registry_row",
            }
        )
        raw_capabilities = record.get("capabilities")
        capabilities = (
            list(raw_capabilities)
            if isinstance(raw_capabilities, Sequence)
            and not isinstance(raw_capabilities, (str, bytes))
            else []
        )
        payload = build_manager_service_payload(
            context=self._manager_context(),
            tid=tid,
            name=str(record.get("name") or "manager"),
            status=SERVICE_STATUS_SUPERSEDED,
            queues=self._manager_record_queues(record),
            runtime_handle=runtime_handle,
            capabilities=capabilities,
            metadata=metadata,
        )
        try:
            message_id = cast(int | None, queue.write(json.dumps(payload)))
        except (BrokerError, OSError, RuntimeError):
            logger.debug("Failed to publish superseded manager record", exc_info=True)
            return

        projected_timestamp = message_id if message_id is not None else time.time_ns()
        projected = project_manager_service_record(
            payload,
            timestamp=projected_timestamp,
            service_key=self._manager_service_key,
        )
        if projected is not None:
            projected["_timestamp"] = projected_timestamp
            self._manager_registry_snapshot[tid] = projected
        self._manager_superseded_observed_ts_by_tid[tid] = observed_timestamp
        self._emit_serve_log(
            "manager_superseded_published",
            component="registry",
            required_level="debug",
            superseded_tid=tid,
            superseded_by=self.tid,
            observed_timestamp=observed_timestamp,
            superseded_message_id=message_id,
        )

    def _self_superseded_manager_record_visible(self, *, now_ns: int) -> bool:
        """Return whether the services registry has superseded this manager."""

        record = self._manager_registry_snapshot.get(self.tid)
        if record is None or record.get("status") != SERVICE_STATUS_SUPERSEDED:
            return False
        timestamp = record.get("_timestamp")
        if isinstance(timestamp, int) and self._registry_entry_is_expired(
            timestamp,
            now_ns=now_ns,
        ):
            return False
        return True

    def _active_dispatch_manager_records(self) -> dict[str, dict[str, Any]] | None:
        now_ns = time.time_ns()
        if not self._update_manager_registry_snapshot():
            return None
        if self._self_superseded_manager_record_visible(now_ns=now_ns):
            self._begin_superseded_shutdown()
            return {}

        active: dict[str, dict[str, Any]] = {}
        stale_timestamps: list[int] = []
        unknown_seen = False
        for tid, record in list(self._manager_registry_snapshot.items()):
            timestamp = record.get("_timestamp")
            if isinstance(timestamp, int) and self._registry_entry_is_expired(
                timestamp,
                now_ns=now_ns,
            ):
                stale_timestamps.append(timestamp)
                self._manager_registry_snapshot.pop(tid, None)
                continue
            if record.get("status") != SERVICE_STATUS_ACTIVE:
                continue
            if tid == self.tid and not self._unregistered and not self.should_stop:
                if record.get("status") == "active" and is_canonical_manager_record(
                    record
                ):
                    active[tid] = record
                continue
            proof = self._manager_leadership_proof(
                record,
                now_ns=now_ns,
                allow_ping=True,
            )
            if proof.liveness == "stale":
                if isinstance(timestamp, int):
                    stale_timestamps.append(timestamp)
                self._manager_registry_snapshot.pop(tid, None)
                continue
            if proof.liveness == "unknown":
                unknown_seen = True
                continue
            if proof.dispatch_eligible:
                active[tid] = record

        queue = self._queue(WEFT_SERVICES_REGISTRY_QUEUE)
        for timestamp in stale_timestamps:
            try:
                queue.delete(message_id=timestamp)
            except (BrokerError, OSError, RuntimeError):
                logger.debug("Failed to prune stale manager record", exc_info=True)

        active_tids = sorted(active)
        leader_tid = canonical_owner_tid(active)
        fields = {
            "active_manager_tids": active_tids,
            "active_manager_count": len(active_tids),
            "leader_tid": leader_tid,
            "stale_pruned_count": len(stale_timestamps),
        }
        self._emit_serve_log_rate_limited(
            "manager_registry_snapshot",
            component="registry",
            required_level="debug" if active_tids else "info",
            severity="info" if active_tids else "warning",
            key="manager_registry_snapshot",
            state=fields,
            log_fields=fields,
        )
        if unknown_seen and not active:
            return None
        return active

    def _read_active_manager_records(self) -> dict[str, dict[str, Any]] | None:
        """Return the live canonical manager snapshot or ``None`` on read failure.

        Spec: [MA-1.4], [MA-3]
        """
        return self._active_dispatch_manager_records()

    def _active_manager_records(self) -> dict[str, dict[str, Any]]:
        active = self._read_active_manager_records()
        if active is None:
            return {}
        return active

    def _leader_tid(self) -> str | None:
        active = self._active_manager_records()
        return canonical_owner_tid(active)

    def _evaluate_dispatch_ownership(self) -> DispatchOwnership:
        """Return the current manager-owned dispatch eligibility state.

        Spec: [MA-1.4], [MF-6]
        """

        if self._queue_names["inbox"] != WEFT_SPAWN_REQUESTS_QUEUE:
            ownership = DispatchOwnership(state="self", leader_tid=self.tid)
            self._emit_manager_ownership_decision(ownership)
            return ownership

        active = self._read_active_manager_records()
        if active is None:
            ownership = DispatchOwnership(state="unknown")
            self._emit_manager_ownership_decision(ownership)
            return ownership

        leader_tid = canonical_owner_tid(active)
        if leader_tid is None:
            ownership = DispatchOwnership(state="none")
            self._emit_manager_ownership_decision(ownership)
            return ownership
        if leader_tid == self.tid and self.tid in active:
            ownership = DispatchOwnership(state="self", leader_tid=leader_tid)
            self._emit_manager_ownership_decision(ownership)
            return ownership
        ownership = DispatchOwnership(state="other", leader_tid=leader_tid)
        self._emit_manager_ownership_decision(ownership)
        return ownership

    def _emit_manager_ownership_decision(
        self, ownership: DispatchOwnership, *, reason: str | None = None
    ) -> None:
        fields = {
            "ownership_state": ownership.state,
            "leader_tid": ownership.leader_tid,
            "reason": reason,
        }
        self._emit_serve_log_rate_limited(
            "manager_ownership_decision",
            component="ownership",
            required_level="debug",
            key="manager_ownership_decision",
            state=fields,
            log_fields=fields,
        )

    def _has_actionable_leadership_work(self) -> bool:
        """Return whether this manager owns work that must run before yield.

        Registry leadership is advisory for public dispatch. A non-leader may
        launch public spawn work that it has already reserved, because broker
        reservation owns exclusivity for that exact message. Pending public
        backlog is not owned work and must not by itself block duplicate-manager
        convergence.

        Spec: [MA-1.4], [MF-6]
        """

        if any(child.persistent for child in self._user_work_children().values()):
            return True
        if self._has_active_child_launches():
            return True
        if self._managed_internal_spawn_enqueued:
            return True
        if self._internal_spawn_pending():
            self._mark_pending_messages_prechecked()
            return True
        reserved_names = {
            reserved_queue
            for reserved_queue, _source_queue in self._spawn_reserved_queue_pairs()
        }
        ctrl_name = self._queue_names.get("ctrl_in")
        for name, config in self._queues.items():
            if name == ctrl_name:
                continue
            if name not in reserved_names:
                continue
            if self._queue_has_pending(config.queue):
                self._mark_pending_messages_prechecked()
                return True
        return self._manager_control_pending_is_actionable()

    def _invalidate_leadership_work_cache(self) -> None:
        """Discard per-turn leadership-work proof after local progress."""

        self._leader_actionable_work_cache = None
        self._leader_check_turn = None

    def _has_actionable_leadership_work_for_turn(self) -> bool:
        """Return cached actionable-work proof for this manager turn."""

        turn = self._loop_iteration
        cached = self._leader_actionable_work_cache
        if cached is not None and cached[0] == turn:
            return cached[1]
        result = self._has_actionable_leadership_work()
        self._leader_actionable_work_cache = (turn, result)
        return result

    def _maybe_yield_leadership(self, *, force: bool = False) -> bool:
        """Check whether this manager should yield leadership and act on it.

        Leadership algorithm
        --------------------
        Multiple manager processes can coexist when a new ``weft serve`` or
        ``weft run`` starts while a previous manager is still alive.  The
        system resolves the conflict by electing the manager with the
        *lowest* TID as the leader.  TIDs are hybrid timestamps (microseconds
        + counter), so the lowest TID is always the earliest-started manager.
        This lets the system prefer stability: the long-running incumbent wins
        rather than the newcomer.

        This method is called periodically by the manager's main loop (rate-
        limited by ``_leader_check_interval_ns`` unless ``force=True``).  On
        each check it reads the live manager registry, finds the current
        leader TID, and compares it to its own TID.

        Yielding conditions and behaviour
        ----------------------------------
        * **Not leader** (another live manager has a lower TID):
          - If this manager has **persistent** child tasks it cannot yield:
            persistent tasks are long-lived workers (e.g. agents) that must
            not be abandoned mid-run.  The manager stays active and continues
            checking until the persistent children finish.
          - If all remaining children are **non-persistent** (or there are no
            children), leadership drain begins:
            ``_begin_leadership_drain`` unregisters this manager from the
            active registry so new submissions are routed to the leader, but
            keeps the process alive until the in-flight non-persistent
            children complete naturally.
          - If there are **no children at all**, the yield is immediate:
            the manager marks itself cancelled, reports the event, unregisters,
            and sets ``should_stop``.
        * **Is leader** (own TID is lowest, or registry is empty):
          Returns ``False`` — nothing to do.

        Returns
        -------
        bool
            ``True`` if leadership is being yielded (caller should treat this
            as a signal to wind down), ``False`` if this manager is the leader
            or the check interval has not elapsed.
        """
        now_ns = time.time_ns()
        if (
            not force
            and now_ns - self._last_leader_check_ns < self._leader_check_interval_ns
        ):
            return self.should_stop
        if not force and self._leader_check_turn == self._loop_iteration:
            return self.should_stop
        self._last_leader_check_ns = now_ns
        self._leader_check_turn = self._loop_iteration

        if self._has_active_child_launches():
            return False

        active = self._read_active_manager_records()
        if active is None:
            self._emit_manager_ownership_decision(
                DispatchOwnership(state="unknown"),
                reason="leadership_yield_check",
            )
            return False
        if self._draining or self.should_stop:
            return True
        leader_tid = canonical_owner_tid(active)
        ownership_state: DispatchOwnershipState
        if leader_tid is None:
            ownership_state = "none"
        elif leader_tid == self.tid:
            ownership_state = "self"
        else:
            ownership_state = "other"
        self._emit_manager_ownership_decision(
            DispatchOwnership(state=ownership_state, leader_tid=leader_tid),
            reason="leadership_yield_check",
        )
        if leader_tid is None or leader_tid == self.tid:
            return False

        actionable_work = (
            self._has_actionable_leadership_work()
            if force
            else self._has_actionable_leadership_work_for_turn()
        )
        if actionable_work:
            return False

        if self._user_work_children():
            user_children = self._user_work_children()
            if any(child.persistent for child in user_children.values()):
                return False
            self._begin_leadership_drain(leader_tid=leader_tid)
            return True

        if not self.should_stop:
            self.taskspec.mark_cancelled(
                reason=f"Superseded by lower-TID manager {leader_tid}"
            )
            self._report_state_change(
                event="manager_leadership_yielded",
                leader_tid=leader_tid,
            )
            self._unregister_manager()
            self.should_stop = True
        return True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _child_has_exited(self, child: ManagedChild) -> bool:
        """Return True when the child process is no longer live.

        The multiprocessing Process view can lag behind the OS process table on
        some platforms. When that happens, relying on ``is_alive()`` alone keeps
        exited children in ``_child_processes`` indefinitely and blocks manager
        shutdown. Treat a missing OS PID as authoritative and reap best-effort.
        """

        exitcode = child.process.exitcode
        if exitcode is not None:
            return True

        pid = child.process.pid
        process_live = False
        try:
            process_live = child.process.is_alive()
        except (AssertionError, OSError, ValueError):  # pragma: no cover - defensive
            pass

        now_ns = time.time_ns()
        if process_live:
            if pid is None:
                return False
            recheck_interval_ns = int(
                MANAGER_PID_LIVENESS_RECHECK_INTERVAL * 1_000_000_000
            )
            newest_probe_ns = max(child.launched_ns, child.last_liveness_probe_ns)
            if (
                child.launched_ns > 0
                and newest_probe_ns > 0
                and now_ns - newest_probe_ns < recheck_interval_ns
            ):
                return False
            child.last_liveness_probe_ns = now_ns

        if pid is not None:
            if self._pid_alive(pid):
                return False
            if child.launched_ns > 0 and now_ns - child.launched_ns < int(
                MANAGER_CHILD_STARTUP_LIVENESS_GRACE_SECONDS * 1_000_000_000
            ):
                return False
            try:
                child.process.join(timeout=0.0)
            except (
                AssertionError,
                OSError,
                ValueError,
            ):  # pragma: no cover - defensive
                pass
            return True

        try:
            return not child.process.is_alive()
        except (AssertionError, OSError, ValueError):  # pragma: no cover - defensive
            return True

    def _child_terminal_proof_visible(self, tid: str, child: ManagedChild) -> bool:
        ctrl_out_name = child.ctrl_out_queue or f"T{tid}.{QUEUE_CTRL_OUT_SUFFIX}"
        ctrl_out = Queue(
            ctrl_out_name,
            db_path=self._db_path,
            persistent=False,
            config=self._config,
        )
        try:
            for entry in ctrl_out.peek_generator(with_timestamps=True):
                body = entry[0] if isinstance(entry, tuple) else entry
                try:
                    payload = json.loads(str(body))
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict):
                    continue
                if (
                    payload.get("type") == TERMINAL_ENVELOPE_TYPE
                    and payload.get("tid") == tid
                    and payload.get("source") == "task"
                ):
                    return True
        except (BrokerError, OSError, RuntimeError):
            logger.debug(
                "Failed to inspect child ctrl_out for terminal proof",
                exc_info=True,
            )
            return True
        finally:
            try:
                ctrl_out.close()
            except (BrokerError, OSError, RuntimeError):
                logger.debug(
                    "Failed to close child ctrl_out proof queue %s",
                    ctrl_out_name,
                    exc_info=True,
                )

        log_queue = self._queue(WEFT_GLOBAL_LOG_QUEUE)
        try:
            since_timestamp = int(tid) - 1 if tid.isdigit() else None
            for payload, _timestamp in iter_queue_json_entries(
                log_queue,
                since_timestamp=since_timestamp,
            ):
                if payload.get("tid") != tid:
                    continue
                status = payload.get("status")
                if isinstance(status, str) and status in TERMINAL_TASK_STATUSES:
                    return True
        except (BrokerError, OSError, RuntimeError):
            logger.debug(
                "Failed to inspect task log for child terminal proof",
                exc_info=True,
            )
            return True
        return False

    def _write_manager_terminal_envelope(
        self,
        tid: str,
        child: ManagedChild,
    ) -> None:
        if self._child_terminal_proof_visible(tid, child):
            return
        ctrl_out_name = child.ctrl_out_queue or f"T{tid}.{QUEUE_CTRL_OUT_SUFFIX}"
        exitcode = child.process.exitcode
        payload: dict[str, Any] = {
            "type": TERMINAL_ENVELOPE_TYPE,
            "source": "manager",
            "tid": tid,
            "status": "failed",
            "error": WRAPPER_LOST_ERROR,
            "timestamp": time.time_ns(),
        }
        if exitcode is not None:
            payload["return_code"] = int(exitcode)
        ctrl_out = Queue(
            ctrl_out_name,
            db_path=self._db_path,
            persistent=False,
            config=self._config,
        )
        try:
            ctrl_out.write(json.dumps(payload))
        except (BrokerError, OSError, RuntimeError):
            logger.debug(
                "Failed to write manager terminal envelope for child %s",
                tid,
                exc_info=True,
            )
        finally:
            try:
                ctrl_out.close()
            except (BrokerError, OSError, RuntimeError):
                logger.debug(
                    "Failed to close manager terminal envelope queue %s",
                    ctrl_out_name,
                    exc_info=True,
                )

    def _cleanup_children(self) -> bool:
        autostart_child_exited = False
        child_exited = False
        for tid, child in list(self._child_processes.items()):
            if self._child_has_exited(child):
                child_exited = True
                self._write_manager_terminal_envelope(tid, child)
                try:
                    # Dead children must never stall the manager control loop.
                    # Treat dead-child cleanup as a local reap only; descendant
                    # teardown belongs to explicit termination paths, not the
                    # hot loop that must stay responsive to new control
                    # messages.
                    child.process.join(timeout=0.1)
                except (
                    AssertionError,
                    OSError,
                    ValueError,
                ):  # pragma: no cover - defensive
                    pass
                finally:
                    self._child_processes.pop(tid, None)
                if child.autostart_source:
                    autostart_child_exited = True
                service_key = self._service_key_for_child(child)
                if service_key:
                    self._register_terminal_managed_service_owner(
                        tid,
                        child,
                        service_key=service_key,
                    )
                    state = self._service_state(service_key)
                    state.locally_terminal_tids.add(tid)
                    self._managed_service_duplicate_scan_pending.add(service_key)
                    if state.active_tid == tid:
                        state.active_tid = None
                    state.spawn_pending = False
                    if service_key == INTERNAL_SERVICE_KEY_TASK_MONITOR:
                        state.next_allowed_ns = (
                            time.time_ns() + self._task_monitor_restart_backoff_ns
                        )

        if autostart_child_exited:
            # Re-evaluate ensure-style autostart manifests immediately after an
            # autostart child exits instead of waiting for the next scan interval.
            self._autostart_last_scan_ns = 0
        if child_exited:
            # Child completion is activity. The manager should only begin its idle
            # countdown after in-flight work has actually finished.
            self._last_activity_ns = time.time_ns()
            self._invalidate_leadership_work_cache()
        return child_exited

    def _terminate_children(self) -> None:
        children: dict[str, ManagedChild] = dict(self._child_processes)
        managed_pids: dict[str, set[int]] = {
            tid: self._managed_pids_for_child(tid) for tid in children
        }

        self._wait_for_children_to_exit(timeout=1.0)
        children.update(self._child_processes)
        for tid in self._child_processes:
            managed_pids.setdefault(tid, set()).update(
                self._managed_pids_for_child(tid)
            )

        if not children:
            return

        for tid, child in list(self._child_processes.items()):
            ctrl_queue = child.ctrl_queue or f"T{tid}.{QUEUE_CTRL_IN_SUFFIX}"
            self._send_stop_command(ctrl_queue)
        self._wait_for_children_to_exit(timeout=1.0)
        children.update(self._child_processes)
        for tid in children:
            managed_pids.setdefault(tid, set()).update(
                self._managed_pids_for_child(tid)
            )

        for tid, child in list(children.items()):
            try:
                if child.process.is_alive():
                    try:
                        child.process.join(timeout=0.2)
                    except (
                        AssertionError,
                        OSError,
                        ValueError,
                    ):  # pragma: no cover - defensive
                        pass
                if child.process.is_alive():
                    if child.process.pid is not None:
                        terminate_process_tree(child.process.pid, timeout=0.5)
                    try:
                        child.process.join(timeout=2.0)
                    except (
                        AssertionError,
                        OSError,
                        ValueError,
                    ):  # pragma: no cover - defensive
                        pass
                    if child.process.is_alive():
                        try:
                            child.process.kill()
                        except OSError:  # pragma: no cover - defensive
                            pass
                        else:
                            child.process.join(timeout=1.0)
                managed_pids.setdefault(tid, set()).update(
                    self._managed_pids_for_child(tid)
                )
                for pid in managed_pids[tid]:
                    terminate_process_tree(pid, timeout=0.2)
            finally:
                self._child_processes.pop(tid, None)

    def _wait_for_children_to_exit(self, *, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while self._child_processes and time.monotonic() < deadline:
            self._cleanup_children()
            if not self._child_processes:
                return
            time.sleep(MANAGER_CHILD_EXIT_POLL_INTERVAL)

    def _signal_children_to_stop(self) -> None:
        """Best-effort STOP broadcast to currently tracked child tasks."""

        for tid, child in list(self._child_processes.items()):
            if tid in self._drain_signaled_children:
                started_ns = self._drain_signal_started_ns.get(tid, time.time_ns())
                if time.time_ns() - started_ns > 2_000_000_000:
                    pid = child.process.pid
                    if isinstance(pid, int) and pid > 0:
                        terminate_process_tree(pid, timeout=0.2)
                continue
            ctrl_queue = child.ctrl_queue or f"T{tid}.{QUEUE_CTRL_IN_SUFFIX}"
            self._send_stop_command(ctrl_queue)
            self._drain_signaled_children.add(tid)
            self._drain_signal_started_ns[tid] = time.time_ns()

    def _begin_graceful_shutdown(self, *, message_id: int | None) -> None:
        self._begin_shutdown_drain(
            message_id=message_id,
            reason="STOP command received",
            event="control_stop",
            completion_event="manager_stop_drained",
        )

    def _begin_shutdown_drain(
        self,
        *,
        message_id: int | None,
        reason: str,
        event: str | None,
        completion_event: str,
    ) -> None:
        if self._draining:
            return

        self._draining = True
        self._drain_signaled_children.clear()
        self._drain_signal_started_ns.clear()
        self._drain_started_ns = time.time_ns()
        self._drain_reason = reason
        self._drain_completion_event = completion_event
        self._drain_stops_children = True
        self._drain_leader_tid = None
        if event is not None:
            self._report_state_change(
                event=event,
                message_id=message_id,
                draining=True,
            )
        self._update_process_title("stopping")

        policy = self.taskspec.spec.reserved_policy_on_stop
        self._apply_reserved_policy(policy)
        if policy is not ReservedPolicy.KEEP:
            self._ensure_reserved_empty()
            self._cleanup_reserved_if_needed()

        self._signal_children_to_stop()

    def _begin_leadership_drain(self, *, leader_tid: str) -> None:
        """Stop accepting new work and drain owned children before yielding.

        Leadership yield must not publish a terminal manager state while this
        manager still owns live child tasks. Once a lower-TID manager exists,
        this manager unregisters from the active registry so new submissions go
        to the leader, but it stays alive long enough to let already-spawned
        non-persistent children finish.
        """

        if self._draining:
            return

        self._draining = True
        self._drain_signaled_children.clear()
        self._drain_signal_started_ns.clear()
        self._drain_started_ns = time.time_ns()
        self._drain_reason = f"Superseded by lower-TID manager {leader_tid}"
        self._drain_completion_event = "manager_leadership_drained"
        self._drain_stops_children = False
        self._drain_leader_tid = leader_tid
        self._last_leadership_drain_revalidate_ns = 0
        self._report_state_change(
            event="manager_leadership_yielded",
            leader_tid=leader_tid,
            draining=True,
        )
        self._update_process_title("draining")
        self._unregister_manager(status="draining")

    def _begin_superseded_shutdown(self) -> None:
        """Stop this manager after observing its own superseded owner row."""

        if self._unregistered or self.should_stop:
            return
        self._unregistered = True
        self._unregistered_status = "superseded"
        if self._user_work_children():
            self._begin_shutdown_drain(
                message_id=None,
                reason="Superseded by replacement manager",
                event="manager_superseded",
                completion_event="manager_superseded_drained",
            )
            return
        if self.taskspec.state.status not in {"completed", "cancelled"}:
            self.taskspec.mark_cancelled(reason="Superseded by replacement manager")
            self._report_state_change(event="manager_superseded")
            self._update_process_title("cancelled")
        self.should_stop = True

    def _finish_graceful_shutdown(self) -> None:
        if self.taskspec.state.status not in {"completed", "cancelled"}:
            self.taskspec.mark_cancelled(reason=self._drain_reason)
            self._report_state_change(event=self._drain_completion_event)
            self._update_process_title("cancelled")
        self._drain_started_ns = None
        self._drain_stops_children = True
        self.should_stop = True

    def handle_termination_signal(self, signum: int) -> None:
        """Record an external signal for processing on the manager loop.

        Python signal handlers may run while the manager is inside a broker
        operation. Starting a drain here would perform queue writes from inside
        that interrupted stack frame and can re-enter backend drivers such as
        psycopg. Keep this handler to plain in-memory state; ``process_once``
        owns the broker-visible shutdown work.
        """

        sigusr1 = getattr(signal, "SIGUSR1", None)
        if sigusr1 is not None and signum == sigusr1:
            self._pending_termination_signal = signum
            self._external_stop_handled = True
            return

        if self._external_stop_handled:
            return
        self._pending_termination_signal = signum
        self._external_stop_handled = True

    def _process_pending_termination_signal(self) -> None:
        """Apply a signal recorded by ``handle_termination_signal``."""

        signum = self._pending_termination_signal
        if signum is None:
            return
        self._pending_termination_signal = None

        sigusr1 = getattr(signal, "SIGUSR1", None)
        if sigusr1 is not None and signum == sigusr1:
            self._terminate_children()
            self._external_stop_handled = False
            super().handle_termination_signal(signum)
            return

        if self._draining or self.should_stop:
            return

        try:
            signal_name = signal.Signals(signum).name
        except ValueError:  # pragma: no cover - defensive
            signal_name = f"signal {signum}"

        self._begin_shutdown_drain(
            message_id=None,
            reason=f"{signal_name} received",
            event=None,
            completion_event="task_signal_stop",
        )

    def _send_child_control_command(self, queue_name: str, command: str) -> None:
        queue = Queue(
            queue_name,
            db_path=self._db_path,
            persistent=True,
            config=self._config,
        )
        try:
            queue.write(command)
        except (BrokerError, OSError, RuntimeError):
            logger.debug(
                "Failed to send %s to %s",
                command,
                queue_name,
                exc_info=True,
            )
        finally:
            try:
                queue.close()
            except (BrokerError, OSError, RuntimeError):
                logger.debug(
                    "Failed to close %s queue %s",
                    command,
                    queue_name,
                    exc_info=True,
                )

    def _send_stop_command(self, queue_name: str) -> None:
        self._send_child_control_command(queue_name, CONTROL_STOP)

    @staticmethod
    def _peeked_message_timestamp(pending: object) -> int | None:
        if isinstance(pending, tuple) and len(pending) == 2:
            _body, timestamp = pending
            return timestamp if isinstance(timestamp, int) else None
        return None

    def _clear_stalled_control_message(self) -> None:
        self._stalled_control_message_id = None
        self._stalled_control_retry_after_ns = 0

    def _stalled_control_retry_active(self, timestamp: int) -> bool:
        return (
            self._stalled_control_message_id == timestamp
            and time.time_ns() < self._stalled_control_retry_after_ns
        )

    def _mark_stalled_control_message(self, *, queue_name: str, timestamp: int) -> None:
        now_ns = time.time_ns()
        retry_ns = int(MANAGER_STALLED_CONTROL_RETRY_SECONDS * 1_000_000_000)
        log_interval_ns = int(
            MANAGER_STALLED_CONTROL_LOG_INTERVAL_SECONDS * 1_000_000_000
        )
        should_log = (
            self._stalled_control_message_id != timestamp
            or now_ns - self._stalled_control_last_log_ns >= log_interval_ns
        )
        self._stalled_control_message_id = timestamp
        self._stalled_control_retry_after_ns = now_ns + retry_ns
        if should_log:
            logger.warning(
                "Manager control message %s on %s did not advance after handling; "
                "yielding this manager turn",
                timestamp,
                queue_name,
            )
            self._stalled_control_last_log_ns = now_ns

    def _manager_control_pending_is_actionable(self) -> bool:
        ctrl_name = self._queue_names.get("ctrl_in")
        if not ctrl_name:
            return False
        pending = self._queue(ctrl_name).peek_one(with_timestamps=True)
        if pending is None:
            self._clear_stalled_control_message()
            return False
        timestamp = self._peeked_message_timestamp(pending)
        if timestamp is None:
            return True
        if self._stalled_control_retry_active(timestamp):
            return False
        return True

    def _queue_has_pending(self, queue: Queue) -> bool:
        ctrl_name = self._queue_names.get("ctrl_in")
        if ctrl_name and getattr(queue, "name", None) == ctrl_name:
            return self._manager_control_pending_is_actionable()
        return super()._queue_has_pending(queue)

    def _has_pending_messages(self) -> bool:
        """Return pending work, excluding temporarily stalled manager control.

        This is a scheduler precheck as well as a predicate. When it proves
        non-control work exists, the next queue drain must probe all queues so
        pending public work cannot wait behind the periodic active-queue scan.
        """

        ctrl_name = self._queue_names.get("ctrl_in")
        for name, config in self._queues.items():
            if name == ctrl_name:
                continue
            if self._queue_has_pending(config.queue):
                self._mark_pending_messages_prechecked()
                return True
        return self._manager_control_pending_is_actionable()

    def _drain_control_queue_first(self) -> None:
        """Handle pending manager control messages before new spawn work.

        The manager owns the global spawn inbox. Once STOP is pending, it must
        stop consuming new spawn requests before launching additional children,
        otherwise cleanup can race with newly-created child tasks.
        """

        ctrl_name = self._queue_names["ctrl_in"]
        ctrl_queue = self._queue(ctrl_name)
        handled = 0
        seen_timestamps: set[int] = set()

        while handled < MANAGER_CONTROL_DRAIN_MAX_MESSAGES:
            pending = ctrl_queue.peek_one(with_timestamps=True)
            if pending is None:
                self._clear_stalled_control_message()
                return

            if isinstance(pending, tuple) and len(pending) == 2:
                body, timestamp = pending
            else:  # pragma: no cover - defensive queue shape guard
                body, timestamp = pending, None

            if not isinstance(timestamp, int):
                return
            if self._stalled_control_retry_active(timestamp):
                return
            if timestamp in seen_timestamps:
                self._mark_stalled_control_message(
                    queue_name=ctrl_name,
                    timestamp=timestamp,
                )
                return
            if (
                self._stalled_control_message_id is not None
                and self._stalled_control_message_id != timestamp
            ):
                self._clear_stalled_control_message()

            context = QueueMessageContext(
                queue_name=ctrl_name,
                queue=ctrl_queue,
                mode=QueueMode.PEEK,
                timestamp=timestamp,
            )
            self._handle_control_message(str(body), timestamp, context)
            self._last_activity_ns = time.time_ns()
            self._invalidate_leadership_work_cache()
            handled += 1
            if self._draining or self.should_stop:
                return
            seen_timestamps.add(timestamp)
            next_pending = ctrl_queue.peek_one(with_timestamps=True)
            if self._peeked_message_timestamp(next_pending) == timestamp:
                self._mark_stalled_control_message(
                    queue_name=ctrl_name,
                    timestamp=timestamp,
                )
                return

        logger.warning(
            "Manager yielded after handling %s control messages in one turn",
            MANAGER_CONTROL_DRAIN_MAX_MESSAGES,
        )

    def _control_allows_child_launch(self) -> bool:
        """Return whether spawn work may still launch a new child.

        STOP can arrive after a spawn request has been reserved but before the
        manager reaches ``_launch_child_task()``. Managers in drain mode must
        not start new child work from that in-flight reserved request.
        """

        if self._draining or self.should_stop:
            return False
        self._drain_control_queue_first()
        return not self._draining and not self.should_stop

    def _read_broker_timestamp(self, *, force: bool = False) -> int:
        """Return newest pending manager-owned input timestamp for idle tracking.

        SimpleBroker's backend-wide ``last_ts`` is intentionally not used here:
        under the Postgres backend, unrelated queues in the same database may
        advance that value and would keep an idle manager alive under load.
        """

        last_known = getattr(self, "_last_broker_timestamp", 0)
        now_ns = time.time_ns()
        if not force:
            last_probe = getattr(self, "_last_broker_probe_ns", 0)
            interval = getattr(self, "_broker_probe_interval_ns", 1_000_000_000)
            if now_ns - last_probe < interval:
                return last_known

        self._last_broker_probe_ns = now_ns
        latest = last_known
        for queue_name in self._idle_activity_queue_names():
            try:
                pending = self._queue(queue_name).peek_one(with_timestamps=True)
            except (BrokerError, OSError, RuntimeError):
                logger.debug(
                    "Manager activity queue %s unavailable for idle tracking",
                    queue_name,
                    exc_info=True,
                )
                continue
            timestamp = self._peeked_message_timestamp(pending)
            if timestamp is not None:
                latest = max(latest, timestamp)

        return latest

    def _update_idle_activity_from_broker(self, *, force: bool = False) -> None:
        """Refresh idle activity from manager-owned input queues only."""

        previous_timestamp = self._last_broker_timestamp
        new_timestamp = self._read_broker_timestamp(force=force)
        if new_timestamp > previous_timestamp:
            self._last_broker_timestamp = new_timestamp
            now_ns = time.time_ns()
            # Broker timestamps are nanosecond-based; ensure we track activity using
            # the greater of broker-reported and wall-clock times.
            self._last_activity_ns = max(
                self._last_activity_ns, max(now_ns, new_timestamp)
            )

    def _ensure_reserved_queue_empty(self, reserved_queue_name: str) -> None:
        """Drain one manager reserved queue when policy requires it."""

        reserved_queue = self._queue(reserved_queue_name)
        try:
            has_pending = reserved_queue.has_pending()
        except (BrokerError, OSError, RuntimeError):
            logger.debug(
                "Failed to check reserved queue %s state",
                reserved_queue_name,
                exc_info=True,
            )
            return

        if not has_pending:
            return

        logger.warning(
            "Reserved queue %s not empty for manager %s",
            reserved_queue_name,
            self.tid,
        )
        while reserved_queue.read_many(64):
            continue

    def _cleanup_reserved_queue_if_needed(self, reserved_queue_name: str) -> None:
        """Remove one manager reserved queue's messages when cleanup is enabled."""

        if not self.taskspec.spec.cleanup_on_exit:
            return

        reserved_queue = self._queue(reserved_queue_name)
        while reserved_queue.read_many(64):
            continue

    def _apply_spawn_reserved_policy(
        self,
        policy: ReservedPolicy,
        *,
        source_queue: str,
        reserved_queue: str,
        message_timestamp: int | None = None,
    ) -> None:
        """Apply a reserved policy to one manager spawn source/reserved pair."""

        if policy is ReservedPolicy.KEEP:
            return

        reserved = self._queue(reserved_queue)

        if policy is ReservedPolicy.CLEAR:
            if message_timestamp is not None:
                try:
                    reserved.delete(message_id=message_timestamp)
                except (BrokerError, OSError, RuntimeError):
                    logger.debug(
                        "Failed to clear reserved spawn message %s",
                        message_timestamp,
                        exc_info=True,
                    )
            else:
                while reserved.read_many(64):
                    continue
            return

        if policy is ReservedPolicy.REQUEUE:
            if message_timestamp is not None:
                try:
                    reserved.move_one(
                        source_queue,
                        exact_timestamp=message_timestamp,
                        require_unclaimed=True,
                        with_timestamps=False,
                    )
                    return
                except (BrokerError, OSError, RuntimeError):
                    logger.debug(
                        "Failed to requeue reserved spawn message %s",
                        message_timestamp,
                        exc_info=True,
                    )
            while True:
                try:
                    batch = reserved.move_many(
                        source_queue,
                        limit=64,
                        require_unclaimed=True,
                        with_timestamps=False,
                    )
                except (BrokerError, OSError, RuntimeError):
                    logger.debug(
                        "Failed moving reserved spawn messages back to %s",
                        source_queue,
                        exc_info=True,
                    )
                    break
                if not batch:
                    break

    def _apply_reserved_policy(
        self,
        policy: ReservedPolicy,
        message_timestamp: int | None = None,
    ) -> None:
        """Apply manager reserved policy across all attached spawn sources."""

        if message_timestamp is not None:
            self._apply_spawn_reserved_policy(
                policy,
                source_queue=self._queue_names["inbox"],
                reserved_queue=self._queue_names["reserved"],
                message_timestamp=message_timestamp,
            )
            return

        for reserved_queue, source_queue in self._spawn_reserved_queue_pairs():
            self._apply_spawn_reserved_policy(
                policy,
                source_queue=source_queue,
                reserved_queue=reserved_queue,
            )

    def _ensure_reserved_empty(self) -> None:
        """Drain all manager spawn reserved queues when policies require it."""

        for reserved_queue, _source_queue in self._spawn_reserved_queue_pairs():
            self._ensure_reserved_queue_empty(reserved_queue)

    def _cleanup_reserved_if_needed(self) -> None:
        """Clean all manager spawn reserved queues when cleanup is enabled."""

        for reserved_queue, _source_queue in self._spawn_reserved_queue_pairs():
            self._cleanup_reserved_queue_if_needed(reserved_queue)

    # ------------------------------------------------------------------
    # Message handling
    # ------------------------------------------------------------------
    def _handle_work_message(
        self, message: str, timestamp: int, context: QueueMessageContext
    ) -> None:
        """Consume a reserved spawn request, build child spec, and launch.

        Atomic broker reservation is the dispatch authority for public spawn
        work. Registry leadership remains advisory and is not a pre-launch
        fence for work this manager has already reserved.

        Spec: [MA-1.1], [MA-2], [MF-6]
        """
        self._last_activity_ns = time.time_ns()
        self._cleanup_children()
        source_queue = context.queue_name
        reserved_queue = context.reserved_queue_name or self._queue_names["reserved"]
        self._emit_serve_log(
            "spawn_reserved",
            component="spawn",
            required_level="trace",
            source_queue=source_queue,
            reserved_queue=reserved_queue,
            message_timestamp=timestamp,
        )
        if not self._control_allows_child_launch():
            self._release_unlaunched_spawn_request(
                source_queue=source_queue,
                reserved_queue=reserved_queue,
                message_timestamp=timestamp,
                reason="control_disallowed_launch_before_validation",
            )
            return

        try:
            payload = json.loads(message) if message else {}
        except json.JSONDecodeError:
            logger.warning("Manager received non-JSON spawn request: %s", message)
            self._emit_serve_log(
                "spawn_spec_validation_failed",
                component="spawn",
                required_level="debug",
                severity="warning",
                source_queue=source_queue,
                reserved_queue=reserved_queue,
                message_timestamp=timestamp,
                error="spawn request is not JSON",
            )
            policy = self.taskspec.spec.reserved_policy_on_error
            self._apply_spawn_reserved_policy(
                policy,
                source_queue=source_queue,
                reserved_queue=reserved_queue,
                message_timestamp=timestamp,
            )
            if policy is not ReservedPolicy.KEEP:
                self._ensure_reserved_queue_empty(reserved_queue)
                self._cleanup_reserved_queue_if_needed(reserved_queue)
            return

        child_spec = self._build_child_spec(payload, timestamp)
        if child_spec is None:
            self._emit_serve_log(
                "spawn_spec_validation_failed",
                component="spawn",
                required_level="debug",
                severity="warning",
                source_queue=source_queue,
                reserved_queue=reserved_queue,
                message_timestamp=timestamp,
                service_key=service_key_from_metadata(payload.get("metadata")),
                error="child TaskSpec validation failed",
            )
            policy = self.taskspec.spec.reserved_policy_on_error
            self._apply_spawn_reserved_policy(
                policy,
                source_queue=source_queue,
                reserved_queue=reserved_queue,
                message_timestamp=timestamp,
            )
            if policy is not ReservedPolicy.KEEP:
                self._ensure_reserved_queue_empty(reserved_queue)
                self._cleanup_reserved_queue_if_needed(reserved_queue)
            return

        inbox_message = payload.get("inbox_message", WORK_ENVELOPE_START)
        if not self._control_allows_child_launch():
            self._release_unlaunched_spawn_request(
                source_queue=source_queue,
                reserved_queue=reserved_queue,
                message_timestamp=timestamp,
                reason="control_disallowed_launch_after_validation",
            )
            return

        launched = self._launch_child_task(
            child_spec,
            inbox_message,
            autostart_source=child_spec.metadata.get(
                INTERNAL_AUTOSTART_SOURCE_METADATA_KEY
            ),
            source_queue=source_queue,
            reserved_queue=reserved_queue,
            message_timestamp=timestamp,
        )
        if not launched:
            if self._draining or self.should_stop:
                self._release_unlaunched_spawn_request(
                    source_queue=source_queue,
                    reserved_queue=reserved_queue,
                    message_timestamp=timestamp,
                    reason="manager_draining_after_launch_attempt",
                    child_tid=child_spec.tid,
                )
            return
        active_launch = (
            self._active_child_launches.get(child_spec.tid)
            if child_spec.tid is not None
            else None
        )
        if (
            active_launch is not None
            and active_launch.reserved_queue == reserved_queue
            and active_launch.message_timestamp == timestamp
        ):
            return

        try:
            self._queue(reserved_queue).delete(message_id=timestamp)
            self._emit_serve_log(
                "spawn_ack_result",
                component="spawn",
                required_level="trace",
                source_queue=source_queue,
                reserved_queue=reserved_queue,
                message_timestamp=timestamp,
                child_tid=child_spec.tid,
                success=True,
            )
        except (BrokerError, OSError, RuntimeError):
            logger.debug(
                "Failed to acknowledge manager message %s", timestamp, exc_info=True
            )
            self._emit_serve_log(
                "spawn_ack_result",
                component="spawn",
                required_level="debug",
                severity="warning",
                source_queue=source_queue,
                reserved_queue=reserved_queue,
                message_timestamp=timestamp,
                child_tid=child_spec.tid,
                success=False,
            )

    def _release_unlaunched_spawn_request(
        self,
        *,
        source_queue: str,
        reserved_queue: str,
        message_timestamp: int,
        reason: str,
        child_tid: str | None = None,
    ) -> None:
        """Return an unlaunched reserved spawn request to its source queue."""

        self._apply_spawn_reserved_policy(
            ReservedPolicy.REQUEUE,
            source_queue=source_queue,
            reserved_queue=reserved_queue,
            message_timestamp=message_timestamp,
        )
        self._emit_serve_log(
            "spawn_reserved_released",
            component="spawn",
            required_level="debug",
            source_queue=source_queue,
            reserved_queue=reserved_queue,
            message_timestamp=message_timestamp,
            child_tid=child_tid,
            reason=reason,
        )

    def _build_child_spec(
        self, payload: dict[str, Any], timestamp: int
    ) -> TaskSpec | None:
        """Parse spawn payload and validate a child TaskSpec (Spec: [MA-1.1], [MA-2])."""
        provided_spec = payload.get("taskspec")
        if provided_spec is not None:
            candidate = copy.deepcopy(provided_spec)
        else:
            spec_section = payload.get("spec")
            if spec_section is None:
                logger.warning("Spawn request missing 'spec' field: %s", payload)
                self._emit_serve_log(
                    "spawn_spec_validation_failed",
                    component="spawn",
                    required_level="debug",
                    severity="warning",
                    message_timestamp=timestamp,
                    error="spawn request missing spec field",
                )
                return None
            candidate = {
                "tid": payload.get("tid"),
                "name": payload.get("name", f"{self.taskspec.name}-child"),
                "version": payload.get("version", "1.0"),
                "spec": spec_section,
                "io": payload.get("io", {}),
                "state": payload.get("state", {}),
                "metadata": payload.get("metadata", {}),
            }

        metadata = candidate.setdefault("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
            candidate["metadata"] = metadata

        internal_runtime_task_class = payload.get(
            INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY
        )
        if isinstance(internal_runtime_task_class, str) and internal_runtime_task_class:
            metadata[INTERNAL_RUNTIME_TASK_CLASS_KEY] = internal_runtime_task_class

        internal_endpoint_name = payload.get(
            INTERNAL_RUNTIME_ENVELOPE_ENDPOINT_NAME_KEY
        )
        if isinstance(internal_endpoint_name, str) and internal_endpoint_name:
            metadata[INTERNAL_RUNTIME_ENDPOINT_NAME_KEY] = internal_endpoint_name

        candidate["metadata"].setdefault("parent_tid", self.tid)

        try:
            resolved_payload = resolve_taskspec_payload(
                candidate,
                tid=str(timestamp),
                inherited_weft_context=getattr(
                    self.taskspec.spec, "weft_context", None
                ),
            )
            child_spec = TaskSpec.model_validate(
                resolved_payload, context={"auto_expand": False}
            )
        except (TypeError, ValueError, ValidationError):
            logger.exception(
                "Failed to validate child TaskSpec from payload %s", payload
            )
            self._emit_serve_log(
                "spawn_spec_validation_failed",
                component="spawn",
                required_level="debug",
                severity="warning",
                message_timestamp=timestamp,
                error="failed to validate child TaskSpec",
            )
            return None

        return child_spec

    # ------------------------------------------------------------------
    # Manager-owned service supervision
    # ------------------------------------------------------------------
    def _service_supervision_allowed(self) -> bool:
        """Return whether this manager may launch singleton services."""

        return not self._draining and not self.should_stop

    def _task_monitor_supervision_allowed(self) -> bool:
        """Return whether this manager may supervise a task monitor."""

        return self._task_monitor_enabled and self._service_supervision_allowed()

    def _build_heartbeat_spawn_payload(self) -> dict[str, Any]:
        """Build the manager-owned spawn envelope for the heartbeat service."""

        spec_section: dict[str, Any] = {
            "type": "function",
            "function_target": DEFAULT_FUNCTION_TARGET,
            "persistent": True,
            "enable_process_title": False,
        }
        weft_context = getattr(self.taskspec.spec, "weft_context", None)
        if weft_context is not None:
            spec_section["weft_context"] = str(weft_context)

        return {
            "taskspec": {
                "name": "heartbeat-service",
                "spec": spec_section,
                "metadata": service_metadata(
                    key=INTERNAL_SERVICE_KEY_HEARTBEAT,
                    lifecycle="ensure",
                    extra={
                        "internal": True,
                        "role": "heartbeat_service",
                        "heartbeat_idle_timeout": 0.0,
                    },
                ),
            },
            "inbox_message": None,
            INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY: (
                INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT
            ),
            INTERNAL_RUNTIME_ENVELOPE_ENDPOINT_NAME_KEY: (
                INTERNAL_HEARTBEAT_ENDPOINT_NAME
            ),
        }

    def _build_task_monitor_spawn_payload(self) -> dict[str, Any]:
        """Build the manager-owned spawn envelope for the supervised monitor."""

        spec_section: dict[str, Any] = {
            "type": "function",
            "function_target": DEFAULT_FUNCTION_TARGET,
            "persistent": True,
            "enable_process_title": False,
        }
        weft_context = getattr(self.taskspec.spec, "weft_context", None)
        if weft_context is not None:
            spec_section["weft_context"] = str(weft_context)

        return {
            "taskspec": {
                "name": "task-monitor",
                "spec": spec_section,
                "metadata": service_metadata(
                    key=INTERNAL_SERVICE_KEY_TASK_MONITOR,
                    lifecycle="ensure",
                    extra={
                        "internal": True,
                        "role": "task_monitor",
                    },
                ),
            },
            "inbox_message": {
                "type": "task_monitor_wakeup",
                "reason": "manager_supervision",
            },
            INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY: (
                INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR
            ),
        }

    def _heartbeat_service_spec(self) -> ManagedServiceSpec:
        return ManagedServiceSpec(
            key=INTERNAL_SERVICE_KEY_HEARTBEAT,
            lifecycle="ensure",
            spawn_payload=self._build_heartbeat_spawn_payload(),
        )

    def _task_monitor_service_spec(self) -> ManagedServiceSpec:
        return ManagedServiceSpec(
            key=INTERNAL_SERVICE_KEY_TASK_MONITOR,
            lifecycle="ensure",
            spawn_payload=self._build_task_monitor_spawn_payload(),
            restart_backoff_ns=self._task_monitor_restart_backoff_ns,
        )

    def _managed_service_spawn_queue_name(self, service: ManagedServiceSpec) -> str:
        """Return the spawn queue for one manager-owned service request."""

        if (
            service.key
            in {INTERNAL_SERVICE_KEY_HEARTBEAT, INTERNAL_SERVICE_KEY_TASK_MONITOR}
            and self._internal_spawn_queue_attached()
        ):
            return self._queue_names["internal_inbox"]
        return self._queue_names["inbox"]

    def _enqueue_managed_service_request(self, service: ManagedServiceSpec) -> bool:
        """Enqueue one manager-owned service spawn request."""

        queue_name = self._managed_service_spawn_queue_name(service)
        try:
            self._queue(queue_name).write(
                json.dumps(service.spawn_payload, ensure_ascii=False)
            )
            self._mark_pending_messages_prechecked()
            if queue_name == self._queue_names.get("internal_inbox"):
                self._managed_internal_spawn_enqueued = True
            self._invalidate_leadership_work_cache()
        except (BrokerError, OSError, RuntimeError):
            logger.warning(
                "Failed to enqueue managed service %s", service.key, exc_info=True
            )
            self._emit_serve_log(
                "managed_service_enqueue",
                component="service",
                required_level="info",
                severity="error",
                service_key=service.key,
                enqueue_queue=queue_name,
                success=False,
            )
            return False
        state = self._service_state(service.key)
        state.spawn_pending = True
        state.launched_once = True
        self._emit_serve_log(
            "managed_service_enqueue",
            component="service",
            required_level="debug",
            service_key=service.key,
            enqueue_queue=queue_name,
            success=True,
        )
        return True

    def _enqueue_task_monitor_request(self) -> bool:
        """Enqueue one internal monitor spawn request on this manager inbox."""

        return self._enqueue_managed_service_request(self._task_monitor_service_spec())

    @staticmethod
    def _service_key_for_child(child: ManagedChild) -> str | None:
        """Return the manager-supervised service key for a tracked child."""

        if child.service_key is not None:
            return child.service_key
        if child.internal_role == INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR:
            return INTERNAL_SERVICE_KEY_TASK_MONITOR
        if child.internal_role == INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT:
            return INTERNAL_SERVICE_KEY_HEARTBEAT
        if child.autostart_source:
            return child.autostart_source
        return None

    def _tracked_service_candidate(
        self,
        service_key: str,
        *,
        scan_terminal_proof: bool = False,
    ) -> ServiceCandidate | None:
        candidates: list[ServiceCandidate] = []
        for tid, child in list(self._child_processes.items()):
            if self._service_key_for_child(child) != service_key:
                continue
            if self._child_has_exited(child):
                continue
            pid = child.process.pid
            force_kill_pids = [pid] if isinstance(pid, int) and pid > 0 else []
            if scan_terminal_proof and self._child_terminal_proof_visible(tid, child):
                candidates.append(
                    ServiceCandidate(
                        key=service_key,
                        tid=tid,
                        state="terminal",
                        source="terminal-envelope",
                        reason="tracked child published terminal proof",
                    )
                )
                continue
            candidates.append(
                ServiceCandidate(
                    key=service_key,
                    tid=tid,
                    state="live",
                    source="manager-child",
                    metadata={
                        key: value
                        for key, value in (
                            ("ctrl_in", child.ctrl_queue),
                            ("ctrl_out", child.ctrl_out_queue),
                            ("pid", pid),
                            ("force_kill_pids", force_kill_pids),
                        )
                        if value not in (None, [])
                    },
                )
            )

        live = select_canonical_live_candidate(candidates)
        if live is not None:
            return live
        return next(iter(candidates), None)

    @staticmethod
    def _child_matches_service(child: ManagedChild, service_key: str) -> bool:
        return Manager._service_key_for_child(child) == service_key

    def _terminate_duplicate_service_candidates(
        self,
        service_key: str,
        *,
        canonical_tid: str,
        candidates: list[ServiceCandidate],
    ) -> None:
        """Terminate non-canonical live singleton owners."""

        duplicate_tids = {
            candidate.tid
            for candidate in candidates
            if candidate.state == "live" and candidate.tid != canonical_tid
        }
        if not duplicate_tids:
            return

        signaled: set[str] = set()
        killed_pids: set[int] = set()
        for tid, child in list(self._child_processes.items()):
            if tid not in duplicate_tids:
                continue
            if not self._child_matches_service(child, service_key):
                continue
            ctrl_queue = child.ctrl_queue or f"T{tid}.{QUEUE_CTRL_IN_SUFFIX}"
            self._send_child_control_command(ctrl_queue, CONTROL_KILL)
            pid = child.process.pid
            if isinstance(pid, int):
                self._kill_duplicate_service_pid(tid, pid, killed_pids=killed_pids)
            signaled.add(tid)

        for candidate in candidates:
            if candidate.tid == canonical_tid:
                continue
            if candidate.state != "live":
                continue
            if candidate.tid not in signaled:
                ctrl_in_name = candidate.metadata.get("ctrl_in")
                if isinstance(ctrl_in_name, str) and ctrl_in_name:
                    self._send_child_control_command(ctrl_in_name, CONTROL_KILL)
                    signaled.add(candidate.tid)
            for pid in self._candidate_force_kill_pids(candidate):
                self._kill_duplicate_service_pid(
                    candidate.tid,
                    pid,
                    killed_pids=killed_pids,
                )

    @staticmethod
    def _candidate_force_kill_pids(candidate: ServiceCandidate) -> tuple[int, ...]:
        raw_pids = candidate.metadata.get("force_kill_pids")
        if not isinstance(raw_pids, Sequence) or isinstance(raw_pids, (str, bytes)):
            return ()
        return tuple(
            pid
            for pid in raw_pids
            if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0
        )

    @staticmethod
    def _kill_duplicate_service_pid(
        tid: str,
        pid: int,
        *,
        killed_pids: set[int],
    ) -> None:
        if pid <= 0 or pid in killed_pids:
            return
        if pid == os.getpid():
            logger.warning(
                "Skipping duplicate service kill for %s because pid %s is current "
                "manager process",
                tid,
                pid,
            )
            return
        kill_process_tree(pid, timeout=0.2)
        killed_pids.add(pid)

    def _latest_tid_runtime_handle(self, tid: str) -> RunnerHandle | None:
        queue = self._queue(WEFT_TID_MAPPINGS_QUEUE)
        latest_payload: dict[str, Any] | None = None
        latest_timestamp = -1
        for payload, timestamp in iter_queue_json_entries(queue):
            if payload.get("full") != tid or timestamp < latest_timestamp:
                continue
            latest_payload = payload
            latest_timestamp = timestamp
        if latest_payload is None:
            return None

        handle_payload = latest_payload.get("runtime_handle")
        if not isinstance(handle_payload, Mapping):
            return None
        try:
            return RunnerHandle.from_dict(handle_payload)
        except ValueError:
            return None

    def _latest_tid_runtime_handles(self, tids: set[str]) -> dict[str, RunnerHandle]:
        """Return latest runtime handles for the requested TIDs from one scan."""

        if not tids:
            return {}
        queue = self._queue(WEFT_TID_MAPPINGS_QUEUE)
        latest_by_tid: dict[str, tuple[dict[str, Any], int]] = {}
        for payload, timestamp in iter_queue_json_entries(queue):
            tid = payload.get("full")
            if not isinstance(tid, str) or tid not in tids:
                continue
            existing = latest_by_tid.get(tid)
            if existing is None or existing[1] <= timestamp:
                latest_by_tid[tid] = (payload, timestamp)

        handles: dict[str, RunnerHandle] = {}
        for tid, (payload, _timestamp) in latest_by_tid.items():
            handle_payload = payload.get("runtime_handle")
            if not isinstance(handle_payload, Mapping):
                continue
            try:
                handles[tid] = RunnerHandle.from_dict(handle_payload)
            except ValueError:
                continue
        return handles

    @staticmethod
    def _service_probe_key(
        *,
        source: str,
        service_key: str,
        tid: str,
        timestamp: int | None,
    ) -> str:
        return f"{source}\x1f{service_key}\x1f{tid}\x1f{timestamp or 0}"

    def _service_pong_candidate(
        self,
        *,
        service_key: str,
        tid: str,
        timestamp: int | None,
        metadata: dict[str, Any],
        ctrl_in_name: str,
        ctrl_out_name: str,
        source: Literal["control-pong", "service-registry-pong"],
    ) -> ServiceCandidate | None:
        """Advance or start one non-blocking service-owner PING probe.

        A pending probe is unknown service evidence. The caller falls through
        to normal recent/stale classification only after the probe deadline.
        """

        key = self._service_probe_key(
            source=source,
            service_key=service_key,
            tid=tid,
            timestamp=timestamp,
        )
        now_ns = time.time_ns()
        pending = self._service_probe_pending.get(key)
        if pending is not None:
            return self._advance_service_pong_probe(
                pending,
                timestamp=timestamp,
                metadata=metadata,
                now_ns=now_ns,
            )

        request_id = uuid.uuid4().hex
        try:
            ctrl_in = self._manager_context().queue(ctrl_in_name, persistent=True)
            try:
                ctrl_in.write(
                    json.dumps({"command": CONTROL_PING, "request_id": request_id})
                )
            finally:
                ctrl_in.close()
        except (BrokerError, OSError, RuntimeError) as exc:
            return ServiceCandidate(
                key=service_key,
                tid=tid,
                state="uncertain",
                source=source,
                timestamp=timestamp,
                reason=str(exc),
                metadata=metadata,
            )

        timeout_ns = int(MANAGED_SERVICE_PING_TIMEOUT_SECONDS * 1_000_000_000)
        self._service_probe_pending[key] = _ServicePendingPongProbe(
            key=key,
            service_key=service_key,
            tid=tid,
            row_timestamp=timestamp,
            source=source,
            ctrl_in_name=ctrl_in_name,
            ctrl_out_name=ctrl_out_name,
            request_id=request_id,
            deadline_ns=now_ns + max(0, timeout_ns),
        )
        return ServiceCandidate(
            key=service_key,
            tid=tid,
            state="uncertain",
            source=source,
            timestamp=timestamp,
            reason="ping_pending",
            metadata=metadata,
        )

    def _advance_service_pong_probe(
        self,
        probe: _ServicePendingPongProbe,
        *,
        timestamp: int | None,
        metadata: dict[str, Any],
        now_ns: int,
    ) -> ServiceCandidate | None:
        """Advance one pending service-owner PING without sleeping."""

        try:
            ctrl_out = self._manager_context().queue(
                probe.ctrl_out_name,
                persistent=False,
            )
            try:
                for item in ctrl_out.peek_generator(with_timestamps=True):
                    if not isinstance(item, tuple) or len(item) != 2:
                        continue
                    body, _message_timestamp = item
                    payload = coerce_pong_response(
                        str(body),
                        tid=probe.tid,
                        request_id=probe.request_id,
                    )
                    if payload is None:
                        continue
                    self._service_probe_pending.pop(probe.key, None)
                    return ServiceCandidate(
                        key=probe.service_key,
                        tid=probe.tid,
                        state="live",
                        source=probe.source,
                        timestamp=timestamp,
                        metadata=metadata,
                    )
            finally:
                ctrl_out.close()
        except (BrokerError, OSError, RuntimeError) as exc:
            self._service_probe_pending.pop(probe.key, None)
            return ServiceCandidate(
                key=probe.service_key,
                tid=probe.tid,
                state="uncertain",
                source=probe.source,
                timestamp=timestamp,
                reason=str(exc),
                metadata=metadata,
            )

        if now_ns >= probe.deadline_ns:
            self._service_probe_pending.pop(probe.key, None)
            return None

        return ServiceCandidate(
            key=probe.service_key,
            tid=probe.tid,
            state="uncertain",
            source=probe.source,
            timestamp=timestamp,
            reason="ping_pending",
            metadata=metadata,
        )

    def _service_candidate_from_task_log(
        self,
        *,
        service_key: str,
        tid: str,
        payload: Mapping[str, Any],
        timestamp: int,
        runtime_handle: RunnerHandle | None = None,
    ) -> ServiceCandidate:
        candidate_metadata = self._service_candidate_metadata(payload)
        ctrl_queues = self._service_candidate_control_queues(payload)

        status = payload.get("status")
        if isinstance(status, str) and status in TERMINAL_TASK_STATUSES:
            return ServiceCandidate(
                key=service_key,
                tid=tid,
                state="terminal",
                source="task-log",
                timestamp=timestamp,
                reason=status,
                metadata=candidate_metadata,
            )

        if runtime_handle is not None and handle_has_live_host_process(runtime_handle):
            force_kill_pids = self._runtime_handle_force_kill_pids(runtime_handle)
            return ServiceCandidate(
                key=service_key,
                tid=tid,
                state="live",
                source="tid-mapping",
                timestamp=timestamp,
                metadata=self._service_candidate_metadata(
                    payload,
                    force_kill_pids=force_kill_pids,
                ),
            )

        if ctrl_queues is not None:
            ctrl_in_name, ctrl_out_name = ctrl_queues
            probe_candidate = self._service_pong_candidate(
                service_key=service_key,
                tid=tid,
                timestamp=timestamp,
                metadata=candidate_metadata,
                ctrl_in_name=ctrl_in_name,
                ctrl_out_name=ctrl_out_name,
                source="control-pong",
            )
            if probe_candidate is not None:
                return probe_candidate

        recent_grace_ns = int(
            MANAGED_SERVICE_RECENT_EVIDENCE_GRACE_SECONDS * 1_000_000_000
        )
        if not tid.isdigit() or time.time_ns() - int(tid) <= recent_grace_ns:
            return ServiceCandidate(
                key=service_key,
                tid=tid,
                state="uncertain",
                source="task-log",
                timestamp=timestamp,
                reason="recent non-terminal state without live runtime proof or PONG",
                metadata=candidate_metadata,
            )

        return ServiceCandidate(
            key=service_key,
            tid=tid,
            state="terminal",
            source="task-log",
            timestamp=timestamp,
            reason="stale non-terminal state without live runtime proof or PONG",
            metadata=candidate_metadata,
        )

    def _service_candidate_from_service_owner_record(
        self,
        record: ServiceOwnerRecord,
    ) -> ServiceCandidate:
        """Project one service-owner row into managed-service evidence."""

        payload = record.payload
        metadata: dict[str, Any] = {}
        queues = payload.get("queues")
        if isinstance(queues, Mapping):
            ctrl_in_name = queues.get("ctrl_in")
            ctrl_out_name = queues.get("ctrl_out")
            if isinstance(ctrl_in_name, str) and isinstance(ctrl_out_name, str):
                metadata["ctrl_in"] = ctrl_in_name
                metadata["ctrl_out"] = ctrl_out_name
        handle_payload = payload.get("runtime_handle")
        runtime_handle: RunnerHandle | None = None
        if isinstance(handle_payload, Mapping):
            try:
                runtime_handle = RunnerHandle.from_dict(handle_payload)
            except ValueError:
                runtime_handle = None
        if runtime_handle is not None:
            pids = self._runtime_handle_force_kill_pids(runtime_handle)
            if pids:
                metadata["force_kill_pids"] = list(pids)

        if record.status in {SERVICE_STATUS_TERMINAL, "stopped"}:
            return ServiceCandidate(
                key=record.service_key,
                tid=record.owner_tid,
                state="terminal",
                source="service-registry",
                timestamp=record.timestamp,
                reason=record.status,
                metadata=metadata,
            )

        if runtime_handle is not None:
            if handle_has_live_host_process(runtime_handle):
                return ServiceCandidate(
                    key=record.service_key,
                    tid=record.owner_tid,
                    state="live",
                    source="service-registry",
                    timestamp=record.timestamp,
                    metadata=metadata,
                )
            if runtime_handle.control.get("authority") == "host-pid":
                return ServiceCandidate(
                    key=record.service_key,
                    tid=record.owner_tid,
                    state="terminal",
                    source="service-registry",
                    timestamp=record.timestamp,
                    reason="registered host pid is not live",
                    metadata=metadata,
                )

        if "ctrl_in" in metadata and "ctrl_out" in metadata:
            probe_candidate = self._service_pong_candidate(
                service_key=record.service_key,
                tid=record.owner_tid,
                timestamp=record.timestamp,
                metadata=metadata,
                ctrl_in_name=metadata["ctrl_in"],
                ctrl_out_name=metadata["ctrl_out"],
                source="service-registry-pong",
            )
            if probe_candidate is not None:
                return probe_candidate

        recent_grace_ns = int(
            MANAGED_SERVICE_RECENT_EVIDENCE_GRACE_SECONDS * 1_000_000_000
        )
        if (
            record.owner_tid.isdigit()
            and time.time_ns() - int(record.owner_tid) <= recent_grace_ns
        ):
            return ServiceCandidate(
                key=record.service_key,
                tid=record.owner_tid,
                state="uncertain",
                source="service-registry",
                timestamp=record.timestamp,
                reason="recent active owner without live runtime proof or PONG",
                metadata=metadata,
            )

        return ServiceCandidate(
            key=record.service_key,
            tid=record.owner_tid,
            state="terminal",
            source="service-registry",
            timestamp=record.timestamp,
            reason="stale active owner without live runtime proof or PONG",
            metadata=metadata,
        )

    @staticmethod
    def _trusted_internal_service_key(
        metadata: Mapping[str, Any],
        *,
        runtime_class: str | None = None,
    ) -> str | None:
        key = service_key_from_metadata(metadata)
        if key == INTERNAL_SERVICE_KEY_HEARTBEAT:
            if metadata.get("internal") is not True:
                return None
            if metadata.get("role") != "heartbeat_service":
                return None
            if runtime_class != INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT:
                return None
            endpoint = metadata.get(INTERNAL_RUNTIME_ENDPOINT_NAME_KEY)
            if endpoint is not None and endpoint != INTERNAL_HEARTBEAT_ENDPOINT_NAME:
                return None
            return key

        if key == INTERNAL_SERVICE_KEY_TASK_MONITOR:
            if metadata.get("internal") is not True:
                return None
            if metadata.get("role") != "task_monitor":
                return None
            if runtime_class != INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR:
                return None
            return key
        return None

    def _trusted_service_key_from_metadata(
        self,
        metadata: Any,
        *,
        desired_keys: set[str],
        runtime_class: str | None = None,
        manager_event_autostart_source: str | None = None,
    ) -> str | None:
        """Return a trusted service key for Manager-owned evidence.

        Public task metadata is not enough to claim an internal singleton. For
        autostart services, evidence is considered only for currently desired
        manifest keys and only when the log entry carries manager-authored
        autostart authority.
        """

        if not isinstance(metadata, Mapping):
            return None

        internal_key = self._trusted_internal_service_key(
            metadata,
            runtime_class=runtime_class,
        )
        if internal_key is not None:
            return internal_key if internal_key in desired_keys else None

        key = service_key_from_metadata(metadata)
        source = metadata.get(INTERNAL_AUTOSTART_SOURCE_METADATA_KEY)
        if key is None and isinstance(source, str):
            key = source
        if not isinstance(key, str) or key not in desired_keys:
            return None
        if key in {INTERNAL_SERVICE_KEY_HEARTBEAT, INTERNAL_SERVICE_KEY_TASK_MONITOR}:
            return None
        if metadata.get("internal") is True:
            return None
        if metadata.get(INTERNAL_AUTOSTART_ENABLED_METADATA_KEY) is not True:
            return None
        if source != key:
            return None
        manager_authority = (
            metadata.get(INTERNAL_SERVICE_AUTHORITY_METADATA_KEY)
            == INTERNAL_SERVICE_AUTHORITY_MANAGER
        )
        legacy_manager_event = manager_event_autostart_source == key
        if not manager_authority and not legacy_manager_event:
            return None
        return key

    def _spawn_request_service_key(
        self,
        payload: Mapping[str, Any],
        *,
        desired_keys: set[str],
    ) -> str | None:
        taskspec_payload = payload.get("taskspec")
        if not isinstance(taskspec_payload, Mapping):
            return None
        metadata = taskspec_payload.get("metadata")
        envelope_runtime_class = payload.get(INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY)
        return self._trusted_service_key_from_metadata(
            metadata,
            desired_keys=desired_keys,
            runtime_class=envelope_runtime_class
            if isinstance(envelope_runtime_class, str)
            else None,
        )

    def _pending_service_keys(
        self,
        desired_keys: set[str],
        *,
        queue_names: Sequence[str] | None = None,
    ) -> set[str]:
        """Return desired service keys that already have unconsumed spawn work."""

        if not desired_keys:
            return set()
        pending: set[str] = {
            request.service_key
            for request in self._active_child_launches.values()
            if request.service_key in desired_keys
        }
        scan_queue_names = (
            self._spawn_inbox_queue_names()
            if queue_names is None
            else tuple(dict.fromkeys(queue_names))
        )
        for queue_name in scan_queue_names:
            queue = self._queue(queue_name)
            for payload, _timestamp in iter_queue_json_entries(queue):
                key = self._spawn_request_service_key(
                    payload,
                    desired_keys=desired_keys,
                )
                if key is not None:
                    pending.add(key)
        return pending

    @staticmethod
    def _service_candidate_taskspec_dump(
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        if payload.get("event") == "task_spawned":
            taskspec_dump = payload.get("child_taskspec")
        else:
            taskspec_dump = payload.get("taskspec")
        if not isinstance(taskspec_dump, Mapping):
            return None
        return taskspec_dump

    @classmethod
    def _service_candidate_control_queues(
        cls,
        payload: Mapping[str, Any],
    ) -> tuple[str, str] | None:
        taskspec_dump = cls._service_candidate_taskspec_dump(payload)
        if taskspec_dump is None:
            return None
        io_section = taskspec_dump.get("io")
        if not isinstance(io_section, Mapping):
            return None
        control = io_section.get("control")
        if not isinstance(control, Mapping):
            return None
        ctrl_in_name = control.get("ctrl_in")
        ctrl_out_name = control.get("ctrl_out")
        if not isinstance(ctrl_in_name, str) or not ctrl_in_name:
            return None
        if not isinstance(ctrl_out_name, str) or not ctrl_out_name:
            return None
        return ctrl_in_name, ctrl_out_name

    @staticmethod
    def _runtime_handle_force_kill_pids(handle: RunnerHandle) -> tuple[int, ...]:
        if handle.control.get("authority") != "host-pid":
            return ()
        return tuple(
            pid for pid, _create_time in live_host_processes_from_handle(handle)
        )

    @classmethod
    def _service_candidate_pid(cls, payload: Mapping[str, Any]) -> int | None:
        child_pid = payload.get("child_pid")
        if isinstance(child_pid, int) and not isinstance(child_pid, bool):
            if child_pid > 0:
                return child_pid

        taskspec_dump = cls._service_candidate_taskspec_dump(payload)
        if taskspec_dump is None:
            return None
        state = taskspec_dump.get("state")
        if not isinstance(state, Mapping):
            return None
        pid = state.get("pid")
        if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
            return pid
        return None

    @classmethod
    def _service_candidate_metadata(
        cls,
        payload: Mapping[str, Any],
        *,
        force_kill_pids: Sequence[int] = (),
    ) -> dict[str, Any]:
        candidate_metadata: dict[str, Any] = {}
        ctrl_queues = cls._service_candidate_control_queues(payload)
        if ctrl_queues is not None:
            ctrl_in_name, ctrl_out_name = ctrl_queues
            candidate_metadata["ctrl_in"] = ctrl_in_name
            candidate_metadata["ctrl_out"] = ctrl_out_name
        pid = cls._service_candidate_pid(payload)
        if pid is not None:
            candidate_metadata["pid"] = pid
        kill_pids = [
            pid
            for pid in force_kill_pids
            if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0
        ]
        if kill_pids:
            candidate_metadata["force_kill_pids"] = kill_pids
        return candidate_metadata

    def _manager_context(self) -> WeftContext:
        return self._task_context()

    def _observed_service_candidates_by_key(
        self,
        desired_keys: set[str],
        *,
        tracked_by_key: Mapping[str, ServiceCandidate | None] | None = None,
        scan_terminal_proof: bool = False,
    ) -> dict[str, list[ServiceCandidate]]:
        candidates_by_key: dict[str, list[ServiceCandidate]] = {
            key: [] for key in desired_keys
        }
        tracked_by_key = tracked_by_key or {}
        for service_key in desired_keys:
            if service_key in tracked_by_key:
                tracked = tracked_by_key[service_key]
            else:
                tracked = self._tracked_service_candidate(
                    service_key,
                    scan_terminal_proof=scan_terminal_proof,
                )
            if tracked is not None:
                candidates_by_key[service_key].append(tracked)

        queue = self._queue(WEFT_SERVICES_REGISTRY_QUEUE)
        registry_entries = tuple(iter_queue_json_entries(queue))
        read = collect_service_owner_records(
            registry_entries,
            service_type=SERVICE_TYPE_MANAGED,
        )
        now_ns = time.time_ns()
        for service_key in desired_keys:
            self._prune_managed_service_registry_history(
                queue,
                plan_service_owner_history_prune(
                    read.records,
                    service_key=service_key,
                    now_ns=now_ns,
                    ttl_ns=self._manager_registry_retention_ns(),
                ),
            )
            decision = reduce_service_ownership(
                service_key,
                read.records,
                own_tid=None,
                now_ns=now_ns,
                ttl_ns=self._manager_registry_retention_ns(),
                read_failed=read.read_failed,
            )
            for record in decision.records:
                state = self._service_state(record.service_key)
                tid = record.owner_tid
                if tid in state.locally_terminal_tids:
                    candidates_by_key.setdefault(record.service_key, []).append(
                        ServiceCandidate(
                            key=record.service_key,
                            tid=tid,
                            state="terminal",
                            source="manager-local",
                            timestamp=record.timestamp,
                            reason="locally reaped child process",
                        )
                    )
                    continue
                candidates_by_key.setdefault(record.service_key, []).append(
                    self._service_candidate_from_service_owner_record(record)
                )
        return candidates_by_key

    def _prune_managed_service_registry_history(
        self,
        queue: Queue,
        message_ids: Sequence[int],
    ) -> None:
        """Delete exact superseded managed-service registry rows."""

        for message_id in message_ids:
            try:
                queue.delete(message_id=message_id)
            except (BrokerError, OSError, RuntimeError):
                logger.debug(
                    "Failed to prune managed service registry history",
                    exc_info=True,
                )

    def _observed_service_candidates(self, service_key: str) -> list[ServiceCandidate]:
        return self._observed_service_candidates_by_key({service_key}).get(
            service_key, []
        )

    @staticmethod
    def _apply_managed_service_decision_state(
        state: ManagedServiceState,
        decision: ManagedServiceDecision,
    ) -> None:
        """Apply pure reducer state updates to Manager-local state."""

        next_state = decision.state
        state.spawn_pending = next_state.spawn_pending
        state.active_tid = next_state.active_tid
        state.next_allowed_ns = next_state.next_allowed_ns
        state.launched_once = next_state.launched_once
        state.restarts = next_state.restarts
        state.uncertain_attempts = next_state.uncertain_attempts
        state.uncertain_since_ns = next_state.uncertain_since_ns
        state.last_uncertain_reason = next_state.last_uncertain_reason
        state.locally_terminal_tids = set(next_state.locally_terminal_tids)

    def _tick_managed_service(
        self,
        service: ManagedServiceSpec,
        *,
        force: bool = False,
        pending_keys: set[str] | None = None,
        candidates: list[ServiceCandidate] | None = None,
        scan_terminal_proof: bool = False,
    ) -> None:
        """Ensure a desired manager-owned service according to its lifecycle."""

        if not self._service_supervision_allowed():
            return

        pending_keys = pending_keys or set()
        candidates_provided = candidates is not None
        candidates = list(candidates or [])
        state = self._service_state(service.key)
        if not candidates_provided:
            tracked = self._tracked_service_candidate(
                service.key,
                scan_terminal_proof=scan_terminal_proof,
            )
            if tracked is not None:
                candidates.insert(0, tracked)

        now_ns = time.time_ns()
        launched_before = state.launched_once
        state_before = self._service_state_for_log(state)
        decision = reduce_managed_service_state(
            service,
            state,
            ManagedServiceEvidence(
                pending_spawn=service.key in pending_keys,
                candidates=tuple(candidates),
            ),
            now_ns=now_ns,
        )
        self._apply_managed_service_decision_state(state, decision)
        enqueue_queue = self._managed_service_spawn_queue_name(service)
        decision_level = (
            "info"
            if decision.action
            in {
                "start_now",
                "schedule_restart",
                "degraded_wait",
                "suppress_max_restarts",
            }
            else "debug"
        )
        self._emit_serve_log(
            "managed_service_decision",
            component="service",
            required_level=decision_level,
            severity="warning" if decision.action == "degraded_wait" else "info",
            service_key=service.key,
            lifecycle=service.lifecycle,
            action=decision.action,
            reason=decision.reason,
            pending_spawn=service.key in pending_keys,
            enqueue_queue=enqueue_queue if decision.action == "start_now" else None,
            canonical_live_tid=decision.canonical_live.tid
            if decision.canonical_live is not None
            else None,
            candidates=self._service_candidates_for_log(candidates),
            state_before=state_before,
            state_after=self._service_state_for_log(state),
        )

        if decision.canonical_live is not None:
            self._terminate_duplicate_service_candidates(
                service.key,
                canonical_tid=decision.canonical_live.tid,
                candidates=candidates,
            )
        if decision.action == "schedule_restart" and service.autostart_source:
            self._schedule_autostart_rescan_at(state.next_allowed_ns)
            return
        if decision.action != "start_now":
            return
        if not self._enqueue_managed_service_request(service):
            return
        state.spawn_pending = True
        state.launched_once = True
        if service.autostart_source:
            self._mark_autostart_enqueued(
                service,
                now_ns=now_ns,
                launched_before=launched_before,
            )
        else:
            state.next_allowed_ns = 0

    def _reconcile_managed_services(
        self,
        *,
        force: bool = False,
        include_internal: bool = True,
        include_autostart: bool = True,
        scan_terminal_proof: bool = False,
    ) -> None:
        """Reconcile all Manager-owned singleton services through one path."""

        if not self._service_supervision_allowed():
            return

        services: list[ManagedServiceSpec] = []
        internal_services: list[ManagedServiceSpec] = []
        autostart_services: list[ManagedServiceSpec] = []
        if (
            include_internal
            and self._task_monitor_enabled
            and self._queue_names["inbox"] == WEFT_SPAWN_REQUESTS_QUEUE
        ):
            # The heartbeat is a dependency of internal periodic services. Do not
            # run it as standalone background work when there is no dependent
            # service enabled.
            internal_services.append(self._heartbeat_service_spec())
            internal_services.append(self._task_monitor_service_spec())
        if include_autostart:
            autostart_services.extend(self._desired_autostart_services(force=force))
        services.extend(internal_services)
        services.extend(autostart_services)
        if not services:
            return

        desired_keys = {service.key for service in services}
        pending_keys: set[str] = set()
        internal_keys = {service.key for service in internal_services}
        if internal_keys:
            if self._internal_spawn_queue_attached():
                pending_keys.update(
                    self._pending_service_keys(
                        internal_keys,
                        queue_names=(self._queue_names["internal_inbox"],),
                    )
                )
            else:
                pending_keys.update(self._pending_service_keys(internal_keys))
        autostart_keys = {service.key for service in autostart_services}
        if autostart_keys:
            pending_keys.update(self._pending_service_keys(autostart_keys))
        tracked_by_key = {
            service.key: self._tracked_service_candidate(
                service.key,
                scan_terminal_proof=scan_terminal_proof,
            )
            for service in services
        }
        keys_needing_evidence: set[str] = set()
        for service in services:
            state = self._service_state(service.key)
            tracked = tracked_by_key.get(service.key)
            if tracked is not None and tracked.state == "live":
                if force or service.key in self._managed_service_duplicate_scan_pending:
                    keys_needing_evidence.add(service.key)
                continue
            if tracked is not None and tracked.state == "terminal":
                keys_needing_evidence.add(service.key)
                continue
            if state.spawn_pending and service.key in pending_keys:
                continue
            if service.key in pending_keys:
                continue
            if service.lifecycle == "once" and state.launched_once:
                continue
            keys_needing_evidence.add(service.key)

        self._emit_serve_log(
            "managed_service_reconcile",
            component="service",
            required_level="debug",
            desired_keys=sorted(desired_keys),
            pending_keys=sorted(pending_keys),
            keys_needing_evidence=sorted(keys_needing_evidence),
            include_internal=include_internal,
            include_autostart=include_autostart,
            force=force,
        )
        candidates_by_key = (
            self._observed_service_candidates_by_key(
                keys_needing_evidence,
                tracked_by_key=tracked_by_key,
                scan_terminal_proof=scan_terminal_proof,
            )
            if keys_needing_evidence
            else {}
        )
        for service in services:
            tracked = tracked_by_key.get(service.key)
            candidates = (
                candidates_by_key.get(service.key, [])
                if service.key in keys_needing_evidence
                else ([tracked] if tracked is not None else [])
            )
            self._tick_managed_service(
                service,
                force=force,
                pending_keys=pending_keys,
                candidates=candidates,
                scan_terminal_proof=scan_terminal_proof,
            )
            if service.key in keys_needing_evidence:
                if tracked is not None and tracked.state == "live":
                    self._managed_service_duplicate_scan_pending.discard(service.key)

    def _tick_internal_services(self, *, force: bool = False) -> None:
        """Ensure built-in Manager-owned services for this live manager."""

        self._reconcile_managed_services(
            force=force,
            include_internal=True,
            include_autostart=False,
            scan_terminal_proof=True,
        )

    def _tick_task_monitor(self, *, force: bool = False) -> None:
        """Ensure one supervised TaskMonitor child exists for this live manager."""

        self._reconcile_managed_services(
            force=force,
            include_internal=True,
            include_autostart=False,
            scan_terminal_proof=True,
        )

    # ------------------------------------------------------------------
    # Autostart handling
    # ------------------------------------------------------------------
    def _autostart_root_dir(self) -> Path | None:
        if self._autostart_dir:
            return self._autostart_dir.parent
        spec_context = getattr(self.taskspec.spec, "weft_context", None)
        if spec_context:
            return Path(spec_context) / get_weft_directory_name(self._config)
        return None

    def _autostart_context_root(self) -> Path | None:
        if self._autostart_dir:
            return self._autostart_dir.parent
        spec_context = getattr(self.taskspec.spec, "weft_context", None)
        if spec_context:
            return Path(spec_context)
        return None

    def _autostart_pipeline_context(self) -> PipelineCompilationContext | None:
        context_root = self._autostart_context_root()
        if context_root is None:
            return None
        broker_config = {
            key: value
            for key, value in self._config.items()
            if key.startswith("BROKER_")
        }
        return PipelineCompilationContext(
            root=context_root,
            broker_target=cast(BrokerTarget, self._db_path),
            broker_config=broker_config,
        )

    @staticmethod
    def _load_autostart_manifest(template_path: Path) -> dict[str, Any] | None:
        try:
            raw = template_path.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            logger.warning("Failed to read autostart manifest %s", template_path)
            return None

        if not isinstance(payload, dict):
            logger.warning(
                "Autostart manifest %s must contain a JSON object", template_path
            )
            return None
        return payload

    def _load_autostart_taskspec(self, name: str) -> dict[str, Any] | None:
        spec_root = self._autostart_root_dir()
        if spec_root is None:
            return None
        try:
            resolved = resolve_named_spec_from_root(
                spec_root,
                name,
                spec_type=SPEC_TYPE_TASK,
            )
        except (FileNotFoundError, ValueError):
            logger.warning("Failed to resolve stored task spec %s", name)
            return None
        return apply_bundle_root_to_taskspec_payload(
            dict(resolved.payload),
            resolved.bundle_root,
        )

    def _load_autostart_pipeline(self, name: str) -> tuple[dict[str, Any], Any] | None:
        spec_root = self._autostart_root_dir()
        context_root = self._autostart_context_root()
        pipeline_context = self._autostart_pipeline_context()
        if spec_root is None or context_root is None or pipeline_context is None:
            return None

        try:
            resolved = resolve_named_spec_from_root(
                spec_root,
                name,
                spec_type=SPEC_TYPE_PIPELINE,
            )
            pipeline_spec = load_pipeline_spec_payload(resolved.payload)
        except (FileNotFoundError, ValueError, ValidationError):
            logger.warning("Failed to resolve stored pipeline %s", name)
            return None

        def _load_pipeline_stage(task_name: str) -> dict[str, Any]:
            stage_resolved = resolve_named_spec_from_root(
                spec_root,
                task_name,
                spec_type=SPEC_TYPE_TASK,
            )
            return apply_bundle_root_to_taskspec_payload(
                dict(stage_resolved.payload),
                stage_resolved.bundle_root,
            )

        try:
            compiled = compile_linear_pipeline(
                pipeline_spec,
                context=pipeline_context,
                task_loader=_load_pipeline_stage,
                source_ref=str(resolved.path),
            )
        except (
            BrokerError,
            FileNotFoundError,
            OSError,
            RuntimeError,
            ValueError,
            ValidationError,
        ):
            logger.warning("Failed to compile stored pipeline %s", name, exc_info=True)
            return None
        return compiled.pipeline_taskspec.model_dump(mode="json"), (
            compiled.bootstrap_input_fallback
        )

    def _build_autostart_spawn_payload(
        self, manifest: dict[str, Any], source: str
    ) -> tuple[dict[str, Any], Any] | None:
        target = manifest.get("target")
        if not isinstance(target, dict):
            logger.warning("Autostart manifest %s missing target", source)
            return None
        target_type = target.get("type")
        target_name = target.get("name")
        if not isinstance(target_name, str) or not target_name:
            logger.warning("Autostart manifest %s missing target name", source)
            return None

        if target_type == "task":
            taskspec_payload = self._load_autostart_taskspec(target_name)
            if taskspec_payload is None:
                return None
            inbox_message: Any = WORK_ENVELOPE_START
        elif target_type == "pipeline":
            pipeline_payload = self._load_autostart_pipeline(target_name)
            if pipeline_payload is None:
                return None
            taskspec_payload, inbox_message = pipeline_payload
        else:
            logger.warning("Autostart manifest %s has invalid target type", source)
            return None

        candidate = copy.deepcopy(taskspec_payload)
        if target_type == "task":
            candidate.pop("tid", None)
        candidate.setdefault("version", "1.0")
        candidate.setdefault("name", target_name)
        candidate.setdefault("spec", {})
        candidate.setdefault("metadata", {})

        defaults = manifest.get("defaults")
        if isinstance(defaults, dict) and target_type == "task":
            spec_section = candidate.get("spec")
            if not isinstance(spec_section, dict):
                spec_section = {}
                candidate["spec"] = spec_section

            args = defaults.get("args")
            if isinstance(args, list):
                spec_section.setdefault("args", [])
                if isinstance(spec_section["args"], list):
                    spec_section["args"].extend(args)

            keyword_args = defaults.get("keyword_args")
            if isinstance(keyword_args, dict):
                spec_section.setdefault("keyword_args", {})
                if isinstance(spec_section["keyword_args"], dict):
                    spec_section["keyword_args"].update(keyword_args)

            env = defaults.get("env")
            if isinstance(env, dict):
                spec_section.setdefault("env", {})
                if isinstance(spec_section["env"], dict):
                    spec_section["env"].update(env)

        candidate["metadata"][INTERNAL_AUTOSTART_SOURCE_METADATA_KEY] = source
        candidate["metadata"][INTERNAL_AUTOSTART_ENABLED_METADATA_KEY] = True
        candidate = apply_service_metadata(
            candidate,
            key=source,
            lifecycle="ensure"
            if self._autostart_manifest_mode(manifest) == "ensure"
            else "once",
        )

        if isinstance(defaults, dict) and "input" in defaults:
            inbox_message = defaults.get("input")

        return candidate, inbox_message

    @staticmethod
    def _autostart_manifest_mode(manifest: Mapping[str, Any]) -> str:
        policy = manifest.get("policy")
        if not isinstance(policy, dict):
            return "once"
        mode = policy.get("mode", "once")
        return mode if isinstance(mode, str) else "once"

    @staticmethod
    def _autostart_manifest_source(path: Path) -> str:
        """Return the canonical service key for an autostart manifest path."""

        return str(path.resolve(strict=False))

    def _autostart_manifest_paths(self) -> list[Path]:
        directory = self._autostart_dir
        if not directory or not directory.exists():
            return []
        try:
            return sorted(path for path in directory.glob("*.json") if path.is_file())
        except OSError:
            logger.debug(
                "Failed to enumerate autostart manifests in %s",
                directory,
                exc_info=True,
            )
            return []

    def _autostart_ensure_obligation_pending(self) -> bool:
        """Return whether an ensure autostart manifest still requires supervision."""

        if not self._autostart_enabled or not self._autostart_dir:
            return False
        for manifest_path in self._autostart_manifest_paths():
            source = self._autostart_manifest_source(manifest_path)
            manifest = self._load_autostart_manifest(manifest_path)
            if manifest is None or self._autostart_manifest_mode(manifest) != "ensure":
                continue
            policy = manifest.get("policy")
            max_restarts_value = (
                policy.get("max_restarts") if isinstance(policy, dict) else None
            )
            max_restarts = (
                int(max_restarts_value)
                if isinstance(max_restarts_value, int)
                and not isinstance(max_restarts_value, bool)
                else None
            )
            state = self._autostart_state.get(source)
            if not isinstance(state, dict):
                return True
            if not bool(state.get("launched_once", False)):
                return True
            if max_restarts is None:
                return True
            restarts = state.get("restarts", 0)
            if isinstance(restarts, int) and not isinstance(restarts, bool):
                if restarts < max_restarts:
                    return True
        return False

    def _active_autostart_sources(self) -> set[str]:
        tracked_sources = {
            child.autostart_source
            for child in self._child_processes.values()
            if child.autostart_source and not self._child_has_exited(child)
        }
        manifest_sources = {
            self._autostart_manifest_source(path)
            for path in self._autostart_manifest_paths()
        }
        desired_sources = {source for source in tracked_sources if source is not None}
        desired_sources.update(manifest_sources)
        candidates_by_key = self._observed_service_candidates_by_key(desired_sources)
        durable_sources = {
            source
            for source, candidates in candidates_by_key.items()
            if select_canonical_live_candidate(candidates) is not None
        }
        return durable_sources | {source for source in tracked_sources if source}

    def _managed_pids_for_child(self, tid: str) -> set[int]:
        queue = self._queue(WEFT_TID_MAPPINGS_QUEUE)
        latest_payload: dict[str, Any] | None = None
        latest_timestamp = -1
        for payload, timestamp in iter_queue_json_entries(queue):
            if payload.get("full") != tid or timestamp < latest_timestamp:
                continue
            latest_payload = payload
            latest_timestamp = timestamp
        if latest_payload is None:
            return set()

        handle_payload = latest_payload.get("runtime_handle")
        if not isinstance(handle_payload, Mapping):
            return set()
        try:
            handle = RunnerHandle.from_dict(handle_payload)
        except ValueError:
            return set()
        return set(handle.scoped_host_pids())

    def _enqueue_autostart_request(
        self, payload: dict[str, Any], inbox_message: Any
    ) -> bool:
        spawn_payload = {
            "taskspec": payload,
            "inbox_message": inbox_message,
        }
        metadata = payload.get("metadata")
        service_key = service_key_from_metadata(metadata)
        if service_key is None and isinstance(metadata, dict):
            source = metadata.get(INTERNAL_AUTOSTART_SOURCE_METADATA_KEY)
            service_key = source if isinstance(source, str) and source else None
        if service_key is None:
            logger.warning("Autostart payload missing service key")
            return False
        lifecycle = (
            "ensure"
            if isinstance(metadata, dict)
            and metadata.get(INTERNAL_SERVICE_LIFECYCLE_METADATA_KEY) == "ensure"
            else "once"
        )
        return self._enqueue_managed_service_request(
            ManagedServiceSpec(
                key=service_key,
                lifecycle=cast(Literal["once", "ensure"], lifecycle),
                spawn_payload=spawn_payload,
                autostart_source=service_key,
            )
        )

    def _prune_autostart_state(self, manifest_sources: set[str]) -> None:
        stale_sources = [
            source for source in self._autostart_state if source not in manifest_sources
        ]
        for source in stale_sources:
            self._autostart_state.pop(source, None)
            self._managed_service_state.pop(source, None)
            self._autostart_launched.discard(source)

        stale_launched = {
            source
            for source in self._autostart_launched
            if source not in manifest_sources
        }
        for source in stale_launched:
            self._autostart_launched.discard(source)

    def _desired_autostart_services(
        self, *, force: bool = False
    ) -> list[ManagedServiceSpec]:
        """Return desired autostart services after manifest and policy checks."""

        if not self._autostart_enabled:
            return []
        now_ns = time.time_ns()
        if (
            not force
            and now_ns - self._autostart_last_scan_ns < self._autostart_scan_interval_ns
        ):
            return []
        self._autostart_last_scan_ns = now_ns

        directory = self._autostart_dir
        if not directory:
            return []
        if not directory.exists():
            self._prune_autostart_state(set())
            return []

        manifests = self._autostart_manifest_paths()
        manifest_sources = {self._autostart_manifest_source(path) for path in manifests}
        self._prune_autostart_state(manifest_sources)

        services: list[ManagedServiceSpec] = []
        for manifest_path in manifests:
            source = self._autostart_manifest_source(manifest_path)
            manifest = self._load_autostart_manifest(manifest_path)
            if manifest is None:
                continue

            policy = (
                manifest.get("policy")
                if isinstance(manifest.get("policy"), dict)
                else {}
            )
            mode = policy.get("mode", "once") if isinstance(policy, dict) else "once"
            state = self._autostart_state.setdefault(
                source,
                {"restarts": 0, "next_allowed_ns": 0, "launched_once": False},
            )
            state.setdefault("restarts", 0)
            state.setdefault("next_allowed_ns", 0)
            state.setdefault("launched_once", False)
            service_state = self._service_state(source)
            service_state.restarts = int(state["restarts"])
            service_state.next_allowed_ns = int(state["next_allowed_ns"])
            service_state.launched_once = bool(state["launched_once"])

            if mode not in {"once", "ensure"}:
                logger.warning("Unknown autostart policy mode %s for %s", mode, source)
                continue

            spawn_payload = self._build_autostart_spawn_payload(manifest, source)
            if spawn_payload is None:
                continue
            taskspec_payload, inbox_message = spawn_payload
            lifecycle = "ensure" if mode == "ensure" else "once"
            backoff = (
                policy.get("backoff_seconds") if isinstance(policy, dict) else None
            )
            restart_backoff_ns = (
                int(float(backoff) * 1_000_000_000)
                if isinstance(backoff, (int, float)) and backoff > 0
                else 0
            )
            max_restarts_value = (
                policy.get("max_restarts") if isinstance(policy, dict) else None
            )
            max_restarts = (
                int(max_restarts_value)
                if isinstance(max_restarts_value, int)
                and not isinstance(max_restarts_value, bool)
                else None
            )
            services.append(
                ManagedServiceSpec(
                    key=source,
                    lifecycle=cast(Literal["once", "ensure"], lifecycle),
                    spawn_payload={
                        "taskspec": taskspec_payload,
                        "inbox_message": inbox_message,
                    },
                    restart_backoff_ns=restart_backoff_ns,
                    max_restarts=max_restarts,
                    autostart_source=source,
                )
            )
        return services

    def _mark_autostart_enqueued(
        self,
        service: ManagedServiceSpec,
        *,
        now_ns: int,
        launched_before: bool,
    ) -> None:
        """Synchronize legacy autostart policy counters after enqueue."""

        source = service.autostart_source
        if source is None:
            return
        self._autostart_launched.add(source)
        state = self._autostart_state.setdefault(
            source,
            {"restarts": 0, "next_allowed_ns": 0, "launched_once": False},
        )
        state.setdefault("restarts", 0)
        state.setdefault("next_allowed_ns", 0)
        if launched_before:
            state["restarts"] = int(state.get("restarts", 0)) + 1
        state["launched_once"] = True
        if service.restart_backoff_ns > 0:
            multiplier = max(0, int(state["restarts"]))
            state["next_allowed_ns"] = now_ns + service.restart_backoff_ns * (
                2**multiplier
            )
        else:
            state["next_allowed_ns"] = 0
        service_state = self._service_state(source)
        service_state.restarts = int(state["restarts"])
        service_state.next_allowed_ns = int(state["next_allowed_ns"])
        service_state.launched_once = True
        if service_state.next_allowed_ns:
            self._schedule_autostart_rescan_at(service_state.next_allowed_ns)

    def _schedule_autostart_rescan_at(self, due_ns: int) -> None:
        """Adjust scan throttling so backoff expiry is observed promptly."""

        self._autostart_last_scan_ns = min(
            self._autostart_last_scan_ns,
            max(0, due_ns - self._autostart_scan_interval_ns),
        )

    def _tick_autostart(self, *, force: bool = False) -> None:
        """Reconcile autostart manifests through the shared service path."""

        self._reconcile_managed_services(
            force=force,
            include_internal=False,
            include_autostart=True,
        )

    def _managed_service_convergence_active_reasons(
        self,
        *,
        include_autostart: bool,
        internal_spawn_pending: bool | None = None,
        include_broker: bool = True,
    ) -> tuple[str, ...]:
        """Return why manager-owned service convergence is active."""

        reasons: list[str] = []
        if self._managed_internal_spawn_enqueued:
            reasons.append("internal_spawn_enqueued")
        if include_broker and internal_spawn_pending is None:
            internal_spawn_pending = self._internal_spawn_pending()
        if include_broker and internal_spawn_pending:
            reasons.append("internal_spawn_pending")
        if self._managed_service_duplicate_scan_pending:
            reasons.append("duplicate_scan_pending")
        internal_keys = {
            INTERNAL_SERVICE_KEY_HEARTBEAT,
            INTERNAL_SERVICE_KEY_TASK_MONITOR,
        }
        for service_key, state in self._managed_service_state.items():
            if state.spawn_pending or state.active_tid is None:
                if state.spawn_pending:
                    reasons.append("spawn_pending")
                if (
                    service_key in internal_keys
                    and self._task_monitor_enabled
                    and self._queue_names["inbox"] == WEFT_SPAWN_REQUESTS_QUEUE
                ):
                    reasons.append("missing_active_tid")
            if state.uncertain_attempts > 0:
                reasons.append("uncertain_attempts")
        if include_autostart and self._autostart_enabled and self._autostart_dir:
            if self._autostart_last_scan_ns == 0:
                reasons.append("autostart_scan_due")
            elif (
                time.time_ns() - self._autostart_last_scan_ns
                >= self._autostart_scan_interval_ns
            ):
                reasons.append("autostart_scan_due")
        return tuple(dict.fromkeys(reasons))

    def _managed_service_convergence_active(self, *, include_autostart: bool) -> bool:
        """Return whether manager-owned convergence has non-stable work to advance."""

        return bool(
            self._managed_service_convergence_active_reasons(
                include_autostart=include_autostart,
            )
        )

    def _managed_service_state_snapshot(self) -> dict[str, dict[str, Any]]:
        """Return a compact manager-local service state snapshot for diagnostics."""

        return {
            service_key: self._service_state_for_log(state)
            for service_key, state in self._managed_service_state.items()
        }

    @staticmethod
    def _managed_service_progress_reasons(
        *,
        before: Mapping[str, Mapping[str, Any]],
        after: Mapping[str, Mapping[str, Any]],
        child_exited: bool,
        service_request_enqueued: bool,
        internal_spawn_drained: bool,
    ) -> tuple[str, ...]:
        """Return coarse progress reasons for one convergence pass."""

        reasons: list[str] = []
        if child_exited:
            reasons.append("child_exited")
        if service_request_enqueued:
            reasons.append("service_request_enqueued")
        if internal_spawn_drained:
            reasons.append("internal_spawn_drained")

        for service_key in sorted(set(before) | set(after)):
            before_state = before.get(service_key, {})
            after_state = after.get(service_key, {})
            if before_state == after_state:
                continue
            if before_state.get("active_tid") != after_state.get("active_tid"):
                reasons.append("active_tid_changed")
            if before_state.get("spawn_pending") != after_state.get("spawn_pending"):
                reasons.append("spawn_pending_changed")
            uncertain_before = (
                before_state.get("uncertain_attempts"),
                before_state.get("uncertain_since_ns"),
                before_state.get("last_uncertain_reason"),
            )
            uncertain_after = (
                after_state.get("uncertain_attempts"),
                after_state.get("uncertain_since_ns"),
                after_state.get("last_uncertain_reason"),
            )
            if uncertain_before != uncertain_after:
                reasons.append("uncertain_state_changed")
            if (
                before_state.get("launched_once") != after_state.get("launched_once")
                or before_state.get("next_allowed_ns")
                != after_state.get("next_allowed_ns")
                or before_state.get("restarts") != after_state.get("restarts")
            ):
                reasons.append("service_state_changed")
        return tuple(dict.fromkeys(reasons))

    def _run_managed_service_convergence(
        self,
        *,
        include_autostart: bool = True,
        max_passes: int = 3,
        force: bool = False,
    ) -> None:
        """Advance Manager-owned work toward a fixed point.

        Each pass reaps locally tracked children, reconciles desired singleton
        service state, and drains the manager-owned internal spawn inbox. This
        keeps service and pipeline bootstrap convergence independent from public
        work queue traffic.

        Spec: [MA-1.4], [MA-1.6a], [MANAGER.15], [MANAGER.16]
        """

        now_ns = time.time_ns()
        local_active_reasons = self._managed_service_convergence_active_reasons(
            include_autostart=include_autostart,
            include_broker=False,
        )
        active = bool(local_active_reasons)
        throttled_reasons = local_active_reasons or ("stable_audit",)
        interval_seconds = (
            MANAGED_SERVICE_CONVERGENCE_INTERVAL_SECONDS
            if active
            else MANAGED_SERVICE_STABLE_AUDIT_INTERVAL_SECONDS
        )
        interval_ns = int(interval_seconds * 1_000_000_000)
        if (
            not force
            and not self._managed_internal_spawn_enqueued
            and self._last_managed_service_convergence_ns
            and now_ns - self._last_managed_service_convergence_ns < interval_ns
        ):
            fields = {
                "active": active,
                "active_reasons": list(throttled_reasons),
                "interval_seconds": interval_seconds,
                "include_autostart": include_autostart,
                "force": force,
                "skipped": True,
            }
            self._emit_serve_log_rate_limited(
                "managed_service_convergence",
                component="service",
                required_level="trace",
                key="managed_service_convergence_throttled",
                state=fields,
                log_fields=fields,
            )
            return
        self._last_managed_service_convergence_ns = now_ns
        internal_spawn_pending = self._internal_spawn_pending()
        internal_spawn_pending_for_pass = internal_spawn_pending
        active_reasons = self._managed_service_convergence_active_reasons(
            include_autostart=include_autostart,
            internal_spawn_pending=internal_spawn_pending,
        )
        active = bool(active_reasons)
        if not active_reasons:
            active_reasons = ("stable_audit",)
        interval_seconds = (
            MANAGED_SERVICE_CONVERGENCE_INTERVAL_SECONDS
            if active
            else MANAGED_SERVICE_STABLE_AUDIT_INTERVAL_SECONDS
        )

        for pass_index in range(max_passes):
            child_exited = self._cleanup_children()
            if self.should_stop or self._draining:
                return

            state_before = self._managed_service_state_snapshot()
            enqueued_before = self._managed_internal_spawn_enqueued
            self._reconcile_managed_services(include_autostart=include_autostart)
            service_request_enqueued = (
                self._managed_internal_spawn_enqueued and not enqueued_before
            )
            should_drain_internal = (
                self._managed_internal_spawn_enqueued or internal_spawn_pending_for_pass
            )
            self._managed_internal_spawn_enqueued = False
            drained = (
                self._drain_internal_spawn_requests() if should_drain_internal else 0
            )
            internal_spawn_pending_for_pass = (
                self._internal_spawn_pending()
                if drained >= MANAGER_INTERNAL_SPAWN_DRAIN_MAX_MESSAGES
                else False
            )
            state_after = self._managed_service_state_snapshot()
            progress_reasons = self._managed_service_progress_reasons(
                before=state_before,
                after=state_after,
                child_exited=child_exited,
                service_request_enqueued=service_request_enqueued,
                internal_spawn_drained=drained > 0,
            )
            self._emit_serve_log(
                "managed_service_convergence",
                component="service",
                required_level="debug",
                active=active,
                active_reasons=list(active_reasons),
                interval_seconds=interval_seconds,
                include_autostart=include_autostart,
                force=force,
                pass_index=pass_index,
                child_exited=child_exited,
                internal_drain_attempted=should_drain_internal,
                internal_drain_count=drained,
                progress=bool(progress_reasons),
                progress_reasons=list(progress_reasons),
                child_count=len(self._child_processes),
            )
            if drained:
                self._last_activity_ns = time.time_ns()

            if self.should_stop or self._draining:
                return
            if not child_exited and drained == 0:
                return
            include_autostart = False

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    def cleanup(self) -> None:
        self._terminate_children()
        self._unregister_manager()
        super().cleanup()

    @staticmethod
    def _timeout_until_ns(due_ns: int, *, now_ns: int) -> float:
        """Return seconds until a nanosecond deadline."""

        return max(0.0, (due_ns - now_ns) / 1_000_000_000)

    def _interval_timeout(
        self,
        last_ns: int,
        interval_seconds: float,
        *,
        now_ns: int,
    ) -> float:
        if last_ns <= 0:
            return 0.0
        interval_ns = int(interval_seconds * 1_000_000_000)
        return self._timeout_until_ns(last_ns + interval_ns, now_ns=now_ns)

    def next_wait_timeout(self) -> float | None:
        """Return the next manager due timer for the shared task loop."""

        now_ns = time.time_ns()
        timeouts: list[float] = []
        if self.should_stop or self._draining or self._pending_termination_signal:
            return 0.0
        if self._managed_internal_spawn_enqueued:
            return 0.0
        if self._stalled_control_retry_after_ns > 0:
            timeouts.append(
                self._timeout_until_ns(
                    self._stalled_control_retry_after_ns,
                    now_ns=now_ns,
                )
            )
        if self._user_work_children():
            timeouts.append(MANAGER_CHILD_EXIT_POLL_INTERVAL)

        local_active_reasons = self._managed_service_convergence_active_reasons(
            include_autostart=True,
            include_broker=False,
        )
        service_interval = (
            MANAGED_SERVICE_CONVERGENCE_INTERVAL_SECONDS
            if local_active_reasons
            else MANAGED_SERVICE_STABLE_AUDIT_INTERVAL_SECONDS
        )
        timeouts.append(
            self._interval_timeout(
                self._last_managed_service_convergence_ns,
                service_interval,
                now_ns=now_ns,
            )
        )
        timeouts.append(
            self._timeout_until_ns(
                self._last_leader_check_ns + self._leader_check_interval_ns,
                now_ns=now_ns,
            )
            if self._last_leader_check_ns > 0
            else 0.0
        )
        timeouts.append(
            self._interval_timeout(
                self._last_registry_heartbeat_ns,
                MANAGER_REGISTRY_HEARTBEAT_INTERVAL_SECONDS,
                now_ns=now_ns,
            )
        )
        if self._last_broker_probe_ns > 0:
            timeouts.append(
                self._timeout_until_ns(
                    self._last_broker_probe_ns + self._broker_probe_interval_ns,
                    now_ns=now_ns,
                )
            )
        if self._idle_timeout > 0:
            idle_timeout_ns = int(float(self._idle_timeout) * 1_000_000_000)
            timeouts.append(
                self._timeout_until_ns(
                    self._last_activity_ns + idle_timeout_ns,
                    now_ns=now_ns,
                )
            )
        if self._autostart_enabled and self._autostart_dir:
            if self._autostart_last_scan_ns <= 0:
                timeouts.append(0.0)
            else:
                timeouts.append(
                    self._timeout_until_ns(
                        self._autostart_last_scan_ns + self._autostart_scan_interval_ns,
                        now_ns=now_ns,
                    )
                )
        if self._manager_log_allows("info") and self._last_public_dispatch_stall_log_ns:
            interval_ns = int(
                MANAGER_DISPATCH_STALL_LOG_INTERVAL_SECONDS * 1_000_000_000
            )
            timeouts.append(
                self._timeout_until_ns(
                    self._last_public_dispatch_stall_log_ns + interval_ns,
                    now_ns=now_ns,
                )
            )
        return min(timeouts) if timeouts else None

    def wait_for_activity(self, timeout: float | None) -> None:
        """Wait for queue activity while preserving the manager due-time ceiling.

        Service supervision is driven by explicit due timers from
        ``next_wait_timeout()``. The SimpleBroker activity waiter can wake the
        manager early for queue work, but the supplied timeout remains the
        supervision ceiling.

        Spec: [MA-1.4], [MANAGER.15], [MANAGER.16]
        """

        super().wait_for_activity(timeout=timeout)

    def process_once(self) -> None:
        self._loop_iteration += 1
        self._child_launch_started_this_turn = False
        self._leader_probe_used_this_turn = False
        self._leader_actionable_work_cache = None
        self._leader_check_turn = None
        self._drain_worker_results()
        self._emit_manager_loop_summary()
        self._process_pending_termination_signal()
        if self.should_stop:
            self._drain_worker_results()
            return
        self._refresh_manager_registration()
        if self._draining:
            # Finish an in-flight drain before reevaluating leadership. Otherwise a
            # slow turn can re-enter the yield path and skip the corresponding
            # *_drained completion event once children are gone.
            self._continue_shutdown_drain()
            self._drain_worker_results()
            return
        if self._maybe_yield_leadership():
            self._drain_worker_results()
            return
        self._drain_control_queue_first()
        if self._draining:
            self._continue_shutdown_drain()
            self._drain_worker_results()
            return
        if self._cleanup_children():
            self._run_managed_service_convergence(
                include_autostart=False,
                force=True,
            )
            if self._draining:
                self._continue_shutdown_drain()
                self._drain_worker_results()
                return
        internal_drained = (
            0
            if self._has_active_child_launches()
            else self._drain_internal_spawn_requests()
        )
        if internal_drained:
            self._last_activity_ns = time.time_ns()
        if not self._has_active_child_launches():
            super().process_once()
        else:
            self._drain_worker_results()
            self._maybe_emit_poll_report()
        public_drained = (
            0
            if self._has_active_child_launches() or self._child_launch_started_this_turn
            else self._drain_public_spawn_requests()
        )
        if public_drained:
            self._last_activity_ns = time.time_ns()
        if self._draining:
            self._continue_shutdown_drain()
            self._drain_worker_results()
            return
        if self._maybe_yield_leadership():
            self._drain_worker_results()
            return
        self._run_managed_service_convergence()
        if self._draining:
            self._continue_shutdown_drain()
            self._drain_worker_results()
            return
        if self._maybe_yield_leadership():
            self._drain_worker_results()
            return
        self._drain_worker_results()
        if self._user_work_children():
            # Idle timeout applies only when the manager is actually idle. Active
            # child tasks are in-flight work, not inactivity.
            return
        if self._idle_timeout <= 0:
            return
        self._update_idle_activity_from_broker()
        now_ns = time.time_ns()
        idle_timeout_ns = int(float(self._idle_timeout) * 1_000_000_000)
        if self._manager_owned_work_pending():
            # Pending reserved spawn work is already claimed by this manager.
            # Keep the manager alive even when the row's timestamp is old; a
            # slow child launch is still active work, not idle time.
            self._last_activity_ns = time.time_ns()
            return
        if self._autostart_ensure_obligation_pending():
            # An ``ensure`` autostart manifest with restart budget remaining is a
            # supervision obligation, even between child exit and the next
            # scheduled scan/backoff tick.
            self._last_activity_ns = time.time_ns()
            return
        if now_ns - self._last_activity_ns < idle_timeout_ns:
            return
        # Re-check manager-owned queue activity without the periodic throttle before
        # deciding the manager is idle. Short manager lifetimes can otherwise race
        # with a freshly submitted spawn request that arrives between ordinary polls.
        self._update_idle_activity_from_broker(force=True)
        now_ns = time.time_ns()
        if now_ns - self._last_activity_ns >= idle_timeout_ns:
            if not self._idle_shutdown_logged:
                self.taskspec.mark_completed(return_code=0)
                self._report_state_change(event="manager_idle_shutdown")
                self._idle_shutdown_logged = True
            self.should_stop = True

    def _internal_spawn_pending(self) -> bool:
        """Return whether the manager-owned internal spawn inbox has work."""

        internal_inbox = self._queue_names.get("internal_inbox")
        if internal_inbox is None:
            return False
        try:
            return self._queue(internal_inbox).has_pending()
        except (BrokerError, OSError, RuntimeError):
            logger.debug(
                "Failed to inspect internal spawn inbox pending state",
                exc_info=True,
            )
            return False

    def _drain_internal_spawn_requests(self) -> int:
        """Drain manager-owned internal spawn work without consuming public work."""

        internal_inbox = self._queue_names.get("internal_inbox")
        if internal_inbox is None:
            return 0
        return self._drain_spawn_requests_from_queue(
            queue_name=internal_inbox,
            max_messages=MANAGER_INTERNAL_SPAWN_DRAIN_MAX_MESSAGES,
            event="internal_spawn_drain",
            component="service",
        )

    def _drain_public_spawn_requests(self) -> int:
        """Drain public spawn work using atomic broker reservation as authority."""

        public_inbox = self._queue_names.get("inbox")
        if public_inbox != WEFT_SPAWN_REQUESTS_QUEUE:
            return 0
        if self.should_stop or self._draining:
            return 0
        drained = self._drain_spawn_requests_from_queue(
            queue_name=public_inbox,
            max_messages=MANAGER_PUBLIC_SPAWN_DRAIN_MAX_MESSAGES,
            event="public_spawn_drain",
            component="spawn",
        )
        if drained > 0:
            self._last_public_spawn_drained_ns = time.time_ns()
        else:
            self._maybe_log_public_dispatch_stall(public_inbox)
        return drained

    def _maybe_log_public_dispatch_stall(self, public_inbox: str) -> None:
        if not self._manager_log_allows("info"):
            return
        try:
            backlog_pending = self._queue(public_inbox).has_pending()
        except (BrokerError, OSError, RuntimeError):
            backlog_pending = False
        if not backlog_pending:
            self._last_public_dispatch_stall_log_ns = 0
            return
        now_ns = time.time_ns()
        interval_ns = int(MANAGER_DISPATCH_STALL_LOG_INTERVAL_SECONDS * 1_000_000_000)
        if now_ns - self._last_public_dispatch_stall_log_ns < interval_ns:
            return
        self._last_public_dispatch_stall_log_ns = now_ns
        ownership = self._evaluate_dispatch_ownership()
        self._emit_serve_log(
            "public_dispatch_stalled",
            component="spawn",
            required_level="info",
            severity="warning",
            queue=public_inbox,
            ownership_state=ownership.state,
            leader_tid=ownership.leader_tid,
            child_count=len(self._child_processes),
            manager_reserved_pending=self._queue_pending_for_log(
                self._queue_names.get("reserved")
            ),
            last_public_spawn_drained_ns=self._last_public_spawn_drained_ns,
        )

    def _drain_spawn_requests_from_queue(
        self,
        *,
        queue_name: str,
        max_messages: int,
        event: str,
        component: str,
    ) -> int:
        """Drain one spawn queue without relying on pending-message hints.

        Queue pending checks and activity waiters are useful scheduling hints,
        but the atomic reservation attempt is the progress authority. A missed
        hint should at worst cost one extra empty reservation attempt, not leave
        accepted spawn work stranded.
        """

        total_processed = 0
        attempted = False
        for _ in range(max_messages):
            if (
                self._has_active_child_launches()
                or self._child_launch_started_this_turn
            ):
                break
            attempted = True
            processed = int(
                self._process_queue_message(queue_name, inactive_candidates=set())
            )
            total_processed += processed
            if processed == 0:
                break
            if (
                self._has_active_child_launches()
                or self._child_launch_started_this_turn
            ):
                break
            if self.should_stop or self._draining:
                break
        if total_processed >= max_messages:
            logger.warning(
                "Manager yielded after draining %s spawn messages from %s in one turn",
                max_messages,
                queue_name,
            )
        self._emit_serve_log(
            event,
            component=component,
            required_level="debug",
            queue=queue_name,
            attempted=attempted,
            processed=total_processed,
            max_messages=max_messages,
        )
        if total_processed > 0:
            self._strategy.notify_activity()
            self._invalidate_leadership_work_cache()
        return total_processed

    def _leadership_drain_leader_still_valid(self) -> bool:
        leader_tid = self._drain_leader_tid
        if leader_tid is None:
            return False
        active = self._active_dispatch_manager_records()
        if active is None:
            return False
        return canonical_owner_tid(active) == leader_tid

    def _resume_leadership_after_drain_loss(self) -> None:
        leader_tid = self._drain_leader_tid
        self._draining = False
        self._drain_signaled_children.clear()
        self._drain_signal_started_ns.clear()
        self._drain_started_ns = None
        self._drain_reason = None
        self._drain_completion_event = "manager_stop_drained"
        self._drain_stops_children = True
        self._drain_leader_tid = None
        self._unregistered = False
        self._unregistered_status = None
        self._registry_message_id = None
        self._last_registry_heartbeat_ns = 0
        self._register_manager()
        self._update_process_title("running")
        self._report_state_change(
            event="manager_leadership_resumed",
            leader_tid=leader_tid,
        )
        self._emit_serve_log(
            "manager_leadership_resumed",
            component="ownership",
            required_level="info",
            severity="warning",
            former_leader_tid=leader_tid,
        )

    def _revalidate_leadership_drain(self) -> bool:
        if self._drain_stops_children:
            return True
        now_ns = time.time_ns()
        interval_ns = int(MANAGER_LEADERSHIP_DRAIN_REVALIDATE_SECONDS * 1_000_000_000)
        if (
            self._last_leadership_drain_revalidate_ns
            and now_ns - self._last_leadership_drain_revalidate_ns < interval_ns
        ):
            return True
        self._last_leadership_drain_revalidate_ns = now_ns
        if self._leadership_drain_leader_still_valid():
            return True
        self._resume_leadership_after_drain_loss()
        return False

    def _continue_shutdown_drain(self) -> None:
        if not self._revalidate_leadership_drain():
            return
        if self._drain_stops_children:
            self._signal_children_to_stop()
        self._cleanup_children()
        if (
            self._drain_stops_children
            and self._child_processes
            and self._shutdown_drain_timed_out()
        ):
            logger.warning(
                "Manager %s drain timed out with %s child task(s); terminating",
                self.tid,
                len(self._child_processes),
            )
            self._terminate_children()
            self._cleanup_children()
        if not self._child_processes:
            self._finish_graceful_shutdown()

    def _shutdown_drain_timed_out(self) -> bool:
        if self._drain_started_ns is None:
            return False
        timeout_ns = int(MANAGER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS * 1_000_000_000)
        return time.time_ns() - self._drain_started_ns >= timeout_ns

    def _atexit_unregister(self) -> None:
        try:
            self._unregister_manager()
        except Exception:  # pragma: no cover - interpreter shutdown cleanup
            pass


__all__ = ["Manager"]
