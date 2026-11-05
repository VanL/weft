"""Shared helpers for long-lived task-shaped services.

This module intentionally stays below service policy. It provides common
mechanics used by long-lived tasks while leaving queue ordering, cleanup,
manager leadership, and service-specific scheduling in the concrete task.
It also owns the one lifecycle rule every internal service shares: the idle
clock. A service defines its own work (``_has_work``); with no work and no
live Manager in its context for ``SERVICE_IDLE_EXIT_SECONDS`` it ends itself.

Spec references:
- docs/specifications/01-Core_Components.md [CC-2.2], [CC-2.3], [CC-2.5]
- docs/specifications/03-Manager_Architecture.md [MA-1] item 7
- docs/specifications/05-Message_Flow_and_State.md [MF-3.2]
- docs/specifications/07-System_Invariants.md [IMPL.8], [IMPL.9]
"""

from __future__ import annotations

import json
import logging
import os
import queue as thread_queue
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from simplebroker.ext import BrokerError
from weft._constants import (
    LOCAL_WORKER_QUEUE_OPERATION_TIMEOUT_SECONDS,
    MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS,
    SERVICE_IDLE_EXIT_SECONDS,
    SERVICE_STATUS_ACTIVE,
    SERVICE_TYPE_MANAGER,
    TERMINAL_TASK_STATUSES,
    WEFT_SERVICES_REGISTRY_QUEUE,
)
from weft.core.deferred import get_manager_registry_record_liveness
from weft.core.service_convergence import (
    ServiceOwnerRecord,
    collect_service_owner_records,
    manager_service_key,
    reduce_latest_by_service_owner,
    timestamp_is_expired,
)
from weft.core.taskspec import TaskSpec
from weft.helpers import closing_queue_iterator

from .base import BaseTask, TaskWorkerResult

logger = logging.getLogger(__name__)

_service_worker_stop = object()
_service_worker_thread_done = object()


def _service_idle_exit_ns() -> int:
    """Return ``SERVICE_IDLE_EXIT_SECONDS`` in nanoseconds (Spec: [MF-3.2])."""

    return int(SERVICE_IDLE_EXIT_SECONDS * 1_000_000_000)


def _service_monotonic_ns() -> int:
    """Return the monotonic clock the service idle rule reads.

    One module-level seam so tests can freeze the idle clock without patching
    the process-wide ``time`` module.

    Spec: [MF-3.2]
    """

    return time.monotonic_ns()


@dataclass(frozen=True, slots=True)
class ServiceWorkerEvent:
    """Typed event published from a service worker to its owning reactor."""

    name: str
    request_id: str
    worker_index: int | None
    kind: str
    value: Any = None
    error: BaseException | None = None
    item_id: str | None = None


@dataclass(frozen=True, slots=True)
class ServiceWorkerContext:
    """Thread-local context passed to an internal service-worker target."""

    name: str
    request_id: str
    worker_index: int
    input_queue: thread_queue.Queue[Any]
    stop_event: threading.Event
    publish: Callable[[ServiceWorkerEvent], bool]
    task_stop_event: threading.Event | None = None

    def stop_requested(self) -> bool:
        """Return whether this worker group or task is stopping."""

        return self.stop_event.is_set() or (
            self.task_stop_event is not None and self.task_stop_event.is_set()
        )

    def iter_items(self) -> Iterator[Any]:
        """Yield input-queue items until the worker group is stopping."""

        while not self.stop_requested():
            item = self.input_queue.get()
            if item is _service_worker_stop:
                return
            yield item

    def publish_event(
        self,
        kind: str,
        value: Any = None,
        *,
        error: BaseException | None = None,
        item_id: str | None = None,
    ) -> bool:
        """Publish one event through the owning task's worker-result channel."""

        return self.publish(
            ServiceWorkerEvent(
                name=self.name,
                request_id=self.request_id,
                worker_index=self.worker_index,
                kind=kind,
                value=value,
                error=error,
                item_id=item_id,
            )
        )


