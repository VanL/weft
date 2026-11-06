"""Internal heartbeat service task.

Registrations are rows on ``weft.state.heartbeats``. The service builds its
in-memory table by peeking that queue, holds a notification-only interest in
it, and deletes only rows it rejects, rows a newer row of the same id
supersedes, and rows whose lease has expired.

Spec references:
- docs/specifications/01-Core_Components.md [CC-2.2], [CC-2.3], [CC-2.4.1]
- docs/specifications/05-Message_Flow_and_State.md [MF-3.1], [MF-3.2], [MF-6]
- docs/specifications/07-System_Invariants.md [QUEUE.7]
"""

from __future__ import annotations

import heapq
import json
import logging
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

from simplebroker import Queue
from simplebroker.ext import BrokerError
from weft._constants import (
    HEARTBEAT_MAX_LEASE_SECONDS,
    HEARTBEAT_MIN_INTERVAL_SECONDS,
    INTERNAL_HEARTBEAT_ENDPOINT_NAME,
    WEFT_ENDPOINTS_REGISTRY_QUEUE,
    WEFT_HEARTBEAT_REGISTRATIONS_QUEUE,
    WEFT_QUEUE_NAMESPACE_PREFIX,
)
from weft.context import WeftContext
from weft.core.endpoints import resolve_endpoint
from weft.core.taskspec import TaskSpec
from weft.helpers import closing_queue_iterator, is_task_tid

from .multiqueue_watcher import QueueMessageContext, QueueMode
from .service import ServiceTask

logger = logging.getLogger(__name__)


class HeartbeatRegistrationRow(BaseModel):
    """One validated heartbeat registration row on ``weft.state.heartbeats``.

    The same model validates at the writer (the registration helper) and at
    the only reader, the heartbeat table build; neither the Manager nor
    ``weft status`` reads the table. ``HEARTBEAT_MIN_INTERVAL_SECONDS <= interval_seconds <=
    lease_seconds <= HEARTBEAT_MAX_LEASE_SECONDS`` keeps every accepted row
    bounded and schedulable.

    Spec: [MF-3.2]
    """

    model_config = ConfigDict(extra="forbid")

    heartbeat_id: str
    registrant_tid: str
    interval_seconds: int
    lease_seconds: int
    destination_queue: str
    message: Any

    @field_validator("heartbeat_id")
    @classmethod
    def _validate_heartbeat_id(cls, value: str) -> str:
        candidate = value.strip()
        if not candidate:
            raise ValueError("heartbeat_id must not be empty")
        return candidate

    @field_validator("registrant_tid")
    @classmethod
    def _validate_registrant_tid(cls, value: str) -> str:
        if not is_task_tid(value):
            raise ValueError("registrant_tid must be a full task TID")
        return value

    @field_validator("destination_queue")
    @classmethod
    def _validate_destination_namespace(cls, value: str) -> str:
        if not value:
            raise ValueError("destination_queue must not be empty")
        if value.startswith(WEFT_QUEUE_NAMESPACE_PREFIX):
            raise ValueError(
                "destination_queue must not use the reserved Weft queue "
                f"namespace {WEFT_QUEUE_NAMESPACE_PREFIX!r}: {value!r}"
            )
        return value

    @field_validator("message")
    @classmethod
    def _validate_message(cls, value: Any) -> Any:
        try:
            json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("message must be JSON-serializable") from exc
        return value

    @model_validator(mode="after")
    def _validate_lease_chain(self) -> HeartbeatRegistrationRow:
        if self.interval_seconds < HEARTBEAT_MIN_INTERVAL_SECONDS:
            raise ValueError(
                f"interval_seconds must be >= {HEARTBEAT_MIN_INTERVAL_SECONDS}"
            )
        if self.lease_seconds < self.interval_seconds:
            raise ValueError("lease_seconds must be >= interval_seconds")
        if self.lease_seconds > HEARTBEAT_MAX_LEASE_SECONDS:
            raise ValueError(f"lease_seconds must be <= {HEARTBEAT_MAX_LEASE_SECONDS}")
        return self

    def expires_at_ns(self, message_id: int) -> int:
        """Return the lease end: the row's message ID plus its lease."""

        return message_id + self.lease_seconds * 1_000_000_000

    def expired(self, message_id: int, now_ns: int) -> bool:
        """Return whether the lease has ended at ``now_ns``."""

        return now_ns >= self.expires_at_ns(message_id)


