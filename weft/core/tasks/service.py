"""Shared helpers for long-lived task-shaped services.

This module intentionally stays below service policy. It provides common
mechanics used by long-lived tasks while leaving queue ordering, cleanup,
manager leadership, and service-specific scheduling in the concrete task.

Spec references:
- docs/specifications/01-Core_Components.md [CC-2.2], [CC-2.3], [CC-2.5]
- docs/specifications/07-System_Invariants.md [IMPL.8], [IMPL.9]
"""

from __future__ import annotations

import os
import queue as thread_queue
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from weft._constants import TASK_REACTOR_WAKEUP_MAX_SECONDS
from weft.core.taskspec import TaskSpec

from .base import BaseTask, TaskWorkerResult

_service_worker_stop = object()
_service_worker_thread_done = object()


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
            try:
                item = self.input_queue.get(timeout=TASK_REACTOR_WAKEUP_MAX_SECONDS)
            except thread_queue.Empty:
                continue
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
        except Exception:
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
        except Exception:
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

    def _stop_service_worker(self, name: str, *, drain: bool = False) -> None:
        """Request stop for one service-worker group."""

        with self._service_worker_lock:
            registration = self._service_worker_registrations.get(name)
            if registration is None:
                return
            handle = registration.active_handle
            registration.stop_event.set()
            worker_count = handle.worker_count if handle is not None else 0
            input_queue = registration.input_queue

        for _ in range(worker_count):
            while True:
                try:
                    input_queue.put(
                        _service_worker_stop,
                        timeout=TASK_REACTOR_WAKEUP_MAX_SECONDS,
                    )
                    break
                except thread_queue.Full:
                    if self._worker_stopping.is_set():
                        break

        if drain:
            while self._service_worker_snapshot(name).get("active"):
                handled = self._drain_worker_results()
                if handled == 0:
                    if not self._has_active_worker_threads():
                        return
                    self._worker_result_event.wait(
                        timeout=TASK_REACTOR_WAKEUP_MAX_SECONDS
                    )

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
        except BaseException as exc:
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

    def cleanup(self) -> None:
        """Stop service-worker groups before shared task cleanup."""

        for name in tuple(self._service_worker_registrations):
            self._stop_service_worker(name)
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
        """Return seconds until ``last_ns + interval_seconds``."""

        if last_ns <= 0:
            return 0.0
        interval_ns = int(interval_seconds * 1_000_000_000)
        return self._timeout_until_ns(last_ns + interval_ns, now_ns=now_ns)