class ServiceWorkerTarget(Protocol):
    """Callable contract for an internal service worker target."""

    def __call__(
        self,
        context: ServiceWorkerContext,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Run service work using the provided thread-local context."""
        ...


@dataclass(frozen=True, slots=True)
class ServiceWorkerSpec:
    """Registered internal service-worker group definition."""

    name: str
    target: ServiceWorkerTarget
    args: tuple[Any, ...] = ()
    kwargs: Mapping[str, Any] | None = None
    worker_count: int = 1
    input_queue_maxsize: int = 0


@dataclass(frozen=True, slots=True)
class ServiceWorkerHandle:
    """Active service-worker group handle."""

    name: str
    request_id: str
    input_queue: thread_queue.Queue[Any]
    stop_event: threading.Event
    worker_count: int


@dataclass(slots=True)
class _ServiceWorkerRegistration:
    """Private mutable service-worker group state."""

    spec: ServiceWorkerSpec
    input_queue: thread_queue.Queue[Any]
    stop_event: threading.Event
    active_handle: ServiceWorkerHandle | None = None
    threads: tuple[threading.Thread, ...] = ()
    threads_started: int = 0
    threads_completed: int = 0
    events_published: int = 0


class ServiceTask(BaseTask):
    """Thin internal base for long-lived services built on ``BaseTask``."""

    def __init__(
        self,
        db: Path | str | Any,
        taskspec: TaskSpec,
        stop_event: threading.Event | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        self._service_lane_work_items: dict[str, Any] = {}
        self._service_worker_registrations: dict[str, _ServiceWorkerRegistration] = {}
        self._service_worker_lock = threading.Lock()
        # Start of the current no-work window; None while the service has work.
        self._idle_since_monotonic_ns: int | None = None
        super().__init__(
            db=db,
            taskspec=taskspec,
            stop_event=stop_event,
            config=config,
        )

    def _activate_service_task(self, *, set_spawning_title: bool = False) -> bool:
        """Publish the standard running lifecycle for a long-lived service."""

        if self.taskspec.state.status != "created":
            return False
        self.taskspec.mark_started(pid=os.getpid())
        if set_spawning_title:
            self._update_process_title("spawning")
        self._report_state_change(event="task_spawning")
        self.taskspec.mark_running(pid=os.getpid())
        self._update_process_title("running")
        self._report_state_change(event="task_started")
        return True

    def _maybe_emit_poll_report(self) -> None:
        """Disable poll-style lifecycle reports for long-lived services.

        Services publish durable lifecycle transitions and expose current
        activity through control/PONG and service registry surfaces. Repeating
        status rows in ``weft.log.tasks`` would make the monitor's cleanup input
        grow because of monitor-owned service work.

        Spec: [CC-2.3], [MF-5]
        """

        return

    def next_wait_timeout(self) -> float | None:
        """Return the idle-clock remainder; services emit no poll-report timer.

        With the idle clock set, no work, and no observation in progress, this
        is the time left in the ``SERVICE_IDLE_EXIT_SECONDS`` window; a
        subclass merges it into its own deadlines with ``min``. With work, or
        while work is undetermined, there is no idle timeout: the pending
        result wakes the reactor, so an expired window never spins.

        Spec: [CC-2.5], [MA-1] item 7, [MF-3.2]
        """

        idle_since = self._idle_since_monotonic_ns
        if idle_since is None or self._has_work() or self._work_undetermined():
            return None
        remaining_ns = _service_idle_exit_ns() - (_service_monotonic_ns() - idle_since)
        return max(0.0, remaining_ns / 1_000_000_000)

    def _reactor_support_routes(self) -> dict[str, str]:
        """Declare the service registry read by the idle rule [QUEUE.7].

        Spec: docs/specifications/07-System_Invariants.md [QUEUE.7]
        """

        routes = super()._reactor_support_routes()
        routes["services_registry"] = WEFT_SERVICES_REGISTRY_QUEUE
        return routes

    def _process_reactor_turn(self) -> None:
        """Run the service's own turn, then settle the idle clock last.

        Controls, worker results and the service's own work reconcile first in
        ``_service_reactor_turn``; the idle check runs after them, so it reads
        the state the turn just updated.

        Spec: [CC-2.2.1], [MA-1] item 7, [MF-3.2]
        """

        self._service_reactor_turn()
        self._settle_idle_clock()

    def _service_reactor_turn(self) -> None:
        """Run one concrete service turn; the default is the BaseTask turn.

        Spec: [CC-2.2.1], [CC-2.5]
        """

        super()._process_reactor_turn()

    def _has_work(self) -> bool:
        """Return whether this service has substantive outstanding work.

        Each built-in service overrides this from state it already keeps.
        Checking for work is never itself work. The default is ``True``: a
        service that does not define its work never idle-exits.

        Spec: [MA-1] item 7, [MF-3.2]
        """

        return True

    def _work_undetermined(self) -> bool:
        """Return whether an observation has started whose result is unapplied.

        While true the idle decision is deferred: the clock neither resets nor
        advances toward an exit. The default is ``False``.

        Spec: [MA-1] item 7, [MF-3.2]
        """

        return False

    def _settle_idle_clock(self) -> None:
        """Apply the service idle rule once, at the end of a reactor turn.

        Order: a stopping or terminal task does nothing (a STOP handled earlier
        in the turn owns the terminal transition). With work the clock is
        cleared. With no work and a live non-superseded Manager in the context
        the window restarts at now, so the service stays and re-checks at the
        next deadline (owner ruling 10.1). An observation in progress leaves
        the clock exactly as it is. Otherwise the first idle turn starts the
        clock, and a turn at or past ``SERVICE_IDLE_EXIT_SECONDS`` marks the
        task completed, reports ``service_idle_exit`` and stops. The registry
        is read only when ``_has_work()`` is false, at most once per turn.

        Spec: [MA-1] item 7, [MF-3.2]
        """

        if self.should_stop or self.taskspec.state.status in TERMINAL_TASK_STATUSES:
            return
        if self._has_work():
            self._idle_since_monotonic_ns = None
            return
        if self._live_manager_present():
            self._idle_since_monotonic_ns = _service_monotonic_ns()
            return
        if self._work_undetermined():
            return
        now_ns = _service_monotonic_ns()
        idle_since = self._idle_since_monotonic_ns
        if idle_since is None:
            self._idle_since_monotonic_ns = now_ns
            return
        idle_ns = now_ns - idle_since
        if idle_ns < _service_idle_exit_ns():
            return
        self.taskspec.mark_completed(return_code=0)
        self._report_state_change(
            event="service_idle_exit",
            idle_seconds=idle_ns / 1_000_000_000,
        )
        self._update_process_title("completed")
        self.should_stop = True

    def _live_manager_present(self) -> bool:
        """Return whether a live non-superseded Manager serves this context.

        Reads ``weft.state.services`` once, keeps the canonical manager key
        through the shared ``service_convergence`` readers, reduces to the
        latest row per Manager owner, and reports whether any ``active`` row
        keeps the service: ``live`` evidence always does, ``unknown`` evidence
        does while the row is younger than the unknown-row age policy the
        Manager reader and ``weft status`` apply
        (``MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS``), and ``stale``
        evidence never does. The evidence is the Manager reader's own
        three-valued ``manager_registry_record_liveness``. A registry that
        cannot be read reports ``True``: absence is not proof ([WT-2a.13]), and
        the next wake re-checks.

        Spec: [MA-1] item 7, [MA-1.6], [MF-3.2]
        """

        entries: list[tuple[Mapping[str, Any], int]] = []
        try:
            rows = self._queue(WEFT_SERVICES_REGISTRY_QUEUE).peek_generator(
                with_timestamps=True
            )
            with closing_queue_iterator(rows) as iterator:
                for body, timestamp in iterator:
                    try:
                        payload = json.loads(body)
                    except (TypeError, ValueError):
                        continue
                    if isinstance(payload, Mapping):
                        entries.append((payload, int(timestamp)))
        except (BrokerError, OSError, RuntimeError):
            logger.debug("Service registry read deferred", exc_info=True)
            return True
        read = collect_service_owner_records(
            entries,
            service_key=manager_service_key(self._task_context()),
            service_type=SERVICE_TYPE_MANAGER,
        )
        now_ns = time.time_ns()
        return any(
            record.status == SERVICE_STATUS_ACTIVE
            and self._manager_owner_record_live(record, now_ns=now_ns)
            for record in reduce_latest_by_service_owner(read.records)
        )

    @staticmethod
    def _manager_owner_record_live(record: ServiceOwnerRecord, *, now_ns: int) -> bool:
        """Return whether one active Manager owner row keeps the service.

        ``live`` keeps it; ``unknown`` keeps it within the unknown-row age
        policy; ``stale`` does not.

        Spec: [MA-1.6], [LIVENESS.R3]
        """

        liveness = get_manager_registry_record_liveness()(dict(record.payload))
        if liveness == "live":
            return True
        if liveness == "stale":
            return False
        return not timestamp_is_expired(
            record.timestamp,
            now_ns,
            int(MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS * 1_000_000_000),
        )

    def _emit_activity_event(self) -> None:
        """Keep service activity live-only instead of writing task-log rows.

        ``BaseTask._set_activity`` still updates the in-memory activity snapshot,
        process title, and TID mapping. The service layer only suppresses the
        lightweight ``task_activity`` row in ``weft.log.tasks``.

        Spec: [CC-2.3], [MF-5]
        """

        return

    def _service_lane_in_flight(self, lane: str | None = None) -> bool:
        """Return whether one or any service lane has uncommitted work."""

        if lane is None:
            return bool(self._service_lane_work_items)
        return lane in self._service_lane_work_items

    def _service_lane_work(self, lane: str) -> Any | None:
        """Return the current work item for a service lane, if present."""

        return self._service_lane_work_items.get(lane)

    def _start_service_lane(
        self,
        lane: str,
        work: Any,
        func: Callable[[], Any],
    ) -> threading.Thread:
        """Start single-flight work on a service lane."""

        return self._start_service_lane_with_submitter(lane, work, func)

    def _start_service_call_lane(
        self,
        lane: str,
        work: Any,
        func: Callable[[], Any],
    ) -> threading.Thread:
        """Start broker-free single-flight work on a service lane."""

        return self._start_service_lane_with_submitter(lane, work, func)

    def _start_service_lane_with_submitter(
        self,
        lane: str,
        work: Any,
        func: Callable[[], Any],
    ) -> threading.Thread:
        """Record single-flight service work and submit it to a worker lane."""

        if lane in self._service_lane_work_items:
            raise RuntimeError(f"Service lane {lane!r} already has work in flight")

        def target(context: ServiceWorkerContext) -> Any:
            del context
            return func()

        self._register_service_worker(
            ServiceWorkerSpec(
                name=lane,
                target=target,
                worker_count=1,
            )
        )
        return self._start_registered_service_lane(
            lane,
            work,
            request_id=f"{self.tid}:{lane}:{time.time_ns()}",
            initial_items=(),
        )

    def _start_registered_service_lane(
        self,
        lane: str,
        work: Any,
        *,
        request_id: str,
        initial_items: Iterable[Any] | None = None,
    ) -> threading.Thread:
        """Start registered single-flight work and track the current item."""

        if lane in self._service_lane_work_items:
            raise RuntimeError(f"Service lane {lane!r} already has work in flight")
        self._service_lane_work_items[lane] = work
        try:
            self._start_service_worker(
                lane,
                request_id=request_id,
                initial_items=(work,) if initial_items is None else initial_items,
            )
        except Exception:  # pragma: no cover - restore lane state before re-raise
            self._service_lane_work_items.pop(lane, None)
            raise
        with self._service_worker_lock:
            registration = self._service_worker_registrations[lane]
            if not registration.threads:
                raise RuntimeError(f"Service lane {lane!r} did not start a worker")
            return registration.threads[0]

    def _pop_service_lane_work(self, lane: str) -> Any | None:
        """Clear and return the current work item for a service lane."""

        return self._service_lane_work_items.pop(lane, None)

    def _register_service_worker(self, spec: ServiceWorkerSpec) -> None:
        """Register an internal service-worker group without starting threads."""

        if not spec.name.strip():
            raise ValueError("service worker name must be non-empty")
        if not callable(spec.target):
            raise TypeError("service worker target must be callable")
        if spec.worker_count < 1:
            raise ValueError("service worker worker_count must be at least 1")
        if spec.input_queue_maxsize < 0:
            raise ValueError("service worker input_queue_maxsize must be >= 0")

        with self._service_worker_lock:
            existing = self._service_worker_registrations.get(spec.name)
            if existing is not None and existing.active_handle is not None:
                raise RuntimeError(f"Service worker {spec.name!r} is active")
            self._service_worker_registrations[spec.name] = _ServiceWorkerRegistration(
                spec=spec,
                input_queue=thread_queue.Queue(maxsize=spec.input_queue_maxsize),
                stop_event=threading.Event(),
            )

    def _start_service_worker(
        self,
        name: str,
        *,
        request_id: str | None = None,
        initial_items: Iterable[Any] = (),
    ) -> ServiceWorkerHandle:
        """Start all threads for a registered service-worker group."""

        with self._service_worker_lock:
            registration = self._service_worker_registrations.get(name)
            if registration is None:
                raise KeyError(f"Unknown service worker {name!r}")
            self._prune_inactive_service_worker_locked(registration)
            if registration.active_handle is not None:
                raise RuntimeError(f"Service worker {name!r} is already active")
            registration.stop_event.clear()
            registration.threads = ()
            registration.threads_started = 0
            registration.threads_completed = 0
            registration.events_published = 0
            handle = ServiceWorkerHandle(
                name=name,
                request_id=request_id or f"{self.tid}:{name}:{time.time_ns()}",
                input_queue=registration.input_queue,
                stop_event=registration.stop_event,
                worker_count=registration.spec.worker_count,
            )
            registration.active_handle = handle

        try:
            for item in initial_items:
                self._enqueue_service_work(name, item, block=False)

            def worker_func(
                index: int, handle: ServiceWorkerHandle
            ) -> Callable[[], object]:
                return lambda: self._run_service_worker_thread(
                    handle.name,
                    handle.request_id,
                    index,
                )

            threads = tuple(
                self._submit_worker_lane(
                    name,
                    worker_func(index, handle),
                )
                for index in range(handle.worker_count)
            )
        except Exception:  # pragma: no cover - unwind partially started workers
            self._stop_service_worker(name)
            with self._service_worker_lock:
                registration = self._service_worker_registrations.get(name)
                if registration is not None:
                    registration.active_handle = None
                    registration.threads = ()
            raise

        with self._service_worker_lock:
            registration = self._service_worker_registrations[name]
            registration.threads = threads
            registration.threads_started = len(threads)
        return handle

    @staticmethod
    def _prune_inactive_service_worker_locked(
        registration: _ServiceWorkerRegistration,
    ) -> None:
        """Clear a stale active handle once all recorded threads are dead."""

        if registration.active_handle is None:
            return
        live_threads = tuple(
            thread for thread in registration.threads if thread.is_alive()
        )
        if live_threads:
            registration.threads = live_threads
            return
        if registration.threads_started <= 0:
            return
        registration.active_handle = None
        registration.threads = ()

    def _enqueue_service_work(
        self,
        name: str,
        item: Any,
        *,
        block: bool = False,
        timeout: float | None = None,
    ) -> bool:
        """Put one item on a service-worker group's input queue."""

        with self._service_worker_lock:
            registration = self._service_worker_registrations.get(name)
            if registration is None:
                raise KeyError(f"Unknown service worker {name!r}")
            input_queue = registration.input_queue
        try:
            input_queue.put(item, block=block, timeout=timeout)
        except thread_queue.Full:
            return False
        return True

    def _stop_service_worker(
        self,
        name: str,
        *,
        deadline: float | None = None,
    ) -> None:
        """Request stop for one service-worker group.

        Sets the group stop event, then delivers one stop sentinel per worker.
        Each sentinel is first offered with ``put_nowait`` before any deadline
        check: a worker can block in ``get()`` only on an empty queue, and every
        group's queue is unbounded or holds at least ``worker_count`` items, so
        a blocked worker always receives its sentinel, even when the cleanup
        deadline has already expired. Only a full queue (no worker blocked)
        falls into the deadline-bounded put-and-evict retry.

        Spec: docs/specifications/07-System_Invariants.md [IMPL.9], [IMPL.10]
        """

        with self._service_worker_lock:
            registration = self._service_worker_registrations.get(name)
            if registration is None:
                return
            handle = registration.active_handle
            registration.stop_event.set()
            worker_count = handle.worker_count if handle is not None else 0
            input_queue = registration.input_queue

        for _ in range(worker_count):
            try:
                input_queue.put_nowait(_service_worker_stop)
                continue
            except thread_queue.Full:
                pass
            while True:
                if deadline is not None:
                    remaining = self._remaining_deadline(deadline)
                    if remaining <= 0:
                        break
                    put_timeout = min(
                        remaining,
                        LOCAL_WORKER_QUEUE_OPERATION_TIMEOUT_SECONDS,
                    )
                else:
                    put_timeout = LOCAL_WORKER_QUEUE_OPERATION_TIMEOUT_SECONDS
                try:
                    input_queue.put(
                        _service_worker_stop,
                        timeout=put_timeout,
                    )
                    break
                except thread_queue.Full:
                    try:
                        input_queue.get_nowait()
                    except thread_queue.Empty:
                        pass
                    if self._worker_stopping.is_set():
                        break

    def _service_worker_snapshot(self, name: str | None = None) -> Mapping[str, Any]:
        """Return cached in-memory worker state for diagnostics."""

        with self._service_worker_lock:
            registrations = dict(self._service_worker_registrations)

        def snapshot_one(registration: _ServiceWorkerRegistration) -> dict[str, Any]:
            handle = registration.active_handle
            return {
                "name": registration.spec.name,
                "active": handle is not None,
                "request_id": handle.request_id if handle is not None else None,
                "worker_count": registration.spec.worker_count,
                "queued_items": registration.input_queue.qsize(),
                "threads_started": registration.threads_started,
                "threads_completed": registration.threads_completed,
                "events_published": registration.events_published,
                "stopping": registration.stop_event.is_set(),
            }

        if name is not None:
            registration = registrations.get(name)
            return {} if registration is None else snapshot_one(registration)
        return {
            worker_name: snapshot_one(registration)
            for worker_name, registration in sorted(registrations.items())
        }

    def _handle_service_worker_event(self, event: ServiceWorkerEvent) -> None:
        """Handle a typed service-worker event on the task reactor thread."""

        return

    def _publish_service_worker_event(self, event: ServiceWorkerEvent) -> bool:
        """Publish one typed service-worker event through BaseTask plumbing."""

        with self._service_worker_lock:
            registration = self._service_worker_registrations.get(event.name)
            if registration is not None:
                registration.events_published += 1
        return self._publish_worker_result(event.name, value=event)

    def _run_service_worker_thread(
        self,
        name: str,
        request_id: str,
        worker_index: int,
    ) -> object:
        """Run one registered service-worker target."""

        with self._service_worker_lock:
            registration = self._service_worker_registrations[name]
            spec = registration.spec
            input_queue = registration.input_queue
            stop_event = registration.stop_event

        context = ServiceWorkerContext(
            name=name,
            request_id=request_id,
            worker_index=worker_index,
            input_queue=input_queue,
            stop_event=stop_event,
            publish=self._publish_service_worker_event,
            task_stop_event=self._stop_event,
        )
        result_kind = "result"
        result_value: Any = None
        result_error: BaseException | None = None
        try:
            context.publish_event("started")
            value = spec.target(context, *spec.args, **dict(spec.kwargs or {}))
        except BaseException as exc:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-347] exception
            stop_event.set()
            result_kind = "error"
            result_error = exc
        else:
            result_value = value
        finally:
            self._mark_service_worker_thread_completed(
                name,
                request_id=request_id,
            )
            context.publish_event(
                result_kind,
                result_value,
                error=result_error,
            )
            context.publish_event("stopped")
        return _service_worker_thread_done

    def _mark_service_worker_thread_completed(
        self,
        name: str,
        *,
        request_id: str,
    ) -> None:
        """Record one worker-thread completion before terminal event delivery."""

        with self._service_worker_lock:
            completed_registration = self._service_worker_registrations.get(name)
            if completed_registration is None:
                return
            completed_registration.threads_completed += 1
            active_handle = completed_registration.active_handle
            if active_handle is None or active_handle.request_id != request_id:
                return
            if completed_registration.threads_completed < active_handle.worker_count:
                return
            completed_registration.active_handle = None
            completed_registration.threads = ()

    def _handle_worker_result(self, result: TaskWorkerResult) -> None:
        """Route typed service-worker events before falling back to BaseTask."""

        if isinstance(result.value, ServiceWorkerEvent):
            self._handle_service_worker_event(result.value)
            return
        if result.value is _service_worker_thread_done and result.error is None:
            return
        with self._service_worker_lock:
            registration = self._service_worker_registrations.get(result.lane)
            handle = registration.active_handle if registration is not None else None
        if registration is not None and result.error is not None:
            self._handle_service_worker_event(
                ServiceWorkerEvent(
                    name=result.lane,
                    request_id=(
                        handle.request_id
                        if handle is not None
                        else f"{self.tid}:{result.lane}"
                    ),
                    worker_index=None,
                    kind="error",
                    error=result.error,
                )
            )
            return
        super()._handle_worker_result(result)

    def _cleanup_task_resources(self, deadline: float) -> None:
        """Stop service-worker groups before parent task resources.

        Spec: docs/specifications/07-System_Invariants.md [IMPL.10]
        """

        for name in tuple(self._service_worker_registrations):
            self._stop_service_worker(name, deadline=deadline)
        super()._cleanup_task_resources(deadline)

    def _abort_partial_initialization(self) -> None:
        """Stop service workers before shared constructor unwind closes queues."""

        failures: list[BaseException] = []
        deadline = time.monotonic() + LOCAL_WORKER_QUEUE_OPERATION_TIMEOUT_SECONDS
        for name in tuple(self._service_worker_registrations):
            try:
                self._stop_service_worker(name, deadline=deadline)
            except BaseException as exc:  # noqa: BLE001 - construction cleanup boundary
                failures.append(exc)
        try:
            super()._abort_partial_initialization()
        except BaseException as exc:  # noqa: BLE001 - construction cleanup boundary
            failures.append(exc)
        if failures:
            primary = failures[0]
            for secondary in failures[1:]:
                primary.add_note(
                    f"Additional service construction cleanup failure: {secondary!r}"
                )
            raise primary

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
        """Return seconds until ``last_ns + interval_seconds``."""

        if last_ns <= 0:
            return 0.0
        interval_ns = int(interval_seconds * 1_000_000_000)
        return self._timeout_until_ns(last_ns + interval_ns, now_ns=now_ns)
