"""Worker task implementation for spawning child tasks."""

from __future__ import annotations

import atexit
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

from simplebroker import Queue
from weft._constants import (
    CONTROL_STOP,
    QUEUE_CTRL_IN_SUFFIX,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_MANAGER_LIFETIME_TIMEOUT,
    WEFT_WORKERS_REGISTRY_QUEUE,
    WORK_ENVELOPE_START,
)
from weft.helpers import redact_taskspec_dump

from .launcher import launch_task_process
from .tasks import Consumer
from .tasks.base import BaseTask, QueueMessageContext
from .taskspec import TaskSpec

logger = logging.getLogger(__name__)


@dataclass
class ManagedChild:
    """Bookkeeping for spawned child processes."""

    process: BaseProcess
    ctrl_queue: str | None
    autostart_source: str | None = None


class Manager(BaseTask):
    """Task that listens for spawn requests and runs child tasks."""

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
        self.taskspec.mark_running(pid=multiprocessing.current_process().pid)
        self._update_process_title("running")
        if self._autostart_enabled:
            self._launch_autostart_templates()
        atexit.register(self._atexit_unregister)

    # ------------------------------------------------------------------
    # Queue configuration
    # ------------------------------------------------------------------
    def _build_queue_configs(self) -> dict[str, dict[str, Any]]:
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
        self._child_processes[child_spec.tid] = ManagedChild(
            process,
            child_spec.io.control.get("ctrl_in"),
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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _cleanup_children(self) -> None:
        for tid, child in list(self._child_processes.items()):
            if not child.process.is_alive():
                try:
                    child.process.join()
                except Exception:  # pragma: no cover - defensive
                    pass
                finally:
                    self._child_processes.pop(tid, None)

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
                try:
                    child.process.terminate()
                except Exception:  # pragma: no cover
                    pass
                else:
                    child.process.join(timeout=1.0)
            self._child_processes.pop(tid, None)

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
        self._cleanup_children()

        try:
            payload = json.loads(message) if message else {}
        except json.JSONDecodeError:
            logger.warning("Worker received non-JSON spawn request: %s", message)
            self._ack_control_message(context.queue_name, context.timestamp)
            return

        child_spec = self._build_child_spec(payload, timestamp)
        if child_spec is None:
            self._ack_control_message(context.queue_name, context.timestamp)
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
        provided_spec = payload.get("taskspec")
        if provided_spec is not None:
            candidate = dict(provided_spec)
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

        provided_tid = candidate.get("tid") or payload.get("tid")
        if provided_tid:
            candidate["tid"] = str(provided_tid)
        else:
            candidate["tid"] = str(timestamp)
        candidate.setdefault("version", "1.0")
        candidate.setdefault("io", {})
        candidate.setdefault("state", {})
        candidate.setdefault("metadata", {})
        candidate["metadata"].setdefault("parent_tid", self.tid)

        try:
            child_spec = TaskSpec.model_validate(
                candidate, context={"auto_expand": True}
            )
            child_spec.apply_defaults()
            child_spec.io.inputs.setdefault("inbox", f"T{child_spec.tid}.inbox")
        except Exception:
            logger.exception(
                "Failed to validate child TaskSpec from payload %s", payload
            )
            return None

        return child_spec

    # ------------------------------------------------------------------
    # Autostart handling
    # ------------------------------------------------------------------
    def _generate_child_tid(self) -> str:
        # TaskSpec enforces string tids; SimpleBroker emits ints.
        return str(self._get_connected_queue().generate_timestamp())

    def _load_autostart_template(self, template_path: Path) -> TaskSpec | None:
        try:
            raw = template_path.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except Exception:
            logger.warning("Failed to read autostart template %s", template_path)
            return None

        if not isinstance(payload, dict):
            logger.warning(
                "Autostart template %s must contain a JSON object", template_path
            )
            return None

        candidate = dict(payload)
        candidate.pop("tid", None)
        candidate.setdefault("version", "1.0")
        candidate.setdefault("name", template_path.stem)
        candidate.setdefault("spec", {})
        candidate.setdefault("io", {})
        candidate.setdefault("state", {})
        candidate.setdefault("metadata", {})
        candidate["tid"] = self._generate_child_tid()

        spec_section = candidate.get("spec")
        if isinstance(spec_section, dict):
            spec_section.setdefault(
                "weft_context", getattr(self.taskspec.spec, "weft_context", None)
            )

        try:
            child_spec = TaskSpec.model_validate(
                candidate, context={"auto_expand": True}
            )
        except Exception:
            logger.warning(
                "Failed to validate autostart TaskSpec from %s",
                template_path,
                exc_info=True,
            )
            return None

        source = str(template_path.resolve())
        child_spec.metadata.setdefault("autostart_source", source)
        child_spec.metadata.setdefault("autostart", True)
        return child_spec

    def _active_autostart_sources(self) -> set[str]:
        queue = self._queue(WEFT_GLOBAL_LOG_QUEUE)
        try:
            entries = queue.peek_many(limit=2048, with_timestamps=True)
        except Exception:
            return set()

        active: dict[str, tuple[str | None, int]] = {}
        if not entries:
            return set()

        for entry in entries:
            if isinstance(entry, tuple):
                body, timestamp = entry
            else:
                body, timestamp = entry, 0
            try:
                payload = json.loads(body)
            except (TypeError, json.JSONDecodeError):
                continue

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

    def _launch_autostart_templates(self) -> None:
        directory = self._autostart_dir
        if not directory or not directory.exists():
            return

        active_sources = self._active_autostart_sources()

        try:
            templates = sorted(
                path for path in directory.glob("*.json") if path.is_file()
            )
        except Exception:
            logger.debug(
                "Failed to enumerate autostart templates in %s",
                directory,
                exc_info=True,
            )
            return

        for template in templates:
            source = str(template.resolve())
            if source in self._autostart_launched or source in active_sources:
                continue

            child_spec = self._load_autostart_template(template)
            if child_spec is None:
                continue

            logger.debug("Auto-start launching task from %s", template)
            self._launch_child_task(
                child_spec, WORK_ENVELOPE_START, autostart_source=source
            )
            self._autostart_launched.add(source)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    def cleanup(self) -> None:
        self._terminate_children()
        self._unregister_worker()
        super().cleanup()

    def process_once(self) -> None:
        super().process_once()
        self._cleanup_children()
        self._update_idle_activity_from_broker()
        now_ns = time.time_ns()
        idle_timeout_ns = int(float(self._idle_timeout) * 1_000_000_000)
        if self._child_processes:
            if (
                self._idle_timeout > 0
                and now_ns - self._last_activity_ns >= idle_timeout_ns
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
