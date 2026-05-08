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
import signal
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import ValidationError

from simplebroker import BrokerTarget, Queue
from simplebroker.ext import BrokerError
from weft._constants import (
    CONTROL_STOP,
    DEFAULT_FUNCTION_TARGET,
    INTERNAL_HEARTBEAT_ENDPOINT_NAME,
    INTERNAL_RUNTIME_ENDPOINT_NAME_KEY,
    INTERNAL_RUNTIME_ENVELOPE_ENDPOINT_NAME_KEY,
    INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY,
    INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT,
    INTERNAL_RUNTIME_TASK_CLASS_KEY,
    INTERNAL_RUNTIME_TASK_CLASS_PIPELINE,
    INTERNAL_RUNTIME_TASK_CLASS_PIPELINE_EDGE,
    INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR,
    INTERNAL_SERVICE_KEY_HEARTBEAT,
    INTERNAL_SERVICE_KEY_TASK_MONITOR,
    INTERNAL_SERVICE_LIFECYCLE_METADATA_KEY,
    MANAGED_SERVICE_PING_TIMEOUT_SECONDS,
    MANAGED_SERVICE_UNCERTAIN_RETRY_LIMIT,
    MANAGER_CHILD_EXIT_POLL_INTERVAL,
    MANAGER_DISPATCH_RECOVERY_MAX_ATTEMPTS,
    MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS,
    MANAGER_REGISTRY_HEARTBEAT_INTERVAL_SECONDS,
    MANAGER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS,
    MANAGER_SPAWN_FENCE_RECOVERY_EXHAUSTED_EVENT,
    MANAGER_SPAWN_FENCE_SUSPENDED_EVENT,
    MANAGER_SPAWN_FENCED_REQUEUED_EVENT,
    MANAGER_SPAWN_FENCED_STRANDED_EVENT,
    QUEUE_CTRL_IN_SUFFIX,
    QUEUE_CTRL_OUT_SUFFIX,
    SPEC_TYPE_PIPELINE,
    SPEC_TYPE_TASK,
    TERMINAL_ENVELOPE_TYPE,
    TERMINAL_TASK_STATUSES,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_MANAGER_LIFETIME_TIMEOUT,
    WEFT_MANAGER_RUNTIME_HANDLE_JSON_ENV,
    WEFT_MANAGERS_REGISTRY_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS_DEFAULT,
    WEFT_TID_MAPPINGS_QUEUE,
    WORK_ENVELOPE_START,
    WRAPPER_LOST_ERROR,
    get_weft_directory_name,
)
from weft.context import WeftContext, build_context
from weft.ext import RunnerHandle
from weft.helpers import (
    canonical_owner_tid,
    detect_container_runtime,
    handle_has_live_host_process,
    is_canonical_manager_record,
    iter_queue_json_entries,
    pid_is_live,
    process_create_time,
    redact_taskspec_dump,
    terminate_process_tree,
)

from .control_probe import send_keyed_ping_probe
from .launcher import launch_task_process
from .manager_services import (
    ManagedServiceSpec,
    ManagedServiceState,
    ServiceCandidate,
    apply_service_metadata,
    select_canonical_live_candidate,
    service_key_from_metadata,
    service_metadata,
)
from .pipelines import (
    PipelineCompilationContext,
    compile_linear_pipeline,
    load_pipeline_spec_payload,
)
from .spec_store import resolve_named_spec_from_root
from .tasks import Consumer
from .tasks.base import BaseTask, ControlRequest, QueueMessageContext, TaskControlPolicy
from .tasks.multiqueue_watcher import QueueMode
from .taskspec import (
    ReservedPolicy,
    TaskSpec,
    apply_bundle_root_to_taskspec_payload,
    resolve_taskspec_payload,
)

logger = logging.getLogger(__name__)

DispatchOwnershipState = Literal["self", "other", "none", "unknown"]
DispatchSuspensionState = Literal["other", "none", "unknown"]


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


@dataclass(frozen=True, slots=True)
class DispatchOwnership:
    """Current runtime-owned view of manager dispatch eligibility.

    Spec: [MA-1.4], [MF-6]
    """

    state: DispatchOwnershipState
    leader_tid: str | None = None


@dataclass(frozen=True, slots=True)
class DispatchSuspension:
    """Manager-owned dispatch suspension state for fenced reserved work.

    Spec: [MA-1.4], [MF-6]
    """

    ownership_state: DispatchSuspensionState
    child_tid: str
    message_id: int
    recovery_pending: bool
    leader_tid: str | None = None
    recovery_attempts: int = 0


