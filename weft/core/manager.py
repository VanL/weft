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
from dataclasses import dataclass
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError

from simplebroker import BrokerTarget, Queue
from simplebroker.ext import BrokerError
from weft._constants import (
    CONTROL_STOP,
    INTERNAL_RUNTIME_TASK_CLASS_KEY,
    INTERNAL_RUNTIME_TASK_CLASS_PIPELINE,
    INTERNAL_RUNTIME_TASK_CLASS_PIPELINE_EDGE,
    MANAGER_CHILD_EXIT_POLL_INTERVAL,
    QUEUE_CTRL_IN_SUFFIX,
    SPEC_TYPE_PIPELINE,
    SPEC_TYPE_TASK,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_MANAGER_LIFETIME_TIMEOUT,
    WEFT_MANAGERS_REGISTRY_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
    WORK_ENVELOPE_START,
)
from weft.helpers import (
    is_canonical_manager_record,
    iter_queue_json_entries,
    pid_is_live,
    redact_taskspec_dump,
    terminate_process_tree,
)

from .launcher import launch_task_process
from .pipelines import (
    PipelineCompilationContext,
    compile_linear_pipeline,
    load_pipeline_spec_payload,
)
from .spec_store import resolve_named_spec_from_root
from .tasks import Consumer
from .tasks.base import BaseTask, QueueMessageContext
from .tasks.multiqueue_watcher import QueueMode
from .taskspec import (
    ReservedPolicy,
    TaskSpec,
    apply_bundle_root_to_taskspec_payload,
    resolve_taskspec_payload,
)

logger = logging.getLogger(__name__)


@dataclass
class ManagedChild:
    """Bookkeeping for spawned child processes."""

    process: BaseProcess
    ctrl_queue: str | None
    persistent: bool = False
    autostart_source: str | None = None


