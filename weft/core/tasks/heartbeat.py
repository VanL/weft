"""Internal heartbeat service task.

Spec references:
- docs/specifications/01-Core_Components.md [CC-2.2], [CC-2.3], [CC-2.4.1]
- docs/specifications/05-Message_Flow_and_State.md [MF-3.1], [MF-6]
"""

from __future__ import annotations

import heapq
import json
import logging
import os
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    field_validator,
    model_validator,
)

from simplebroker.ext import BrokerError
from weft._constants import (
    HEARTBEAT_IDLE_TIMEOUT_SECONDS,
    HEARTBEAT_MIN_INTERVAL_SECONDS,
    INTERNAL_HEARTBEAT_ENDPOINT_NAME,
)
from weft.context import WeftContext, build_context
from weft.core.endpoints import resolve_endpoint
from weft.core.taskspec import ReservedPolicy, TaskSpec

from .base import BaseTask
from .multiqueue_watcher import QueueMessageContext, QueueMode

logger = logging.getLogger(__name__)

HeartbeatAction = Literal["upsert", "cancel"]


class HeartbeatMutation(BaseModel):
    """Validated heartbeat registration request."""

    model_config = ConfigDict(extra="forbid")

    action: HeartbeatAction
    heartbeat_id: str
    interval_seconds: int | None = None
    destination_queue: str | None = None
    message: Any = None

    @field_validator("heartbeat_id")
    @classmethod
    def _validate_heartbeat_id(cls, value: str) -> str:
        candidate = value.strip()
        if not candidate:
            raise ValueError("heartbeat_id must not be empty")
        return candidate

    @model_validator(mode="after")
    def _validate_shape(self) -> HeartbeatMutation:
        if self.action == "cancel":
            return self
        if (
            self.interval_seconds is None
            or self.interval_seconds < HEARTBEAT_MIN_INTERVAL_SECONDS
        ):
            raise ValueError(
                f"interval_seconds must be >= {HEARTBEAT_MIN_INTERVAL_SECONDS}"
            )
        if not isinstance(self.destination_queue, str) or not self.destination_queue:
            raise ValueError("destination_queue must not be empty")
        return self


@dataclass(slots=True)
class HeartbeatRegistration:
    """In-memory heartbeat registration state."""

    heartbeat_id: str
    interval_seconds: int
    destination_queue: str
    message_text: str
    next_due_at: float


