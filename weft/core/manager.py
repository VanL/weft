"""Worker task implementation for spawning child tasks."""

from __future__ import annotations

import atexit
import json
import logging
import multiprocessing
import threading
import time
from collections.abc import Mapping
from multiprocessing.process import BaseProcess
from typing import Any, cast

from simplebroker import Queue
from weft._constants import (
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
        self._child_processes: dict[str, BaseProcess] = {}
        self._idle_timeout = float(
            taskspec.metadata.get(
                "idle_timeout",
                self._config.get(
                    "WEFT_MANAGER_LIFETIME_TIMEOUT",
                    WEFT_MANAGER_LIFETIME_TIMEOUT,
                ),
            )
        )
        self._last_activity = time.time()
        self._idle_shutdown_logged = False
        self._unregistered = False
        self._registry_message_id: int | None = None
        self._register_worker()
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
            message_id = cast(
                int | None, registry_queue.write(json.dumps(payload))
            )
        except Exception:
            logger.debug("Failed to register worker", exc_info=True)
        else:
            self._registry_message_id = message_id

    def _unregister_worker(self) -> None:
        if self._unregistered:
            return
        registry_queue = self._queue(WEFT_WORKERS_REGISTRY_QUEUE)
        stopped_timestamp = registry_queue.generate_timestamp()

        try:
            if self._registry_message_id is not None:
                registry_queue.delete(message_id=self._registry_message_id)
        except Exception:
            logger.debug("Failed to prune active registry entry", exc_info=True)

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
        try:
            registry_queue.write(json.dumps(payload))
        except Exception:
            logger.debug("Failed to record stopped manager state", exc_info=True)

        self._registry_message_id = None
        self._unregistered = True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _cleanup_children(self) -> None:
        for tid, process in list(self._child_processes.items()):
            if not process.is_alive():
                process.join()
                del self._child_processes[tid]

    def _terminate_children(self) -> None:
        for tid, process in list(self._child_processes.items()):
            if process.is_alive():
                process.terminate()
                process.join(timeout=1.0)
            del self._child_processes[tid]

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
        if inbox_message is not None:
            inbox_name = child_spec.io.inputs.get("inbox")
            if inbox_name:
                Queue(
                    inbox_name,
                    db_path=self._db_path,
                    persistent=True,
                    config=self._config,
                ).write(
                    json.dumps(inbox_message)
                    if not isinstance(inbox_message, str)
                    else inbox_message
                )

        process = launch_task_process(
            Consumer,
            self._db_path,
            child_spec,
            config=self._config,
        )
        self._child_processes[child_spec.tid] = process
        self._last_activity = time.time()

        self._report_state_change(
            event="task_spawned",
            child_tid=child_spec.tid,
            child_taskspec=redact_taskspec_dump(
                child_spec.model_dump(mode="json"), self._taskspec_redaction_paths
            ),
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
    # Cleanup
    # ------------------------------------------------------------------
    def cleanup(self) -> None:
        self._terminate_children()
        self._unregister_worker()
        super().cleanup()

    def process_once(self) -> None:
        super().process_once()
        self._cleanup_children()
        if self._child_processes:
            self._last_activity = time.time()
            return
        if self._idle_timeout <= 0:
            return
        if time.time() - self._last_activity >= self._idle_timeout:
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