class Manager(BaseTask):
    """Task that listens for spawn requests and runs child tasks.

    Spec: [MA-0], [MA-1], [MF-6], [MF-7]
    """

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
        self._autostart_enabled = bool(self._config.get("WEFT_AUTOSTART_TASKS", True))
        autostart_dir = self._config.get("WEFT_AUTOSTART_DIR")
        self._autostart_dir = Path(autostart_dir) if autostart_dir else None
        self._autostart_launched: set[str] = set()
        self._autostart_state: dict[str, dict[str, Any]] = {}
        self._autostart_last_scan_ns = 0
        self._autostart_scan_interval_ns = 1_000_000_000
        self._leader_check_interval_ns = 100_000_000
        self._last_leader_check_ns = 0
        self._broker_activity_queue: Queue | None = None
        self._broker_probe_interval_ns = 1_000_000_000  # probe at most once per second
        self._last_broker_probe_ns = 0
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
        if self._autostart_enabled:
            self._tick_autostart(force=True)
        atexit.register(self._atexit_unregister)

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

    def _handle_control_command(
        self, command: str, context: QueueMessageContext
    ) -> bool:
        """Override STOP to drain active children before manager exit."""

        if command == CONTROL_STOP:
            self._begin_graceful_shutdown(message_id=context.timestamp)
            self._send_control_response("STOP", "ack", draining=True)
            return True
        return super()._handle_control_command(command, context)

    def _launch_child_task(
        self,
        child_spec: TaskSpec,
        inbox_message: Any | None,
        *,
        autostart_source: str | None = None,
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
            try:
                Queue(
                    inbox_name,
                    db_path=self._db_path,
                    persistent=True,
                    config=self._config,
                ).write(payload)
            except (BrokerError, OSError, RuntimeError):
                logger.debug(
                    "Failed to seed inbox %s for child %s",
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

        process = launch_task_process(
            task_cls,
            self._db_path,
            child_spec,
            config=self._config,
        )
        assert child_spec.tid is not None
        self._child_processes[child_spec.tid] = ManagedChild(
            process,
            child_spec.io.control.get("ctrl_in"),
            bool(getattr(child_spec.spec, "persistent", False)),
            autostart_source,
        )
        self._last_activity_ns = time.time_ns()

        event_payload = {
            "child_tid": child_spec.tid,
            "child_taskspec": redact_taskspec_dump(
                child_spec.model_dump(mode="json"), self._taskspec_redaction_paths
            ),
        }
        if autostart_source:
            event_payload["autostart_source"] = autostart_source

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
        raise ValueError(f"unknown internal runtime task class '{runtime_class}'")

    # ------------------------------------------------------------------
    # Manager bookkeeping
    # ------------------------------------------------------------------
    def _register_manager(self) -> None:
        """Publish an active record to the manager registry (Spec: [MA-1.4], [MF-7])."""
        registry_queue = self._queue(WEFT_MANAGERS_REGISTRY_QUEUE)
        timestamp = registry_queue.generate_timestamp()

        payload = {
            "tid": self.tid,
            "name": self.taskspec.name,
            "capabilities": self.taskspec.metadata.get("capabilities", []),
            "status": "active",
            "pid": multiprocessing.current_process().pid,
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

    def _unregister_manager(self) -> None:
        """Replace active record with stopped record on shutdown (Spec: [MA-1.4])."""
        if self._unregistered:
            return
        registry_queue = self._queue(WEFT_MANAGERS_REGISTRY_QUEUE)
        stopped_timestamp = registry_queue.generate_timestamp()

        deletion_performed = False
        try:
            if self._registry_message_id is not None:
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
            "pid": multiprocessing.current_process().pid,
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
            "pid",
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

    def _active_manager_records(self) -> dict[str, dict[str, Any]]:
        queue = self._queue(WEFT_MANAGERS_REGISTRY_QUEUE)
        snapshot: dict[str, dict[str, Any]] = {}
        stale_timestamps: list[int] = []

        for payload, timestamp in iter_queue_json_entries(queue):
            tid = payload.get("tid")
            if not isinstance(tid, str) or not tid:
                continue
            payload["_timestamp"] = timestamp
            existing = snapshot.get(tid)
            existing_timestamp = int(existing.get("_timestamp", -1)) if existing else -1
            if existing is None or existing_timestamp < timestamp:
                snapshot[tid] = payload

        active: dict[str, dict[str, Any]] = {}
        for tid, record in snapshot.items():
            if record.get("status") != "active":
                continue
            if not is_canonical_manager_record(record):
                continue
            pid = record.get("pid")
            if not isinstance(pid, int) or not self._pid_alive(pid):
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

    def _leader_tid(self) -> str | None:
        active = self._active_manager_records()
        if not active:
            return None
        return min(active, key=int)

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
        self._last_leader_check_ns = now_ns

        leader_tid = self._leader_tid()
        if leader_tid is None or leader_tid == self.tid:
            return False

        if self._child_processes:
            if any(child.persistent for child in self._child_processes.values()):
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

    def _cleanup_children(self) -> None:
        autostart_child_exited = False
        child_exited = False
        for tid, child in list(self._child_processes.items()):
            if self._child_has_exited(child):
                child_exited = True
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

        if autostart_child_exited:
            # Re-evaluate ensure-style autostart manifests immediately after an
            # autostart child exits instead of waiting for the next scan interval.
            self._autostart_last_scan_ns = 0
        if child_exited:
            # Child completion is activity. The manager should only begin its idle
            # countdown after in-flight work has actually finished.
            self._last_activity_ns = time.time_ns()

    def _terminate_children(self) -> None:
        self._wait_for_children_to_exit(timeout=1.0)
        if not self._child_processes:
            return

        for tid, child in list(self._child_processes.items()):
            ctrl_queue = child.ctrl_queue or f"T{tid}.{QUEUE_CTRL_IN_SUFFIX}"
            self._send_stop_command(ctrl_queue)
        self._wait_for_children_to_exit(timeout=1.0)

        for tid, child in list(self._child_processes.items()):
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
            for pid in self._managed_pids_for_child(tid):
                terminate_process_tree(pid, timeout=0.2)
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
            ctrl_queue = child.ctrl_queue or f"T{tid}.{QUEUE_CTRL_IN_SUFFIX}"
            self._send_stop_command(ctrl_queue)

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
        self._drain_reason = reason
        self._drain_completion_event = completion_event
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
        self._drain_reason = f"Superseded by lower-TID manager {leader_tid}"
        self._drain_completion_event = "manager_leadership_drained"
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
        self.should_stop = True

    def handle_termination_signal(self, signum: int) -> None:
        sigusr1 = getattr(signal, "SIGUSR1", None)
        if sigusr1 is not None and signum == sigusr1:
            self._terminate_children()
            super().handle_termination_signal(signum)
            return

        if self._external_stop_handled:
            return
        self._external_stop_handled = True

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
        try:
            Queue(
                queue_name,
                db_path=self._db_path,
                persistent=False,
                config=self._config,
            ).write(CONTROL_STOP)
        except (BrokerError, OSError, RuntimeError):
            logger.debug("Failed to send STOP to %s", queue_name, exc_info=True)

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

        if self._draining or self.should_stop:
            return False
        self._drain_control_queue_first()
        return not self._draining and not self.should_stop

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

        candidate = queue.last_ts
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
    # Autostart handling
    # ------------------------------------------------------------------
    def _autostart_root_dir(self) -> Path | None:
        if self._autostart_dir:
            return self._autostart_dir.parent
        spec_context = getattr(self.taskspec.spec, "weft_context", None)
        if spec_context:
            return Path(spec_context) / ".weft"
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

        if isinstance(defaults, dict) and "input" in defaults:
            inbox_message = defaults.get("input")

        return candidate, inbox_message

    def _active_autostart_sources(self) -> set[str]:
        queue = self._queue(WEFT_GLOBAL_LOG_QUEUE)
        active: dict[str, tuple[str | None, int]] = {}
        for payload, timestamp in iter_queue_json_entries(queue):
            taskspec_dump = payload.get("taskspec")
            if not isinstance(taskspec_dump, dict):
                continue
            metadata = taskspec_dump.get("metadata") or {}
            source = metadata.get("autostart_source")
            if not source:
                continue
            recorded = active.get(source)
            if recorded is None or recorded[1] <= timestamp:
                active[source] = (payload.get("status"), timestamp)

        terminal = {"completed", "failed", "timeout", "cancelled", "killed"}
        return {
            source for source, (status, _ts) in active.items() if status not in terminal
        }

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

        managed = latest_payload.get("managed_pids")
        if not isinstance(managed, list):
            return set()
        return {pid for pid in managed if isinstance(pid, int) and pid > 0}

    def _enqueue_autostart_request(
        self, payload: dict[str, Any], inbox_message: Any
    ) -> bool:
        spawn_payload = {
            "taskspec": payload,
            "inbox_message": inbox_message,
        }
        serialized_payload = json.dumps(spawn_payload, ensure_ascii=False)
        try:
            self._queue(self._queue_names["inbox"]).write(serialized_payload)
        except (BrokerError, OSError, RuntimeError):
            logger.warning("Failed to enqueue autostart spawn request", exc_info=True)
            return False
        return True

    def _tick_autostart(self, *, force: bool = False) -> None:
        """Scan autostart manifests and enqueue spawn requests (Spec: [MA-1.6])."""
        if not self._autostart_enabled:
            return
        now_ns = time.time_ns()
        if (
            not force
            and now_ns - self._autostart_last_scan_ns < self._autostart_scan_interval_ns
        ):
            return
        self._autostart_last_scan_ns = now_ns

        directory = self._autostart_dir
        if not directory or not directory.exists():
            return

        active_sources = self._active_autostart_sources()

        try:
            manifests = sorted(
                path for path in directory.glob("*.json") if path.is_file()
            )
        except OSError:
            logger.debug(
                "Failed to enumerate autostart manifests in %s",
                directory,
                exc_info=True,
            )
            return

        for manifest_path in manifests:
            source = str(manifest_path.resolve())
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

            if mode == "once":
                if source in self._autostart_launched or source in active_sources:
                    continue
            elif mode == "ensure":
                if source in active_sources:
                    continue
                launched_once = bool(state.get("launched_once", False))
                max_restarts = (
                    policy.get("max_restarts") if isinstance(policy, dict) else None
                )
                if (
                    launched_once
                    and max_restarts is not None
                    and state["restarts"] >= max_restarts
                ):
                    continue
                next_allowed = state.get("next_allowed_ns", 0)
                if launched_once and next_allowed and now_ns < next_allowed:
                    continue
            else:
                logger.warning("Unknown autostart policy mode %s for %s", mode, source)
                continue

            spawn_payload = self._build_autostart_spawn_payload(manifest, source)
            if spawn_payload is None:
                continue
            taskspec_payload, inbox_message = spawn_payload

            logger.debug("Auto-start enqueuing spawn request from %s", manifest_path)
            if not self._enqueue_autostart_request(taskspec_payload, inbox_message):
                continue
            self._autostart_launched.add(source)

            if mode == "ensure":
                launched_once = bool(state.get("launched_once", False))
                if launched_once:
                    state["restarts"] += 1
                state["launched_once"] = True
                backoff = (
                    policy.get("backoff_seconds") if isinstance(policy, dict) else None
                )
                if isinstance(backoff, (int, float)) and backoff > 0:
                    multiplier = max(0, int(state["restarts"]))
                    delay = float(backoff) * (2**multiplier)
                    state["next_allowed_ns"] = now_ns + int(delay * 1_000_000_000)
                else:
                    state["next_allowed_ns"] = 0

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    def cleanup(self) -> None:
        self._terminate_children()
        self._unregister_manager()
        super().cleanup()

    def process_once(self) -> None:
        if self._maybe_yield_leadership():
            return
        if self._draining:
            self._signal_children_to_stop()
            self._cleanup_children()
            if not self._child_processes:
                self._finish_graceful_shutdown()
            return
        self._drain_control_queue_first()
        if self._draining:
            self._signal_children_to_stop()
            self._cleanup_children()
            if not self._child_processes:
                self._finish_graceful_shutdown()
            return
        super().process_once()
        if self._draining:
            self._signal_children_to_stop()
            self._cleanup_children()
            if not self._child_processes:
                self._finish_graceful_shutdown()
            return
        if self._maybe_yield_leadership():
            return
        self._cleanup_children()
        self._tick_autostart()
        self._update_idle_activity_from_broker()
        now_ns = time.time_ns()
        idle_timeout_ns = int(float(self._idle_timeout) * 1_000_000_000)
        if self._child_processes:
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

    def _atexit_unregister(self) -> None:
        try:
            self._unregister_manager()
        except Exception:  # pragma: no cover - interpreter shutdown cleanup
            pass


__all__ = ["Manager"]