@dataclass(frozen=True, slots=True)
class DispatchSuspensionRefresh:
    """Outcome of one dispatch-suspension refresh cycle.

    Spec: [MA-1.4], [MF-6]
    """

    ownership: DispatchOwnership
    halt_turn: bool = False


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
        self._registry_message_id: int | None = None
        self._draining = False
        self._drain_reason: str | None = None
        self._drain_completion_event = "manager_stop_drained"
        self._drain_stops_children = True
        self._drain_signaled_children: set[str] = set()
        self._drain_signal_started_ns: dict[str, int] = {}
        self._drain_started_ns: int | None = None
        self._pending_termination_signal: int | None = None
        self._dispatch_suspension: DispatchSuspension | None = None
        self._autostart_enabled = bool(self._config.get("WEFT_AUTOSTART_TASKS", True))
        autostart_dir = self._config.get("WEFT_AUTOSTART_DIR")
        self._autostart_dir = Path(autostart_dir) if autostart_dir else None
        self._autostart_launched: set[str] = set()
        self._managed_service_state: dict[str, ManagedServiceState] = {}
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
        self._leader_check_interval_ns = 100_000_000
        self._last_leader_check_ns = 0
        self._broker_activity_queue: Queue | None = None
        self._broker_probe_interval_ns = 1_000_000_000  # probe at most once per second
        self._last_broker_probe_ns = 0
        self._last_registry_heartbeat_ns = 0
        try:
            # Reuse any connected queue so watcher-driven updates also refresh last_ts.
            self._broker_activity_queue = self._get_connected_queue()
        except (BrokerError, OSError, RuntimeError):
            logger.debug("Failed to prime broker activity queue", exc_info=True)
        self._last_broker_timestamp = self._read_broker_timestamp(force=True)
        self._register_manager()
        if self._maybe_yield_leadership(force=True):
            return
        self.taskspec.mark_started(pid=multiprocessing.current_process().pid)
        self._update_process_title("spawning")
        self._report_state_change(event="task_spawning")
        self.taskspec.mark_running(pid=multiprocessing.current_process().pid)
        self._update_process_title("running")
        self._report_state_change(event="task_started")
        self._reconcile_managed_services(force=True)
        atexit.register(self._atexit_unregister)

    def _service_state(self, service_key: str) -> ManagedServiceState:
        """Return Manager-local mutable state for a supervised service key."""

        return self._managed_service_state.setdefault(
            service_key,
            ManagedServiceState(),
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
    def _build_queue_configs(self) -> dict[str, dict[str, Any]]:
        """Configure inbox reserve mode and control peek mode for the manager.

        Spec: [CC-2.2], [CC-2.5], [MA-1]
        """
        return {
            self._queue_names["inbox"]: self._reserve_queue_config(
                self._handle_work_message,
                reserved_queue=self._queue_names["reserved"],
            ),
            self._queue_names["ctrl_in"]: self._peek_queue_config(
                self._handle_control_message
            ),
            self._queue_names["reserved"]: self._peek_queue_config(
                self._handle_reserved_message
            ),
        }

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
        weft_context = getattr(self.taskspec.spec, "weft_context", None)
        if weft_context is not None:
            payload["weft_context"] = str(weft_context)
        return payload

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
    ) -> bool:
        """Seed child inbox, spawn process, and emit task_spawned event.

        Spec: [MF-1], [MF-6]
        """
        if self._draining or self.should_stop:
            logger.debug(
                "Skipping child launch for %s while manager %s is draining",
                child_spec.tid,
                self.tid,
            )
            return False

        inbox_name = child_spec.io.inputs.get("inbox")
        if inbox_message is not None and inbox_name:
            payload = (
                json.dumps(inbox_message)
                if not isinstance(inbox_message, str)
                else inbox_message
            )
            inbox_queue = Queue(
                inbox_name,
                db_path=self._db_path,
                persistent=True,
                config=self._config,
            )
            try:
                inbox_queue.write(payload)
            except (BrokerError, OSError, RuntimeError):
                logger.debug(
                    "Failed to seed inbox %s for child %s",
                    inbox_name,
                    child_spec.tid,
                    exc_info=True,
                )
            finally:
                try:
                    inbox_queue.close()
                except (BrokerError, OSError, RuntimeError):
                    logger.debug(
                        "Failed to close seeded inbox %s for child %s",
                        inbox_name,
                        child_spec.tid,
                        exc_info=True,
                    )

        child_spec.metadata.setdefault("parent_tid", self.tid)
        if autostart_source:
            child_spec.metadata.setdefault("autostart_source", autostart_source)
            child_spec.metadata.setdefault("autostart", True)

        try:
            task_cls = self._resolve_child_task_class(child_spec)
        except ValueError as exc:
            logger.warning("Rejecting child launch for %s: %s", child_spec.tid, exc)
            self._report_state_change(
                event="task_spawn_rejected",
                child_tid=child_spec.tid,
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

        process = launch_task_process(
            task_cls,
            self._db_path,
            child_spec,
            config=self._config,
        )
        assert child_spec.tid is not None
        self._child_processes[child_spec.tid] = ManagedChild(
            process=process,
            ctrl_queue=child_spec.io.control.get("ctrl_in"),
            ctrl_out_queue=child_spec.io.control.get("ctrl_out"),
            persistent=bool(getattr(child_spec.spec, "persistent", False)),
            autostart_source=autostart_source,
            internal_role=internal_role,
            service_key=service_key,
        )
        if service_key is not None:
            state = self._service_state(service_key)
            state.active_tid = child_spec.tid
            state.spawn_pending = False
            state.locally_terminal_tids.discard(child_spec.tid)
        self._last_activity_ns = time.time_ns()

        event_payload = {
            "child_tid": child_spec.tid,
            "child_taskspec": redact_taskspec_dump(
                child_spec.model_dump(mode="json"), self._taskspec_redaction_paths
            ),
        }
        if autostart_source:
            event_payload["autostart_source"] = autostart_source
        if service_key:
            event_payload["service_key"] = service_key

        self._report_state_change(event="task_spawned", **event_payload)
        return True

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
            from .tasks.task_monitor import TaskMonitorTask

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
        """Publish an active record to the manager registry (Spec: [MA-1.4], [MF-7])."""
        registry_queue = self._queue(WEFT_MANAGERS_REGISTRY_QUEUE)
        timestamp = registry_queue.generate_timestamp()
        runtime_handle = self._manager_runtime_handle()
        previous_message_id = self._registry_message_id

        payload = {
            "tid": self.tid,
            "name": self.taskspec.name,
            "capabilities": self.taskspec.metadata.get("capabilities", []),
            "status": "active",
            "runtime_handle": runtime_handle.to_dict(),
            "timestamp": timestamp,
            "inbox": self._queue_names["inbox"],
            "requests": self._queue_names["inbox"],
            "ctrl_in": self._queue_names["ctrl_in"],
            "ctrl_out": self._queue_names["ctrl_out"],
            "outbox": self._queue_names["outbox"],
            "role": self.taskspec.metadata.get("role", "manager"),
        }
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

    def _refresh_manager_registration(self) -> None:
        if self._unregistered or self.should_stop:
            return
        now_ns = time.time_ns()
        interval_ns = int(MANAGER_REGISTRY_HEARTBEAT_INTERVAL_SECONDS * 1_000_000_000)
        if now_ns - self._last_registry_heartbeat_ns < interval_ns:
            return
        self._register_manager()

    def _unregister_manager(self) -> None:
        """Replace active record with stopped record on shutdown (Spec: [MA-1.4])."""
        if self._unregistered:
            return
        registry_queue = self._queue(WEFT_MANAGERS_REGISTRY_QUEUE)
        stopped_timestamp = registry_queue.generate_timestamp()

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
                if payload.get("status") == "active":
                    try:
                        registry_queue.delete(message_id=latest_ts)
                    except (BrokerError, OSError, RuntimeError):
                        logger.debug(
                            "Failed to prune latest registry entry for %s",
                            self.tid,
                            exc_info=True,
                        )

        payload = {
            "tid": self.tid,
            "name": self.taskspec.name,
            "capabilities": self.taskspec.metadata.get("capabilities", []),
            "status": "stopped",
            "runtime_handle": self._manager_runtime_handle().to_dict(),
            "timestamp": stopped_timestamp,
            "inbox": self._queue_names["inbox"],
            "requests": self._queue_names["inbox"],
            "ctrl_in": self._queue_names["ctrl_in"],
            "ctrl_out": self._queue_names["ctrl_out"],
            "outbox": self._queue_names["outbox"],
            "role": self.taskspec.metadata.get("role", "manager"),
        }
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

        self._registry_message_id = None
        self._unregistered = True

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
                metadata={},
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
            metadata={},
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
            if payload.get("tid") == tid:
                latest = (payload, timestamp)
        return latest

    @staticmethod
    def _registry_entries_match(
        existing: Mapping[str, Any], candidate: Mapping[str, Any]
    ) -> bool:
        keys = {
            "tid",
            "status",
            "runtime_handle",
            "name",
            "capabilities",
            "inbox",
            "requests",
            "ctrl_in",
            "ctrl_out",
            "outbox",
            "role",
        }
        for key in keys:
            if existing.get(key) != candidate.get(key):
                return False
        return True

    @staticmethod
    def _pid_alive(pid: int | None) -> bool:
        return pid_is_live(pid)

    @staticmethod
    def _manager_record_is_live(record: Mapping[str, Any]) -> bool:
        payload = record.get("runtime_handle")
        if not isinstance(payload, Mapping):
            return False
        try:
            handle = RunnerHandle.from_dict(payload)
        except ValueError:
            return False
        if handle.control.get("authority") == "external-supervisor":
            if handle.scoped_host_processes():
                return handle_has_live_host_process(handle)
            timestamp = record.get("_timestamp")
            if not isinstance(timestamp, int):
                return True
            stale_after_ns = int(
                MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS * 1_000_000_000
            )
            return time.time_ns() - timestamp <= stale_after_ns
        if handle.control.get("authority") == "host-pid":
            return handle_has_live_host_process(handle)
        return True

    def _read_active_manager_records(self) -> dict[str, dict[str, Any]] | None:
        """Return the live canonical manager snapshot or ``None`` on read failure.

        Spec: [MA-1.4], [MA-3]
        """

        queue = self._queue(WEFT_MANAGERS_REGISTRY_QUEUE)
        snapshot: dict[str, dict[str, Any]] = {}
        stale_timestamps: list[int] = []

        try:
            raw_entries = queue.peek_generator(with_timestamps=True)
        except (BrokerError, OSError, RuntimeError):
            logger.debug("Failed to open manager registry replay", exc_info=True)
            return None

        try:
            for entry in raw_entries:
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
                tid = payload.get("tid")
                if not isinstance(tid, str) or not tid:
                    continue
                payload["_timestamp"] = timestamp
                existing = snapshot.get(tid)
                existing_timestamp = (
                    int(existing.get("_timestamp", -1)) if existing else -1
                )
                if existing is None or existing_timestamp < timestamp:
                    snapshot[tid] = payload
        except (BrokerError, OSError, RuntimeError):
            logger.debug("Failed to replay manager registry", exc_info=True)
            return None

        active: dict[str, dict[str, Any]] = {}
        for tid, record in snapshot.items():
            if record.get("status") != "active":
                continue
            if not is_canonical_manager_record(record):
                continue
            if not self._manager_record_is_live(record):
                stale_timestamp = record.get("_timestamp")
                if isinstance(stale_timestamp, int):
                    stale_timestamps.append(stale_timestamp)
                continue
            active[tid] = record

        for timestamp in stale_timestamps:
            try:
                queue.delete(message_id=timestamp)
            except (BrokerError, OSError, RuntimeError):
                logger.debug("Failed to prune stale manager record", exc_info=True)

        return active

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
            return DispatchOwnership(state="self", leader_tid=self.tid)

        active = self._read_active_manager_records()
        if active is None:
            return DispatchOwnership(state="unknown")

        leader_tid = canonical_owner_tid(active)
        if leader_tid is None:
            return DispatchOwnership(state="none")
        if leader_tid == self.tid and self.tid in active:
            return DispatchOwnership(state="self", leader_tid=leader_tid)
        return DispatchOwnership(state="other", leader_tid=leader_tid)

    def _refresh_dispatch_suspension(self) -> DispatchSuspensionRefresh | None:
        """Refresh manager-wide dispatch suspension, clearing it once owned again.

        Spec: [MA-1.4], [MF-6]
        """

        if self._dispatch_suspension is None:
            return None

        ownership = self._evaluate_dispatch_ownership()
        suspension = self._dispatch_suspension

        if ownership.state == "self":
            if not suspension.recovery_pending:
                self._dispatch_suspension = None
                return DispatchSuspensionRefresh(ownership=ownership)

            if self._reserved_spawn_request_missing(message_id=suspension.message_id):
                self._dispatch_suspension = None
                return DispatchSuspensionRefresh(ownership=ownership, halt_turn=True)

            requeued = self._requeue_reserved_spawn_request(
                message_id=suspension.message_id
            )
            if requeued:
                self._dispatch_suspension = None
            else:
                self._record_dispatch_recovery_failure(
                    suspension,
                    ownership=ownership,
                )
            return DispatchSuspensionRefresh(ownership=ownership, halt_turn=True)

        self._dispatch_suspension = DispatchSuspension(
            ownership_state=ownership.state,
            child_tid=suspension.child_tid,
            message_id=suspension.message_id,
            recovery_pending=suspension.recovery_pending,
            leader_tid=ownership.leader_tid,
            recovery_attempts=suspension.recovery_attempts,
        )

        if suspension.recovery_pending and ownership.state == "other":
            if self._reserved_spawn_request_missing(message_id=suspension.message_id):
                self._dispatch_suspension = DispatchSuspension(
                    ownership_state="other",
                    child_tid=suspension.child_tid,
                    message_id=suspension.message_id,
                    recovery_pending=False,
                    leader_tid=ownership.leader_tid,
                    recovery_attempts=suspension.recovery_attempts,
                )
            else:
                requeued = self._requeue_reserved_spawn_request(
                    message_id=suspension.message_id
                )
                if requeued:
                    self._dispatch_suspension = DispatchSuspension(
                        ownership_state="other",
                        child_tid=suspension.child_tid,
                        message_id=suspension.message_id,
                        recovery_pending=False,
                        leader_tid=ownership.leader_tid,
                        recovery_attempts=suspension.recovery_attempts,
                    )
                else:
                    self._record_dispatch_recovery_failure(
                        suspension,
                        ownership=ownership,
                    )

        return DispatchSuspensionRefresh(ownership=ownership)

    @property
    def _dispatch_suspended_state(self) -> DispatchSuspensionState | None:
        suspension = self._dispatch_suspension
        if suspension is None:
            return None
        return suspension.ownership_state

    def _dispatch_recovery_pending(self) -> bool:
        suspension = self._dispatch_suspension
        return bool(suspension is not None and suspension.recovery_pending)

    def _record_dispatch_recovery_failure(
        self,
        suspension: DispatchSuspension,
        *,
        ownership: DispatchOwnership,
    ) -> None:
        """Record one failed exact requeue attempt for fenced reserved work.

        Spec: [MF-6]
        """

        attempts = suspension.recovery_attempts + 1
        leader_tid = ownership.leader_tid or suspension.leader_tid
        if attempts >= MANAGER_DISPATCH_RECOVERY_MAX_ATTEMPTS:
            self._report_state_change(
                event=MANAGER_SPAWN_FENCE_RECOVERY_EXHAUSTED_EVENT,
                child_tid=suspension.child_tid,
                leader_tid=leader_tid,
                reserved_queue=self._queue_names["reserved"],
                message_id=suspension.message_id,
                ownership_state=ownership.state,
                attempts=attempts,
            )
            if ownership.state == "self":
                self._dispatch_suspension = None
                return
            self._dispatch_suspension = DispatchSuspension(
                ownership_state=ownership.state,
                child_tid=suspension.child_tid,
                message_id=suspension.message_id,
                recovery_pending=False,
                leader_tid=leader_tid,
                recovery_attempts=attempts,
            )
            return

        ownership_state = (
            "other" if ownership.state == "other" else suspension.ownership_state
        )
        self._dispatch_suspension = DispatchSuspension(
            ownership_state=ownership_state,
            child_tid=suspension.child_tid,
            message_id=suspension.message_id,
            recovery_pending=True,
            leader_tid=leader_tid,
            recovery_attempts=attempts,
        )

    def _reserved_spawn_request_missing(self, *, message_id: int) -> bool:
        try:
            return (
                self._get_reserved_queue().peek_one(exact_timestamp=message_id) is None
            )
        except (BrokerError, OSError, RuntimeError):
            logger.debug(
                "Failed to inspect reserved queue for fenced spawn request %s",
                message_id,
                exc_info=True,
            )
            return False

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
        if self._dispatch_recovery_pending():
            return False

        now_ns = time.time_ns()
        if (
            not force
            and now_ns - self._last_leader_check_ns < self._leader_check_interval_ns
        ):
            return self.should_stop
        self._last_leader_check_ns = now_ns

        leader_tid = self._leader_tid()
        if leader_tid is None or leader_tid == self.tid:
            return False

        if self._child_processes:
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
        if pid is not None and not self._pid_alive(pid):
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

    def _cleanup_children(self) -> None:
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
                service_key = child.service_key
                if service_key is None and child.internal_role == (
                    INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR
                ):
                    service_key = INTERNAL_SERVICE_KEY_TASK_MONITOR
                if service_key is None and child.internal_role == (
                    INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT
                ):
                    service_key = INTERNAL_SERVICE_KEY_HEARTBEAT
                if service_key is None and child.autostart_source:
                    service_key = child.autostart_source
                if service_key:
                    state = self._service_state(service_key)
                    state.locally_terminal_tids.add(tid)
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
        self._report_state_change(
            event="manager_leadership_yielded",
            leader_tid=leader_tid,
            draining=True,
        )
        self._update_process_title("draining")
        self._unregister_manager()

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

    def _send_stop_command(self, queue_name: str) -> None:
        queue = Queue(
            queue_name,
            db_path=self._db_path,
            persistent=True,
            config=self._config,
        )
        try:
            queue.write(CONTROL_STOP)
        except (BrokerError, OSError, RuntimeError):
            logger.debug("Failed to send STOP to %s", queue_name, exc_info=True)
        finally:
            try:
                queue.close()
            except (BrokerError, OSError, RuntimeError):
                logger.debug("Failed to close STOP queue %s", queue_name, exc_info=True)

    def _drain_control_queue_first(self) -> None:
        """Handle pending manager control messages before new spawn work.

        The manager owns the global spawn inbox. Once STOP is pending, it must
        stop consuming new spawn requests before launching additional children,
        otherwise cleanup can race with newly-created child tasks.
        """

        ctrl_name = self._queue_names["ctrl_in"]
        ctrl_queue = self._queue(ctrl_name)

        while True:
            pending = ctrl_queue.peek_one(with_timestamps=True)
            if pending is None:
                return

            if isinstance(pending, tuple) and len(pending) == 2:
                body, timestamp = pending
            else:  # pragma: no cover - defensive queue shape guard
                body, timestamp = pending, None

            if not isinstance(timestamp, int):
                return

            context = QueueMessageContext(
                queue_name=ctrl_name,
                queue=ctrl_queue,
                mode=QueueMode.PEEK,
                timestamp=timestamp,
            )
            self._handle_control_message(str(body), timestamp, context)
            if self._draining or self.should_stop:
                return

    def _control_allows_child_launch(self) -> bool:
        """Return whether spawn work may still launch a new child.

        STOP can arrive after a spawn request has been reserved but before the
        manager reaches ``_launch_child_task()``. Managers in drain mode must
        not start new child work from that in-flight reserved request.
        """

        if self._draining or self.should_stop or self._dispatch_suspension is not None:
            return False
        self._drain_control_queue_first()
        return (
            not self._draining
            and not self.should_stop
            and self._dispatch_suspension is None
        )

    def _read_broker_timestamp(self, *, force: bool = False) -> int:
        last_known = getattr(self, "_last_broker_timestamp", 0)
        now_ns = time.time_ns()
        if not force:
            last_probe = getattr(self, "_last_broker_probe_ns", 0)
            interval = getattr(self, "_broker_probe_interval_ns", 1_000_000_000)
            if now_ns - last_probe < interval:
                return last_known

        self._last_broker_probe_ns = now_ns

        queue = getattr(self, "_broker_activity_queue", None)
        if queue is None:
            try:
                queue = self._get_connected_queue()
            except (BrokerError, OSError, RuntimeError):
                logger.debug(
                    "Broker activity queue unavailable for idle tracking",
                    exc_info=True,
                )
                return last_known
            else:
                self._broker_activity_queue = queue

        try:
            if force and hasattr(queue, "refresh_last_ts"):
                candidate = queue.refresh_last_ts()
            else:
                candidate = queue.last_ts
        except (BrokerError, OSError, RuntimeError):
            logger.debug(
                "Failed to refresh broker activity timestamp",
                exc_info=True,
            )
            return last_known
        if candidate is None:
            return last_known

        if not isinstance(candidate, int):
            try:
                candidate = int(candidate)
            except (TypeError, ValueError):
                return last_known

        return candidate

    def _update_idle_activity_from_broker(self, *, force: bool = False) -> None:
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

    # ------------------------------------------------------------------
    # Message handling
    # ------------------------------------------------------------------
    def _requeue_reserved_spawn_request(self, *, message_id: int) -> bool:
        """Attempt to move one reserved spawn request back to the global inbox.

        Spec: [MF-6]
        """

        try:
            moved = self._get_reserved_queue().move_one(
                WEFT_SPAWN_REQUESTS_QUEUE,
                exact_timestamp=message_id,
                require_unclaimed=True,
                with_timestamps=False,
            )
        except (BrokerError, OSError, RuntimeError):
            logger.debug(
                "Failed to requeue reserved spawn request %s",
                message_id,
                exc_info=True,
            )
            return False
        return moved is not None

    def _apply_final_dispatch_fence(
        self,
        child_spec: TaskSpec,
        *,
        message_id: int,
    ) -> bool:
        """Apply the final ownership fence before a child launch side effect.

        Spec: [MA-1.1], [MA-1.4], [MF-6]
        """

        ownership = self._evaluate_dispatch_ownership()
        if ownership.state == "self":
            return True

        child_tid = child_spec.tid
        assert child_tid is not None
        reserved_queue = self._queue_names["reserved"]

        if ownership.state == "other":
            self._dispatch_suspension = DispatchSuspension(
                ownership_state="other",
                child_tid=child_tid,
                message_id=message_id,
                recovery_pending=True,
                leader_tid=ownership.leader_tid,
            )
            requeued = self._requeue_reserved_spawn_request(message_id=message_id)
            self._dispatch_suspension = DispatchSuspension(
                ownership_state="other",
                child_tid=child_tid,
                message_id=message_id,
                recovery_pending=not requeued,
                leader_tid=ownership.leader_tid,
            )
            event = (
                MANAGER_SPAWN_FENCED_REQUEUED_EVENT
                if requeued
                else MANAGER_SPAWN_FENCED_STRANDED_EVENT
            )
            self._report_state_change(
                event=event,
                child_tid=child_tid,
                leader_tid=ownership.leader_tid,
                reserved_queue=reserved_queue,
                message_id=message_id,
            )
            if not requeued:
                assert self._dispatch_suspension is not None
                self._record_dispatch_recovery_failure(
                    self._dispatch_suspension,
                    ownership=ownership,
                )
            if requeued:
                self._maybe_yield_leadership(force=True)
            return False

        self._dispatch_suspension = DispatchSuspension(
            ownership_state=cast(DispatchSuspensionState, ownership.state),
            child_tid=child_tid,
            message_id=message_id,
            recovery_pending=True,
        )
        self._report_state_change(
            event=MANAGER_SPAWN_FENCE_SUSPENDED_EVENT,
            child_tid=child_tid,
            reserved_queue=reserved_queue,
            message_id=message_id,
            ownership_state=ownership.state,
        )
        return False

    def _handle_work_message(
        self, message: str, timestamp: int, context: QueueMessageContext
    ) -> None:
        """Consume a spawn request, build child spec, and launch (Spec: [MA-1.1], [MA-2], [MF-6])."""
        self._cleanup_children()
        if not self._control_allows_child_launch():
            return

        try:
            payload = json.loads(message) if message else {}
        except json.JSONDecodeError:
            logger.warning("Manager received non-JSON spawn request: %s", message)
            policy = self.taskspec.spec.reserved_policy_on_error
            self._apply_reserved_policy(policy, message_timestamp=timestamp)
            if policy is not ReservedPolicy.KEEP:
                self._ensure_reserved_empty()
                self._cleanup_reserved_if_needed()
            return

        child_spec = self._build_child_spec(payload, timestamp)
        if child_spec is None:
            policy = self.taskspec.spec.reserved_policy_on_error
            self._apply_reserved_policy(policy, message_timestamp=timestamp)
            if policy is not ReservedPolicy.KEEP:
                self._ensure_reserved_empty()
                self._cleanup_reserved_if_needed()
            return

        inbox_message = payload.get("inbox_message", WORK_ENVELOPE_START)
        if not self._control_allows_child_launch():
            return
        if not self._apply_final_dispatch_fence(child_spec, message_id=timestamp):
            return

        launched = self._launch_child_task(
            child_spec,
            inbox_message,
            autostart_source=child_spec.metadata.get("autostart_source"),
        )
        if not launched:
            return

        try:
            self._get_reserved_queue().delete(message_id=timestamp)
        except (BrokerError, OSError, RuntimeError):
            logger.debug(
                "Failed to acknowledge manager message %s", timestamp, exc_info=True
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
            return None

        return child_spec

    # ------------------------------------------------------------------
    # Manager-owned service supervision
    # ------------------------------------------------------------------
    def _service_supervision_allowed(self) -> bool:
        """Return whether this manager may launch singleton services."""

        if self._draining or self.should_stop or self._dispatch_suspension is not None:
            return False
        return self._evaluate_dispatch_ownership().state == "self"

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

    def _enqueue_managed_service_request(self, service: ManagedServiceSpec) -> bool:
        """Enqueue one manager-owned service spawn request."""

        try:
            self._queue(self._queue_names["inbox"]).write(
                json.dumps(service.spawn_payload, ensure_ascii=False)
            )
        except (BrokerError, OSError, RuntimeError):
            logger.warning(
                "Failed to enqueue managed service %s", service.key, exc_info=True
            )
            return False
        state = self._service_state(service.key)
        state.spawn_pending = True
        state.launched_once = True
        return True

    def _enqueue_task_monitor_request(self) -> bool:
        """Enqueue one internal monitor spawn request on this manager inbox."""

        return self._enqueue_managed_service_request(self._task_monitor_service_spec())

    def _tracked_service_candidate(self, service_key: str) -> ServiceCandidate | None:
        for tid, child in list(self._child_processes.items()):
            child_service_key = child.service_key
            if child_service_key is None and child.internal_role == (
                INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR
            ):
                child_service_key = INTERNAL_SERVICE_KEY_TASK_MONITOR
            if child_service_key is None and child.internal_role == (
                INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT
            ):
                child_service_key = INTERNAL_SERVICE_KEY_HEARTBEAT
            if child_service_key is None and child.autostart_source:
                child_service_key = child.autostart_source
            if child_service_key != service_key:
                continue
            if self._child_has_exited(child):
                continue
            return ServiceCandidate(
                key=service_key,
                tid=tid,
                state="live",
                source="manager-child",
            )
        return None

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

    def _service_candidate_from_task_log(
        self,
        *,
        service_key: str,
        tid: str,
        payload: Mapping[str, Any],
        timestamp: int,
        runtime_handle: RunnerHandle | None = None,
    ) -> ServiceCandidate:
        status = payload.get("status")
        if isinstance(status, str) and status in TERMINAL_TASK_STATUSES:
            return ServiceCandidate(
                key=service_key,
                tid=tid,
                state="terminal",
                source="task-log",
                timestamp=timestamp,
                reason=status,
            )

        if runtime_handle is not None and handle_has_live_host_process(runtime_handle):
            return ServiceCandidate(
                key=service_key,
                tid=tid,
                state="live",
                source="tid-mapping",
                timestamp=timestamp,
            )

        ctrl_queues = self._service_candidate_control_queues(payload)
        if ctrl_queues is not None:
            ctrl_in_name, ctrl_out_name = ctrl_queues
            probe = send_keyed_ping_probe(
                self._manager_context(),
                tid=tid,
                ctrl_in_name=ctrl_in_name,
                ctrl_out_name=ctrl_out_name,
                timeout=MANAGED_SERVICE_PING_TIMEOUT_SECONDS,
            )
            if probe.matched is not None:
                return ServiceCandidate(
                    key=service_key,
                    tid=tid,
                    state="live",
                    source="control-pong",
                    timestamp=timestamp,
                )
            if probe.error:
                return ServiceCandidate(
                    key=service_key,
                    tid=tid,
                    state="uncertain",
                    source="control-pong",
                    timestamp=timestamp,
                    reason=probe.error,
                )

        return ServiceCandidate(
            key=service_key,
            tid=tid,
            state="terminal",
            source="task-log",
            timestamp=timestamp,
            reason="non-terminal state without live runtime proof or PONG",
        )

    @staticmethod
    def _trusted_internal_service_key(
        metadata: Mapping[str, Any],
        *,
        envelope_runtime_class: str | None = None,
    ) -> str | None:
        key = service_key_from_metadata(metadata)
        if key == INTERNAL_SERVICE_KEY_HEARTBEAT:
            if metadata.get("internal") is not True:
                return None
            if metadata.get("role") != "heartbeat_service":
                return None
            runtime_class = envelope_runtime_class or metadata.get(
                INTERNAL_RUNTIME_TASK_CLASS_KEY
            )
            if (
                runtime_class is not None
                and runtime_class != INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT
            ):
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
            runtime_class = envelope_runtime_class or metadata.get(
                INTERNAL_RUNTIME_TASK_CLASS_KEY
            )
            if (
                runtime_class is not None
                and runtime_class != INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR
            ):
                return None
            return key
        return None

    def _trusted_service_key_from_metadata(
        self,
        metadata: Any,
        *,
        desired_keys: set[str],
        envelope_runtime_class: str | None = None,
    ) -> str | None:
        """Return a trusted service key for Manager-owned evidence.

        Public task metadata is not enough to claim an internal singleton. For
        autostart services, evidence is considered only for currently desired
        manifest keys and only when the log entry carries autostart metadata.
        """

        if not isinstance(metadata, Mapping):
            return None

        internal_key = self._trusted_internal_service_key(
            metadata,
            envelope_runtime_class=envelope_runtime_class,
        )
        if internal_key is not None:
            return internal_key if internal_key in desired_keys else None

        key = service_key_from_metadata(metadata)
        source = metadata.get("autostart_source")
        if key is None and isinstance(source, str):
            key = source
        if not isinstance(key, str) or key not in desired_keys:
            return None
        if key in {INTERNAL_SERVICE_KEY_HEARTBEAT, INTERNAL_SERVICE_KEY_TASK_MONITOR}:
            return None
        if metadata.get("internal") is True:
            return None
        if metadata.get("autostart") is not True:
            return None
        if source != key:
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
            envelope_runtime_class=envelope_runtime_class
            if isinstance(envelope_runtime_class, str)
            else None,
        )

    def _pending_service_keys(self, desired_keys: set[str]) -> set[str]:
        """Return desired service keys that already have unconsumed spawn work."""

        if not desired_keys:
            return set()
        pending: set[str] = set()
        queue = self._queue(self._queue_names["inbox"])
        for payload, _timestamp in iter_queue_json_entries(queue):
            key = self._spawn_request_service_key(payload, desired_keys=desired_keys)
            if key is not None:
                pending.add(key)
        return pending

    @staticmethod
    def _service_candidate_control_queues(
        payload: Mapping[str, Any],
    ) -> tuple[str, str] | None:
        taskspec_dump = payload.get("taskspec")
        if not isinstance(taskspec_dump, dict):
            return None
        io_section = taskspec_dump.get("io")
        if not isinstance(io_section, dict):
            return None
        control = io_section.get("control")
        if not isinstance(control, dict):
            return None
        ctrl_in_name = control.get("ctrl_in")
        ctrl_out_name = control.get("ctrl_out")
        if not isinstance(ctrl_in_name, str) or not ctrl_in_name:
            return None
        if not isinstance(ctrl_out_name, str) or not ctrl_out_name:
            return None
        return ctrl_in_name, ctrl_out_name

    def _manager_context(self) -> WeftContext:
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

    def _observed_service_candidates_by_key(
        self, desired_keys: set[str]
    ) -> dict[str, list[ServiceCandidate]]:
        candidates_by_key: dict[str, list[ServiceCandidate]] = {
            key: [] for key in desired_keys
        }
        for service_key in desired_keys:
            tracked = self._tracked_service_candidate(service_key)
            if tracked is not None:
                candidates_by_key[service_key].append(tracked)

        latest_by_tid: dict[str, tuple[str, Mapping[str, Any], int]] = {}
        queue = self._queue(WEFT_GLOBAL_LOG_QUEUE)
        for payload, timestamp in iter_queue_json_entries(queue):
            tid = payload.get("tid")
            if not isinstance(tid, str) or not tid:
                continue
            taskspec_dump = payload.get("taskspec")
            if not isinstance(taskspec_dump, dict):
                continue
            metadata = taskspec_dump.get("metadata") or {}
            if not isinstance(metadata, dict):
                continue
            candidate_key = self._trusted_service_key_from_metadata(
                metadata,
                desired_keys=desired_keys,
            )
            if candidate_key is None:
                continue
            existing = latest_by_tid.get(tid)
            if existing is None or existing[2] <= timestamp:
                latest_by_tid[tid] = (candidate_key, payload, timestamp)

        runtime_handles = self._latest_tid_runtime_handles(set(latest_by_tid))
        for tid, (service_key, logged_payload, timestamp) in latest_by_tid.items():
            state = self._service_state(service_key)
            if tid in state.locally_terminal_tids:
                candidates_by_key.setdefault(service_key, []).append(
                    ServiceCandidate(
                        key=service_key,
                        tid=tid,
                        state="terminal",
                        source="manager-local",
                        timestamp=timestamp,
                        reason="locally reaped child process",
                    )
                )
                continue
            candidates_by_key.setdefault(service_key, []).append(
                self._service_candidate_from_task_log(
                    service_key=service_key,
                    tid=tid,
                    payload=logged_payload,
                    timestamp=timestamp,
                    runtime_handle=runtime_handles.get(tid),
                )
            )
        return candidates_by_key

    def _observed_service_candidates(self, service_key: str) -> list[ServiceCandidate]:
        return self._observed_service_candidates_by_key({service_key}).get(
            service_key, []
        )

    @staticmethod
    def _reset_service_uncertainty(state: ManagedServiceState) -> None:
        state.uncertain_attempts = 0
        state.uncertain_since_ns = None
        state.last_uncertain_reason = None

    def _service_has_owner_evidence(
        self,
        service_key: str,
        candidates: list[ServiceCandidate] | None = None,
    ) -> bool:
        if candidates is None:
            candidates = self._observed_service_candidates(service_key)
        live = select_canonical_live_candidate(candidates)
        state = self._service_state(service_key)
        if live is not None:
            state.active_tid = live.tid
            self._reset_service_uncertainty(state)
            return True
        uncertain = [
            candidate for candidate in candidates if candidate.state == "uncertain"
        ]
        if not uncertain:
            self._reset_service_uncertainty(state)
            return False
        state.uncertain_attempts += 1
        state.uncertain_since_ns = state.uncertain_since_ns or time.time_ns()
        state.last_uncertain_reason = uncertain[0].reason
        return state.uncertain_attempts < MANAGED_SERVICE_UNCERTAIN_RETRY_LIMIT

    def _tick_managed_service(
        self,
        service: ManagedServiceSpec,
        *,
        force: bool = False,
        pending_keys: set[str] | None = None,
        candidates: list[ServiceCandidate] | None = None,
    ) -> None:
        """Ensure a desired manager-owned service according to its lifecycle."""

        if not self._service_supervision_allowed():
            return

        pending_keys = pending_keys or set()
        candidates = candidates or []
        state = self._service_state(service.key)
        if self._tracked_service_candidate(service.key) is not None:
            self._reset_service_uncertainty(state)
            return
        if (
            state.active_tid is not None
            and state.active_tid in self._child_processes
            and not self._child_has_exited(self._child_processes[state.active_tid])
        ):
            return
        if state.spawn_pending and service.key not in pending_keys:
            state.spawn_pending = False
        if service.key in pending_keys:
            state.spawn_pending = True
            return
        if state.spawn_pending:
            return
        if self._service_has_owner_evidence(service.key, candidates):
            return
        if service.lifecycle == "once" and state.launched_once:
            return
        if (
            service.lifecycle == "ensure"
            and service.max_restarts is not None
            and state.launched_once
            and state.restarts >= service.max_restarts
        ):
            return

        now_ns = time.time_ns()
        if not force and state.next_allowed_ns and now_ns < state.next_allowed_ns:
            if service.autostart_source:
                self._schedule_autostart_rescan_at(state.next_allowed_ns)
            return
        launched_before = state.launched_once
        if not self._enqueue_managed_service_request(service):
            return
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
    ) -> None:
        """Reconcile all Manager-owned singleton services through one path."""

        if not self._service_supervision_allowed():
            return

        services: list[ManagedServiceSpec] = []
        if (
            include_internal
            and self._task_monitor_enabled
            and self._queue_names["inbox"] == WEFT_SPAWN_REQUESTS_QUEUE
        ):
            # The heartbeat is a dependency of internal periodic services. Do not
            # run it as standalone background work when there is no dependent
            # service enabled.
            services.append(self._heartbeat_service_spec())
            services.append(self._task_monitor_service_spec())
        if include_autostart:
            services.extend(self._desired_autostart_services(force=force))
        if not services:
            return

        desired_keys = {service.key for service in services}
        pending_keys = self._pending_service_keys(desired_keys)
        keys_needing_evidence: set[str] = set()
        for service in services:
            state = self._service_state(service.key)
            if self._tracked_service_candidate(service.key) is not None:
                continue
            if state.spawn_pending and service.key in pending_keys:
                continue
            if service.key in pending_keys:
                continue
            if service.lifecycle == "once" and state.launched_once:
                continue
            keys_needing_evidence.add(service.key)

        candidates_by_key = (
            self._observed_service_candidates_by_key(keys_needing_evidence)
            if keys_needing_evidence
            else {}
        )
        for service in services:
            self._tick_managed_service(
                service,
                force=force,
                pending_keys=pending_keys,
                candidates=candidates_by_key.get(service.key, []),
            )

    def _tick_internal_services(self, *, force: bool = False) -> None:
        """Ensure built-in Manager-owned services for the elected leader."""

        self._reconcile_managed_services(
            force=force,
            include_internal=True,
            include_autostart=False,
        )

    def _tick_task_monitor(self, *, force: bool = False) -> None:
        """Ensure one supervised TaskMonitor child exists for the leader manager."""

        self._reconcile_managed_services(
            force=force,
            include_internal=True,
            include_autostart=False,
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
        except (FileNotFoundError, ValueError, ValidationError):
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

        candidate["metadata"]["autostart_source"] = source
        candidate["metadata"]["autostart"] = True
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
            source = metadata.get("autostart_source")
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

            if mode == "once":
                if source in self._autostart_launched or service_state.launched_once:
                    continue
            elif mode == "ensure":
                launched_once = bool(state.get("launched_once", False))
                max_restarts = (
                    policy.get("max_restarts") if isinstance(policy, dict) else None
                )
                parsed_max_restarts = (
                    int(max_restarts)
                    if isinstance(max_restarts, int)
                    and not isinstance(max_restarts, bool)
                    else None
                )
                if (
                    launched_once
                    and parsed_max_restarts is not None
                    and int(state["restarts"]) >= parsed_max_restarts
                ):
                    continue
            else:
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

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    def cleanup(self) -> None:
        self._terminate_children()
        self._unregister_manager()
        super().cleanup()

    def process_once(self) -> None:
        self._process_pending_termination_signal()
        if self.should_stop:
            return
        self._refresh_manager_registration()
        if self._draining:
            # Finish an in-flight drain before reevaluating leadership. Otherwise a
            # slow turn can re-enter the yield path and skip the corresponding
            # *_drained completion event once children are gone.
            self._continue_shutdown_drain()
            return
        if self._maybe_yield_leadership():
            return
        self._drain_control_queue_first()
        if self._draining:
            self._continue_shutdown_drain()
            return
        refresh = self._refresh_dispatch_suspension()
        ownership = refresh.ownership if refresh is not None else None
        if refresh is not None and refresh.halt_turn:
            self._cleanup_children()
            return
        if ownership is not None and ownership.state == "other":
            if self._maybe_yield_leadership(force=True):
                return
        if self._dispatch_suspension is not None:
            self._cleanup_children()
            if self._user_work_children():
                self._last_activity_ns = max(self._last_activity_ns, time.time_ns())
                self._idle_shutdown_logged = False
            return
        super().process_once()
        if self._draining:
            self._continue_shutdown_drain()
            return
        if self._maybe_yield_leadership():
            return
        self._cleanup_children()
        self._reconcile_managed_services()
        self._update_idle_activity_from_broker()
        now_ns = time.time_ns()
        idle_timeout_ns = int(float(self._idle_timeout) * 1_000_000_000)
        if self._user_work_children():
            # Idle timeout applies only when the manager is actually idle. Active
            # child tasks are in-flight work, not inactivity.
            return
        if self._idle_timeout <= 0:
            return
        try:
            inbox_queue = self._queue(self._queue_names["inbox"])
            if inbox_queue.has_pending():
                self._last_activity_ns = time.time_ns()
                return
        except (BrokerError, OSError, RuntimeError):  # pragma: no cover - defensive
            logger.debug(
                "Failed to inspect inbox queue for pending work", exc_info=True
            )
        # Re-check broker activity without the periodic throttle before deciding the
        # manager is idle. Short manager lifetimes can otherwise race with a freshly
        # submitted spawn request that arrives between ordinary polls.
        self._update_idle_activity_from_broker(force=True)
        now_ns = time.time_ns()
        if now_ns - self._last_activity_ns >= idle_timeout_ns:
            if not self._idle_shutdown_logged:
                self.taskspec.mark_completed(return_code=0)
                self._report_state_change(event="manager_idle_shutdown")
                self._idle_shutdown_logged = True
            self.should_stop = True

    def _continue_shutdown_drain(self) -> None:
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