@dataclass(slots=True)
class HeartbeatRegistration:
    """In-memory heartbeat registration state."""

    heartbeat_id: str
    interval_seconds: int
    destination_queue: str
    message_text: str
    next_due_at: float
    expires_at_ns: int


class HeartbeatTask(ServiceTask):
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
        with self._initialization_scope():
            self._initialize_heartbeat_runtime(taskspec)

    def _initialize_heartbeat_runtime(self, taskspec: TaskSpec) -> None:
        """Initialize heartbeat state and publish its eager service lifecycle."""

        self._context: WeftContext = self._task_context()
        self._ownership_check_required = True
        self._last_endpoint_registry_version: int | None = None
        self._cached_service_ownership: tuple[
            Literal["self", "other", "unknown"], str | None
        ] = ("unknown", None)
        self._registrations: dict[str, HeartbeatRegistration] = {}
        self._due_heap: list[tuple[float, str]] = []
        self._registrations_cursor: int | None = None
        self._registrations_rebuild_pending = True
        self._row_ids: dict[str, int] = {}
        self._activate_waiter()
        self._set_activity("waiting", waiting_on=self._queue_names["inbox"])

    def _activate_waiter(self) -> None:
        self._activate_service_task()

    def _reactor_support_routes(self) -> dict[str, str]:
        """Declare the registrations queue as a fixed support route [QUEUE.7]."""

        routes = super()._reactor_support_routes()
        routes["heartbeat_registrations"] = WEFT_HEARTBEAT_REGISTRATIONS_QUEUE
        return routes

    def _activity_wait_queues(self) -> list[Queue]:
        """Add the registrations notification interest, not a delivery route.

        Spec: docs/specifications/07-System_Invariants.md [QUEUE.7]
        """

        queues = super()._activity_wait_queues()
        queues.append(self._queue(WEFT_HEARTBEAT_REGISTRATIONS_QUEUE))
        return queues

    def _has_pending_observation(self) -> bool:
        """Report a new registration row or a removed table row [QUEUE.7].

        A row newer than the publication cursor, or the absence of a row the
        table holds, sets the rebuild flag. The check ignores ``_paused`` and
        due times, so a cancel while paused still empties the table. Only the
        rebuild advances the cursor.

        Spec: [MF-3.2], [QUEUE.7]
        """

        if self._registrations_rebuild_pending:
            return True
        queue = self._queue(WEFT_HEARTBEAT_REGISTRATIONS_QUEUE)
        try:
            changed = queue.has_pending(after_timestamp=self._registrations_cursor)
            if not changed and self._row_ids:
                present: set[int] = set()
                rows = queue.peek_generator(with_timestamps=True)
                with closing_queue_iterator(rows) as iterator:
                    for _body, message_id in iterator:
                        present.add(int(message_id))
                changed = any(
                    row_id not in present for row_id in self._row_ids.values()
                )
        except (BrokerError, OSError, RuntimeError):
            logger.debug("Heartbeat registration observation deferred", exc_info=True)
            return False
        if changed:
            self._registrations_rebuild_pending = True
        return changed

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

    def _service_reactor_turn(self) -> None:
        """Run one bounded Heartbeat turn behind the service idle template.

        Spec:
        - docs/specifications/01-Core_Components.md [CC-2.2.1], [CC-2.3]
        - docs/specifications/05-Message_Flow_and_State.md [MF-3.2]
        """
        self._drain_worker_results()
        if self._drain_one_control_message():
            self._maybe_emit_poll_report()
            return
        if self.should_stop:
            self._maybe_emit_poll_report()
            return
        if self._drain_one_registration_message():
            self._maybe_emit_poll_report()
            return
        if self.should_stop:
            self._maybe_emit_poll_report()
            return
        if self._registrations and time.time_ns() >= min(
            item.expires_at_ns for item in self._registrations.values()
        ):
            self._registrations_rebuild_pending = True
        if self._registrations_rebuild_pending:
            self._rebuild_registrations()
        if self._exit_if_superseded():
            self._maybe_emit_poll_report()
            return
        if not self._paused and self._emit_due_registrations():
            self._maybe_emit_poll_report()
            return
        self._maybe_emit_poll_report()

    def _drain_one_control_message(self) -> bool:
        ctrl_queue = self._queue(self._queue_names["ctrl_in"])
        pending = ctrl_queue.peek_one(with_timestamps=True)
        if pending is None:
            return False

        body, timestamp = pending

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

        body, timestamp = moved

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
        """Reject every inbox row: registrations are queue rows [MF-3.2]."""

        del message, context
        self._report_state_change(
            event="heartbeat_request_invalid",
            message_id=timestamp,
            error="heartbeat registrations are rows on weft.state.heartbeats",
        )
        policy = self.taskspec.spec.reserved_policy_on_error
        self._apply_reserved_policy(policy, message_timestamp=timestamp)

    def _delete_registration_row(self, message_id: int) -> bool:
        """Delete one registration row by exact ID; best-effort, reported."""

        try:
            self._queue(WEFT_HEARTBEAT_REGISTRATIONS_QUEUE).delete(
                message_id=message_id
            )
        except (BrokerError, OSError, RuntimeError):
            logger.debug(
                "Failed to delete heartbeat registration row %s",
                message_id,
                exc_info=True,
            )
            return False
        return True

    def _rebuild_registrations(self) -> None:
        """Rebuild the registration table by peeking ``weft.state.heartbeats``.

        The registration of a ``heartbeat_id`` is its newest row. Per id, in
        order: delete every older row (residue of an interrupted replacement)
        in ascending message-ID order; reject and delete an invalid newest
        row; delete an expired newest row with a
        ``heartbeat_registration_expired`` event; otherwise keep it as the
        registration. Older rows go first, in ascending order, so a build
        interrupted by process death leaves the newest row in place and no
        older row can become current. The rebuild flag is cleared before the
        build (a held flag would spin the reactor); a build that cannot read
        the queue does nothing.

        Spec: [MF-3.2], [QUEUE.7]
        """

        self._registrations_rebuild_pending = False
        grouped = self._peek_registration_rows()
        if grouped is None:
            return
        now_ns = time.time_ns()
        registrations: dict[str, HeartbeatRegistration] = {}
        row_ids: dict[str, int] = {}
        for heartbeat_id, id_rows in grouped.items():
            current_row = self._current_registration_row(id_rows, now_ns=now_ns)
            if current_row is None:
                continue
            message_id, row = current_row
            row_ids[heartbeat_id] = message_id
            current = self._registrations.get(heartbeat_id)
            if current is not None and self._row_ids.get(heartbeat_id) == message_id:
                registrations[heartbeat_id] = current
                continue
            registration = HeartbeatRegistration(
                heartbeat_id=row.heartbeat_id,
                interval_seconds=int(row.interval_seconds),
                destination_queue=row.destination_queue,
                message_text=self._serialize_message_payload(row.message),
                next_due_at=time.monotonic() + float(row.interval_seconds),
                expires_at_ns=row.expires_at_ns(message_id),
            )
            registrations[heartbeat_id] = registration
            heapq.heappush(
                self._due_heap,
                (registration.next_due_at, registration.heartbeat_id),
            )

        self._registrations = registrations
        self._row_ids = row_ids

    def _peek_registration_rows(
        self,
    ) -> dict[str, list[tuple[dict[str, Any], int]]] | None:
        """Peek every row, advance the cursor, and group rows by canonical id.

        A row without a readable ``heartbeat_id`` is rejected and deleted here.
        Returns ``None`` when the queue cannot be read (best-effort).
        """

        rows: list[tuple[str, int]] = []
        try:
            iterator = self._queue(WEFT_HEARTBEAT_REGISTRATIONS_QUEUE).peek_generator(
                with_timestamps=True
            )
            with closing_queue_iterator(iterator) as entries:
                for body, message_id in entries:
                    rows.append((str(body), int(message_id)))
        except (BrokerError, OSError, RuntimeError):
            logger.debug("Heartbeat registration rebuild deferred", exc_info=True)
            return None
        rows.sort(key=lambda item: item[1])
        if rows and (
            self._registrations_cursor is None
            or rows[-1][1] > self._registrations_cursor
        ):
            self._registrations_cursor = rows[-1][1]

        grouped: dict[str, list[tuple[dict[str, Any], int]]] = {}
        for body, message_id in rows:
            try:
                payload = json.loads(body)
            except ValueError:
                # JSONDecodeError, or an integer past the conversion digit
                # limit: a malformed row, rejected below like any other.
                payload = None
            heartbeat_id = (
                payload.get("heartbeat_id") if isinstance(payload, dict) else None
            )
            if (
                isinstance(payload, dict)
                and isinstance(heartbeat_id, str)
                and heartbeat_id.strip()
            ):
                grouped.setdefault(heartbeat_id.strip(), []).append(
                    (payload, message_id)
                )
                continue
            self._report_state_change(
                event="heartbeat_request_invalid",
                message_id=message_id,
                error="registration row must be a JSON object with a heartbeat_id",
            )
            self._delete_registration_row(message_id)
        return grouped

    def _current_registration_row(
        self,
        id_rows: list[tuple[dict[str, Any], int]],
        *,
        now_ns: int,
    ) -> tuple[int, HeartbeatRegistrationRow] | None:
        """Apply the per-id newest-row rule; return the registration row, if any."""

        payload, message_id = id_rows[-1]
        barrier = False
        for _older, older_id in id_rows[:-1]:
            if not self._delete_registration_row(older_id):
                barrier = True
                break
        try:
            row = HeartbeatRegistrationRow.model_validate(payload)
            self._validate_destination_queue(row.destination_queue)
        except (ValidationError, ValueError) as exc:
            if not barrier:
                self._report_state_change(
                    event="heartbeat_request_invalid",
                    message_id=message_id,
                    error=str(exc),
                )
                self._delete_registration_row(message_id)
            return None
        if row.expired(message_id, now_ns):
            if not barrier and self._delete_registration_row(message_id):
                self._report_state_change(
                    event="heartbeat_registration_expired",
                    message_id=message_id,
                    heartbeat_id=row.heartbeat_id,
                    registrant_tid=row.registrant_tid,
                )
            return None
        return message_id, row

    def _validate_destination_queue(self, destination_queue: str) -> None:
        """Reject unsafe runtime egress when the registration table is built.

        Heartbeat destinations are payload-directed rather than construction
        topology. Ordinary application and task-local queues remain valid, but
        a heartbeat may not route back into its own input/reserved/control lanes
        or any Weft-owned system queue.

        Spec:
        - docs/specifications/05-Message_Flow_and_State.md [MF-3.2]
        - docs/specifications/07-System_Invariants.md [QUEUE.7]
        """

        self_routes = {
            self._queue_names["inbox"],
            self._queue_names["reserved"],
            self._queue_names["ctrl_in"],
            self._queue_names["ctrl_out"],
        }
        if destination_queue in self_routes:
            raise ValueError(
                "destination_queue must not target this Heartbeat task's "
                f"input, reserved, or control lanes: {destination_queue!r}"
            )
        if destination_queue.startswith(WEFT_QUEUE_NAMESPACE_PREFIX):
            raise ValueError(
                "destination_queue must not use the reserved Weft queue "
                f"namespace {WEFT_QUEUE_NAMESPACE_PREFIX!r}: "
                f"{destination_queue!r}"
            )

    @staticmethod
    def _serialize_message_payload(payload: Any) -> str:
        if isinstance(payload, str):
            return payload
        try:
            return json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("message must be JSON-serializable") from exc

    def _emit_due_registrations(self) -> bool:  # noqa: C901 approved [TS-3.1] [RUFF-SUP-043] exception
        """Emit every due registration after a pre-emission table rebuild.

        Linearization boundary: when anything is due, the table is rebuilt
        first and the due list is taken from the rebuilt table. A cancel
        committed before that rebuild suppresses this turn's emission; a
        cancel committed after it may be followed by at most one emission
        (this turn's). The turn never emits for a row that rebuild found
        expired; an emission whose rebuild found the row unexpired may land
        after the expiry instant by at most this turn's duration.

        Spec: [MF-3.2]
        """

        now = time.monotonic()
        next_due = self._next_due_timeout(now=now)
        if next_due is None or next_due > 0.0:
            return False
        self._rebuild_registrations()
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
                    self.taskspec.mark_completed(return_code=0)
                    self._report_state_change(
                        event="heartbeat_service_superseded",
                        owner_tid=owner_tid,
                    )
                    self._update_process_title("completed")
                    self.should_stop = True
                    return True
                if ownership_state != "self":
                    self._reschedule_registration(registration, now=now)
                    continue
                try:
                    with self._get_connected_queue().get_connection() as broker:
                        broker.write(
                            registration.destination_queue, registration.message_text
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
        version_before = self._endpoint_registry_version()
        if (
            not self._ownership_check_required
            and version_before is not None
            and version_before == self._last_endpoint_registry_version
        ):
            return self._cached_service_ownership

        with self._get_connected_queue().get_connection() as broker:
            resolved = resolve_endpoint(
                self._context, INTERNAL_HEARTBEAT_ENDPOINT_NAME, broker=broker
            )
        version_after = self._endpoint_registry_version()
        if resolved is None:
            ownership: tuple[Literal["self", "other", "unknown"], str | None] = (
                "unknown",
                None,
            )
        else:
            owner_tid = resolved.record.tid
            ownership = (
                ("self", owner_tid) if owner_tid == self.tid else ("other", owner_tid)
            )

        self._cached_service_ownership = ownership
        self._last_endpoint_registry_version = version_after
        self._ownership_check_required = (
            version_before is None
            or version_after is None
            or version_before != version_after
        )
        return ownership

    def _endpoint_registry_version(self) -> int | None:
        queue = self._queue(WEFT_ENDPOINTS_REGISTRY_QUEUE)
        try:
            rows = queue.peek_many(limit=1, order="newest", with_timestamps=True)
        except (BrokerError, OSError, RuntimeError):
            return None
        if not rows:
            return None
        _body, timestamp = rows[0]
        return int(timestamp)

    def _exit_if_superseded(self) -> bool:
        ownership_state, owner_tid = self._service_ownership()
        if ownership_state != "other":
            return False
        self.taskspec.mark_completed(return_code=0)
        self._report_state_change(
            event="heartbeat_service_superseded",
            owner_tid=owner_tid,
        )
        self._update_process_title("completed")
        self.should_stop = True
        return True

    def _has_work(self) -> bool:
        """Return whether the heartbeat has work: rows, results, or a rebuild.

        Inputs, all already held by the reactor: ``_registrations`` (any row
        in the in-memory table), pending worker results, and
        ``_registrations_rebuild_pending``. A rebuild that finds no row clears
        the flag and leaves the table empty, so it is not work.

        Spec: [MA-1] item 7, [MF-3.2]
        """

        return bool(
            self._registrations
            or self._registrations_rebuild_pending
            or self._has_pending_worker_results()
        )

    def next_wait_timeout(self) -> float | None:
        """Return the next due emission or lease expiry, else the idle clock.

        With rows the deadlines are the next due emission and the earliest
        lease expiry, and the idle clock is not armed. With an empty table the
        only deadline is the idle-clock remainder the ``ServiceTask`` base
        supplies; otherwise the task blocks on its watcher (ctrl_in and its
        notification interest on ``weft.state.heartbeats``).

        Spec: [MA-1] item 7, [MF-3.2]
        """

        if self._has_pending_worker_results() or self._registrations_rebuild_pending:
            return 0.0
        now = time.monotonic()
        next_due = None if self._paused else self._next_due_timeout(now=now)
        base_timeout = super().next_wait_timeout()
        expiry_timeout: float | None = None
        if self._registrations:
            earliest = min(item.expires_at_ns for item in self._registrations.values())
            expiry_timeout = max(0.0, (earliest - time.time_ns()) / 1_000_000_000)
        timeouts = [
            value
            for value in (
                base_timeout,
                next_due,
                expiry_timeout,
            )
            if value is not None
        ]
        if not timeouts:
            return None
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


__all__ = ["HeartbeatRegistrationRow", "HeartbeatTask"]
