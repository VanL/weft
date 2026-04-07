"""Worker task implementation for spawning child tasks.

Spec references:
- docs/specifications/01-Core_Components.md [CC-2.2], [CC-2.3], [CC-2.5]
- docs/specifications/03-Worker_Architecture.md [WA-0], [WA-1], [WA-2], [WA-3]
"""

from __future__ import annotations

import atexit
import copy
import json
import logging
import multiprocessing
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import Any, cast

import psutil

from simplebroker import Queue
from weft._constants import (
    CONTROL_STOP,
    QUEUE_CTRL_IN_SUFFIX,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_MANAGER_LIFETIME_TIMEOUT,
    WEFT_TID_MAPPINGS_QUEUE,
    WEFT_WORKERS_REGISTRY_QUEUE,
    WORK_ENVELOPE_START,
)
from weft.helpers import (
    iter_queue_json_entries,
    redact_taskspec_dump,
    terminate_process_tree,
)

from .launcher import launch_task_process
from .tasks import Consumer
from .tasks.base import BaseTask, QueueMessageContext
from .taskspec import ReservedPolicy, TaskSpec, resolve_taskspec_payload

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

    Spec: [WA-0], [WA-1], [MF-6], [MF-7]
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
        except Exception:
            logger.debug("Failed to prime broker activity queue", exc_info=True)
        self._last_broker_timestamp = self._read_broker_timestamp(force=True)
        self._register_worker()
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

        Spec: [CC-2.2], [CC-2.5], [WA-1]
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

    def _launch_child_task(
        self,
        child_spec: TaskSpec,
        inbox_message: Any | None,
        *,
        autostart_source: str | None = None,
    ) -> None:
        """Seed child inbox, spawn process, and emit task_spawned event.

        Spec: [MF-1], [MF-6]
        """
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
            except Exception:
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

        process = launch_task_process(
            Consumer,
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

    # ------------------------------------------------------------------
    # Worker bookkeeping
    # ------------------------------------------------------------------
    def _register_worker(self) -> None:
        """Publish an active record to the worker registry (Spec: [WA-1.4], [MF-7])."""
        registry_queue = self._queue(WEFT_WORKERS_REGISTRY_QUEUE)
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
        try:
            message_id = cast(int | None, registry_queue.write(json.dumps(payload)))
        except Exception:
            logger.debug("Failed to register worker", exc_info=True)
        else:
            if message_id is None:
                latest = self._latest_registry_entry(registry_queue, self.tid)
                self._registry_message_id = latest[1] if latest else None
            else:
                self._registry_message_id = message_id

    def _unregister_worker(self) -> None:
        """Replace active record with stopped record on shutdown (Spec: [WA-1.4])."""
        if self._unregistered:
            return
        registry_queue = self._queue(WEFT_WORKERS_REGISTRY_QUEUE)
        stopped_timestamp = registry_queue.generate_timestamp()

        deletion_performed = False
        try:
            if self._registry_message_id is not None:
                registry_queue.delete(message_id=self._registry_message_id)
                deletion_performed = True
        except Exception:
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
                    except Exception:
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

        try:
            registry_queue.write(json.dumps(payload))
        except Exception:
            logger.debug("Failed to record stopped manager state", exc_info=True)

        self._registry_message_id = None
        self._unregistered = True

    def _latest_registry_entry(
        self, queue: Queue, tid: str
    ) -> tuple[dict[str, Any], int] | None:
        latest: tuple[dict[str, Any], int] | None = None
        try:
            generator = queue.peek_generator(with_timestamps=True)
        except Exception:
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
        if pid is None or pid <= 0:
            return False
        try:
            process = psutil.Process(pid)
            return process.is_running()
        except psutil.Error:
            return False

    def _active_manager_records(self) -> dict[str, dict[str, Any]]:
        queue = self._queue(WEFT_WORKERS_REGISTRY_QUEUE)
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
            if record.get("role", "manager") != "manager":
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
            except Exception:
                logger.debug("Failed to prune stale manager record", exc_info=True)

        return active

    def _leader_tid(self) -> str | None:
        active = self._active_manager_records()
        if not active:
            return None
        return min(active, key=int)

    def _maybe_yield_leadership(self, *, force: bool = False) -> bool:
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

        if not self.should_stop:
            self.taskspec.mark_cancelled(
                reason=f"Superseded by lower-TID manager {leader_tid}"
            )
            self._report_state_change(
                event="manager_leadership_yielded",
                leader_tid=leader_tid,
            )
            self._unregister_worker()
            self.should_stop = True
        return True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _cleanup_children(self) -> None:
        autostart_child_exited = False
        for tid, child in list(self._child_processes.items()):
            if not child.process.is_alive():
                for pid in self._managed_pids_for_child(tid):
                    terminate_process_tree(pid, timeout=0.2)
                try:
                    child.process.join()
                except Exception:  # pragma: no cover - defensive
                    pass
                finally:
                    self._child_processes.pop(tid, None)
                if child.autostart_source:
                    autostart_child_exited = True

        if autostart_child_exited:
            # Re-evaluate ensure-style autostart manifests immediately after an
            # autostart child exits instead of waiting for the next scan interval.
            self._autostart_last_scan_ns = 0

    def _terminate_children(self) -> None:
        for tid, child in list(self._child_processes.items()):
            ctrl_queue = child.ctrl_queue or f"T{tid}.{QUEUE_CTRL_IN_SUFFIX}"
            self._send_stop_command(ctrl_queue)
            if child.process.is_alive():
                try:
                    child.process.join(timeout=1.0)
                except Exception:  # pragma: no cover - defensive
                    pass
            if child.process.is_alive():
                if child.process.pid is not None:
                    terminate_process_tree(child.process.pid, timeout=0.5)
                try:
                    child.process.join(timeout=2.0)
                except Exception:  # pragma: no cover - defensive
                    pass
                if child.process.is_alive():
                    try:
                        child.process.kill()
                    except Exception:  # pragma: no cover - defensive
                        pass
                    else:
                        child.process.join(timeout=1.0)
            for pid in self._managed_pids_for_child(tid):
                terminate_process_tree(pid, timeout=0.2)
            self._child_processes.pop(tid, None)

    def handle_termination_signal(self, signum: int) -> None:
        self._terminate_children()
        super().handle_termination_signal(signum)

    def _send_stop_command(self, queue_name: str) -> None:
        try:
            Queue(
                queue_name,
                db_path=self._db_path,
                persistent=False,
                config=self._config,
            ).write(CONTROL_STOP)
        except Exception:
            logger.debug("Failed to send STOP to %s", queue_name, exc_info=True)

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
            except Exception:
                logger.debug(
                    "Broker activity queue unavailable for idle tracking",
                    exc_info=True,
                )
                return last_known
            else:
                self._broker_activity_queue = queue

        try:
            candidate = queue.last_ts
        except Exception:
            logger.debug(
                "Failed to read queue.last_ts for idle tracking",
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

    def _update_idle_activity_from_broker(self) -> None:
        previous_timestamp = self._last_broker_timestamp
        new_timestamp = self._read_broker_timestamp()
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
        """Consume a spawn request, build child spec, and launch (Spec: [WA-1.1], [WA-2], [MF-6])."""
        self._cleanup_children()

        try:
            payload = json.loads(message) if message else {}
        except json.JSONDecodeError:
            logger.warning("Worker received non-JSON spawn request: %s", message)
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
        self._launch_child_task(
            child_spec,
            inbox_message,
            autostart_source=child_spec.metadata.get("autostart_source"),
        )

        try:
            self._get_reserved_queue().delete(message_id=timestamp)
        except Exception:
            logger.debug(
                "Failed to acknowledge worker message %s", timestamp, exc_info=True
            )

    def _build_child_spec(
        self, payload: dict[str, Any], timestamp: int
    ) -> TaskSpec | None:
        """Parse spawn payload and validate a child TaskSpec (Spec: [WA-1.1], [WA-2])."""
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
        except Exception:
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

    @staticmethod
    def _load_autostart_manifest(template_path: Path) -> dict[str, Any] | None:
        try:
            raw = template_path.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except Exception:
            logger.warning("Failed to read autostart manifest %s", template_path)
            return None

        if not isinstance(payload, dict):
            logger.warning(
                "Autostart manifest %s must contain a JSON object", template_path
            )
            return None
        return payload

    def _load_autostart_taskspec(self, name: str) -> dict[str, Any] | None:
        root_dir = self._autostart_root_dir()
        if root_dir is None:
            return None
        path = root_dir / "tasks" / f"{name}.json"
        try:
            raw = path.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except Exception:
            logger.warning("Failed to read stored task spec %s", path)
            return None
        if not isinstance(payload, dict):
            logger.warning("Stored task spec %s must contain a JSON object", path)
            return None
        return payload

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
        elif target_type == "pipeline":
            logger.warning("Autostart pipeline target %s not yet supported", source)
            return None
        else:
            logger.warning("Autostart manifest %s has invalid target type", source)
            return None

        candidate = copy.deepcopy(taskspec_payload)
        candidate.pop("tid", None)
        candidate.setdefault("version", "1.0")
        candidate.setdefault("name", target_name)
        candidate.setdefault("spec", {})
        candidate.setdefault("metadata", {})

        defaults = manifest.get("defaults")
        if isinstance(defaults, dict):
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

        inbox_message: Any = WORK_ENVELOPE_START
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
    ) -> None:
        spawn_payload = {
            "taskspec": payload,
            "inbox_message": inbox_message,
        }
        try:
            self._queue(self._queue_names["inbox"]).write(
                json.dumps(spawn_payload, ensure_ascii=False)
            )
        except Exception:
            logger.warning("Failed to enqueue autostart spawn request", exc_info=True)

    def _tick_autostart(self, *, force: bool = False) -> None:
        """Scan autostart manifests and enqueue spawn requests (Spec: [WA-1.6])."""
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
        except Exception:
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
                source, {"restarts": 0, "next_allowed_ns": 0}
            )

            if mode == "once":
                if source in self._autostart_launched or source in active_sources:
                    continue
            elif mode == "ensure":
                if source in active_sources:
                    continue
                max_restarts = (
                    policy.get("max_restarts") if isinstance(policy, dict) else None
                )
                if max_restarts is not None and state["restarts"] >= max_restarts:
                    continue
                next_allowed = state.get("next_allowed_ns", 0)
                if next_allowed and now_ns < next_allowed:
                    continue
            else:
                logger.warning("Unknown autostart policy mode %s for %s", mode, source)
                continue

            spawn_payload = self._build_autostart_spawn_payload(manifest, source)
            if spawn_payload is None:
                continue
            taskspec_payload, inbox_message = spawn_payload

            logger.debug("Auto-start enqueuing spawn request from %s", manifest_path)
            self._enqueue_autostart_request(taskspec_payload, inbox_message)
            self._autostart_launched.add(source)

            if mode == "ensure":
                state["restarts"] += 1
                backoff = (
                    policy.get("backoff_seconds") if isinstance(policy, dict) else None
                )
                if isinstance(backoff, (int, float)) and backoff > 0:
                    multiplier = max(0, state["restarts"] - 1)
                    delay = float(backoff) * (2**multiplier)
                    state["next_allowed_ns"] = now_ns + int(delay * 1_000_000_000)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    def cleanup(self) -> None:
        self._terminate_children()
        self._unregister_worker()
        super().cleanup()

    def process_once(self) -> None:
        if self._maybe_yield_leadership():
            return
        super().process_once()
        if self._maybe_yield_leadership():
            return
        self._cleanup_children()
        self._tick_autostart()
        self._update_idle_activity_from_broker()
        now_ns = time.time_ns()
        idle_timeout_ns = int(float(self._idle_timeout) * 1_000_000_000)
        if self._child_processes:
            has_persistent_children = any(
                child.persistent for child in self._child_processes.values()
            )
            if (
                self._idle_timeout > 0
                and now_ns - self._last_activity_ns >= idle_timeout_ns
                and not has_persistent_children
            ):
                logger.debug(
                    "Idle timeout reached; forcing shutdown of %d child tasks",
                    len(self._child_processes),
                )
                self._terminate_children()
                self._last_activity_ns = now_ns
            else:
                return
        if self._idle_timeout <= 0:
            return
        try:
            inbox_queue = self._queue(self._queue_names["inbox"])
            if inbox_queue.has_pending():
                self._last_activity_ns = time.time_ns()
        except Exception:  # pragma: no cover - defensive
            logger.debug(
                "Failed to inspect inbox queue for pending work", exc_info=True
            )
        if now_ns - self._last_activity_ns >= idle_timeout_ns:
            if not self._idle_shutdown_logged:
                self.taskspec.mark_completed(return_code=0)
                self._report_state_change(event="manager_idle_shutdown")
                self._idle_shutdown_logged = True
            self.should_stop = True

    def _atexit_unregister(self) -> None:
        try:
            self._unregister_worker()
        except Exception:  # pragma: no cover - best effort cleanup
            pass


__all__ = ["Manager"]
