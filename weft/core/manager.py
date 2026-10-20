"""Manager task implementation for spawning child tasks.

Spec references:
- docs/specifications/01-Core_Components.md [CC-2.2], [CC-2.3], [CC-2.5]
- docs/specifications/03-Manager_Architecture.md [MA-0], [MA-1], [MA-1.8], [MA-2], [MA-3]
- docs/specifications/07-System_Invariants.md [MANAGER.18]
- docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]
- docs/specifications/05-Message_Flow_and_State.md [MF-3], [MF-3.1]
"""

from __future__ import annotations

import atexit
import copy
import json
import logging
import multiprocessing
import os
import queue as thread_queue
import signal
import socket
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import cache, partial
from heapq import nsmallest
from itertools import islice
from multiprocessing.connection import Connection
from multiprocessing.connection import wait as wait_for_connections
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import ValidationError

from simplebroker import BrokerTarget, Queue
from simplebroker.ext import BrokerError, DatabaseError
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
    INTERNAL_RUNTIME_TASK_CLASS_LIVENESS_MONITOR,
    INTERNAL_RUNTIME_TASK_CLASS_PIPELINE,
    INTERNAL_RUNTIME_TASK_CLASS_PIPELINE_EDGE,
    INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR,
    INTERNAL_SERVICE_AUTHORITY_MANAGER,
    INTERNAL_SERVICE_AUTHORITY_METADATA_KEY,
    INTERNAL_SERVICE_KEY_HEARTBEAT,
    INTERNAL_SERVICE_KEY_LIVENESS_MONITOR,
    INTERNAL_SERVICE_KEY_TASK_MONITOR,
    INTERNAL_SERVICE_LIFECYCLE_METADATA_KEY,
    MANAGED_SERVICE_CONVERGENCE_INTERVAL_SECONDS,
    MANAGED_SERVICE_RECENT_EVIDENCE_GRACE_SECONDS,
    MANAGED_SERVICE_STABLE_AUDIT_INTERVAL_SECONDS,
    MANAGER_ADMISSION_RECHECK_SECONDS,
    MANAGER_ADMISSION_REFRESH_BATCH_SIZE,
    MANAGER_CHILD_INBOX_SEED_ATTEMPTS,
    MANAGER_CHILD_INBOX_SEED_RETRY_DELAY_BASE_SECONDS,
    MANAGER_CHILD_LAUNCH_WORKER_LANE,
    MANAGER_CHILD_SENTINEL_OBSERVER_CLOSE_TIMEOUT_SECONDS,
    MANAGER_CHILD_SENTINEL_WINDOWS_WAIT_SLICE_SECONDS,
    MANAGER_CHILD_STARTUP_LIVENESS_GRACE_SECONDS,
    MANAGER_CHILD_STOP_ESCALATION_SECONDS,
    MANAGER_CHILD_TERMINAL_PROOF_GRACE_SECONDS,
    MANAGER_CONTROL_DRAIN_MAX_MESSAGES,
    MANAGER_DISPATCH_STALL_LOG_INTERVAL_SECONDS,
    MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS,
    MANAGER_FAMILY_CLASSIFY_PAGE_SIZE,
    MANAGER_INTERNAL_SPAWN_DRAIN_MAX_MESSAGES,
    MANAGER_LEADERSHIP_CHECK_INTERVAL_SECONDS,
    MANAGER_LEADERSHIP_DRAIN_REVALIDATE_SECONDS,
    MANAGER_LEADERSHIP_PING_CACHE_TTL_SECONDS,
    MANAGER_PID_LIVENESS_RECHECK_INTERVAL,
    MANAGER_PUBLIC_SPAWN_DRAIN_MAX_MESSAGES,
    MANAGER_REGISTRY_HEARTBEAT_INTERVAL_SECONDS,
    MANAGER_SERVE_LOG_CHILD_LIMIT,
    MANAGER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS,
    MANAGER_STALE_INTERNAL_RESERVED_CLEANUP_INTERVAL_SECONDS,
    MANAGER_STALE_INTERNAL_RESERVED_CLEANUP_MESSAGE_LIMIT,
    MANAGER_STALE_INTERNAL_RESERVED_CLEANUP_QUEUE_LIMIT,
    MANAGER_STALLED_CONTROL_LOG_INTERVAL_SECONDS,
    MANAGER_STALLED_CONTROL_RETRY_SECONDS,
    MANAGER_STARTUP_FAST_PATH_WINDOW,
    QUEUE_CTRL_IN_SUFFIX,
    QUEUE_CTRL_OUT_SUFFIX,
    QUEUE_INTERNAL_RESERVED_SUFFIX,
    QUEUE_PRIORITY_INTERNAL,
    QUEUE_PRIORITY_NORMAL,
    SERVICE_STATUS_ACTIVE,
    SERVICE_STATUS_DRAINING,
    SERVICE_STATUS_STOPPED,
    SERVICE_STATUS_SUPERSEDED,
    SERVICE_STATUS_TERMINAL,
    SERVICE_TYPE_MANAGED,
    SERVICE_TYPE_MANAGER,
    SPEC_TYPE_PIPELINE,
    SPEC_TYPE_TASK,
    TASK_CLEANUP_TIMEOUT_SECONDS,
    TERMINAL_ENVELOPE_TYPE,
    TERMINAL_EVENT_WRITE_RETRIES,
    TERMINAL_EVENT_WRITE_RETRY_INTERVAL,
    TERMINAL_TASK_STATUSES,
    WEFT_FAMILY_PENDING_PREFIX,
    WEFT_FAMILY_PENDING_SCHEMA,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE,
    WEFT_MANAGER_LIFETIME_TIMEOUT,
    WEFT_MANAGER_OUTBOX_QUEUE,
    WEFT_MANAGER_RUNTIME_HANDLE_JSON_ENV,
    WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS_DEFAULT,
    WEFT_SERVICES_REGISTRY_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS_DEFAULT,
    WORK_ENVELOPE_START,
    WRAPPER_LOST_ERROR,
    get_weft_directory_name,
)
from weft.context import WeftContext
from weft.core.endpoints import (
    latest_tid_state_entries_for_endpoint_resolution,
)
from weft.core.manager_runtime import manager_registry_record_liveness
from weft.core.task_state import (
    latest_task_state_rows,
    read_task_state_snapshot,
    task_state_queue_name,
)
from weft.ext import RunnerHandle
from weft.helpers import (
    canonical_owner_tid,
    closing_queue_iterator,
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
from weft.liveness import analyze_liveness
from weft.liveness.policy import mapping_row_is_live

from .admission import (
    AdmissionController,
    AdmissionDecision,
    AdmissionLane,
    _startup_window_allows_launch,
)
from .control_messages import (
    ControlRequest,
    encode_control_message,
    parse_control_request,
)
from .control_probe import (
    coerce_pong_response,
    pong_proves_dispatch_eligible,
)
from .family_scheduling import FamilyScheduler, PendingRequest
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
from .runtime_identity import (
    RuntimeIdentity,
    canonical_runtime_context,
    runtime_identity_for_task,
    validate_submission_lineage,
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
    discard_v1_service_registry_rows,
    manager_service_key,
    parse_service_owner_record,
    project_manager_service_record,
    reduce_service_ownership,
)
from .spec_store import resolve_named_spec_from_root
from .tasks import Consumer
from .tasks.base import (
    BaseTask,
    QueueMessageContext,
    TaskControlPolicy,
    TaskWorkerResult,
)
from .tasks.multiqueue_watcher import QueueMode, QueueRuntimeConfig
from .tasks.service import (
    ServiceTask,
    ServiceWorkerContext,
    ServiceWorkerEvent,
    ServiceWorkerSpec,
)
from .taskspec import (
    ReservedPolicy,
    TaskSpec,
    decode_taskspec_transport_payload,
    encode_taskspec_transport_payload,
    validate_taskspec_payload,
)

if TYPE_CHECKING:
    from simplebroker_pg import ConnectionUsage


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
    source_queue: str | None = None
    reserved_queue: str | None = None
    message_timestamp: int | None = None
    launched_ns: int = 0
    last_liveness_probe_ns: int = 0
    terminal_proof_missing_since_ns: int = 0
    terminal_retry_after_ns: int = 0
    sentinel_observed_ns: int = 0


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
    runtime_identity: RuntimeIdentity | None = None
    submitter_tid: str | None = None


@dataclass(frozen=True, slots=True)
class _ManagerChildLaunchResult:
    """Result returned from the child-launch worker lane."""

    request: _ManagerChildLaunchRequest
    process: BaseProcess | None = None
    launched_ns: int = 0
    error: BaseException | None = None
    launch_started_at_ns: int = 0
    launch_elapsed_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class _ManagerPendingPongProbe:
    """Non-blocking manager-liveness PING awaiting a matching PONG."""

    tid: str
    row_timestamp: int | None
    request_id: str
    created_turn: int
    pong: dict[str, Any] | None = None


@dataclass(slots=True)
class _ManagerStartupFeedback:
    """Transient per-launch observation, separate from durable custody [MA-1.8]."""

    queue: Queue | None = None
    publication_id: int | None = None
    publication_pending: bool = False


@dataclass(frozen=True, slots=True)
class _ServicePendingPongProbe:
    """Non-blocking service-owner PING awaiting a matching PONG."""

    key: str
    service_key: str
    tid: str
    row_timestamp: int | None
    source: Literal["control-pong", "service-registry-pong"]
    request_id: str
    created_turn: int
    pong: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class _ManagerChildSentinelEvent:
    """One child-sentinel readiness event observed off the reactor thread."""

    tid: str
    sentinel: int


@dataclass(frozen=True, slots=True)
class _ManagerChildSentinelFailure:
    """Fatal observer failure delivered back to the Manager reactor."""

    error: BaseException


class _ManagerChildSentinelAdapter:
    """Bridge child process sentinels into the Manager's local wake source.

    The observer owns only immutable ``(tid, sentinel)`` identities. The
    Manager reactor remains the sole owner of Process objects, registry
    mutation, liveness checks, and reaping.

    Spec: docs/specifications/03-Manager_Architecture.md [MA-1.6a]
    """

    def __init__(self, notify_activity: Callable[[], None]) -> None:
        recv_conn, send_conn = multiprocessing.Pipe(duplex=False)
        self._recv_conn: Connection = recv_conn
        self._send_conn: Connection = send_conn
        self._notify_activity = notify_activity
        self._lock = threading.Lock()
        self._membership: dict[str, int] = {}
        self._suppressed: set[tuple[str, int]] = set()
        self._wake_token_pending = False
        self._stopping = False
        self._closed = False
        self._results: thread_queue.Queue[
            _ManagerChildSentinelEvent | _ManagerChildSentinelFailure
        ] = thread_queue.Queue()
        self._result_event = threading.Event()
        self._thread = threading.Thread(
            target=self._observe,
            name="weft-manager-child-sentinels",
            daemon=True,
        )
        self._thread.start()

    @staticmethod
    def _wait_for_ready(
        wake_connection: Connection,
        membership: Sequence[tuple[str, int]],
    ) -> set[Any]:
        """Wait for the adapter wake pipe or child sentinels.

        Windows limits each ``WaitForMultipleObjects`` call to 63 handles. The
        wake pipe is included in every chunk so membership changes and close
        requests can still interrupt a large child set.
        """

        sentinels = [sentinel for _tid, sentinel in membership]
        waitables: list[Any] = [wake_connection]
        waitables.extend(sentinels)
        if os.name != "nt" or len(waitables) <= 63:
            return set(wait_for_connections(waitables))

        chunk_size = 62
        while True:
            for index in range(0, len(sentinels), chunk_size):
                chunk: list[Any] = [wake_connection]
                chunk.extend(sentinels[index : index + chunk_size])
                ready = set(
                    wait_for_connections(
                        chunk,
                        MANAGER_CHILD_SENTINEL_WINDOWS_WAIT_SLICE_SECONDS,
                    )
                )
                if ready:
                    return ready

    def add(self, tid: str, sentinel: int) -> None:
        """Register one child identity, preserving identical exit suppression."""
        with self._lock:
            if self._stopping or self._closed:
                return
            previous = self._membership.get(tid)
            if previous == sentinel:
                return
            if previous is not None:
                self._suppressed.discard((tid, previous))
            self._membership[tid] = sentinel
            self._wake_observer_locked()

    def remove(self, tid: str) -> None:
        """Forget one child and invalidate any queued observation of its identity."""
        with self._lock:
            previous = self._membership.pop(tid, None)
            if previous is None:
                return
            self._suppressed.discard((tid, previous))
            if self._stopping or self._closed:
                return
            self._wake_observer_locked()

    def drain(
        self,
    ) -> list[_ManagerChildSentinelEvent | _ManagerChildSentinelFailure]:
        """Drain all observer results without blocking."""

        results: list[_ManagerChildSentinelEvent | _ManagerChildSentinelFailure] = []
        while True:
            try:
                results.append(self._results.get_nowait())
            except thread_queue.Empty:
                self._result_event.clear()
                try:
                    results.append(self._results.get_nowait())
                except thread_queue.Empty:
                    return results
                self._result_event.set()

    def wait(self, timeout: float) -> bool:
        """Wait for an observed sentinel or observer failure."""

        return self._result_event.wait(timeout=max(0.0, timeout))

    def close(self, *, timeout: float | None = None) -> None:
        """Stop the observer, close its pipe, and surface a fatal failure."""

        with self._lock:
            if self._closed:
                return
            self._stopping = True
            self._wake_observer_locked()
            thread = self._thread
        if thread is not threading.current_thread():
            thread.join(timeout=timeout)
            if thread.is_alive():
                raise RuntimeError("Manager child-sentinel observer did not stop")
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._recv_conn.close()
        self._send_conn.close()
        for result in self.drain():
            if isinstance(result, _ManagerChildSentinelFailure):
                raise result.error

    def _wake_observer_locked(self) -> None:
        if self._wake_token_pending or self._closed:
            return
        try:
            self._send_conn.send_bytes(b"w")
        except (BrokenPipeError, EOFError, OSError) as exc:
            self._publish_failure(exc)
            return
        self._wake_token_pending = True

    def _publish_failure(self, error: BaseException) -> None:
        self._results.put(_ManagerChildSentinelFailure(error))
        self._result_event.set()
        self._notify_activity()

    def _observe(self) -> None:
        try:
            while True:
                with self._lock:
                    if self._stopping:
                        return
                    membership = tuple(
                        item
                        for item in self._membership.items()
                        if item not in self._suppressed
                    )
                ready = self._wait_for_ready(self._recv_conn, membership)
                if self._recv_conn in ready:
                    while self._recv_conn.poll():
                        self._recv_conn.recv_bytes()
                    with self._lock:
                        self._wake_token_pending = False
                        if self._stopping:
                            return
                fired = [item for item in membership if item[1] in ready]
                if not fired:
                    continue
                with self._lock:
                    fired = [
                        (tid, sentinel)
                        for tid, sentinel in fired
                        if self._membership.get(tid) == sentinel
                    ]
                    self._suppressed.update(fired)
                for tid, sentinel in fired:
                    self._results.put(_ManagerChildSentinelEvent(tid, sentinel))
                self._result_event.set()
                self._notify_activity()
        except BaseException as exc:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-376] exception
            self._publish_failure(exc)