class HeartbeatTask(BaseTask):
    """Persistent internal task that multiplexes runtime-scoped heartbeat emits."""

    def __init__(
        self,
        db: Path | str | Any,
        taskspec: TaskSpec,
        *,
        stop_event: threading.Event | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(db, taskspec, stop_event=stop_event, config=config)
        self._context: WeftContext = build_context(
            spec_context=taskspec.spec.weft_context,
            config=self._config,
        )
        self._registrations: dict[str, HeartbeatRegistration] = {}
        self._due_heap: list[tuple[float, str]] = []
        self._idle_timeout_seconds = float(
            taskspec.metadata.get(
                "heartbeat_idle_timeout",
                HEARTBEAT_IDLE_TIMEOUT_SECONDS,
            )
        )
        self._empty_since_monotonic: float | None = time.monotonic()
        self._activity_waiters: list[Any] = []
        self._activate_waiter()
        self._set_activity("waiting", waiting_on=self._queue_names["inbox"])

        for queue_name in (self._queue_names["inbox"], self._queue_names["ctrl_in"]):
            try:
                waiter = self._queue(queue_name).create_activity_waiter(
                    stop_event=self._stop_event
                )
            except (BrokerError, OSError, RuntimeError):
                logger.debug(
                    "Heartbeat activity waiter unavailable for %s",
                    queue_name,
                    exc_info=True,
                )
                continue
            if waiter is not None:
                self._activity_waiters.append(waiter)

    def _activate_waiter(self) -> None:
        if self.taskspec.state.status != "created":
            return
        self.taskspec.mark_started(pid=os.getpid())
        self._report_state_change(event="task_spawning")
        self.taskspec.mark_running(pid=os.getpid())
        self._update_process_title("running")
        self._report_state_change(event="task_started")

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

    def cleanup(self) -> None:
        for waiter in self._activity_waiters:
            try:
                waiter.close()
            except Exception:  # pragma: no cover - defensive
                logger.debug("Failed to close heartbeat activity waiter", exc_info=True)
        self._activity_waiters.clear()
        super().cleanup()

    def process_once(self) -> None:
        if self.should_stop:
            return

        while not self.should_stop:
            if self._drain_one_control_message():
                return
            if self._exit_if_superseded():
                return
            if self._drain_one_registration_message():
                return
            if not self._paused and self._emit_due_registrations():
                return
            if self._maybe_idle_shutdown():
                return
            self._wait_for_activity(timeout=self._next_wait_timeout())

    def _drain_one_control_message(self) -> bool:
        ctrl_queue = self._queue(self._queue_names["ctrl_in"])
        pending = ctrl_queue.peek_one(with_timestamps=True)
        if pending is None:
            return False
        if not isinstance(pending, tuple) or len(pending) != 2:
            return False

        body, timestamp = pending
        if not isinstance(timestamp, int):
            return False

        context = QueueMessageContext(
            queue_name=self._queue_names["ctrl_in"],
            queue=ctrl_queue,
            mode=QueueMode.PEEK,
            timestamp=timestamp,
        )
        self._handle_control_message(str(body), timestamp, context)
        return True

    def _drain_one_registration_message(self) -> bool:
        inbox_queue = self._queue(self._queue_names["inbox"])
        moved = inbox_queue.move_one(
            self._queue_names["reserved"],
            with_timestamps=True,
        )
        if moved is None:
            return False
        if not isinstance(moved, tuple) or len(moved) != 2:
            return False

        body, timestamp = moved
        if not isinstance(timestamp, int):
            return False

        context = QueueMessageContext(
            queue_name=self._queue_names["inbox"],
            queue=inbox_queue,
            mode=QueueMode.RESERVE,
            timestamp=timestamp,
            reserved_queue_name=self._queue_names["reserved"],
        )
        self._handle_work_message(str(body), timestamp, context)
        return True

    def _handle_work_message(
        self, message: str, timestamp: int, context: QueueMessageContext
    ) -> None:
        del context
        try:
            mutation = HeartbeatMutation.model_validate_json(message)
        except (ValidationError, json.JSONDecodeError) as exc:
            self._report_state_change(
                event="heartbeat_request_invalid",
                message_id=timestamp,
                error=str(exc),
            )
            policy = self.taskspec.spec.reserved_policy_on_error
            self._apply_reserved_policy(policy, message_timestamp=timestamp)
            if policy is not ReservedPolicy.KEEP:
                self._ensure_reserved_empty()
                self._cleanup_reserved_if_needed()
            return

        if mutation.action == "cancel":
            removed = self._registrations.pop(mutation.heartbeat_id, None)
            if removed is not None and not self._registrations:
                self._empty_since_monotonic = time.monotonic()
            self._report_state_change(
                event="heartbeat_cancelled",
                message_id=timestamp,
                heartbeat_id=mutation.heartbeat_id,
                existed=removed is not None,
            )
            self._delete_reserved_message(timestamp)
            return

        assert mutation.interval_seconds is not None
        assert mutation.destination_queue is not None
        registration = HeartbeatRegistration(
            heartbeat_id=mutation.heartbeat_id,
            interval_seconds=int(mutation.interval_seconds),
            destination_queue=mutation.destination_queue,
            message_text=self._serialize_message_payload(mutation.message),
            next_due_at=time.monotonic() + float(mutation.interval_seconds),
        )
        self._registrations[mutation.heartbeat_id] = registration
        heapq.heappush(
            self._due_heap,
            (registration.next_due_at, registration.heartbeat_id),
        )
        self._empty_since_monotonic = None
        self._report_state_change(
            event="heartbeat_upserted",
            message_id=timestamp,
            heartbeat_id=registration.heartbeat_id,
            interval_seconds=registration.interval_seconds,
            destination_queue=registration.destination_queue,
        )
        self._delete_reserved_message(timestamp)

    @staticmethod
    def _serialize_message_payload(payload: Any) -> str:
        if isinstance(payload, str):
            return payload
        try:
            return json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("message must be JSON-serializable") from exc

    def _delete_reserved_message(self, message_id: int) -> None:
        try:
            self._get_reserved_queue().delete(message_id=message_id)
        finally:
            self._ensure_reserved_empty()
            self._cleanup_reserved_if_needed()

    def _emit_due_registrations(self) -> bool:
        now = time.monotonic()
        due_ids: list[str] = []

        while self._due_heap:
            next_due_at, heartbeat_id = self._due_heap[0]
            registration = self._registrations.get(heartbeat_id)
            if registration is None or registration.next_due_at != next_due_at:
                heapq.heappop(self._due_heap)
                continue
            if next_due_at > now:
                break
            heapq.heappop(self._due_heap)
            due_ids.append(heartbeat_id)

        if not due_ids:
            return False

        self._set_activity("working")
        try:
            for heartbeat_id in due_ids:
                registration = self._registrations.get(heartbeat_id)
                if registration is None:
                    continue
                ownership_state, owner_tid = self._service_ownership()
                if ownership_state == "other":
                    self._report_state_change(
                        event="heartbeat_service_superseded",
                        owner_tid=owner_tid,
                    )
                    self.taskspec.mark_completed(return_code=0)
                    self._update_process_title("completed")
                    self.should_stop = True
                    return True
                if ownership_state != "self":
                    self._reschedule_registration(registration, now=now)
                    continue
                try:
                    self._queue(registration.destination_queue).write(
                        registration.message_text
                    )
                except (BrokerError, OSError, RuntimeError):
                    logger.debug(
                        "Failed to emit heartbeat %s to %s",
                        heartbeat_id,
                        registration.destination_queue,
                        exc_info=True,
                    )
                    self._report_state_change(
                        event="heartbeat_emit_failed",
                        heartbeat_id=heartbeat_id,
                        destination_queue=registration.destination_queue,
                    )
                else:
                    self._report_state_change(
                        event="heartbeat_emitted",
                        heartbeat_id=heartbeat_id,
                        destination_queue=registration.destination_queue,
                        interval_seconds=registration.interval_seconds,
                    )
                self._reschedule_registration(registration, now=now)
        finally:
            self._set_activity("waiting", waiting_on=self._queue_names["inbox"])

        return True

    def _reschedule_registration(
        self,
        registration: HeartbeatRegistration,
        *,
        now: float,
    ) -> None:
        next_due_at = registration.next_due_at
        interval_seconds = float(registration.interval_seconds)
        while next_due_at <= now:
            next_due_at += interval_seconds
        registration.next_due_at = next_due_at
        heapq.heappush(
            self._due_heap,
            (registration.next_due_at, registration.heartbeat_id),
        )

    def _service_ownership(
        self,
    ) -> tuple[Literal["self", "other", "unknown"], str | None]:
        resolved = resolve_endpoint(self._context, INTERNAL_HEARTBEAT_ENDPOINT_NAME)
        if resolved is None:
            return "unknown", None
        owner_tid = resolved.record.tid
        if owner_tid == self.tid:
            return "self", owner_tid
        return "other", owner_tid

    def _exit_if_superseded(self) -> bool:
        ownership_state, owner_tid = self._service_ownership()
        if ownership_state != "other":
            return False
        self._report_state_change(
            event="heartbeat_service_superseded",
            owner_tid=owner_tid,
        )
        self.taskspec.mark_completed(return_code=0)
        self._update_process_title("completed")
        self.should_stop = True
        return True

    def _maybe_idle_shutdown(self) -> bool:
        if self._registrations:
            self._empty_since_monotonic = None
            return False
        if self._idle_timeout_seconds <= 0:
            return False
        if self._empty_since_monotonic is None:
            self._empty_since_monotonic = time.monotonic()
            return False
        idle_for = time.monotonic() - self._empty_since_monotonic
        if idle_for < self._idle_timeout_seconds:
            return False
        self._report_state_change(
            event="heartbeat_service_idle_shutdown",
            idle_for_seconds=idle_for,
            idle_timeout_seconds=self._idle_timeout_seconds,
        )
        self.taskspec.mark_completed(return_code=0)
        self._update_process_title("completed")
        self.should_stop = True
        return True

    def _next_wait_timeout(self) -> float | None:
        now = time.monotonic()
        next_due = self._next_due_timeout(now=now)
        idle_timeout = self._next_idle_timeout(now=now)
        timeouts = [value for value in (next_due, idle_timeout) if value is not None]
        if not timeouts:
            return 1.0
        return max(0.0, min(timeouts))

    def _next_due_timeout(self, *, now: float) -> float | None:
        while self._due_heap:
            next_due_at, heartbeat_id = self._due_heap[0]
            registration = self._registrations.get(heartbeat_id)
            if registration is None or registration.next_due_at != next_due_at:
                heapq.heappop(self._due_heap)
                continue
            return next_due_at - now
        return None

    def _next_idle_timeout(self, *, now: float) -> float | None:
        if self._registrations or self._idle_timeout_seconds <= 0:
            return None
        if self._empty_since_monotonic is None:
            return self._idle_timeout_seconds
        return (self._empty_since_monotonic + self._idle_timeout_seconds) - now

    def _has_pending_runtime_input(self) -> bool:
        for queue_name in (self._queue_names["ctrl_in"], self._queue_names["inbox"]):
            try:
                if self._queue(queue_name).has_pending():
                    return True
            except (BrokerError, OSError, RuntimeError):
                logger.debug(
                    "Failed to inspect heartbeat queue %s for pending work",
                    queue_name,
                    exc_info=True,
                )
        return False

    def _wait_for_activity(self, *, timeout: float | None) -> None:
        wait_timeout = 1.0 if timeout is None else max(0.0, timeout)
        if wait_timeout <= 0:
            return

        deadline = time.monotonic() + wait_timeout
        while not self.should_stop:
            if self._stop_event is not None and self._stop_event.is_set():
                return
            if self._has_pending_runtime_input():
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            chunk = min(remaining, 1.0)
            waiters = list(self._activity_waiters)
            if waiters:
                waiter_chunk = chunk / float(len(waiters))
                for waiter in waiters:
                    if waiter.wait(waiter_chunk):
                        return
                continue
            if self._stop_event is not None:
                if self._stop_event.wait(timeout=chunk):
                    return
                continue
            time.sleep(chunk)


__all__ = ["HeartbeatTask"]