class Manager(ServiceTask):
    """Task that listens for spawn requests and runs child tasks.

    Spec: [MA-0], [MA-1], [MF-6], [MF-7]
    """

    # --- Section map ------------------------------------------------------
    # Section map only names ownership clusters. The spec mapping in
    # 03-Manager_Architecture.md is normative if these labels drift.
    #
    #   1. Bootstrap and queue binding       - see [MA-1.1], [MA-3]
    #   2. Spawn dispatch and reservation    - see [MA-1.1], [MA-1.7]
    #   3. Child launch and inbox seeding    - see [MA-1.2], [MA-1.3]
    #   4. Registry heartbeat and leadership - see [MA-1.4]
    #   5. Idle timing and reactor waits     - see [MA-1.5], [MA-1.6a]
    #   6. Autostart reconciliation          - see [MA-1.6]
    #   7. Managed internal services         - see [MA-1.6a]
    #   8. Control handling                  - see [MA-1.7]
    #   9. Shutdown and unregister           - see [MA-1.4], [MA-3]
    # ---------------------------------------------------------------------

    _allowed_reserved_policies = frozenset(ReservedPolicy)

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
        with self._initialization_scope():
            self._initialize_manager_runtime(taskspec)

    def _initialize_manager_runtime(self, taskspec: TaskSpec) -> None:
        """Initialize Manager-owned state and eager registry effects."""

        discard_v1_service_registry_rows(self._queue(WEFT_SERVICES_REGISTRY_QUEUE))
        self._register_service_worker(
            ServiceWorkerSpec(
                name=MANAGER_CHILD_LAUNCH_WORKER_LANE,
                target=self._run_child_launch_service_worker,
            )
        )
        self._startup_feedback: dict[str, _ManagerStartupFeedback] = {}
        self._startup_blocked = False
        self._child_processes: dict[str, ManagedChild] = {}
        self._child_sentinel_adapter = _ManagerChildSentinelAdapter(
            self._strategy.notify_activity
        )
        self._active_child_launches: dict[str, _ManagerChildLaunchRequest] = {}
        self._admission_max_connections = int(
            self._weft_config.get("ADMISSION_MAX_CONNECTIONS", 0)
        )
        self._admission_reserve_fraction = float(
            self._weft_config.get("ADMISSION_RESERVE_FRACTION", 0.1)
        )
        # A derived cache of existing task/Manager runtime evidence, not permits.
        self._scheduler_residents: dict[str, dict[str, Any]] = {}
        self._family_scheduler = FamilyScheduler()
        self._scheduler_checkpoint_dirty = False
        self._scheduler_undurable: set[str] = set()
        self._family_index_retry_after = 0.0
        self._family_classify_after = 0
        self._family_repair_after = 0
        self._family_repair_due = 0.0
        self._family_classification_pending = False
        self._family_invalid: dict[int, None] = {}
        self._family_candidate: PendingRequest | None = None
        self._family_label_names: deque[str] = deque()
        self._family_label_after = 0
        self._orphan_reserved: dict[str, tuple[str, int]] = {}
        self._admission_bootstrapped = False
        self._admission_refresh_after = 0.0
        self._admission_evidence_unavailable = False
        self._admission_observation: dict[str, int] = {}
        self._admission_blocked_lanes: set[AdmissionLane] = set()
        self._admission_retry_after_ns = 0
        self._admission_last_decision_state: tuple[str, str | None] = ("open", None)
        self._admission_probe_memo: dict[str, tuple[str, bool, float]] = {}
        self._child_launch_started_ns: dict[str, int] = {}
        self._child_launch_started_this_turn = False
        self._active_cleanup_deadline: float | None = None
        self._last_child_cleanup_survivors: tuple[dict[str, Any], ...] = ()
        self._idle_timeout: float = float(
            taskspec.metadata.get(
                "idle_timeout",
                self._weft_config.get(
                    "MANAGER_LIFETIME_TIMEOUT",
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
        self._atexit_callback: Callable[[], None] | None = None
        self._registry_message_id: int | None = None
        self._draining = False
        self._drain_reason: str | None = None
        self._drain_completion_event = "manager_stop_drained"
        self._drain_stops_children = True
        self._drain_leader_tid: str | None = None
        self._drain_signaled_children: set[str] = set()
        self._drain_escalated_children: set[str] = set()
        self._drain_signal_started_ns: dict[str, int] = {}
        self._drain_started_ns: int | None = None
        self._drain_immediate_work_pending = False
        self._stalled_control_message_id: int | None = None
        self._stalled_control_retry_after_ns = 0
        self._stalled_control_last_log_ns = 0
        self._loop_iteration = 0
        self._serve_log_last_emit_ns: dict[str, int] = {}
        self._serve_log_last_state: dict[str, str] = {}
        self._serve_log_runtime_handle_id = self._resolve_serve_log_runtime_handle_id()
        self._autostart_enabled = bool(self._weft_config.get("AUTOSTART_TASKS", True))
        autostart_dir = self._weft_config.get("AUTOSTART_DIR")
        self._autostart_dir = Path(autostart_dir) if autostart_dir else None
        self._autostart_sources: set[str] = set()
        self._managed_service_state: dict[str, ManagedServiceState] = {}
        self._managed_service_duplicate_scan_pending: set[str] = set()
        self._managed_internal_spawn_enqueued = False
        self._last_managed_service_convergence_ns = 0
        self._autostart_last_scan_ns = 0
        self._autostart_last_scan_complete = False
        self._autostart_scan_interval_ns = 1_000_000_000
        self._task_monitor_enabled = bool(
            self._weft_config.get("TASK_MONITOR_ENABLED", True)
        )
        self._liveness_monitor_enabled = bool(
            self._weft_config.get("LIVENESS_MONITOR_ENABLED", True)
        )
        self._admission_controller = AdmissionController(
            self._admission_max_connections,
            self._admission_reserve_fraction,
            self._liveness_monitor_enabled,
        )
        self._task_monitor_restart_backoff_ns = int(
            float(
                self._weft_config.get(
                    "TASK_MONITOR_RESTART_BACKOFF_SECONDS",
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
        self._last_stale_internal_reserved_cleanup_ns = 0
        self._broker_probe_interval_ns = 1_000_000_000  # probe at most once per second
        self._last_broker_probe_ns = 0
        self._last_registry_heartbeat_ns = 0
        if self._idle_timeout > 0:
            self._last_broker_timestamp = self._read_broker_timestamp(force=True)
        else:
            self._last_broker_timestamp = 0
            self._last_broker_probe_ns = 0
        self._manager_service_key = manager_service_key(self._manager_context())
        leader_probes_before = set(self._leader_probe_pending)
        self._register_manager()
        if self._maybe_yield_leadership(force=True):
            return
        if set(self._leader_probe_pending) - leader_probes_before:
            self._last_leader_check_ns = time.time_ns()
        self._activate_service_task(set_spawning_title=True)
        self._emit_manager_loop_summary(force=True)
        service_probes_before = set(self._service_probe_pending)
        self._reconcile_managed_services(force=True)
        if set(self._service_probe_pending) - service_probes_before:
            self._last_managed_service_convergence_ns = time.time_ns()
        self._managed_service_duplicate_scan_pending.clear()
        self._managed_internal_spawn_enqueued = False
        self._register_atexit_callback()

    def _service_state(self, service_key: str) -> ManagedServiceState:
        """Return Manager-local mutable state for a supervised service key."""

        return self._managed_service_state.setdefault(
            service_key,
            ManagedServiceState(),
        )

    def _manager_log_enabled(self) -> bool:
        """Return whether foreground manager operational logging is enabled."""

        return serve_log_level(self._weft_config) != "off"

    def _manager_log_allows(self, required_level: str) -> bool:
        """Return whether a foreground manager operational-log event is allowed."""

        return serve_log_allows(self._weft_config, required_level)

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
                config=self._weft_config,
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

    def _timed_manager_call[T](
        self, phase: str, operation: Callable[[], T], *, child_tid: str | None = None
    ) -> T:
        """Time existing work only at trace level; never publish queue state [MA-3].

        Spans may nest (for example checkpoint inside dispatch). Compare their
        wall-clock boundaries, not the sum of inclusive elapsed times. Time
        between spans remains visible rather than being called manager work.
        """
        if not self._manager_log_allows("trace"):
            return operation()
        started_at_ns = time.time_ns()
        started = time.perf_counter()
        try:
            return operation()
        finally:
            self._emit_manager_phase_timing(
                phase=phase,
                started_at_ns=started_at_ns,
                elapsed_seconds=time.perf_counter() - started,
                child_tid=child_tid,
            )

    def _emit_manager_phase_timing(
        self,
        *,
        phase: str,
        started_at_ns: int,
        elapsed_seconds: float,
        child_tid: str | None = None,
    ) -> None:
        """Publish one trace span from the reactor-owned Manager state."""

        if not self._manager_log_allows("trace"):
            return
        try:
            self._emit_serve_log(
                "manager_phase_timing",
                component="spawn",
                required_level="trace",
                phase=phase,
                started_at_ns=started_at_ns,
                elapsed_seconds=elapsed_seconds,
                public_inbox_active=(self._queue_names["inbox"] in self._active_queues),
                precheck_confirmed=self._pending_messages_precheck_confirmed,
                inactive_probe_due_in_seconds=max(
                    0.0, self._next_inactive_probe_at - time.monotonic()
                ),
                child_tid=child_tid,
                startup_feedback_tids=list(self._startup_feedback),
                startup_blocked=self._startup_dispatch_blocked(),
                candidate_request_id=(
                    str(self._family_candidate.request_id)
                    if self._family_candidate is not None
                    else None
                ),
                active_launches=len(self._active_child_launches),
                launch_started_this_turn=self._child_launch_started_this_turn,
                admission_blocked=bool(self._admission_blocked_lanes),
                undurable_launches=len(self._scheduler_undurable),
            )
        except Exception:  # pragma: no cover - diagnostics must not affect runtime
            logger.debug("Failed to emit manager phase timing", exc_info=True)

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
            self._weft_config.get(
                "MANAGER_SERVE_LOG_INTERVAL_SECONDS",
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
        for tid, child in nsmallest(
            MANAGER_SERVE_LOG_CHILD_LIMIT,
            self._child_processes.items(),
            key=lambda item: item[0],
        ):
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

    @property
    def _liveness_monitor_tid(self) -> str | None:
        return self._service_state(INTERNAL_SERVICE_KEY_LIVENESS_MONITOR).active_tid

    @_liveness_monitor_tid.setter
    def _liveness_monitor_tid(self, value: str | None) -> None:
        self._service_state(INTERNAL_SERVICE_KEY_LIVENESS_MONITOR).active_tid = value

    @property
    def _liveness_monitor_spawn_pending(self) -> bool:
        return self._service_state(INTERNAL_SERVICE_KEY_LIVENESS_MONITOR).spawn_pending

    @_liveness_monitor_spawn_pending.setter
    def _liveness_monitor_spawn_pending(self, value: bool) -> None:
        self._service_state(INTERNAL_SERVICE_KEY_LIVENESS_MONITOR).spawn_pending = value

    # ------------------------------------------------------------------
    # Queue configuration
    # ------------------------------------------------------------------
    def _reactor_queue_roles(self) -> dict[str, str]:
        """Declare Manager-owned watched and reserved reactor lanes.

        Spec: docs/specifications/07-System_Invariants.md [QUEUE.7]
        """

        roles = super()._reactor_queue_roles()
        # Blast radius: every fixed lane added to _build_queue_configs() must be
        # declared here before dict keys can collapse an existing handler.
        internal_inbox = self._queue_names.get("internal_inbox")
        internal_reserved = self._queue_names.get("internal_reserved")
        if internal_inbox is not None:
            roles["internal_inbox"] = internal_inbox
        if internal_reserved is not None:
            roles["internal_reserved"] = internal_reserved
        return roles

    def _reactor_support_routes(self) -> dict[str, str]:
        """Declare Manager's fixed service-registry route.

        Spec: docs/specifications/07-System_Invariants.md [QUEUE.7]
        """

        routes = super()._reactor_support_routes()
        routes["services_registry"] = WEFT_SERVICES_REGISTRY_QUEUE
        return routes

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
        for role in ("internal_inbox",):
            inbox = self._queue_names.get(role)
            if inbox is not None:
                queue_names.insert(0, inbox)
        return tuple(dict.fromkeys(queue_names))

    def _spawn_reserved_queue_pairs(self) -> tuple[tuple[str, str], ...]:
        pairs = [(self._queue_names["reserved"], self._queue_names["inbox"])]
        for prefix in ("internal",):
            reserved = self._queue_names.get(f"{prefix}_reserved")
            inbox = self._queue_names.get(f"{prefix}_inbox")
            if reserved is not None and inbox is not None:
                pairs.insert(0, (reserved, inbox))
        return tuple(dict.fromkeys(pairs))

    @staticmethod
    def _internal_reserved_queue_tid(queue_name: str) -> str | None:
        """Return the manager TID encoded in a ``T{tid}.internal_reserved`` name."""

        prefix = "T"
        suffix = f".{QUEUE_INTERNAL_RESERVED_SUFFIX}"
        if not queue_name.startswith(prefix) or not queue_name.endswith(suffix):
            return None
        tid = queue_name[len(prefix) : -len(suffix)]
        if not tid or not tid.isdigit():
            return None
        return tid

    def _idle_activity_queue_names(self) -> tuple[str, ...]:
        """Return manager-owned queues whose pending work should reset idle time."""

        queue_names = list(self._spawn_inbox_queue_names())
        queue_names.extend(
            reserved_queue
            for reserved_queue, _source in self._spawn_reserved_queue_pairs()
        )
        return tuple(dict.fromkeys(queue_names))

    def _manager_owned_work_pending(self) -> bool:
        """Launch worker activity is owned work; retained custody is maintenance."""
        if self._has_active_child_launches():
            return True
        for queue_name in self._spawn_inbox_queue_names():
            try:
                if self._queue_has_pending(self._queue(queue_name)):
                    return True
            except (BrokerError, OSError, RuntimeError):
                logger.debug("Unable to inspect manager-owned work", exc_info=True)
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

    def _queue_counts_as_wait_activity(self, config: QueueRuntimeConfig) -> bool:
        """Exclude reserved queues from ordinary wait/discovery activity."""

        reserved_names = {
            reserved_queue
            for reserved_queue, _source_queue in self._spawn_reserved_queue_pairs()
        }
        if config.name in reserved_names:
            return False
        lane = self._admission_lane_for_queue(config.name)
        if lane in getattr(self, "_admission_blocked_lanes", set()):
            return False
        return super()._queue_counts_as_wait_activity(config)

    def _build_tid_state_payload(self) -> dict[str, Any]:
        """Checkpoint unresolved launches and finite paths [MF-5], [MANAGER.11]."""
        payload = super()._build_tid_state_payload()
        payload["role"] = "manager"
        scheduler = getattr(self, "_family_scheduler", None)
        payload["scheduler_frontiers"] = {
            "stacks": [list(stack) for stack in scheduler.frontiers]
            if scheduler
            else [],
            "residents": copy.deepcopy(getattr(self, "_scheduler_residents", {})),
            "startup_feedback_tids": list(getattr(self, "_startup_feedback", {})),
            "source_queue": self._queue_names["inbox"],
            "reserved_queue": self._queue_names["reserved"],
        }
        return payload

    def _control_snapshot_fields(self) -> dict[str, Any]:
        """Include manager-selection fields in STATUS/PING control snapshots."""

        payload = super()._control_snapshot_fields()
        payload["should_stop"] = self.should_stop or self._draining
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
        payload["active_child_launches"] = [
            {
                "tid": request.child_spec.tid,
                "name": request.child_spec.name,
                "internal_role": request.internal_role,
                "service_key": request.service_key,
                "source_queue": request.source_queue,
                "reserved_queue": request.reserved_queue,
                "message_timestamp": request.message_timestamp,
                "started_ns": self._child_launch_started_ns.get(
                    str(request.child_spec.tid)
                ),
            }
            for request in self._active_child_launches.values()
        ]
        payload["worker_activity"] = self._worker_activity_snapshot()
        payload["service_workers"] = self._service_worker_snapshot()
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

    def _clear_active_child_launch(self, tid: str) -> _ManagerChildLaunchRequest | None:
        """Remove active child-launch bookkeeping for one TID."""

        self._child_launch_started_ns.pop(tid, None)
        self._clear_admission_retry()
        return self._active_child_launches.pop(tid, None)

    def _retry_stale_child_launches(self) -> bool:
        """Missing launch results are ambiguous; reconcile without duplicate spawn [MF-6]."""
        if not self._active_child_launches or self._has_worker_activity():
            return False
        now_ns = time.time_ns()
        recovered = False
        for tid in tuple(self._active_child_launches):
            started = self._child_launch_started_ns.get(tid, now_ns)
            if now_ns - started < int(
                MANAGER_CHILD_STARTUP_LIVENESS_GRACE_SECONDS * 1e9
            ):
                continue
            request = self._clear_active_child_launch(tid)
            if request is not None:
                self._scheduler_undurable.add(tid)
                if (
                    request.runtime_identity is not None
                    and request.runtime_identity.scheduling_class != "internal"
                ):
                    self._startup_feedback.setdefault(tid, _ManagerStartupFeedback())
            self._reconcile_launch_now(tid)
            logger.warning(
                "Launch result missing for %s; retained reservation requires reconciliation",
                tid,
            )
            recovered = True
        return recovered

    def _handle_control_command(
        self, request: ControlRequest, context: QueueMessageContext
    ) -> bool:
        """Override STOP to drain active children before manager exit."""

        if request.command == CONTROL_STOP:
            self._begin_graceful_shutdown(message_id=context.timestamp)
            response_extra: dict[str, Any] = {"draining": True}
            if request.request_id is not None:
                response_extra["request_id"] = request.request_id
            self._send_control_response("STOP", "ack", **response_extra)
            return True
        return super()._handle_control_command(request, context)

    def _handle_control_reply(
        self, message: str, timestamp: int, context: QueueMessageContext
    ) -> None:
        """Store one exactly matched PONG for the owning policy reducer.

        Reply dispatch remains an in-memory operation. The shared control
        handler owns acknowledgement of the source row.

        Spec: [MA-1.4], [MA-1.6a], [MF-6], [MANAGER.8], [MANAGER.15]
        """

        del timestamp, context
        matches: list[tuple[str, str, dict[str, Any]]] = []
        for key, leader_probe in self._leader_probe_pending.items():
            payload = coerce_pong_response(
                message,
                tid=leader_probe.tid,
                request_id=leader_probe.request_id,
            )
            if payload is not None:
                matches.append(("leader", key, payload))
        for key, service_probe in self._service_probe_pending.items():
            payload = coerce_pong_response(
                message,
                tid=service_probe.tid,
                request_id=service_probe.request_id,
            )
            if payload is not None:
                matches.append(("service", key, payload))
        if len(matches) != 1:
            return

        kind, key, payload = matches[0]
        if kind == "leader":
            stored_leader_probe = self._leader_probe_pending.get(key)
            if stored_leader_probe is not None:
                self._leader_probe_pending[key] = replace(
                    stored_leader_probe,
                    pong=payload,
                )
            return
        stored_service_probe = self._service_probe_pending.get(key)
        if stored_service_probe is not None:
            self._service_probe_pending[key] = replace(
                stored_service_probe,
                pong=payload,
            )

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
        submitter_tid: str | None = None,
        runtime_identity: RuntimeIdentity | None = None,
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
        identity = runtime_identity or runtime_identity_for_task(
            child_spec,
            self._db_path,
            internal=source_queue == self._queue_names.get("internal_inbox")
            or (source_queue is None and internal_role is not None),
            config=self._manager_context().config,
        )
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
            runtime_identity=identity,
            submitter_tid=submitter_tid,
        )
        self._active_child_launches[child_spec.tid] = request
        self._child_launch_started_ns[child_spec.tid] = time.time_ns()
        self._child_launch_started_this_turn = True
        try:
            self._timed_manager_call(
                "launch_checkpoint",
                lambda: self._checkpoint_admission_launch(request),
                child_tid=child_spec.tid,
            )
            self._start_service_worker(
                MANAGER_CHILD_LAUNCH_WORKER_LANE,
                request_id=child_spec.tid,
                initial_items=(request,),
            )
        except Exception as exc:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-346] exception
            self._clear_active_child_launch(child_spec.tid)
            self._handle_child_launch_failure(
                _ManagerChildLaunchResult(request=request, error=exc)
            )
            return False
        return True

    def _publish_scheduler_checkpoint(self) -> bool:
        """Publish all current obligations; retry failures without releasing custody."""
        written = self._timed_manager_call(
            "scheduler_checkpoint", self._register_tid_state
        )
        self._scheduler_checkpoint_dirty = not written
        if written:
            self._scheduler_undurable.difference_update(
                tid
                for tid, evidence in self._scheduler_residents.items()
                if evidence.get("host_processes")
            )
        return written

    def _checkpoint_admission_launch(self, request: _ManagerChildLaunchRequest) -> None:
        """Persist reservation custody before OS process creation [MANAGER.11]."""
        identity = request.runtime_identity
        assert identity is not None
        evidence: dict[str, Any] = {
            "context": identity.context,
            "root_tid": identity.root_tid,
            "submitter_tid": identity.submitter_tid,
            "ancestry_tids": list(identity.ancestry_tids),
            "scheduling_class": identity.scheduling_class,
            "connection_owner": identity.connection_owner,
            "hostname": socket.gethostname(),
        }
        if request.reserved_queue is not None and request.message_timestamp is not None:
            evidence.update(
                source_queue=request.source_queue,
                reserved_queue=request.reserved_queue,
                request_id=request.message_timestamp,
            )
        self._scheduler_residents[identity.tid] = evidence
        if identity.scheduling_class != "internal":
            self._startup_feedback.setdefault(identity.tid, _ManagerStartupFeedback())
        if not self._publish_scheduler_checkpoint():
            self._scheduler_residents.pop(identity.tid)
            self._release_startup_feedback(identity.tid)
            raise RuntimeError("unable to publish launch custody checkpoint")

    def _checkpoint_spawned_admission(
        self, result: _ManagerChildLaunchResult
    ) -> dict[str, Any]:
        """Retain exact process identity before startup feedback releases [MF-5]."""
        identity = result.request.runtime_identity
        assert identity is not None and result.process is not None
        evidence = self._scheduler_residents[identity.tid]
        pid = result.process.pid
        if isinstance(pid, int):
            processes = self._admission_host_processes(evidence)
            created = process_create_time(pid)
            if created is not None or pid not in processes:
                processes[pid] = {"pid": pid, "create_time": created}
            evidence["host_processes"] = list(processes.values())
        evidence["launched_ns"] = result.launched_ns or time.time_ns()
        self._scheduler_undurable.add(identity.tid)
        if identity.scheduling_class != "internal":
            self._startup_feedback.setdefault(identity.tid, _ManagerStartupFeedback())
        self._publish_scheduler_checkpoint()
        return {
            key: copy.deepcopy(value)
            for key, value in evidence.items()
            if key not in {"source_queue", "reserved_queue", "request_id"}
        }

    def _run_child_launch_service_worker(
        self,
        context: ServiceWorkerContext,
    ) -> _ManagerChildLaunchResult | None:
        """Run one queued child-launch request through the service-worker API."""

        for item in context.iter_items():
            if not isinstance(item, _ManagerChildLaunchRequest):
                raise TypeError("manager child-launch worker received invalid work")
            result = self._run_child_launch_worker(item)
            process = result.process
            if process is not None and (
                context.stop_requested() or self._active_cleanup_deadline is not None
            ):
                deadline = self._active_cleanup_deadline or time.monotonic()
                remaining = self._remaining_deadline(deadline)
                pid = process.pid
                if isinstance(pid, int) and pid > 0:
                    if remaining > 0:
                        # Split-budget KILL escalation; see _terminate_children.
                        terminate_process_tree(
                            pid,
                            timeout=remaining / 2.0,
                            kill_after=True,
                        )
                    else:
                        # The shared deadline forbids a new grace period, but
                        # already-visible descendants must not be abandoned
                        # with the late wrapper.
                        kill_process_tree(pid, timeout=0.0)
                try:
                    if process.is_alive():
                        process.kill()
                except (AssertionError, OSError, ValueError):
                    logger.debug(
                        "Failed to reap child launched during manager cleanup",
                        exc_info=True,
                    )
                return _ManagerChildLaunchResult(
                    request=item,
                    process=process,
                    error=RuntimeError("manager stopped while child launch completed"),
                )
            return result
        return None

    def _run_child_launch_worker(
        self,
        request: _ManagerChildLaunchRequest,
    ) -> _ManagerChildLaunchResult:
        """Launch the child process without touching broker queues."""

        started_at_ns = time.time_ns()
        started = time.perf_counter()
        try:
            process = launch_task_process(
                request.task_cls,
                self._db_path,
                request.child_spec,
                config=self._weft_config,
                detach_stdio=request.detach_stdio,
                runtime_identity=request.runtime_identity,
            )
        except BaseException as exc:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-347] exception
            return _ManagerChildLaunchResult(
                request=request,
                error=exc,
                launch_started_at_ns=started_at_ns,
                launch_elapsed_seconds=time.perf_counter() - started,
            )
        return _ManagerChildLaunchResult(
            request=request,
            process=process,
            launched_ns=time.time_ns(),
            launch_started_at_ns=started_at_ns,
            launch_elapsed_seconds=time.perf_counter() - started,
        )

    def _register_launched_frontier(self, identity: RuntimeIdentity) -> None:
        """Late results preserve adopted paths and released startup lineage [MF-5]."""
        evidence = self._scheduler_residents.get(identity.tid, {})
        if evidence.get("full") == identity.tid and (
            identity.scheduling_class != "finite"
            or not self._scheduler_evidence_live(identity.tid, evidence)
        ):
            return
        self._family_scheduler.launched(
            PendingRequest(
                int(identity.tid),
                identity.root_tid or identity.tid,
                identity.submitter_tid,
                identity.ancestry_tids,
            ),
            identity.scheduling_class,
        )

    def _child_launch_already_committed(self, tid: str, process: BaseProcess) -> bool:
        """Recognize one replayed local result without replacing process custody."""
        existing = self._child_processes.get(tid)
        if existing is None:
            return False
        if existing.process is not process:
            raise RuntimeError("child launch result conflicts with its owned process")
        return True

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
        if self._child_launch_already_committed(child_spec.tid, process):
            return
        self._child_processes[child_spec.tid] = ManagedChild(
            process=process,
            ctrl_queue=child_spec.io.control.get("ctrl_in"),
            ctrl_out_queue=child_spec.io.control.get("ctrl_out"),
            persistent=bool(getattr(child_spec.spec, "persistent", False)),
            autostart_source=request.autostart_source,
            internal_role=request.internal_role,
            service_key=request.service_key,
            source_queue=request.source_queue,
            reserved_queue=request.reserved_queue,
            message_timestamp=request.message_timestamp,
            launched_ns=result.launched_ns or time.time_ns(),
        )
        identity = request.runtime_identity
        assert identity is not None
        self._register_launched_frontier(identity)
        self._family_candidate = None
        self._register_child_sentinel(
            child_spec.tid, self._child_processes[child_spec.tid]
        )
        self._clear_admission_retry()
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
            if state.pending_request_id == request.message_timestamp:
                state.pending_request_id = None
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

        event_payload["submitter_tid"] = request.submitter_tid
        event_payload.update(self._checkpoint_spawned_admission(result))
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
        if request.runtime_identity is not None:
            if result.process is None:
                tid = str(child_spec.tid)
                self._scheduler_residents.pop(tid, None)
                self._scheduler_undurable.discard(tid)
                self._release_startup_feedback(tid)
                self._register_tid_state()
            else:
                self._checkpoint_spawned_admission(result)
        self._schedule_admission_retry_for_source(request.source_queue)
        logger.warning("Child launch failed for %s: %s", child_spec.tid, error)
        self._report_state_change(
            event="task_spawn_rejected",
            child_tid=child_spec.tid,
            error=str(error),
            reason="launch_worker_failed",
        )
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
        if policy is ReservedPolicy.CLEAR and request.service_key is not None:
            state = self._service_state(request.service_key)
            if state.pending_request_id == request.message_timestamp:
                try:
                    row = self._queue(request.reserved_queue).peek_one(
                        exact_timestamp=request.message_timestamp
                    )
                except (BrokerError, OSError, RuntimeError):
                    logger.debug(
                        "Failed to confirm rejected spawn disposition", exc_info=True
                    )
                else:
                    if row is None:
                        state.pending_request_id = None

    def _handle_child_launch_result(self, result: _ManagerChildLaunchResult) -> None:
        """Commit launch, preserving reservation until task-state handoff [MANAGER.11]."""
        tid = result.request.child_spec.tid
        if result.launch_started_at_ns > 0:
            self._emit_manager_phase_timing(
                phase="process_launch",
                started_at_ns=result.launch_started_at_ns,
                elapsed_seconds=result.launch_elapsed_seconds,
                child_tid=tid,
            )
        if tid is not None:
            self._clear_active_child_launch(tid)
        self._child_launch_started_this_turn = True
        if result.error is not None:
            self._handle_child_launch_failure(result)
            return
        self._commit_child_launch_success(result)
        self._reconcile_launch_now(str(tid))
        self._resume_startup_dispatch()

    def _startup_dispatch_blocked(self) -> bool:
        """Readiness only; a free slot never supplies resource authority [MA-1.8]."""
        if not self._startup_feedback:
            return False
        limit = (
            MANAGER_STARTUP_FAST_PATH_WINDOW
            if self._admission_backend_name() == "postgres"
            else 1
        )
        return self._startup_blocked or len(self._startup_feedback) >= limit

    def _resume_startup_dispatch(self) -> None:
        """Make known work eligible for a fresh check and wake the next turn."""
        self._clear_admission_retry()
        if not self._startup_dispatch_blocked():
            self._mark_queue_active(self._queue_names["inbox"])
            # A receipt used this turn's launch allowance. Its original worker
            # wake may already be consumed; readiness needs its own local wake.
            self._strategy.notify_activity()

    def _release_startup_feedback(self, tid: str) -> None:
        """Release only this launch's transient observation, never its custody."""
        if tid not in self._startup_feedback:
            return
        self._clear_startup_notification_interest(tid)
        self._startup_feedback.pop(tid)
        self._resume_startup_dispatch()

    def _clear_startup_notification_interest(self, tid: str | None = None) -> None:
        """Close one or all reactor-owned startup observation handles [MA-1.8]."""
        feedback = getattr(self, "_startup_feedback", {})
        records = tuple(feedback.values()) if tid is None else (feedback[tid],)
        for record in records:
            queue, record.queue = record.queue, None
            record.publication_id = None
            record.publication_pending = False
            if queue is not None:
                queue.close()

    def _activity_wait_queues(self) -> list[Queue]:
        """Include startup observation without adding a delivery route [MA-1.8]."""
        queues = super()._activity_wait_queues()
        for tid, record in getattr(self, "_startup_feedback", {}).items():
            if tid in self._active_child_launches:
                continue
            if record.queue is None:
                record.queue = self._manager_context().queue(task_state_queue_name(tid))
            queues.append(record.queue)
        return queues

    def _has_pending_observation(self) -> bool:
        """Read a publication cursor on the reactor, never consume task state.

        The waiter installs interest before this check. A rejected publication
        advances only the cursor, retaining interest in subsequent evidence.
        Spec: docs/specifications/03-Manager_Architecture.md [MA-1.8]
        """
        records = getattr(self, "_startup_feedback", {})
        if any(record.publication_pending for record in records.values()):
            return True
        interested = [(tid, record) for tid, record in records.items() if record.queue]
        if not interested:
            return False
        try:
            with self._get_connected_queue().get_connection() as broker:
                for tid, record in interested:
                    rows = broker.peek_many(
                        task_state_queue_name(tid),
                        limit=1,
                        order="newest",
                        with_timestamps=True,
                        after_timestamp=record.publication_id,
                    )
                    if rows:
                        record.publication_id = int(rows[0][1])
                        record.publication_pending = True
                        return True
        except (BrokerError, OSError, RuntimeError, ValueError):
            logger.debug("Startup publication observation deferred", exc_info=True)
            return False
        return False

    def _handle_service_worker_event(self, event: ServiceWorkerEvent) -> None:
        """Route child-launch service-worker events on the manager reactor."""

        if event.name != MANAGER_CHILD_LAUNCH_WORKER_LANE:
            super()._handle_service_worker_event(event)
            return
        if event.kind in {"started", "stopped"}:
            return
        if event.kind == "error":
            request = next(iter(self._active_child_launches.values()), None)
            if request is None:
                return
            self._handle_child_launch_result(
                _ManagerChildLaunchResult(
                    request=request,
                    error=event.error
                    or RuntimeError("manager child launch worker failed"),
                )
            )
            return
        if event.kind != "result":
            return
        if not isinstance(event.value, _ManagerChildLaunchResult):
            request = next(iter(self._active_child_launches.values()), None)
            if request is None:
                return
            self._handle_child_launch_result(
                _ManagerChildLaunchResult(
                    request=request,
                    error=RuntimeError(
                        "manager child launch worker returned invalid result"
                    ),
                )
            )
            return
        self._handle_child_launch_result(event.value)

    def _handle_worker_result(self, result: TaskWorkerResult) -> None:
        if isinstance(result.value, ServiceWorkerEvent):
            super()._handle_worker_result(result)
            return
        if result.lane == MANAGER_CHILD_LAUNCH_WORKER_LANE:
            if result.error is not None:
                raise RuntimeError(
                    "Manager child launch worker failed"
                ) from result.error
            if not isinstance(result.value, _ManagerChildLaunchResult):
                super()._handle_worker_result(result)
                return
            self._handle_child_launch_result(result.value)
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
        for attempt in range(MANAGER_CHILD_INBOX_SEED_ATTEMPTS):
            try:
                with self._get_connected_queue().get_connection() as broker:
                    broker.write(inbox_name, payload)
                return True
            except (BrokerError, OSError, RuntimeError) as exc:
                last_error = exc
                if attempt < MANAGER_CHILD_INBOX_SEED_ATTEMPTS - 1:
                    # The constant documents why child-launch inbox writes use backoff.
                    time.sleep(
                        MANAGER_CHILD_INBOX_SEED_RETRY_DELAY_BASE_SECONDS
                        * (attempt + 1)
                    )

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
            from .monitor.task_monitor import TaskMonitor

            return TaskMonitor
        if runtime_class == INTERNAL_RUNTIME_TASK_CLASS_LIVENESS_MONITOR:
            from .tasks.liveness_monitor import LivenessMonitor

            return LivenessMonitor
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
            INTERNAL_RUNTIME_TASK_CLASS_LIVENESS_MONITOR,
        }

    def _user_work_children(self) -> dict[str, ManagedChild]:
        """Return children that should block idle shutdown and leadership yield."""

        return {
            tid: child
            for tid, child in self._child_processes.items()
            if not self._child_is_supervision_only(child)
        }

    def _admission_enabled(self) -> bool:
        """Return whether backend-specific admission is enabled."""

        return (
            self._admission_max_connections > 0
            or self._admission_backend_name() == "postgres"
        )

    def _admission_lane_for_queue(self, queue_name: str) -> AdmissionLane | None:
        """Map one Manager spawn source to its admission lane."""

        if queue_name == self._queue_names.get("internal_inbox"):
            return "internal"
        if queue_name == self._queue_names.get("inbox"):
            return "public"
        return None

    @staticmethod
    def _admission_state(
        blocked_lanes: set[AdmissionLane],
        *,
        unavailable: bool = False,
    ) -> str:
        """Return the compact private admission transition state."""

        if unavailable:
            return "unavailable"
        if "internal" in blocked_lanes:
            return "all_paused"
        if "public" in blocked_lanes:
            return "public_paused"
        return "open"

    def _admission_backend_name(self) -> str:
        """Return the active broker backend used for admission observation."""

        return str(self._get_connected_queue().backend_name)

    def _observe_admission_usage(self) -> int | None:
        """Measure global actual PG pressure or SQLite live/unknown TIDs [MA-1.8]."""
        if self._admission_backend_name() == "postgres":
            self._admission_observation.clear()
            try:
                if self._admission_max_connections == 0:
                    stats = self._read_postgres_connection_stats()
                    self._admission_controller.set_max_connections(
                        stats["max_connections"]
                    )
                usage = self._read_postgres_connection_usage()
                self._admission_observation = {"observed": usage["total"]}
                return usage["total"]
            except (BrokerError, DatabaseError, OSError, RuntimeError, ValueError):
                return None
        try:
            with self._get_connected_queue().get_connection() as broker:
                mappings = latest_tid_state_entries_for_endpoint_resolution(
                    self._task_context(), broker=broker
                )
            live_tids = {
                tid
                for tid, payload in mappings.items()
                if self._admission_mapping_is_live(tid, payload)
            }
            live_tids.update(self._active_child_launches)
            live_tids.update(self._child_processes)
            live_tids.update(self._scheduler_residents)
            return len(live_tids)
        except (BrokerError, OSError, RuntimeError):
            return None

    def _adopt_scheduler_evidence(self, tid: str, evidence: Mapping[str, Any]) -> None:
        """Merge immutable lineage and scoped wrapper/worker evidence [OBS.6]."""
        if evidence.get("root_tid") is None or evidence.get("role") == "manager":
            return
        identity = RuntimeIdentity(
            tid=tid,
            context=evidence["context"],
            backend_name=self._admission_backend_name(),
            root_tid=evidence["root_tid"],
            submitter_tid=evidence.get("submitter_tid"),
            ancestry_tids=tuple(evidence["ancestry_tids"]),
            scheduling_class=evidence["scheduling_class"],
            connection_owner=evidence["connection_owner"],
        )
        if identity.context != canonical_runtime_context(self._db_path):
            raise ValueError("scheduler evidence belongs to another context")
        previous = self._scheduler_residents.get(tid, {})
        for key in (
            "root_tid",
            "submitter_tid",
            "ancestry_tids",
            "scheduling_class",
            "connection_owner",
            "context",
        ):
            if key in previous and previous[key] != evidence.get(key):
                raise ValueError("task scheduling identity changed")
        merged = {**previous, **evidence}
        processes = self._admission_host_processes(previous)
        processes.update(self._admission_host_processes(evidence))
        handle_payload = evidence.get("runtime_handle")
        if isinstance(handle_payload, Mapping):
            handle = RunnerHandle.from_dict(handle_payload)
            for pid, created in handle.scoped_host_processes():
                processes[pid] = {"pid": pid, "create_time": created}
        merged["host_processes"] = list(processes.values())
        if processes:
            merged.setdefault("launched_ns", time.time_ns())
        self._scheduler_residents[tid] = merged
        if merged["host_processes"] != previous.get("host_processes"):
            self._scheduler_checkpoint_dirty = True
        if (
            self._admission_bootstrapped
            and processes
            and identity.scheduling_class == "finite"
            and tid not in self._active_child_launches
            and tid not in self._family_scheduler.resident_tids
            and self._scheduler_evidence_live(tid, merged)
        ):
            self._family_scheduler.recover(
                tid, identity.root_tid or tid, identity.ancestry_tids
            )
            self._family_candidate = None

    def _scheduler_evidence_live(self, tid: str, evidence: Mapping[str, Any]) -> bool:
        """Unknown identity remains resident; only scoped staleness permits pop."""
        processes = evidence.get("host_processes")
        if not processes or evidence.get("hostname") != socket.gethostname():
            return True
        handle = RunnerHandle(
            runner="host",
            kind="process",
            id=str(processes[0]["pid"]),
            control={"authority": "host-pid"},
            observations={"host_processes": processes},
        )
        return (
            analyze_liveness(tid, {"runtime_handle": handle.to_dict()}).evidence
            != "stale"
        )

    def _snapshot_hands_off_launch(
        self, evidence: Mapping[str, Any], payload: Mapping[str, Any]
    ) -> bool:
        """Require self-contained task lineage and exact process identity [MANAGER.11]."""
        for key in (
            "context",
            "root_tid",
            "submitter_tid",
            "ancestry_tids",
            "scheduling_class",
            "connection_owner",
        ):
            if evidence.get(key) != payload.get(key):
                return False
        expected = self._admission_host_processes(evidence)
        observed = self._admission_host_processes(payload)
        return bool(expected) and all(
            pid in observed
            and process["create_time"] is not None
            and process["create_time"] == observed[pid]["create_time"]
            for pid, process in expected.items()
        )

    def _ack_launch_custody(self, tid: str, *, broker: Any) -> None:
        """Exact ACK before dropping its checkpoint reference [MANAGER.11]."""
        evidence = self._scheduler_residents.get(tid)
        self._scheduler_undurable.discard(tid)
        if evidence is None or evidence.get("reserved_queue") is None:
            return
        queue_name, request_id = evidence["reserved_queue"], evidence["request_id"]
        broker.delete_message_ids(queue_name, [request_id])
        if (
            broker.peek_one(
                queue_name, exact_timestamp=request_id, include_claimed=True
            )
            is not None
        ):
            raise RuntimeError("launch acknowledgement not confirmed")
        for key in ("source_queue", "reserved_queue", "request_id"):
            evidence.pop(key, None)
        self._scheduler_checkpoint_dirty = True

    def _advance_launch_custody(
        self,
        tid: str,
        payload: Mapping[str, Any] | None,
        *,
        broker: Any,
        observe_usage: Callable[[], ConnectionUsage] | None = None,
    ) -> None:
        """Startup release and durable handoff are separate transitions [MA-1.8]."""
        evidence = self._scheduler_residents.get(tid)
        if evidence is None:
            return
        handoff = payload is not None and self._snapshot_hands_off_launch(
            evidence, payload
        )
        if handoff:
            # A result can be lost before local frontier commit. Snapshot handoff
            # must restore that path in both immediate and periodic reconciliation.
            if (
                evidence["scheduling_class"] == "finite"
                and tid not in self._family_scheduler.resident_tids
                and self._scheduler_evidence_live(tid, evidence)
            ):
                self._family_scheduler.recover(
                    tid, evidence["root_tid"], tuple(evidence["ancestry_tids"])
                )
                self._family_candidate = None
            self._ack_launch_custody(tid, broker=broker)
        if tid not in self._startup_feedback or tid in self._active_child_launches:
            return
        elapsed = (time.time_ns() - evidence.get("launched_ns", time.time_ns())) / 1e9
        ready = handoff
        if not ready and self._admission_backend_name() == "postgres":
            ready = (observe_usage or self._read_postgres_connection_usage)()[
                "by_owner"
            ].get(evidence["connection_owner"], 0) > 0
        if not ready and elapsed >= MANAGER_ADMISSION_RECHECK_SECONDS:
            latest = read_task_state_snapshot(
                self._manager_context(), tid, broker=broker
            )
            if latest and self._snapshot_hands_off_launch(evidence, latest[1]):
                self._ack_launch_custody(tid, broker=broker)
                handoff = True
            self._emit_serve_log_rate_limited(
                "startup_feedback_snapshot_missing",
                component="spawn",
                required_level="info",
                severity="warning",
                key="startup_feedback_snapshot_missing",
                state={"child_tid": tid},
                log_fields={"child_tid": tid},
            )
            ready = bool(evidence.get("host_processes"))
        if ready and (handoff or tid not in self._scheduler_undurable):
            self._release_startup_feedback(tid)
            self._family_scheduler.release_background(tid)
            self._family_candidate = None
            self._scheduler_checkpoint_dirty = True

    @staticmethod
    def _admission_host_processes(
        evidence: Mapping[str, Any],
    ) -> dict[int, dict[str, Any]]:
        """Validate retained exact process evidence at the runtime boundary [MF-5]."""
        raw = evidence.get("host_processes", [])
        if not isinstance(raw, list):
            raise TypeError("admission host_processes must be a list")
        processes = {}
        for item in raw:
            if (
                not isinstance(item, Mapping)
                or type(item.get("pid")) is not int
                or item["pid"] <= 0
            ):
                raise ValueError("admission host process requires a positive PID")
            create_time = item.get("create_time")
            if create_time is not None and type(create_time) not in (int, float):
                raise ValueError("admission create_time must be numeric or unknown")
            processes[item["pid"]] = {"pid": item["pid"], "create_time": create_time}
        return processes

    def _record_reaped_admission(self, tid: str) -> None:
        """Refresh one exited wrapper; evidence failure retains custody without blocking reap [MA-1.8]."""
        # While unhealthy, bounded maintenance owns recovery. Later reaps keep
        # their custody and cannot displace the first failed record from retry.
        if self._admission_evidence_unavailable or tid not in self._scheduler_residents:
            return
        try:
            latest = read_task_state_snapshot(self._manager_context(), tid)
            if latest is not None:
                self._adopt_scheduler_evidence(tid, latest[1])
        except (BrokerError, OSError, RuntimeError, ValueError, TypeError):
            self._admission_evidence_unavailable = True
            self._scheduler_residents = {
                tid: self._scheduler_residents[tid],
                **self._scheduler_residents,
            }
            self._admission_refresh_after = 0.0
            logger.debug(
                "Unable to refresh reaped task admission evidence", exc_info=True
            )
            return
        self._retire_scheduler_residents((tid,))

    def _refresh_scheduler_evidence(self) -> None:
        """Bootstrap once, then reconcile a bounded round-robin batch [MF-5]."""
        if time.monotonic() < self._admission_refresh_after:
            return
        try:
            # Share only within this maintenance pass, and sample lazily so a
            # usage-observer failure cannot obstruct an already valid handoff.
            observe_usage = cache(self._read_postgres_connection_usage)
            with self._get_connected_queue().get_connection() as broker:
                if not self._admission_bootstrapped:
                    snapshots = latest_task_state_rows(
                        self._manager_context(), broker=broker
                    )
                    self._bootstrap_scheduler_evidence(snapshots, broker)
                    selected = tuple(self._scheduler_residents)
                else:
                    selected = tuple(
                        islice(
                            self._scheduler_residents,
                            MANAGER_ADMISSION_REFRESH_BATCH_SIZE,
                        )
                    )
                selected = tuple(dict.fromkeys((*self._startup_feedback, *selected)))
                self._maintain_orphan_reservations(broker)
                for tid in selected:
                    latest = read_task_state_snapshot(
                        self._manager_context(), tid, broker=broker
                    )
                    if latest is not None:
                        self._adopt_scheduler_evidence(tid, latest[1])
                    self._advance_launch_custody(
                        tid,
                        latest[1] if latest else None,
                        broker=broker,
                        observe_usage=observe_usage,
                    )
                    if tid in self._scheduler_residents:
                        evidence = self._scheduler_residents.pop(tid)
                        self._scheduler_residents[tid] = evidence
            self._retire_scheduler_residents(selected)
            if self._scheduler_checkpoint_dirty:
                self._publish_scheduler_checkpoint()
        except (BrokerError, OSError, RuntimeError, ValueError, TypeError, KeyError):
            self._admission_evidence_unavailable = True
            logger.debug("Unable to reconcile scheduler evidence", exc_info=True)
        else:
            self._admission_bootstrapped = True
            self._admission_evidence_unavailable = False
        finally:
            self._admission_refresh_after = (
                time.monotonic() + MANAGER_ADMISSION_RECHECK_SECONDS
            )

    def _bootstrap_scheduler_evidence(
        self, snapshots: Mapping[str, tuple[int, dict[str, Any]]], broker: Any
    ) -> None:
        """Adopt orphan paths from task snapshots and reservation checkpoints [MF-5]."""
        covered: set[str] = set()
        for manager_tid, (_stamp, payload) in snapshots.items():
            checkpoint = payload.get("scheduler_frontiers")
            if payload.get("role") != "manager" or not isinstance(checkpoint, Mapping):
                continue
            residents = checkpoint.get("residents", {})
            if not isinstance(residents, Mapping):
                raise TypeError("invalid Manager resident checkpoint")
            if manager_tid != self.tid and self._admission_mapping_is_live(
                manager_tid, payload
            ):
                covered.update(residents)
                continue
            for tid, evidence in residents.items():
                if isinstance(tid, str) and isinstance(evidence, Mapping):
                    self._adopt_scheduler_evidence(tid, evidence)
        for tid, (_stamp, payload) in snapshots.items():
            if tid not in covered:
                self._adopt_scheduler_evidence(tid, payload)
        self._recover_scheduler_paths()
        self._restore_family_pending(broker)
        self._recover_unlaunched_reservations(snapshots, broker)
        if not self._publish_scheduler_checkpoint():
            raise RuntimeError("unable to checkpoint adopted scheduler responsibility")

    def _recover_unlaunched_reservations(
        self,
        snapshots: Mapping[str, tuple[int, dict[str, Any]]],
        broker: Any,
    ) -> None:
        """A retained current-protocol checkpoint proves pre-fence custody [MF-6.2]."""
        for owner_tid, (_stamp, payload) in snapshots.items():
            checkpoint = payload.get("scheduler_frontiers")
            if (
                owner_tid == self.tid
                or payload.get("role") != "manager"
                or not isinstance(checkpoint, Mapping)
                or self._admission_mapping_is_live(owner_tid, payload)
            ):
                continue
            source = checkpoint.get("source_queue")
            reserved = checkpoint.get("reserved_queue")
            if source == self._queue_names["inbox"] and isinstance(reserved, str):
                self._orphan_reserved[reserved] = (source, 0)
        self._maintain_orphan_reservations(broker)

    def _maintain_orphan_reservations(self, broker: Any) -> None:
        """Retry one exact, bounded dead-Manager reservation page [MF-6.2]."""
        if not self._orphan_reserved:
            return
        queue_name = next(iter(self._orphan_reserved))
        source, cursor = self._orphan_reserved[queue_name]
        with closing_queue_iterator(
            broker.peek_generator(
                queue_name,
                with_timestamps=True,
                after_timestamp=cursor,
                include_claimed=True,
            )
        ) as rows:
            page = list(
                islice(
                    cast(Iterator[tuple[str, int]], rows),
                    MANAGER_FAMILY_CLASSIFY_PAGE_SIZE,
                )
            )
        protected = {
            (entry.get("reserved_queue"), entry.get("request_id"))
            for entry in self._scheduler_residents.values()
        }
        for _body, request_id in page:
            cursor = request_id
            if (queue_name, request_id) in protected:
                continue
            snapshot = read_task_state_snapshot(
                self._manager_context(), str(request_id), broker=broker
            )
            if snapshot is not None:
                continue
            # Broker moves preserve the original ID/body and claim eligibility.
            broker.move_one(queue_name, source, exact_timestamp=request_id)
        self._orphan_reserved.pop(queue_name)
        if page:
            self._orphan_reserved[queue_name] = (
                source,
                cursor if len(page) == MANAGER_FAMILY_CLASSIFY_PAGE_SIZE else 0,
            )

    def _recover_scheduler_paths(self) -> None:
        """Build maximal live ancestry paths once during bootstrap [MF-5]."""
        for tid, evidence in self._scheduler_residents.items():
            if not evidence.get("host_processes"):
                # Process.start may have succeeded after the durable pre-launch
                # fence. Without a receipt, neither replay nor new admission is safe.
                self._scheduler_undurable.add(tid)
            if self._scheduler_evidence_live(tid, evidence):
                self._family_scheduler.recover(
                    tid,
                    evidence["root_tid"],
                    tuple(evidence["ancestry_tids"]),
                    evidence["scheduling_class"],
                    defer=True,
                )
        self._family_scheduler.finish_recovery()

    def _family_request_identity(self, body: str, request_id: int) -> RuntimeIdentity:
        """Reuse canonical transport validation for classification and restored hints."""
        payload = json.loads(body)
        self._spawn_submitter_tid(payload, self._queue_names["inbox"])
        spec = self._build_child_spec(payload, request_id)
        if spec is None:
            raise ValueError("invalid task spec")
        return runtime_identity_for_task(
            spec,
            self._db_path,
            submitted_by=payload.get("submitted_by"),
            config=self._manager_context().config,
        )

    def _read_family_hint(
        self, queue_name: str, body: str, label_id: int, *, broker: Any
    ) -> PendingRequest | None:
        """A pending hint grants no ancestry or launch authority independently of source."""
        try:
            token = json.loads(body)
            if (
                not isinstance(token, Mapping)
                or set(token)
                != {
                    "schema",
                    "request_id",
                    "root_tid",
                    "submitter_tid",
                    "ancestry_tids",
                }
                or token["schema"] != WEFT_FAMILY_PENDING_SCHEMA
            ):
                return None
            request_id = token["request_id"]
            if type(request_id) is not int or not isinstance(
                token["ancestry_tids"], list
            ):
                return None
            source = broker.peek_one(
                self._queue_names["inbox"],
                exact_timestamp=request_id,
                with_timestamps=False,
            )
            if source is None:
                return None
            identity = self._family_request_identity(source, request_id)
            request = PendingRequest(
                request_id,
                identity.root_tid or str(request_id),
                identity.submitter_tid,
                identity.ancestry_tids,
                label_id,
            )
            if (
                request.queue_name != queue_name
                or token["root_tid"] != request.root_tid
                or token["submitter_tid"] != request.submitter_tid
                or tuple(token["ancestry_tids"]) != request.ancestry_tids
            ):
                return None
            return request
        except (ValueError, TypeError, KeyError):
            return None

    def _repair_family_pending(self, broker: Any) -> None:
        """Remove orphan/duplicate hints in bounded resumable pages, not per leaf."""
        if not self._family_label_names:
            self._family_label_names.extend(
                name
                for name in broker.list_queues(prefix=WEFT_FAMILY_PENDING_PREFIX)
                if ".pending." in name
            )
        if not self._family_label_names:
            return
        queue_name = self._family_label_names[0]
        with closing_queue_iterator(
            broker.peek_generator(
                queue_name,
                with_timestamps=True,
                after_timestamp=self._family_label_after,
            )
        ) as rows:
            page = list(
                islice(
                    cast(Iterator[tuple[str, int]], rows),
                    MANAGER_FAMILY_CLASSIFY_PAGE_SIZE,
                )
            )
        for body, label_id in page:
            request = self._read_family_hint(queue_name, body, label_id, broker=broker)
            cached = (
                self._family_scheduler.pending(request.request_id) if request else None
            )
            if request is None or (
                cached is not None and cached.label_message_id != label_id
            ):
                broker.delete_message_ids(queue_name, [label_id])
            elif cached is None:
                self._family_scheduler.add_pending(request)
                self._family_candidate = None
            self._family_label_after = label_id
        if len(page) < MANAGER_FAMILY_CLASSIFY_PAGE_SIZE:
            self._family_label_names.popleft()
            self._family_label_after = 0

    def _restore_family_pending(self, broker: Any) -> None:
        """Restore validated source-backed hints once, never trusting a projection [OBS.6b]."""
        for queue_name in broker.list_queues(prefix=WEFT_FAMILY_PENDING_PREFIX):
            if ".pending." not in queue_name:
                continue
            with closing_queue_iterator(
                broker.peek_generator(queue_name, with_timestamps=True)
            ) as rows:
                tokens = list(cast(Iterator[tuple[str, int]], rows))
            for body, label_id in tokens:
                request = self._read_family_hint(
                    queue_name, body, label_id, broker=broker
                )
                if (
                    request is None
                    or self._family_scheduler.pending(request.request_id) is not None
                ):
                    broker.delete_message_ids(queue_name, [label_id])
                else:
                    self._family_scheduler.add_pending(request)

    def _retire_scheduler_residents(self, tids: Iterable[str] | None = None) -> None:
        """Retire paths only with positive scoped process exit [MF-5]."""
        changed = False
        for tid in tuple(self._scheduler_residents if tids is None else tids):
            evidence = self._scheduler_residents.get(tid)
            if evidence is None or tid in self._active_child_launches:
                continue
            if not evidence.get("host_processes") or self._scheduler_evidence_live(
                tid, evidence
            ):
                continue
            with self._get_connected_queue().get_connection() as broker:
                self._ack_launch_custody(tid, broker=broker)
            self._family_scheduler.retire(tid)
            self._scheduler_residents.pop(tid, None)
            self._release_startup_feedback(tid)
            changed = True
        if changed:
            self._publish_scheduler_checkpoint()
            self._family_candidate = None
            self._clear_admission_retry()

    def _admission_mapping_is_live(
        self,
        tid: str,
        payload: Mapping[str, Any],
    ) -> bool:
        """Return memoized shared-probe liveness for one latest mapping row.

        A dead verdict is permanent while the row's runtime handle and
        normalized terminal hint are unchanged: a probed
        ``(pid, create_time)`` identity never becomes live again, while a
        terminal-hint change must take effect immediately. A live verdict
        expires after ``MANAGER_ADMISSION_RECHECK_SECONDS`` so death is
        observed promptly without re-probing every decision.
        """

        fingerprint = json.dumps(
            {
                "runtime_handle": payload.get("runtime_handle"),
                "terminal": payload.get("terminal") is True,
            },
            sort_keys=True,
            default=str,
        )
        memo = self._admission_probe_memo.get(tid)
        now = time.monotonic()
        if memo is not None:
            memo_fingerprint, memo_live, memo_expires_at = memo
            if memo_fingerprint == fingerprint and (
                not memo_live or now < memo_expires_at
            ):
                return memo_live
        is_live = mapping_row_is_live(payload)
        self._admission_probe_memo[tid] = (
            fingerprint,
            is_live,
            now + MANAGER_ADMISSION_RECHECK_SECONDS,
        )
        return is_live

    def _record_admission_decision(
        self,
        *,
        lane: AdmissionLane,
        backend: str,
        decision: AdmissionDecision,
    ) -> None:
        """Install eligibility and bounded evidence ([MA-1.8], [MANAGER.18])."""

        previous_state = self._admission_last_decision_state
        blocked_lanes = set(decision.blocked_lanes)
        self._admission_blocked_lanes = blocked_lanes
        self._admission_retry_after_ns = (
            time.time_ns() + int(MANAGER_ADMISSION_RECHECK_SECONDS * 1_000_000_000)
            if blocked_lanes
            else self._admission_retry_after_ns
            if self._startup_blocked
            else 0
        )
        unavailable = decision.capacity is None
        state = self._admission_state(blocked_lanes, unavailable=unavailable)
        self._admission_last_decision_state = (state, decision.wait_phase)
        if self._admission_last_decision_state == previous_state and not unavailable:
            return

        identity: dict[str, Any] = {
            "backend": backend,
            "state": state,
            "lane": lane,
        }
        fields = dict(identity)
        fields["reason"] = (
            "usage_unavailable"
            if decision.capacity is None
            else "measured_capacity_exhausted"
            if blocked_lanes
            else "capacity_available"
        )
        if decision.wait_phase is not None:
            identity["wait_phase"] = decision.wait_phase
            fields.update(
                wait_phase=decision.wait_phase,
                elapsed_seconds=decision.elapsed_seconds,
                idle_seconds=decision.idle_seconds,
            )
        if decision.capacity is not None:
            fields.update(
                used=decision.capacity["used"],
                reserve=decision.capacity["reserve"],
                public_limit=decision.capacity["public_limit"],
                internal_limit=decision.capacity["internal_limit"],
            )
        if backend == "postgres":
            fields.update(self._admission_observation)
        if unavailable:
            fields["error"] = "admission_usage_unavailable"
        self._emit_serve_log_rate_limited(
            "admission_state",
            component="spawn",
            required_level="info",
            severity="warning" if blocked_lanes else "info",
            key="admission_state",
            state=identity,
            log_fields=fields,
        )

    def _clear_admission_retry(self) -> None:
        """Make blocked lanes eligible for an immediate fresh observation."""

        self._admission_blocked_lanes.clear()
        self._startup_blocked = False
        self._admission_retry_after_ns = 0

    def _schedule_admission_retry_for_source(self, source_queue: str | None) -> None:
        """Backstop a restored launch request with one bounded lane retry."""

        if not self._admission_enabled() or source_queue is None:
            return
        lane = self._admission_lane_for_queue(source_queue)
        if lane is None:
            return
        if lane == "internal":
            self._admission_blocked_lanes.update(("public", "internal"))
        else:
            self._admission_blocked_lanes.add(lane)
        self._admission_retry_after_ns = time.time_ns() + int(
            MANAGER_ADMISSION_RECHECK_SECONDS * 1_000_000_000
        )

    def _expire_admission_retry_if_due(self) -> None:
        """Make a due blocked source eligible for one fresh admission decision."""

        if (
            self._admission_retry_after_ns > 0
            and time.time_ns() >= self._admission_retry_after_ns
        ):
            self._clear_admission_retry()

    def _read_postgres_connection_stats(self) -> dict[str, int]:
        """Read the current public backend ceiling on the existing Queue [SB-0.4]."""
        # PostgreSQL is an optional backend on SQLite-only installations.
        from simplebroker_pg import get_connection_stats

        return get_connection_stats(self._get_connected_queue())

    def _read_postgres_connection_usage(self) -> ConnectionUsage:
        """Use the released coherent population, outside a caller transaction [SB-0.4]."""
        # PostgreSQL is an optional backend on SQLite-only installations.
        from simplebroker_pg import get_connection_usage

        return get_connection_usage(self._get_connected_queue())

    def _admission_allows_queue(self, queue_name: str) -> bool:
        """Evaluate one spawn lane before broker reservation.

        Spec:
        - docs/specifications/03-Manager_Architecture.md [MA-1.8]
        - docs/specifications/05-Message_Flow_and_State.md [MF-6]
        - docs/specifications/07-System_Invariants.md [MANAGER.18]
        """

        lane = self._admission_lane_for_queue(queue_name)
        if lane is None or not self._admission_enabled():
            return True
        if lane in self._admission_blocked_lanes:
            return False
        if lane == "public" and self._startup_dispatch_blocked():
            return False

        backend = self._admission_backend_name()
        used = self._observe_admission_usage()
        decision = self._admission_controller.evaluate(
            used=used, backend=backend, now=time.monotonic()
        )
        self._record_admission_decision(lane=lane, backend=backend, decision=decision)
        if lane in decision.blocked_lanes:
            return False
        if lane == "public" and not _startup_window_allows_launch(
            unfinished=len(self._startup_feedback),
            backend=backend,
            capacity=decision.capacity,
        ):
            self._startup_blocked = True
            self._admission_retry_after_ns = time.time_ns() + int(
                MANAGER_ADMISSION_RECHECK_SECONDS * 1_000_000_000
            )
            return False
        return True

    def _register_manager(self) -> None:  # noqa: C901 approved [TS-3.1] [RUFF-SUP-005] exception
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
        return now_ns - timestamp > retention_ns

    def _prune_expired_manager_registry_entries(  # noqa: C901 approved [TS-3.1] [RUFF-SUP-005] exception
        self, queue: Queue, *, now_ns: int | None = None
    ) -> None:
        """Delete only this manager owner's expired rows ([MF-3.1])."""

        observed_now_ns = time.time_ns() if now_ns is None else now_ns
        service_key = self._manager_service_key
        expired_timestamps: list[int] = []
        try:
            entries = queue.peek_generator(with_timestamps=True)
        except (BrokerError, OSError, RuntimeError):
            logger.debug("Failed to inspect manager registry for expiry", exc_info=True)
            return

        try:
            with closing_queue_iterator(entries) as rows:
                for entry in rows:
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
                    if payload.get("owner_tid") != self.tid:
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
            with closing_queue_iterator(entries) as rows:
                for entry in rows:
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

    def _self_registry_status_timestamp(  # noqa: C901 approved [TS-3.1] [RUFF-SUP-005] exception
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
            with closing_queue_iterator(entries) as rows:
                for entry in rows:
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

    def _prune_older_self_registry_entries(self, queue: Queue) -> None:  # noqa: C901 approved [TS-3.1] [RUFF-SUP-005] exception
        """Keep only this manager's latest registry row."""

        latest_ts: int | None = None
        self_timestamps: list[int] = []
        try:
            entries = queue.peek_generator(with_timestamps=True)
        except (AttributeError, BrokerError, OSError, RuntimeError):
            logger.debug("Failed to inspect self manager registry rows", exc_info=True)
            return

        try:
            with closing_queue_iterator(entries) as rows:
                for entry in rows:
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

    def _unregister_manager(  # noqa: C901 approved [TS-3.1] [RUFF-SUP-005] exception
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
        config = getattr(self, "_weft_config", {})
        raw_handle = config.get("MANAGER_RUNTIME_HANDLE_JSON")
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
            observations = container.observations(container_pid=pid)
            if container.runtime == "docker":
                observations["liveness_provider"] = "docker"
            return RunnerHandle(
                runner="manager-supervisor",
                kind="supervised-process",
                id=f"{container.runtime}:{container.identifier or 'unknown'}",
                control={"authority": "external-supervisor"},
                observations=observations,
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

        with closing_queue_iterator(generator) as rows:
            for entry in rows:
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
    def _manager_record_liveness(
        record: Mapping[str, Any],
    ) -> Literal["live", "stale", "unknown"]:
        """Share exact manager runtime evidence across readers ([MA-1])."""
        return manager_registry_record_liveness(dict(record))

    @staticmethod
    def _manager_record_is_live(record: Mapping[str, Any]) -> bool:
        return Manager._manager_record_liveness(record) == "live"

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
        return pong_proves_dispatch_eligible(
            payload,
            record=record,
            ctrl_in_name=ctrl_in_name,
            ctrl_out_name=ctrl_out_name,
            outbox_name=WEFT_MANAGER_OUTBOX_QUEUE,
            root_context=str(self._manager_context().root),
        )

    def _manager_pong_dispatch_proof(
        self,
        record: Mapping[str, Any],
        *,
        now_ns: int,
        consume_stored: bool = False,
        resolve_unanswered: bool = False,
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
                    consume_stored=consume_stored,
                    resolve_unanswered=resolve_unanswered,
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
        request_id = uuid.uuid4().hex
        ping_message = encode_control_message(
            CONTROL_PING,
            request_id=request_id,
            reply_to=self._queue_names["ctrl_in"],
        )
        try:
            with self._get_connected_queue().get_connection() as broker:
                broker.write(ctrl_in_name, ping_message)
        except (BrokerError, OSError, RuntimeError) as exc:
            proof = ManagerLeadershipProof(
                "unknown",
                source="control-pong",
                reason=str(exc),
            )
            self._leader_probe_cache[cache_key] = (now_ns, row_timestamp, proof)
            return proof
        self._leader_probe_pending[cache_key] = _ManagerPendingPongProbe(
            tid=tid,
            row_timestamp=row_timestamp,
            request_id=request_id,
            created_turn=self._loop_iteration,
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
        consume_stored: bool,
        resolve_unanswered: bool,
    ) -> ManagerLeadershipProof:
        """Advance one event-fed manager PING on its owning policy cadence."""

        if consume_stored and probe.pong is not None:
            ctrl_in_name = self._manager_ctrl_queue_name(probe.tid, record)
            ctrl_out_name = self._manager_ctrl_out_queue_name(probe.tid, record)
            proof = self._manager_pong_payload_proof(
                probe.pong,
                record=record,
                ctrl_in_name=ctrl_in_name,
                ctrl_out_name=ctrl_out_name,
            )
            self._complete_manager_pong_probe(probe, proof, now_ns=now_ns)
            return proof

        if resolve_unanswered and probe.created_turn < self._loop_iteration:
            proof = ManagerLeadershipProof(
                "unknown",
                source="control-pong",
                reason="ping_unanswered",
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
        consume_stored: bool = False,
        resolve_unanswered: bool = False,
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
            return ManagerLeadershipProof("stale", source="runtime-handle")
        timestamp = record.get("_timestamp", record.get("timestamp"))
        if isinstance(timestamp, int) and self._registry_entry_is_expired(
            timestamp, now_ns=now_ns
        ):
            return ManagerLeadershipProof(
                "stale", source="registry-heartbeat", reason="expired"
            )
        if allow_ping:
            return self._manager_pong_dispatch_proof(
                record,
                now_ns=now_ns,
                consume_stored=consume_stored,
                resolve_unanswered=resolve_unanswered,
            )
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
        if not isinstance(timestamp, int):
            return True
        return not self._registry_entry_is_expired(timestamp, now_ns=now_ns)

    def _active_dispatch_manager_records(  # noqa: C901 approved [TS-3.1] [RUFF-SUP-007] exception
        self,
        *,
        consume_stored: bool = False,
        resolve_unanswered: bool = False,
        retire_absent: bool = False,
    ) -> dict[str, dict[str, Any]] | None:
        now_ns = time.time_ns()
        if not self._update_manager_registry_snapshot():
            return None
        if self._self_superseded_manager_record_visible(now_ns=now_ns):
            self._begin_superseded_shutdown()
            return {}

        active: dict[str, dict[str, Any]] = {}
        stale_timestamps: list[int] = []
        unknown_seen = False
        observed_probe_sources: set[tuple[str, int | None]] = set()
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
            if is_canonical_manager_record(record):
                observed_probe_sources.add(
                    (tid, timestamp if isinstance(timestamp, int) else None)
                )
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
                consume_stored=consume_stored,
                resolve_unanswered=resolve_unanswered,
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

        if retire_absent:
            for key, probe in tuple(self._leader_probe_pending.items()):
                if (probe.tid, probe.row_timestamp) not in observed_probe_sources:
                    self._leader_probe_pending.pop(key, None)

        active_tids = sorted(active)
        leader_tid = canonical_owner_tid(active)
        fields = {
            "active_manager_tids": active_tids,
            "active_manager_count": len(active_tids),
            "leader_tid": leader_tid,
            "stale_omitted_count": len(stale_timestamps),
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

    def _read_active_manager_records(
        self,
        *,
        consume_stored: bool = False,
        resolve_unanswered: bool = False,
        retire_absent: bool = False,
    ) -> dict[str, dict[str, Any]] | None:
        """Return the live canonical manager snapshot or ``None`` on read failure.

        Spec: [MA-1.4], [MA-3]
        """
        return self._active_dispatch_manager_records(
            consume_stored=consume_stored,
            resolve_unanswered=resolve_unanswered,
            retire_absent=retire_absent,
        )

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
        reservation owns exclusivity for that exact message. Pending public or
        internal shared backlog is not owned work and must not by itself block
        duplicate-manager convergence.

        Spec: [MA-1.4], [MF-6]
        """

        if any(child.persistent for child in self._user_work_children().values()):
            return True
        if self._has_active_child_launches():
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
                self._mark_queue_active(name)
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

    def _maybe_yield_leadership(self, *, force: bool = False) -> bool:  # noqa: C901 approved [TS-3.1] [RUFF-SUP-007] exception
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
        ordinary_due = not force and (
            self._last_leader_check_ns <= 0
            or now_ns - self._last_leader_check_ns >= self._leader_check_interval_ns
        )
        stored_reply_due = any(
            probe.pong is not None for probe in self._leader_probe_pending.values()
        )
        if not force and not ordinary_due and not stored_reply_due:
            return self.should_stop
        if ordinary_due and self._leader_check_turn == self._loop_iteration:
            ordinary_due = False
            if not stored_reply_due:
                return self.should_stop

        if self._has_active_child_launches():
            if ordinary_due:
                self._last_leader_check_ns = now_ns
                self._leader_check_turn = self._loop_iteration
            return False

        if ordinary_due:
            self._last_leader_check_ns = now_ns
            self._leader_check_turn = self._loop_iteration

        active = self._read_active_manager_records(
            consume_stored=True,
            resolve_unanswered=ordinary_due,
            retire_absent=True,
        )
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

        if (
            not self._has_active_child_launches()
            and not self._user_work_children()
            and not self._requeue_public_reserved_spawn_requests_before_yield(
                leader_tid=leader_tid
            )
        ):
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

    def _reserved_queue_has_any_message(self, queue_name: str) -> bool:
        """Return whether a reserved queue has any row, including claimed rows."""

        try:
            return self._queue(queue_name).peek_one(include_claimed=True) is not None
        except (BrokerError, OSError, RuntimeError):
            logger.debug(
                "Failed to inspect reserved queue %s before leadership yield",
                queue_name,
                exc_info=True,
            )
            return True

    def _requeue_public_reserved_spawn_requests_before_yield(
        self, *, leader_tid: str
    ) -> bool:
        """Yield unlaunched public reservations without replaying live custody [MF-6]."""
        self._apply_spawn_reserved_policy(
            ReservedPolicy.REQUEUE,
            source_queue=self._queue_names["inbox"],
            reserved_queue=self._queue_names["reserved"],
        )
        return True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _register_child_sentinel(self, tid: str, child: ManagedChild) -> None:
        """Register the newly committed child's identity.

        Spec: docs/specifications/03-Manager_Architecture.md [MA-1.6a]
        """
        try:
            sentinel = child.process.sentinel
        except (AttributeError, ValueError):
            return
        if isinstance(sentinel, int):
            self._child_sentinel_adapter.add(tid, sentinel)

    def _drain_child_sentinel_events(self) -> int:
        """Record current child-sentinel events on the Manager reactor."""

        observed = 0
        for result in self._child_sentinel_adapter.drain():
            if isinstance(result, _ManagerChildSentinelFailure):
                raise result.error
            child = self._child_processes.get(result.tid)
            if child is None:
                continue
            try:
                current_sentinel = child.process.sentinel
            except (AttributeError, ValueError):
                continue
            if current_sentinel != result.sentinel:
                continue
            child.sentinel_observed_ns = time.time_ns()
            child.last_liveness_probe_ns = 0
            observed += 1
        return observed

    def _child_has_exited(self, child: ManagedChild) -> bool:  # noqa: C901 approved [TS-3.1] [RUFF-SUP-008] exception
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
            if pid_is_live(pid):
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

    def _child_terminal_proof_visible(
        self, tid: str, child: ManagedChild
    ) -> bool | None:
        """Read task-owned local proof; a broker failure is unknown.

        Spec: docs/specifications/03-Manager_Architecture.md [MA-1.6a]
        """
        ctrl_out_name = child.ctrl_out_queue or f"T{tid}.{QUEUE_CTRL_OUT_SUFFIX}"
        # Dynamic child queues borrow the connection, never a cached queue handle.
        try:
            with self._get_connected_queue().get_connection() as broker:
                iterator = broker.peek_generator(ctrl_out_name, with_timestamps=True)
                with closing_queue_iterator(iterator) as rows:
                    for entry in rows:
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
                            and isinstance(payload.get("status"), str)
                            and payload.get("status") in TERMINAL_TASK_STATUSES
                        ):
                            return True
        except (BrokerError, OSError, RuntimeError):
            logger.debug(
                "Failed to inspect child ctrl_out for terminal proof",
                exc_info=True,
            )
            return None
        return False

    def _child_terminal_proof_still_within_grace(
        self,
        tid: str,
        child: ManagedChild,
    ) -> bool:
        """Return whether a cleanly exited child gets more time for proof writes."""

        exitcode = child.process.exitcode
        if child.launched_ns <= 0:
            return False
        if exitcode not in (None, 0):
            return False
        if self._child_terminal_proof_visible(tid, child) is not False:
            return False
        now_ns = time.time_ns()
        if child.terminal_proof_missing_since_ns <= 0:
            child.terminal_proof_missing_since_ns = now_ns
            return True
        grace_ns = int(MANAGER_CHILD_TERMINAL_PROOF_GRACE_SECONDS * 1_000_000_000)
        return now_ns - child.terminal_proof_missing_since_ns < grace_ns

    def _write_manager_terminal_envelope(
        self,
        tid: str,
        child: ManagedChild,
        *,
        deadline: float | None = None,
    ) -> bool:
        """Commit the local failsafe before retiring exited-child custody.

        Spec: docs/specifications/03-Manager_Architecture.md [MA-1.6a]
        """
        if self._child_terminal_proof_visible(tid, child) is True:
            return True
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
        serialized = json.dumps(payload)
        attempts = max(1, TERMINAL_EVENT_WRITE_RETRIES)
        for attempt in range(attempts):
            if deadline is not None and self._remaining_deadline(deadline) <= 0:
                return False
            try:
                with self._get_connected_queue().get_connection() as broker:
                    broker.write(ctrl_out_name, serialized)
                return True
            except (BrokerError, OSError, RuntimeError):
                if attempt + 1 >= attempts:
                    logger.warning(
                        "Failed to write manager terminal envelope for child %s",
                        tid,
                        exc_info=True,
                    )
                    return False
                pause = TERMINAL_EVENT_WRITE_RETRY_INTERVAL
                if deadline is not None:
                    pause = min(pause, self._remaining_deadline(deadline))
                if pause > 0:
                    time.sleep(pause)
        return False

    def _cleanup_children(self, *, deadline: float | None = None) -> bool:  # noqa: C901 approved [TS-3.1] [RUFF-SUP-009] exception
        autostart_child_exited = False
        child_exited = False
        for tid, child in list(self._child_processes.items()):
            if deadline is not None and self._remaining_deadline(deadline) <= 0:
                break
            if self._child_has_exited(child):
                if child.terminal_retry_after_ns > time.time_ns():
                    continue
                if self._child_terminal_proof_still_within_grace(tid, child):
                    continue
                committed = (
                    self._write_manager_terminal_envelope(tid, child)
                    if deadline is None
                    else self._write_manager_terminal_envelope(
                        tid, child, deadline=deadline
                    )
                )
                if not committed:
                    child.terminal_retry_after_ns = time.time_ns() + int(
                        MANAGER_CHILD_TERMINAL_PROOF_GRACE_SECONDS * 1_000_000_000
                    )
                    continue
                child_exited = True
                try:
                    # Dead children must never stall the manager control loop.
                    # Treat dead-child cleanup as a local reap only; descendant
                    # teardown belongs to explicit termination paths, not the
                    # hot loop that must stay responsive to new control
                    # messages.
                    join_timeout = 0.1
                    if deadline is not None:
                        join_timeout = min(
                            join_timeout,
                            self._remaining_deadline(deadline),
                        )
                    child.process.join(timeout=join_timeout)
                except (
                    AssertionError,
                    OSError,
                    ValueError,
                ):  # pragma: no cover - defensive
                    pass
                finally:
                    self._child_processes.pop(tid, None)
                    self._child_sentinel_adapter.remove(tid)
                    self._record_reaped_admission(tid)
                if (
                    child.reserved_queue is not None
                    and child.message_timestamp is not None
                ):
                    try:
                        self._queue(child.reserved_queue).delete(
                            message_id=child.message_timestamp,
                        )
                    except (BrokerError, OSError, RuntimeError):
                        logger.debug(
                            "Failed to acknowledge terminal child spawn message %s",
                            child.message_timestamp,
                            exc_info=True,
                        )
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
                    if service_key in {
                        INTERNAL_SERVICE_KEY_TASK_MONITOR,
                        INTERNAL_SERVICE_KEY_LIVENESS_MONITOR,
                    }:
                        state.next_allowed_ns = (
                            time.time_ns() + self._task_monitor_restart_backoff_ns
                        )

        if autostart_child_exited:
            # Re-evaluate ensure-style autostart manifests immediately after an
            # autostart child exits instead of waiting for the next scan interval.
            self._autostart_last_scan_ns = 0
        if child_exited:
            self._admission_controller.note_child_reap(now=time.monotonic())
            # Child completion is activity. The manager should only begin its idle
            # countdown after in-flight work has actually finished.
            self._last_activity_ns = time.time_ns()
            self._invalidate_leadership_work_cache()
            self._clear_admission_retry()
        return child_exited

    def _terminate_children(self, deadline: float) -> None:  # noqa: C901 approved [TS-3.1] [RUFF-SUP-009] exception
        """Stop tracked children under one absolute monotonic deadline.

        Spec: docs/specifications/07-System_Invariants.md [IMPL.10]
        """

        children: dict[str, ManagedChild] = dict(self._child_processes)
        managed_pids: dict[str, set[int]] = {
            tid: self._managed_pids_for_child(tid) for tid in children
        }

        observer_failure: BaseException | None = None
        try:
            self._drain_child_sentinel_events()
        except BaseException as exc:
            observer_failure = exc
            logger.warning(
                "Child-sentinel observer failed during Manager cleanup; "
                "continuing child termination",
                exc_info=True,
            )

        self._cleanup_children(deadline=deadline)
        children.update(self._child_processes)
        for tid in self._child_processes:
            managed_pids.setdefault(tid, set()).update(
                self._managed_pids_for_child(tid)
            )

        if not children:
            if observer_failure is not None:
                raise observer_failure
            return

        for tid, child in list(self._child_processes.items()):
            ctrl_queue = child.ctrl_queue or f"T{tid}.{QUEUE_CTRL_IN_SUFFIX}"
            self._send_stop_command(ctrl_queue)
        grace_deadline = time.monotonic() + (self._remaining_deadline(deadline) / 2.0)
        try:
            self._wait_for_children_to_exit(min(deadline, grace_deadline))
        except BaseException as exc:
            if observer_failure is None:
                observer_failure = exc
            logger.warning(
                "Child-sentinel observer failed during Manager cleanup; "
                "continuing child termination",
                exc_info=True,
            )
        children.update(self._child_processes)
        for tid in children:
            managed_pids.setdefault(tid, set()).update(
                self._managed_pids_for_child(tid)
            )

        survivors: list[dict[str, Any]] = []
        for tid, child in list(children.items()):
            try:
                if child.process.is_alive():
                    try:
                        child.process.join(
                            timeout=min(
                                0.2,
                                self._remaining_deadline(deadline),
                            )
                        )
                    except (
                        AssertionError,
                        OSError,
                        ValueError,
                    ):  # pragma: no cover - defensive
                        pass
                kill_issued = False
                if child.process.is_alive():
                    remaining = self._remaining_deadline(deadline)
                    if child.process.pid is not None and remaining > 0:
                        # Split the remaining budget across the TERM wait and
                        # the SIGKILL reap wait so escalation for
                        # TERM-resistant descendants stays inside one caller
                        # deadline instead of being dropped entirely.
                        terminate_process_tree(
                            child.process.pid,
                            timeout=remaining / 2.0,
                            kill_after=True,
                        )
                    if child.process.is_alive():
                        try:
                            child.process.kill()
                            kill_issued = True
                        except OSError:  # pragma: no cover - defensive
                            pass
                managed_pids.setdefault(tid, set()).update(
                    self._managed_pids_for_child(tid)
                )
                for pid in managed_pids[tid]:
                    remaining = self._remaining_deadline(deadline)
                    if remaining <= 0:
                        break
                    terminate_process_tree(
                        pid,
                        timeout=remaining / 2.0,
                        kill_after=True,
                    )
                try:
                    still_alive = child.process.is_alive()
                except (AssertionError, OSError, ValueError):
                    still_alive = True
                surviving_managed_pids = sorted(
                    pid for pid in managed_pids.get(tid, set()) if pid_is_live(pid)
                )
                if still_alive or surviving_managed_pids:
                    survivors.append(
                        {
                            "tid": tid,
                            "pid": child.process.pid,
                            "managed_pids": sorted(managed_pids.get(tid, set())),
                            "surviving_managed_pids": surviving_managed_pids,
                            "kill_issued": kill_issued,
                        }
                    )
            finally:
                self._child_processes.pop(tid, None)
                self._child_sentinel_adapter.remove(tid)

        self._last_child_cleanup_survivors = tuple(survivors)
        if survivors:
            logger.warning(
                "Manager %s cleanup deadline left process survival uncertain: %s",
                self.tid,
                survivors,
            )
        if observer_failure is not None:
            raise observer_failure

    def _wait_for_children_to_exit(self, deadline: float) -> None:
        while self._child_processes and self._remaining_deadline(deadline) > 0:
            self._drain_child_sentinel_events()
            self._cleanup_children(deadline=deadline)
            if not self._child_processes:
                return
            self._child_sentinel_adapter.wait(self._remaining_deadline(deadline))

    def _signal_children_to_stop(self) -> None:
        """Best-effort STOP broadcast to currently tracked child tasks."""

        for tid, child in list(self._child_processes.items()):
            if tid in self._drain_signaled_children:
                if tid in self._drain_escalated_children:
                    continue
                started_ns = self._drain_signal_started_ns.get(tid, time.time_ns())
                escalation_ns = int(
                    MANAGER_CHILD_STOP_ESCALATION_SECONDS * 1_000_000_000
                )
                if time.time_ns() - started_ns >= escalation_ns:
                    pid = child.process.pid
                    if isinstance(pid, int) and pid > 0:
                        # SIGTERM only at this rung. Task processes defer
                        # signal handling to their run loop's next bounded
                        # wait turn (see launcher._install_signal_handlers),
                        # so an immediate SIGKILL follow-up would kill them
                        # before the deferred terminal transition (state
                        # event, ctrl_out envelope, reserved policy) can run.
                        # The hard-kill rung stays at drain timeout via
                        # _terminate_children.
                        terminate_process_tree(pid, timeout=0.2, kill_after=False)
                    self._drain_escalated_children.add(tid)
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
        self._drain_escalated_children.clear()
        self._drain_signal_started_ns.clear()
        self._drain_started_ns = time.time_ns()
        self._drain_immediate_work_pending = True
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
        self._drain_escalated_children.clear()
        self._drain_signal_started_ns.clear()
        self._drain_started_ns = time.time_ns()
        self._drain_immediate_work_pending = True
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
            self._draining = True
            self._drain_signaled_children.clear()
            self._drain_escalated_children.clear()
            self._drain_signal_started_ns.clear()
            self._drain_started_ns = time.time_ns()
            self._drain_immediate_work_pending = True
            self._drain_reason = "Superseded by replacement manager"
            self._drain_completion_event = "manager_superseded_drained"
            self._drain_stops_children = False
            self._drain_leader_tid = None
            self._last_leadership_drain_revalidate_ns = 0
            self._report_state_change(
                event="manager_superseded",
                draining=True,
            )
            self._update_process_title("draining")
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
        self._drain_immediate_work_pending = False
        self._drain_signaled_children.clear()
        self._drain_escalated_children.clear()
        self._drain_signal_started_ns.clear()
        self._drain_stops_children = True
        self.should_stop = True

    def _apply_termination_request(
        self,
        signum: int,
        *,
        parent_lost: bool,
    ) -> None:
        """Preserve Manager drain policy while sharing BaseTask recording.

        Spec: docs/specifications/07-System_Invariants.md [IMPL.10]
        """

        sigusr1 = getattr(signal, "SIGUSR1", None)
        if sigusr1 is not None and signum == sigusr1:
            self._terminate_children(time.monotonic() + TASK_CLEANUP_TIMEOUT_SECONDS)
            self._external_stop_handled = False
            del parent_lost
            BaseTask.handle_termination_signal(self, signum)
            return

        if self._draining or self.should_stop:
            return

        try:
            signal_name = "parent loss" if parent_lost else signal.Signals(signum).name
        except ValueError:  # pragma: no cover - defensive
            signal_name = f"signal {signum}"

        self._begin_shutdown_drain(
            message_id=None,
            reason=(
                "Parent process exited" if parent_lost else f"{signal_name} received"
            ),
            event=None,
            completion_event="task_signal_stop",
        )

    def _send_child_control_command(self, queue_name: str, command: str) -> None:
        # Borrow the task's thread-owned connection without caching child names.
        try:
            with self._get_connected_queue().get_connection() as broker:
                broker.write(queue_name, encode_control_message(command))
        except (BrokerError, OSError, RuntimeError):
            logger.debug(
                "Failed to send %s to %s",
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
        return not self._stalled_control_retry_active(timestamp)

    def _queue_has_pending(self, queue: Queue) -> bool:
        """Retained launch custody does not create actionable reactor work [MF-6]."""
        name = getattr(queue, "name", None)
        if name == self._queue_names.get("ctrl_in"):
            return self._manager_control_pending_is_actionable()
        if name in {
            reserved for reserved, _source in self._spawn_reserved_queue_pairs()
        }:
            return False
        if name == self._queue_names.get("inbox"):
            if (
                self._startup_dispatch_blocked()
                or self._admission_evidence_unavailable
                or self._scheduler_undurable
            ):
                return self._family_classification_pending
            return (
                self._family_classification_pending
                or bool(self._family_invalid)
                or self._peek_family_candidate() is not None
            )
        return super()._queue_has_pending(queue)

    def _has_pending_messages(self) -> bool:
        """Return pending work, excluding blocked spawn sources and stalled control.

        This is a scheduler precheck as well as a predicate. When it proves
        non-control work exists, the next queue drain must probe all queues so
        pending public work cannot wait behind the periodic active-queue scan.
        Retained reserved custody never counts as actionable work.
        An empty eligible set alone does not prove the source itself is empty.
        """

        ctrl_name = self._queue_names.get("ctrl_in")
        empty_spawn_lanes: set[AdmissionLane] = set()
        for name, config in self._queues.items():
            if name == ctrl_name:
                continue
            lane = self._admission_lane_for_queue(name)
            if lane is not None and lane in self._admission_blocked_lanes:
                continue
            if self._queue_has_pending(config.queue):
                self._mark_pending_messages_prechecked()
                return True
            if lane is not None and (
                name != self._queue_names["inbox"] or not config.queue.has_pending()
            ):
                empty_spawn_lanes.add(lane)
                if (
                    len(empty_spawn_lanes) == len(self._spawn_inbox_queue_names())
                    and not self._stop_event.is_set()
                ):
                    # Reuse only this scan's successful probes. A retry wake
                    # alone cannot prove an idle gap ([MANAGER.18]).
                    self._admission_controller.reset_wait()
        return self._manager_control_pending_is_actionable()

    def _note_control_request_activity(self, body: str) -> None:
        """Reset the idle clock for a canonical control request only.

        A reply to this manager's own PING probe, or an unparseable control
        row, is consumed without counting as activity, so recurring liveness
        probes cannot hold an otherwise idle manager alive.

        Spec: [MA-1.5]
        """

        if parse_control_request(body) is not None:
            self._last_activity_ns = time.time_ns()

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
            self._note_control_request_activity(str(body))
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

    def _process_queue_message(
        self, queue_name: str, inactive_candidates: set[str]
    ) -> bool:
        """Single-flight selection, validation under pressure, and no custody PEEK spin."""
        reserved_names = {name for name, _source in self._spawn_reserved_queue_pairs()}
        if queue_name in reserved_names:
            return False
        if queue_name in self._spawn_inbox_queue_names():
            if (
                self._has_active_child_launches()
                or self._child_launch_started_this_turn
            ):
                inactive_candidates.add(queue_name)
                return False
            if queue_name == self._queue_names["inbox"]:
                if (
                    self._startup_dispatch_blocked()
                    or self._admission_evidence_unavailable
                    or self._scheduler_undurable
                ):
                    return False
                if not self._family_invalid and self._peek_family_candidate() is None:
                    return False
            invalid_only = queue_name == self._queue_names["inbox"] and bool(
                self._family_invalid
            )
            if not invalid_only and not self._timed_manager_call(
                "admission", lambda: self._admission_allows_queue(queue_name)
            ):
                return False
        return super()._process_queue_message(queue_name, inactive_candidates)

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
                pending = self._queue(queue_name).peek_one(
                    with_timestamps=True,
                    order="newest",
                )
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

    def _delete_queue_messages_by_id(
        self,
        queue_name: str,
        *,
        limit: int | None,
    ) -> int:
        """Delete visible and claimed queue rows by exact message ID."""

        # Stale manager names are dynamic; only the connection stays task-owned.
        with self._get_connected_queue().get_connection() as broker:
            message_ids: list[int] = []
            iterator = broker.peek_generator(queue_name, with_timestamps=True)
            with closing_queue_iterator(iterator) as rows:
                for entry in rows:
                    if limit is not None and len(message_ids) >= limit:
                        break
                    if not isinstance(entry, tuple) or len(entry) != 2:
                        continue
                    _body, timestamp = entry
                    if isinstance(timestamp, int):
                        message_ids.append(timestamp)
            deleted = 0
            for message_id in message_ids:
                if broker.delete_message_ids(queue_name, [message_id]):
                    deleted += 1
        return deleted

    def _cleanup_own_internal_reserved_queue(self) -> None:
        """Delete this manager's private internal spawn reservations on shutdown."""

        internal_reserved = self._queue_names.get("internal_reserved")
        if internal_reserved is None:
            return
        try:
            deleted = self._delete_queue_messages_by_id(internal_reserved, limit=None)
        except (BrokerError, OSError, RuntimeError):
            logger.debug(
                "Failed to clean manager internal reserved queue %s",
                internal_reserved,
                exc_info=True,
            )
            return
        if deleted:
            self._emit_serve_log(
                "internal_reserved_cleanup",
                component="service",
                required_level="debug",
                queue=internal_reserved,
                rows_deleted=deleted,
                owner_tid=self.tid,
                reason="manager_shutdown",
            )

    def _cleanup_stale_internal_reserved_queues(self, *, force: bool = False) -> None:  # noqa: C901 approved [TS-3.1] [RUFF-SUP-010] exception
        """Boundedly delete internal spawn reservations for non-live managers."""

        if not self._internal_spawn_queue_attached():
            return
        now_ns = time.time_ns()
        interval_ns = int(
            MANAGER_STALE_INTERNAL_RESERVED_CLEANUP_INTERVAL_SECONDS * 1_000_000_000
        )
        if (
            not force
            and self._last_stale_internal_reserved_cleanup_ns > 0
            and now_ns - self._last_stale_internal_reserved_cleanup_ns < interval_ns
        ):
            return
        self._last_stale_internal_reserved_cleanup_ns = now_ns

        active = self._read_active_manager_records()
        if active is None:
            return
        protected_tids = set(active)
        protected_tids.update(
            tid
            for tid, record in self._manager_registry_snapshot.items()
            if record.get("status") == SERVICE_STATUS_ACTIVE
        )
        protected_tids.add(self.tid)

        pattern = f"T*.{QUEUE_INTERNAL_RESERVED_SUFFIX}"
        try:
            with self._get_connected_queue().get_connection() as broker:
                queue_names = tuple(
                    str(name) for name in broker.list_queues(pattern=pattern)
                )
        except (BrokerError, OSError, RuntimeError):
            logger.debug("Failed to list stale internal reserved queues", exc_info=True)
            return

        candidates: list[tuple[str, str]] = []
        for queue_name in sorted(queue_names):
            tid = self._internal_reserved_queue_tid(queue_name)
            if tid is None or tid in protected_tids:
                continue
            candidates.append((queue_name, tid))
            if len(candidates) >= MANAGER_STALE_INTERNAL_RESERVED_CLEANUP_QUEUE_LIMIT:
                break

        rows_deleted = 0
        queues_touched = 0
        for queue_name, _tid in candidates:
            try:
                deleted = self._delete_queue_messages_by_id(
                    queue_name,
                    limit=MANAGER_STALE_INTERNAL_RESERVED_CLEANUP_MESSAGE_LIMIT,
                )
            except (BrokerError, OSError, RuntimeError):
                logger.debug(
                    "Failed to clean stale internal reserved queue %s",
                    queue_name,
                    exc_info=True,
                )
                continue
            if deleted:
                queues_touched += 1
                rows_deleted += deleted

        if queues_touched:
            self._emit_serve_log(
                "stale_internal_reserved_cleanup",
                component="service",
                required_level="debug",
                queues_touched=queues_touched,
                rows_deleted=rows_deleted,
                protected_tids=sorted(protected_tids),
            )

    def _apply_spawn_reserved_policy(
        self,
        policy: ReservedPolicy,
        *,
        source_queue: str,
        reserved_queue: str,
        message_timestamp: int | None = None,
    ) -> None:
        """Exact disposition of positively unlaunched rows only [MF-6], [MANAGER.11]."""
        if policy is ReservedPolicy.KEEP:
            return
        reserved = self._queue(reserved_queue)
        if message_timestamp is None:
            with closing_queue_iterator(
                reserved.peek_generator(with_timestamps=True, include_claimed=True)
            ) as rows:
                ids = [stamp for _body, stamp in cast(Iterator[tuple[str, int]], rows)]
        else:
            ids = [message_timestamp]
        protected = {
            entry.get("request_id")
            for entry in self._scheduler_residents.values()
            if entry.get("reserved_queue") == reserved_queue
        }
        for stamp in ids:
            if stamp in protected:
                continue
            try:
                if policy is ReservedPolicy.CLEAR:
                    reserved.delete(message_id=stamp)
                elif policy is ReservedPolicy.REQUEUE:
                    moved = reserved.move_one(
                        source_queue, exact_timestamp=stamp, with_timestamps=True
                    )
                    if moved is not None and source_queue == self._queue_names["inbox"]:
                        body, request_id = moved
                        with self._get_connected_queue().get_connection() as broker:
                            self._index_family_request(body, request_id, broker=broker)
            except (BrokerError, OSError, RuntimeError):
                logger.warning(
                    "Reserved spawn disposition failed on %s for request %s; custody retained",
                    reserved_queue,
                    stamp,
                    exc_info=True,
                )

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
            return

        try:
            submitter_tid = self._spawn_submitter_tid(payload, source_queue)
        except (TypeError, ValueError) as exc:
            self._report_state_change(
                event="task_spawn_rejected", child_tid=str(timestamp), error=str(exc)
            )
            self._apply_spawn_reserved_policy(
                self.taskspec.spec.reserved_policy_on_error,
                source_queue=source_queue,
                reserved_queue=reserved_queue,
                message_timestamp=timestamp,
            )
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
            submitter_tid=submitter_tid,
            runtime_identity=runtime_identity_for_task(
                child_spec,
                self._db_path,
                submitted_by=payload.get("submitted_by"),
                internal=source_queue == self._queue_names.get("internal_inbox"),
                config=self._manager_context().config,
            ),
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

        self._reconcile_launch_now(str(child_spec.tid))

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

    def _reconcile_launch_now(self, tid: str) -> None:
        """Exact handoff observation without namespace replay on launch completion."""
        try:
            with self._get_connected_queue().get_connection() as broker:
                latest = read_task_state_snapshot(
                    self._manager_context(), tid, broker=broker
                )
                if latest is not None:
                    self._adopt_scheduler_evidence(tid, latest[1])
                self._advance_launch_custody(
                    tid, latest[1] if latest else None, broker=broker
                )
            if self._scheduler_checkpoint_dirty:
                self._publish_scheduler_checkpoint()
        except (
            BrokerError,
            DatabaseError,
            OSError,
            RuntimeError,
            ValueError,
            TypeError,
        ):
            logger.debug("Launch evidence handoff deferred", exc_info=True)

    def _peek_family_candidate(self) -> PendingRequest | None:
        if self._family_candidate is None:
            self._family_candidate = self._family_scheduler.select()
        return self._family_candidate

    def _index_family_request(self, body: str, request_id: int, *, broker: Any) -> None:
        """Canonical classification writes only a compact secondary reference [OBS.6b]."""
        if (
            self._family_scheduler.pending(request_id) is not None
            or request_id in self._family_invalid
        ):
            return
        try:
            identity = self._family_request_identity(body, request_id)
        except (ValueError, TypeError, KeyError):
            if len(self._family_invalid) < MANAGER_FAMILY_CLASSIFY_PAGE_SIZE:
                self._family_invalid[request_id] = None
            return
        request = PendingRequest(
            request_id,
            identity.root_tid or str(request_id),
            identity.submitter_tid,
            identity.ancestry_tids,
        )
        token = {
            "schema": WEFT_FAMILY_PENDING_SCHEMA,
            "request_id": request_id,
            "root_tid": request.root_tid,
            "submitter_tid": request.submitter_tid,
            "ancestry_tids": list(request.ancestry_tids),
        }
        label_id = int(broker.write(request.queue_name, json.dumps(token)))
        self._family_scheduler.add_pending(
            PendingRequest(
                request_id,
                request.root_tid,
                request.submitter_tid,
                request.ancestry_tids,
                label_id,
            )
        )
        self._family_candidate = None

    def _classify_family_requests(self) -> None:
        """Advance bounded discovery even when admission or custody gates close [MF-6]."""
        if (
            not self._admission_bootstrapped
            or time.monotonic() < self._family_index_retry_after
        ):
            return
        try:
            with self._get_connected_queue().get_connection() as broker:
                queue_name = self._queue_names["inbox"]
                with closing_queue_iterator(
                    broker.peek_generator(
                        queue_name,
                        with_timestamps=True,
                        after_timestamp=self._family_classify_after,
                    )
                ) as rows:
                    page = list(
                        islice(
                            cast(Iterator[tuple[str, int]], rows),
                            MANAGER_FAMILY_CLASSIFY_PAGE_SIZE,
                        )
                    )
                for body, stamp in page:
                    self._index_family_request(body, stamp, broker=broker)
                    self._family_classify_after = stamp
                self._family_classification_pending = (
                    len(page) == MANAGER_FAMILY_CLASSIFY_PAGE_SIZE
                )
                if time.monotonic() >= self._family_repair_due:
                    with closing_queue_iterator(
                        broker.peek_generator(
                            queue_name,
                            with_timestamps=True,
                            after_timestamp=self._family_repair_after,
                        )
                    ) as rows:
                        repair = list(
                            islice(
                                cast(Iterator[tuple[str, int]], rows),
                                MANAGER_FAMILY_CLASSIFY_PAGE_SIZE,
                            )
                        )
                    for body, stamp in repair:
                        self._index_family_request(body, stamp, broker=broker)
                        self._family_repair_after = stamp
                    if len(repair) < MANAGER_FAMILY_CLASSIFY_PAGE_SIZE:
                        self._family_repair_after = 0
                    self._repair_family_pending(broker)
                    self._family_repair_due = (
                        time.monotonic() + MANAGER_ADMISSION_RECHECK_SECONDS
                    )
        except (BrokerError, OSError, RuntimeError):
            self._family_classification_pending = False
            self._family_index_retry_after = (
                time.monotonic() + MANAGER_ADMISSION_RECHECK_SECONDS
            )
            self._family_repair_due = self._family_index_retry_after
            logger.debug(
                "Family index repair deferred; primary acceptance retained",
                exc_info=True,
            )

    def _fetch_next_message(self, config: QueueRuntimeConfig) -> tuple[str, int] | None:
        """Reserve the exact eligible primary ID, consuming only its pending hint [MF-6]."""
        if config.name != self._queue_names["inbox"]:
            return super()._fetch_next_message(config)
        request = None if self._family_invalid else self._peek_family_candidate()
        stamp = (
            next(iter(self._family_invalid))
            if self._family_invalid
            else (request.request_id if request else None)
        )
        if stamp is None:
            return None
        assert config.reserved_queue_name is not None
        result = config.queue.move_one(
            config.reserved_queue_name, exact_timestamp=stamp, with_timestamps=True
        )
        # A concurrent return can make the exact request pending again. Claimed
        # rows are consumed/deletion-pending, so they cannot retain a candidate.
        if result is None and config.queue.peek_one(exact_timestamp=stamp) is not None:
            return None
        self._family_invalid.pop(stamp, None)
        if request is not None:
            try:
                with self._get_connected_queue().get_connection() as broker:
                    broker.delete_message_ids(
                        request.queue_name, [request.label_message_id]
                    )
            except (BrokerError, OSError, RuntimeError):
                logger.debug(
                    "Pending hint cleanup deferred after durable reservation",
                    exc_info=True,
                )
            self._family_scheduler.remove_pending(stamp)
        self._family_candidate = None
        return result

    def _spawn_submitter_tid(
        self, payload: Mapping[str, Any], source_queue: str
    ) -> str | None:
        """Validate same-context lineage on the primary or trusted internal source [MF-6]."""
        if not isinstance(payload, Mapping):
            raise TypeError("spawn request must be a JSON object")
        submitted_by = payload.get("submitted_by")
        if submitted_by is None:
            return None
        if not isinstance(submitted_by, Mapping):
            raise TypeError("submitted_by must be a runtime lineage record")
        return validate_submission_lineage(
            submitted_by, canonical_runtime_context(self._db_path)
        )[1]

    def _build_child_spec(
        self, payload: dict[str, Any], timestamp: int
    ) -> TaskSpec | None:
        """Parse spawn payload and validate a child TaskSpec (Spec: [MA-1.1], [MA-2])."""
        provided_spec = payload.get("taskspec")
        try:
            bundle_root = None
            if provided_spec is not None:
                transported = decode_taskspec_transport_payload(
                    provided_spec,
                    template=True,
                )
                candidate = transported.model_dump(mode="json")
                bundle_root = transported.get_bundle_root()
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
            if (
                isinstance(internal_runtime_task_class, str)
                and internal_runtime_task_class
            ):
                metadata[INTERNAL_RUNTIME_TASK_CLASS_KEY] = internal_runtime_task_class

            internal_endpoint_name = payload.get(
                INTERNAL_RUNTIME_ENVELOPE_ENDPOINT_NAME_KEY
            )
            if isinstance(internal_endpoint_name, str) and internal_endpoint_name:
                metadata[INTERNAL_RUNTIME_ENDPOINT_NAME_KEY] = internal_endpoint_name

            submitted_by = payload.get("submitted_by")
            parent_tid = (
                submitted_by.get("tid") if isinstance(submitted_by, Mapping) else None
            )
            metadata.setdefault("parent_tid", parent_tid or self.tid)

            child_spec = validate_taskspec_payload(
                candidate,
                bundle_root=bundle_root,
                resolved_tid=str(timestamp),
                inherited_weft_context=getattr(
                    self.taskspec.spec, "weft_context", None
                ),
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

    def _liveness_monitor_supervision_allowed(self) -> bool:
        """Return whether this manager may supervise a liveness monitor."""

        return self._liveness_monitor_enabled and self._service_supervision_allowed()

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

    def _build_liveness_monitor_spawn_payload(self) -> dict[str, Any]:
        """Build the manager-owned spawn envelope for the liveness monitor."""

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
                "name": "liveness-monitor",
                "spec": spec_section,
                "metadata": service_metadata(
                    key=INTERNAL_SERVICE_KEY_LIVENESS_MONITOR,
                    lifecycle="ensure",
                    extra={
                        "internal": True,
                        "role": "liveness_monitor",
                    },
                ),
            },
            "inbox_message": None,
            INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY: (
                INTERNAL_RUNTIME_TASK_CLASS_LIVENESS_MONITOR
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

    def _liveness_monitor_service_spec(self) -> ManagedServiceSpec:
        return ManagedServiceSpec(
            key=INTERNAL_SERVICE_KEY_LIVENESS_MONITOR,
            lifecycle="ensure",
            spawn_payload=self._build_liveness_monitor_spawn_payload(),
            restart_backoff_ns=self._task_monitor_restart_backoff_ns,
        )

    def _managed_service_spawn_queue_name(self, service: ManagedServiceSpec) -> str:
        """Return the spawn queue for one manager-owned service request."""

        if (
            service.key
            in {
                INTERNAL_SERVICE_KEY_HEARTBEAT,
                INTERNAL_SERVICE_KEY_TASK_MONITOR,
                INTERNAL_SERVICE_KEY_LIVENESS_MONITOR,
            }
            and self._internal_spawn_queue_attached()
        ):
            return self._queue_names["internal_inbox"]
        return self._queue_names["inbox"]

    def _enqueue_managed_service_request(self, service: ManagedServiceSpec) -> bool:
        """Enqueue one manager-owned service spawn request."""

        queue_name = self._managed_service_spawn_queue_name(service)
        try:
            request_id = self._queue(queue_name).write(
                json.dumps(service.spawn_payload, ensure_ascii=False)
            )
            if queue_name == self._queue_names.get("internal_inbox"):
                self._managed_internal_spawn_enqueued = True
            self._invalidate_leadership_work_cache()
            self._strategy.notify_activity()
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
        state.pending_request_id = request_id
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

    def _enqueue_liveness_monitor_request(self) -> bool:
        """Enqueue one internal liveness monitor spawn request."""

        return self._enqueue_managed_service_request(
            self._liveness_monitor_service_spec()
        )

    @staticmethod
    def _service_key_for_child(child: ManagedChild) -> str | None:
        """Return the manager-supervised service key for a tracked child."""

        if child.service_key is not None:
            return child.service_key
        if child.internal_role == INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR:
            return INTERNAL_SERVICE_KEY_TASK_MONITOR
        if child.internal_role == INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT:
            return INTERNAL_SERVICE_KEY_HEARTBEAT
        if child.internal_role == INTERNAL_RUNTIME_TASK_CLASS_LIVENESS_MONITOR:
            return INTERNAL_SERVICE_KEY_LIVENESS_MONITOR
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
            if scan_terminal_proof and self._tracked_service_terminal_proof_visible(
                tid, child
            ):
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

    def _tracked_service_terminal_proof_visible(
        self, tid: str, child: ManagedChild
    ) -> bool:
        """Preserve explicit singleton audits without scanning history on reaping.

        Spec: docs/specifications/03-Manager_Architecture.md [MA-1.6a]
        """
        if self._child_terminal_proof_visible(tid, child) is True:
            return True
        try:
            since_timestamp = int(tid) - 1 if tid.isdigit() else None
            entries = iter_queue_json_entries(
                self._queue(WEFT_GLOBAL_LOG_QUEUE),
                since_timestamp=since_timestamp,
                strict=True,
            )
            with closing_queue_iterator(entries) as rows:
                for payload, _timestamp in rows:
                    status = payload.get("status")
                    if (
                        payload.get("tid") == tid
                        and isinstance(status, str)
                        and status in TERMINAL_TASK_STATUSES
                    ):
                        return True
        except (BrokerError, OSError, RuntimeError):
            logger.debug("Failed to audit service terminal proof", exc_info=True)
        return False

    @staticmethod
    def _child_matches_service(child: ManagedChild, service_key: str) -> bool:
        return Manager._service_key_for_child(child) == service_key

    def _terminate_duplicate_service_candidates(  # noqa: C901 approved [TS-3.1] [RUFF-SUP-012] exception
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
        """Read the canonical newest valid mapping handle ([OBS.4])."""
        with self._get_connected_queue().get_connection() as broker:
            latest = read_task_state_snapshot(
                self._manager_context(), tid, broker=broker
            )
        if latest is None:
            return None
        _timestamp, latest_payload = latest

        handle_payload = latest_payload.get("runtime_handle")
        if not isinstance(handle_payload, Mapping):
            return None
        try:
            return RunnerHandle.from_dict(handle_payload)
        except ValueError:
            return None

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
        source: Literal["control-pong", "service-registry-pong"],
        resolve_unanswered: bool = False,
    ) -> ServiceCandidate | None:
        """Advance or start one non-blocking service-owner PING probe.

        A pending probe is unknown service evidence. The caller falls through
        to normal recent/stale classification only on the next eligible
        ordinary active-service evaluation.
        """

        key = self._service_probe_key(
            source=source,
            service_key=service_key,
            tid=tid,
            timestamp=timestamp,
        )
        pending = self._service_probe_pending.get(key)
        if pending is not None:
            return self._advance_service_pong_probe(
                pending,
                timestamp=timestamp,
                metadata=metadata,
                resolve_unanswered=resolve_unanswered,
            )

        request_id = uuid.uuid4().hex
        ping_message = encode_control_message(
            CONTROL_PING,
            request_id=request_id,
            reply_to=self._queue_names["ctrl_in"],
        )
        try:
            with self._get_connected_queue().get_connection() as broker:
                broker.write(ctrl_in_name, ping_message)
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
        self._service_probe_pending[key] = _ServicePendingPongProbe(
            key=key,
            service_key=service_key,
            tid=tid,
            row_timestamp=timestamp,
            source=source,
            request_id=request_id,
            created_turn=self._loop_iteration,
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
        resolve_unanswered: bool,
    ) -> ServiceCandidate | None:
        """Advance one event-fed service PING on its owning policy cadence."""

        if probe.pong is not None:
            self._service_probe_pending.pop(probe.key, None)
            return ServiceCandidate(
                key=probe.service_key,
                tid=probe.tid,
                state="live",
                source=probe.source,
                timestamp=timestamp,
                metadata=metadata,
            )

        if resolve_unanswered and probe.created_turn < self._loop_iteration:
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
        resolve_unanswered: bool = False,
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
            ctrl_in_name, _ctrl_out_name = ctrl_queues
            probe_candidate = self._service_pong_candidate(
                service_key=service_key,
                tid=tid,
                timestamp=timestamp,
                metadata=candidate_metadata,
                ctrl_in_name=ctrl_in_name,
                source="control-pong",
                resolve_unanswered=resolve_unanswered,
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

    def _service_candidate_from_service_owner_record(  # noqa: C901 approved [TS-3.1] [RUFF-SUP-012] exception
        self,
        record: ServiceOwnerRecord,
        *,
        resolve_unanswered: bool = False,
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
                source="service-registry-pong",
                resolve_unanswered=resolve_unanswered,
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
        if key is None:
            return None
        expected_identity = {
            INTERNAL_SERVICE_KEY_HEARTBEAT: (
                "heartbeat_service",
                INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT,
            ),
            INTERNAL_SERVICE_KEY_TASK_MONITOR: (
                "task_monitor",
                INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR,
            ),
            INTERNAL_SERVICE_KEY_LIVENESS_MONITOR: (
                "liveness_monitor",
                INTERNAL_RUNTIME_TASK_CLASS_LIVENESS_MONITOR,
            ),
        }.get(key)
        if expected_identity is None or metadata.get("internal") is not True:
            return None
        expected_role, expected_runtime_class = expected_identity
        if (
            metadata.get("role") != expected_role
            or runtime_class != expected_runtime_class
        ):
            return None

        endpoint = metadata.get(INTERNAL_RUNTIME_ENDPOINT_NAME_KEY)
        if key == INTERNAL_SERVICE_KEY_HEARTBEAT:
            if endpoint is not None and endpoint != INTERNAL_HEARTBEAT_ENDPOINT_NAME:
                return None
        elif key == INTERNAL_SERVICE_KEY_LIVENESS_MONITOR and endpoint is not None:
            return None
        return key

    def _trusted_service_key_from_metadata(
        self,
        metadata: Any,
        *,
        desired_keys: set[str],
        runtime_class: str | None = None,
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
        if key in {
            INTERNAL_SERVICE_KEY_HEARTBEAT,
            INTERNAL_SERVICE_KEY_TASK_MONITOR,
            INTERNAL_SERVICE_KEY_LIVENESS_MONITOR,
        }:
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
        if not manager_authority:
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
        """Confirm known requests before strict durable recovery.

        Spec: docs/specifications/03-Manager_Architecture.md [MA-1.6a]
        """

        if not desired_keys:
            return set()
        pending: set[str] = {
            request.service_key
            for request in self._active_child_launches.values()
            if request.service_key in desired_keys
        }
        unresolved = desired_keys - pending
        if not unresolved:
            return pending
        scan_queue_names = tuple(
            dict.fromkeys(
                (
                    *self._spawn_inbox_queue_names(),
                    *(
                        reserved_queue
                        for reserved_queue, _source_queue in (
                            self._spawn_reserved_queue_pairs()
                        )
                    ),
                )
                if queue_names is None
                else queue_names
            )
        )
        for key in tuple(unresolved):
            request_id = self._service_state(key).pending_request_id
            if request_id is None:
                continue
            if self._pending_request_reference_is_valid(
                key, request_id, scan_queue_names
            ):
                pending.add(key)
                unresolved.remove(key)
        if not unresolved:
            return pending
        return pending | self._recover_pending_service_requests(
            unresolved, scan_queue_names
        )

    def _recover_pending_service_requests(
        self, unresolved: set[str], scan_queue_names: Sequence[str]
    ) -> set[str]:
        """Recover unknown references; only a complete scan proves absence.

        Spec: docs/specifications/03-Manager_Architecture.md [MA-1.6a]
        """
        pending: set[str] = set()
        for queue_name in scan_queue_names:
            queue = self._queue(queue_name)
            entries = iter_queue_json_entries(queue, strict=True)
            with closing_queue_iterator(entries) as rows:
                for payload, timestamp in rows:
                    key = self._spawn_request_service_key(
                        payload,
                        desired_keys=unresolved,
                    )
                    if key is not None:
                        self._service_state(key).pending_request_id = timestamp
                        pending.add(key)
                        unresolved.remove(key)
                    if not unresolved:
                        return pending
        # Only complete successful recovery establishes absence. An exception
        # above leaves unresolved references intact for the next observation.
        for key in unresolved:
            self._service_state(key).pending_request_id = None
        return pending

    def _pending_request_reference_is_valid(
        self, key: str, request_id: int, queue_names: Sequence[str]
    ) -> bool:
        """Confirm one trusted request within the existing queue scope.

        Spec: docs/specifications/03-Manager_Architecture.md [MA-1.6a]
        """
        for name in queue_names:
            row = self._queue(name).peek_one(
                exact_timestamp=request_id, with_timestamps=True
            )
            if not isinstance(row, tuple):
                continue
            try:
                payload = json.loads(row[0])
            except json.JSONDecodeError:
                continue
            if (
                isinstance(payload, dict)
                and self._spawn_request_service_key(payload, desired_keys={key}) == key
            ):
                return True
        return False

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
        if (
            isinstance(child_pid, int)
            and not isinstance(child_pid, bool)
            and child_pid > 0
        ):
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

    def _preserve_pending_service_probes_after_failed_scan(
        self,
        candidates_by_key: dict[str, list[ServiceCandidate]],
    ) -> None:
        """Keep pending probes as evidence when a registry scan proves nothing."""

        for probe in tuple(self._service_probe_pending.values()):
            if probe.service_key not in candidates_by_key:
                continue
            candidate = self._advance_service_pong_probe(
                probe,
                timestamp=probe.row_timestamp,
                metadata={},
                resolve_unanswered=False,
            )
            if candidate is not None:
                candidates_by_key[probe.service_key].append(candidate)

    def _observed_service_candidates_by_key(
        self,
        desired_keys: set[str],
        *,
        tracked_by_key: Mapping[str, ServiceCandidate | None] | None = None,
        scan_terminal_proof: bool = False,
        resolve_unanswered: bool = False,
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
        try:
            registry_entries = tuple(iter_queue_json_entries(queue, strict=True))
        except (BrokerError, OSError, RuntimeError):
            logger.debug("Failed to scan service-owner registry", exc_info=True)
            self._preserve_pending_service_probes_after_failed_scan(candidates_by_key)
            return candidates_by_key
        read = collect_service_owner_records(
            registry_entries,
            service_type=SERVICE_TYPE_MANAGED,
        )
        now_ns = time.time_ns()
        observed_probe_sources: set[str] = set()
        for service_key in desired_keys:
            decision = reduce_service_ownership(
                service_key,
                read.records,
                own_tid=None,
                now_ns=now_ns,
                ttl_ns=self._manager_registry_retention_ns(),
                read_failed=read.read_failed,
            )
            for record in decision.records:
                observed_probe_sources.add(
                    self._service_probe_key(
                        source="service-registry-pong",
                        service_key=record.service_key,
                        tid=record.owner_tid,
                        timestamp=record.timestamp,
                    )
                )
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
                    self._service_candidate_from_service_owner_record(
                        record,
                        resolve_unanswered=resolve_unanswered,
                    )
                )
        for key, probe in tuple(self._service_probe_pending.items()):
            if (
                probe.source == "service-registry-pong"
                and probe.service_key in desired_keys
                and key not in observed_probe_sources
            ):
                self._service_probe_pending.pop(key, None)
        return candidates_by_key

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
        state.pending_request_id = next_state.pending_request_id
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

    def _reconcile_managed_services(  # noqa: C901 approved [TS-3.1] [RUFF-SUP-012] exception
        self,
        *,
        force: bool = False,
        include_internal: bool = True,
        include_autostart: bool = True,
        scan_terminal_proof: bool = False,
        resolve_unanswered: bool = False,
    ) -> None:
        """Reconcile all Manager-owned singleton services through one path."""

        if not self._service_supervision_allowed():
            return

        services: list[ManagedServiceSpec] = []
        internal_services: list[ManagedServiceSpec] = []
        autostart_services: list[ManagedServiceSpec] = []
        known_autostart_sources = set(self._autostart_sources)
        stored_autostart_reply = include_autostart and any(
            probe.pong is not None and probe.service_key in known_autostart_sources
            for probe in self._service_probe_pending.values()
        )
        if include_internal and self._queue_names["inbox"] == WEFT_SPAWN_REQUESTS_QUEUE:
            # TaskMonitor is the only internal heartbeat dependent; LivenessMonitor
            # schedules from its own due heap. Do not run heartbeat as standalone
            # background work when no dependent service is enabled ([MA-1] item 7).
            if self._task_monitor_enabled:
                internal_services.append(self._heartbeat_service_spec())
            if self._task_monitor_enabled:
                internal_services.append(self._task_monitor_service_spec())
            if self._liveness_monitor_enabled:
                internal_services.append(self._liveness_monitor_service_spec())
        if include_autostart:
            autostart_services.extend(
                self._desired_autostart_services(
                    force=force or stored_autostart_reply,
                )
            )
            if self._autostart_last_scan_complete:
                for key, probe in tuple(self._service_probe_pending.items()):
                    if (
                        probe.service_key in known_autostart_sources
                        and probe.service_key not in self._autostart_sources
                    ):
                        self._service_probe_pending.pop(key, None)
        services.extend(internal_services)
        services.extend(autostart_services)
        if not services:
            return

        desired_keys = {service.key for service in services}
        tracked_by_key = {
            service.key: self._tracked_service_candidate(
                service.key,
                scan_terminal_proof=scan_terminal_proof,
            )
            for service in services
        }
        needs_pending = {
            key
            for key, candidate in tracked_by_key.items()
            if candidate is None or candidate.state != "live"
        }
        pending_keys: set[str] = set()
        internal_keys = {service.key for service in internal_services} & needs_pending
        if internal_keys:
            if self._internal_spawn_queue_attached():
                pending_keys.update(
                    self._pending_service_keys(
                        internal_keys,
                        queue_names=(
                            self._queue_names["internal_inbox"],
                            self._queue_names["internal_reserved"],
                        ),
                    )
                )
            else:
                pending_keys.update(self._pending_service_keys(internal_keys))
        autostart_keys = {service.key for service in autostart_services} & needs_pending
        if autostart_keys:
            pending_keys.update(self._pending_service_keys(autostart_keys))
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
        keys_needing_evidence.update(
            probe.service_key
            for probe in self._service_probe_pending.values()
            if probe.service_key in desired_keys
        )

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
                resolve_unanswered=resolve_unanswered,
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
            if (
                service.key in keys_needing_evidence
                and tracked is not None
                and tracked.state == "live"
            ):
                self._managed_service_duplicate_scan_pending.discard(service.key)

    def _tick_internal_services(self, *, force: bool = False) -> None:
        """Drive one convergence pass over all built-in Manager-owned services.

        Test/diagnostic seam only. Production scheduling of internal-service
        convergence is owned by ``_run_managed_service_convergence``; this wrapper
        is not on the production reactor path.
        """

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
            return Path(spec_context) / get_weft_directory_name(self._weft_config)
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
        return PipelineCompilationContext(
            root=context_root,
            broker_target=cast(BrokerTarget, self._db_path),
            broker_config=self._broker_config,
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
        return encode_taskspec_transport_payload(
            validate_taskspec_payload(
                resolved.payload,
                bundle_root=resolved.bundle_root,
                template=True,
            )
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
            return encode_taskspec_transport_payload(
                validate_taskspec_payload(
                    stage_resolved.payload,
                    bundle_root=stage_resolved.bundle_root,
                    template=True,
                )
            )

        try:
            with self._get_connected_queue().get_connection() as broker:
                compiled = compile_linear_pipeline(
                    pipeline_spec,
                    context=pipeline_context,
                    task_loader=_load_pipeline_stage,
                    source_ref=str(resolved.path),
                    broker=broker,
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

    def _build_autostart_spawn_payload(  # noqa: C901 approved [TS-3.1] [RUFF-SUP-013] exception
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
        paths, _complete = self._autostart_manifest_path_snapshot()
        return paths

    def _autostart_manifest_path_snapshot(self) -> tuple[list[Path], bool]:
        """Return manifest paths and whether absence is authoritative."""

        directory = self._autostart_dir
        if not directory or not directory.exists():
            return [], True
        try:
            return (
                sorted(path for path in directory.glob("*.json") if path.is_file()),
                True,
            )
        except OSError:
            logger.debug(
                "Failed to enumerate autostart manifests in %s",
                directory,
                exc_info=True,
            )
            return [], False

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
            state = self._managed_service_state.get(source)
            if state is None:
                return True
            if not state.launched_once:
                return True
            if max_restarts is None:
                return True
            if state.restarts < max_restarts:
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
        handle = self._latest_tid_runtime_handle(tid)
        if handle is None:
            return set()
        # Create-time-gated so a recycled PID held by a stale mapping is never
        # force-reaped ([MA-1] item 4); falls back to liveness when no create_time
        # was recorded. Mirrors the liveness path's resolution.
        return {pid for pid, _create_time in live_host_processes_from_handle(handle)}

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
        stale_sources = self._autostart_sources - manifest_sources
        for source in stale_sources:
            self._managed_service_state.pop(source, None)
        self._autostart_sources = set(manifest_sources)

    def _desired_autostart_services(
        self, *, force: bool = False
    ) -> list[ManagedServiceSpec]:
        """Return desired autostart services after manifest and policy checks."""

        self._autostart_last_scan_complete = False
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
            self._autostart_last_scan_complete = True
            return []

        manifests, scan_complete = self._autostart_manifest_path_snapshot()
        if not scan_complete:
            return []
        manifest_sources = {self._autostart_manifest_source(path) for path in manifests}
        self._prune_autostart_state(manifest_sources)
        self._autostart_last_scan_complete = True

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
            self._service_state(source)

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
        """Advance canonical autostart lifecycle state after enqueue."""

        source = service.autostart_source
        if source is None:
            return
        state = self._service_state(source)
        if launched_before:
            state.restarts += 1
        state.launched_once = True
        if service.restart_backoff_ns > 0:
            multiplier = max(0, state.restarts)
            state.next_allowed_ns = now_ns + service.restart_backoff_ns * (
                2**multiplier
            )
        else:
            state.next_allowed_ns = 0
        if state.next_allowed_ns:
            self._schedule_autostart_rescan_at(state.next_allowed_ns)

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

    def _pending_service_probes_for_scope(
        self,
        *,
        include_autostart: bool,
    ) -> tuple[_ServicePendingPongProbe, ...]:
        """Return pending probes owned by the current convergence scope."""

        internal_keys: set[str] = set()
        if self._queue_names["inbox"] == WEFT_SPAWN_REQUESTS_QUEUE:
            if self._task_monitor_enabled:
                internal_keys.update(
                    {
                        INTERNAL_SERVICE_KEY_HEARTBEAT,
                        INTERNAL_SERVICE_KEY_TASK_MONITOR,
                    }
                )
            if self._liveness_monitor_enabled:
                internal_keys.add(INTERNAL_SERVICE_KEY_LIVENESS_MONITOR)
        return tuple(
            probe
            for probe in self._service_probe_pending.values()
            if probe.service_key in internal_keys
            or include_autostart
            and probe.service_key in self._autostart_sources
        )

    def _pending_service_probe_active_reasons(
        self,
        *,
        include_autostart: bool,
    ) -> tuple[str, ...]:
        """Return the convergence reason contributed by pending PONG probes."""

        if self._pending_service_probes_for_scope(include_autostart=include_autostart):
            return ("pong_probe_pending",)
        return ()

    def _managed_service_convergence_active_reasons(
        self,
        *,
        include_autostart: bool,
        internal_spawn_pending: bool | None = None,
        include_broker: bool = True,
    ) -> tuple[str, ...]:
        """Return why manager-owned service convergence is active."""

        reasons = list(
            self._pending_service_probe_active_reasons(
                include_autostart=include_autostart
            )
        )
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
            INTERNAL_SERVICE_KEY_LIVENESS_MONITOR,
        }
        for service_key, state in self._managed_service_state.items():
            service_unsettled = state.spawn_pending or state.active_tid is None
            if state.spawn_pending:
                reasons.append("spawn_pending")
            if (
                service_unsettled
                and service_key in internal_keys
                and (
                    service_key == INTERNAL_SERVICE_KEY_TASK_MONITOR
                    and self._task_monitor_enabled
                    or service_key == INTERNAL_SERVICE_KEY_LIVENESS_MONITOR
                    and self._liveness_monitor_enabled
                    or service_key == INTERNAL_SERVICE_KEY_HEARTBEAT
                    and self._task_monitor_enabled
                )
                and self._queue_names["inbox"] == WEFT_SPAWN_REQUESTS_QUEUE
            ):
                reasons.append("missing_active_tid")
            if state.uncertain_attempts > 0:
                reasons.append("uncertain_attempts")
        if (
            include_autostart
            and self._autostart_enabled
            and self._autostart_dir
            and (
                self._autostart_last_scan_ns == 0
                or time.time_ns() - self._autostart_last_scan_ns
                >= self._autostart_scan_interval_ns
            )
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
        autostart_scan_due = "autostart_scan_due" in local_active_reasons
        stored_reply_due = any(
            probe.pong is not None
            for probe in self._pending_service_probes_for_scope(
                include_autostart=include_autostart
            )
        )
        ordinary_due = (
            self._last_managed_service_convergence_ns <= 0
            or now_ns - self._last_managed_service_convergence_ns >= interval_ns
        )
        if (
            not force
            and not self._managed_internal_spawn_enqueued
            and not autostart_scan_due
            and not stored_reply_due
            and not ordinary_due
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
        if not force and (
            ordinary_due or self._managed_internal_spawn_enqueued or autostart_scan_due
        ):
            self._last_managed_service_convergence_ns = now_ns
        internal_spawn_pending = self._internal_spawn_pending()
        if internal_spawn_pending:
            self._mark_pending_messages_prechecked()
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
            self._reconcile_managed_services(
                include_autostart=include_autostart,
                resolve_unanswered=ordinary_due,
            )
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
    def _drain_active_child_launches_for_cleanup(self, deadline: float) -> None:
        """Commit in-flight child launches before child termination cleanup."""

        while (
            self._has_active_child_launches() and self._remaining_deadline(deadline) > 0
        ):
            handled = self._drain_worker_results()
            if not self._has_active_child_launches():
                return
            if handled:
                continue
            if not self._has_worker_activity():
                logger.warning(
                    "Discarding stale child launch requests during manager cleanup: %s",
                    sorted(self._active_child_launches),
                )
                self._active_child_launches.clear()
                return
            self._sync_worker_result_event()
            self._worker_result_event.wait(
                timeout=min(0.05, self._remaining_deadline(deadline))
            )

    def _cleanup_task_resources(self, deadline: float) -> None:
        """Release Manager-owned children and registrations by the deadline.

        Spec: docs/specifications/07-System_Invariants.md [IMPL.10]
        """

        self._draining = True
        self._active_cleanup_deadline = deadline
        self._clear_startup_notification_interest()
        failures: list[BaseException] = []

        def attempt(operation: Callable[[], None]) -> None:
            try:
                operation()
            except BaseException as exc:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-377] exception
                failures.append(exc)

        attempt(lambda: self._drain_active_child_launches_for_cleanup(deadline))
        attempt(lambda: self._terminate_children(deadline))
        attempt(self._cleanup_own_internal_reserved_queue)
        attempt(self._unregister_manager)
        attempt(
            lambda: self._child_sentinel_adapter.close(
                timeout=max(
                    self._remaining_deadline(deadline),
                    MANAGER_CHILD_SENTINEL_OBSERVER_CLOSE_TIMEOUT_SECONDS,
                )
            )
        )
        attempt(lambda: super(Manager, self)._cleanup_task_resources(deadline))
        attempt(self._unregister_atexit_callback)
        if failures:
            raise failures[0]

    def _abort_partial_initialization(self) -> None:
        """Close Manager-owned adapters during constructor unwind."""

        self._clear_startup_notification_interest()
        failures: list[BaseException] = []
        adapter = getattr(self, "_child_sentinel_adapter", None)
        if adapter is not None:
            try:
                adapter.close(timeout=0.05)
            except BaseException as exc:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-377] exception
                failures.append(exc)
        try:
            super()._abort_partial_initialization()
        except BaseException as exc:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-377] exception
            failures.append(exc)
        if failures:
            raise failures[0]

    def _admission_retry_timeouts(self, *, now_ns: int) -> list[float]:
        """Return dispatch retry and bounded evidence-maintenance deadlines."""

        timeouts = []
        if self._admission_bootstrapped:
            timeouts.append(max(0.0, self._family_repair_due - time.monotonic()))
        if self._family_classification_pending:
            timeouts.append(0.0)
        if self._admission_retry_after_ns > 0:
            timeouts.append(
                self._timeout_until_ns(self._admission_retry_after_ns, now_ns=now_ns)
            )
        if (
            not self._admission_bootstrapped
            or self._scheduler_residents
            or self._admission_evidence_unavailable
        ):
            timeouts.append(max(0.0, self._admission_refresh_after - time.monotonic()))
        return timeouts

    def next_wait_timeout(self) -> float | None:
        """Return the next manager due timer for the shared task loop."""

        now_ns = time.time_ns()
        timeouts = self._manager_child_timeouts(now_ns=now_ns)
        if self.should_stop or self._has_pending_termination_request():
            return 0.0
        timeouts.extend(self._manager_drain_timeouts(now_ns=now_ns))
        timeouts.extend(self._stale_child_launch_timeouts(now_ns=now_ns))
        if self._stalled_control_retry_after_ns > 0:
            timeouts.append(
                self._timeout_until_ns(
                    self._stalled_control_retry_after_ns,
                    now_ns=now_ns,
                )
            )
        if self._draining:
            return min(timeouts) if timeouts else None
        timeouts.extend(self._admission_retry_timeouts(now_ns=now_ns))
        base_timeout = super().next_wait_timeout()
        if base_timeout is not None:
            timeouts.append(base_timeout)
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
        if self._idle_timeout > 0 and self._last_broker_probe_ns > 0:
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

    def _manager_drain_timeouts(self, *, now_ns: int) -> list[float]:
        """Return exact clocks needed while a Manager drain is in progress."""

        if not self._draining:
            return []
        if self._drain_immediate_work_pending:
            return [0.0]

        timeouts: list[float] = []
        if self._drain_stops_children:
            if self._child_processes and self._drain_started_ns is not None:
                shutdown_ns = int(
                    MANAGER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS * 1_000_000_000
                )
                timeouts.append(
                    self._timeout_until_ns(
                        self._drain_started_ns + shutdown_ns,
                        now_ns=now_ns,
                    )
                )
            escalation_ns = int(MANAGER_CHILD_STOP_ESCALATION_SECONDS * 1_000_000_000)
            for tid in self._child_processes:
                if tid in self._drain_escalated_children:
                    continue
                started_ns = self._drain_signal_started_ns.get(tid)
                if started_ns is None:
                    continue
                timeouts.append(
                    self._timeout_until_ns(
                        started_ns + escalation_ns,
                        now_ns=now_ns,
                    )
                )
            return timeouts

        if self._drain_leader_tid is not None:
            if self._last_leadership_drain_revalidate_ns <= 0:
                timeouts.append(0.0)
            else:
                interval_ns = int(
                    MANAGER_LEADERSHIP_DRAIN_REVALIDATE_SECONDS * 1_000_000_000
                )
                timeouts.append(
                    self._timeout_until_ns(
                        self._last_leadership_drain_revalidate_ns + interval_ns,
                        now_ns=now_ns,
                    )
                )
        return timeouts

    def _stale_child_launch_timeouts(self, *, now_ns: int) -> list[float]:
        """Return recovery deadlines for launch workers that retired silently."""

        if not self._active_child_launches or self._has_worker_activity():
            return []
        grace_ns = int(MANAGER_CHILD_STARTUP_LIVENESS_GRACE_SECONDS * 1_000_000_000)
        return [
            self._timeout_until_ns(started_ns + grace_ns, now_ns=now_ns)
            for tid in self._active_child_launches
            if (started_ns := self._child_launch_started_ns.get(tid, 0)) > 0
        ]

    def _manager_child_timeouts(self, *, now_ns: int) -> list[float]:
        """Return child sentinel and post-exit deadlines."""

        timeouts: list[float] = []
        sentinel_recheck_ns = int(MANAGER_PID_LIVENESS_RECHECK_INTERVAL * 1_000_000_000)
        terminal_grace_ns = int(
            MANAGER_CHILD_TERMINAL_PROOF_GRACE_SECONDS * 1_000_000_000
        )
        for child in self._child_processes.values():
            if child.terminal_retry_after_ns > 0:
                timeouts.append(
                    self._timeout_until_ns(child.terminal_retry_after_ns, now_ns=now_ns)
                )
                continue
            if (
                child.sentinel_observed_ns > 0
                and child.terminal_proof_missing_since_ns <= 0
            ):
                due_ns = (
                    max(
                        child.sentinel_observed_ns,
                        child.last_liveness_probe_ns,
                    )
                    + sentinel_recheck_ns
                )
                timeouts.append(self._timeout_until_ns(due_ns, now_ns=now_ns))
            if child.terminal_proof_missing_since_ns > 0:
                due_ns = child.terminal_proof_missing_since_ns + terminal_grace_ns
                timeouts.append(self._timeout_until_ns(due_ns, now_ns=now_ns))
        return timeouts

    def _process_reactor_turn(self) -> None:
        """Run one Manager reactor turn."""

        self._drain_child_sentinel_events()
        self._timed_manager_call("turn", self._process_manager_reactor_turn)

    def _wait_for_reactor_activity(self, timeout: float | None) -> None:
        """Measure the existing arbiter wait, without adding a deadline [MA-3]."""
        wait = super()._wait_for_reactor_activity
        self._timed_manager_call("activity_wait", lambda: wait(timeout))

    def _ensure_wait_strategy_started(self) -> None:
        """Separate notification-interest setup from blocking in the arbiter."""
        self._timed_manager_call("wait_setup", super()._ensure_wait_strategy_started)

    def _process_manager_reactor_turn(self) -> None:  # noqa: C901 approved [TS-3.1] [RUFF-SUP-014] exception
        """Run one Manager turn behind the owner-enforcing template.

        Spec:
        - docs/specifications/01-Core_Components.md [CC-2.2.1], [CC-2.3]
        - docs/specifications/05-Message_Flow_and_State.md [MF-6]
        """
        self._loop_iteration += 1
        self._expire_admission_retry_if_due()
        self._child_launch_started_this_turn = False
        self._leader_probe_used_this_turn = False
        self._leader_actionable_work_cache = None
        self._leader_check_turn = None
        self._timed_manager_call("worker_results", self._drain_worker_results)
        for tid, feedback in tuple(self._startup_feedback.items()):
            if feedback.publication_pending:
                feedback.publication_pending = False
                self._timed_manager_call(
                    "startup_handoff",
                    partial(self._reconcile_launch_now, tid),
                    child_tid=tid,
                )
        self._retry_stale_child_launches()
        self._drain_control_queue_first()
        if self._draining:
            # Finish an in-flight drain before reevaluating leadership. Otherwise a
            # slow turn can re-enter the yield path and skip the corresponding
            # *_drained completion event once children are gone.
            self._continue_shutdown_drain()
            self._drain_worker_results()
            return
        self._timed_manager_call("evidence_refresh", self._refresh_scheduler_evidence)
        self._timed_manager_call(
            "family_classification", self._classify_family_requests
        )
        self._emit_manager_loop_summary()
        self._timed_manager_call("registration", self._refresh_manager_registration)
        self._timed_manager_call(
            "stale_reservations", self._cleanup_stale_internal_reserved_queues
        )
        if self._timed_manager_call("leadership", self._maybe_yield_leadership):
            self._drain_worker_results()
            return
        if self._timed_manager_call("child_cleanup", self._cleanup_children):
            self._run_managed_service_convergence(
                include_autostart=False,
                force=True,
            )
            if self._draining:
                self._continue_shutdown_drain()
                self._drain_worker_results()
                return
        if not self._has_active_child_launches():
            self._timed_manager_call("queue_dispatch", super()._process_reactor_turn)
        else:
            self._drain_worker_results()
            self._maybe_emit_poll_report()
        public_inbox = self._queue_names.get("inbox")
        public_drained = 0
        if (
            not self._has_active_child_launches()
            and not self._child_launch_started_this_turn
            and public_inbox == WEFT_SPAWN_REQUESTS_QUEUE
            and any(
                name in self._active_queues for name in self._spawn_inbox_queue_names()
            )
        ):
            public_drained += self._timed_manager_call(
                "public_dispatch", self._drain_public_spawn_requests
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
        self._timed_manager_call(
            "service_convergence", self._run_managed_service_convergence
        )
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
        if self._managed_service_convergence_active(include_autostart=True):
            # Supervision-only service children do not block idle by themselves,
            # but a missing or uncertain manager-owned service is active
            # convergence work until it is either replaced or explicitly
            # suppressed by policy.
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
        """Drain one watcher-active spawn queue.

        Queue pending checks and activity waiters identify work for the shared
        task loop. Once a queue is active, atomic reservation remains the
        ownership authority for the specific spawn message.
        """

        total_processed = 0
        attempted = False
        self._update_active_queues()
        for _ in range(max_messages):
            if (
                self._has_active_child_launches()
                or self._child_launch_started_this_turn
            ):
                break
            if queue_name not in self._active_queues:
                break
            attempted = True
            processed = self._drain_round_robin_pass(queue_names=(queue_name,))
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
        self._drain_escalated_children.clear()
        self._drain_signal_started_ns.clear()
        self._drain_started_ns = None
        self._drain_immediate_work_pending = False
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
        if self._drain_leader_tid is None:
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
        self._drain_immediate_work_pending = False
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
            self._terminate_children(time.monotonic() + TASK_CLEANUP_TIMEOUT_SECONDS)
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
        except Exception:  # pragma: no cover - interpreter shutdown cleanup  # noqa: BLE001,S110 approved [TS-3.1] [RUFF-SUP-283] exception
            pass

    def _register_atexit_callback(self) -> None:
        """Register best-effort manager cleanup for interpreter shutdown."""

        callback = self._atexit_unregister
        self._atexit_callback = callback
        atexit.register(callback)

    def _unregister_atexit_callback(self) -> None:
        """Remove the explicit atexit callback after normal cleanup."""

        callback = self._atexit_callback
        if callback is None:
            return
        self._atexit_callback = None
        try:
            atexit.unregister(callback)
        except Exception:  # pragma: no cover - best-effort normal cleanup  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-284] exception
            logger.warning("Failed to unregister manager atexit callback")


__all__ = ["Manager"]
