"""Multi-queue watcher primitives tailored for Weft tasks.

The module extends SimpleBroker's watcher so that each queue can opt into a
specific processing mode (read, peek, reserve) while sharing a common database
and error-handling strategy. The implementation also threads through Weft's
runtime configuration so callers can honour project-wide defaults without
duplicating wiring logic.

Spec references:
- docs/specifications/01-Core_Components.md [CC-2.1]
- docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]
"""

from __future__ import annotations

import itertools
import logging
import threading
import time
import weakref
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from simplebroker import BrokerTarget, Queue, create_activity_waiter_for_queues
from simplebroker.ext import (
    BaseWatcher,
    BrokerError,
    PollingStrategy,
    default_error_handler,
)
from weft._constants import (
    QUEUE_PRIORITY_NORMAL,
    TASK_INACTIVE_QUEUE_DISCOVERY_INTERVAL_SECONDS,
    load_config,
)
from weft.context import resolve_context_broker_target

logger = logging.getLogger(__name__)


class QueueMode(StrEnum):
    """Supported queue processing behaviours (Spec: [CC-2.1])."""

    READ = "read"
    RESERVE = "reserve"
    PEEK = "peek"


@dataclass
class QueueMessageContext:
    """Context passed to queue handlers describing the active message (Spec: [CC-2.1])."""

    queue_name: str
    queue: Queue
    mode: QueueMode
    timestamp: int
    reserved_queue_name: str | None = None


@dataclass
class QueueRuntimeConfig:
    """Internal representation of a queue configuration (Spec: [CC-2.1])."""

    name: str
    queue: Queue
    handler: Callable[[str, int, QueueMessageContext], None]
    mode: QueueMode
    error_handler: Callable[[Exception, str, int], bool | None]
    reserved_queue_name: str | None = None
    priority: int = QUEUE_PRIORITY_NORMAL


@dataclass(slots=True)
class _TopologyMutation:
    """One synchronous dynamic-topology request owned by the drive thread."""

    kind: str
    queue_name: str
    handler: Callable[[str, int, QueueMessageContext], None] | None = None
    mode: QueueMode = QueueMode.READ
    reserved_queue_name: str | None = None
    error_handler: Callable[[Exception, str, int], bool | None] | None = None
    priority: int = QUEUE_PRIORITY_NORMAL
    done: threading.Event = field(default_factory=threading.Event)
    error: BaseException | None = None


class _TopologyDriveError(Exception):
    """Wrap an owner transaction defect that must enter inherited retry."""

    def __init__(self, cause: Exception) -> None:
        super().__init__(str(cause))
        self.cause = cause


def _resolve_db_target(
    db: BrokerTarget | str | Path | None,
    fallback: BrokerTarget,
) -> BrokerTarget | str:
    """Derive a broker target shared across Queue instances."""
    if isinstance(db, BrokerTarget):
        return db
    if isinstance(db, (str, Path)):
        return str(db)
    return fallback


def _detach_queue_stop_event(queue: Queue) -> None:
    """Keep queue connections usable after the watcher stop event is set."""
    if hasattr(queue, "set_stop_event"):
        queue.set_stop_event(None)


class MultiQueueWatcher(BaseWatcher):
    """Monitor multiple queues with per-queue processing semantics (Spec: [CC-2.1], [SB-0.4])."""

    def __init__(
        self,
        queue_configs: Mapping[str, Mapping[str, object]],
        *,
        db: BrokerTarget | str | Path | None = None,
        stop_event: threading.Event | None = None,
        persistent: bool = True,
        polling_strategy: PollingStrategy | None = None,
        yield_strategy: str = "round_robin",
        check_interval: int = 10,
        inactive_probe_interval: float = TASK_INACTIVE_QUEUE_DISCOVERY_INTERVAL_SECONDS,
        default_error_handler_fn: Callable[
            [Exception, str, int], bool | None
        ] = default_error_handler,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        """Initialize the watcher with queue-specific configurations.

        Args:
            queue_configs: Mapping of queue name to configuration dict with keys:
                - handler (callable): required, signature (message, timestamp, context)
                - mode (QueueMode or str): optional, defaults to QueueMode.READ
                - error_handler (callable): optional override per queue
            db: Explicit broker target or filesystem path for the watched queues
            stop_event: Event used to signal watcher shutdown
            persistent: Whether queues should be persistent
            polling_strategy: Optional SimpleBroker polling strategy override
            yield_strategy: Queue iteration strategy (currently round_robin)
            check_interval: Legacy turn-count discovery setting retained for
                existing callers; inactive discovery is now time-bounded.
            inactive_probe_interval: Minimum seconds between broad inactive
                queue discovery probes when no native activity hint is pending.
            default_error_handler_fn: Fallback error handler when queue config
                does not supply one (defaults to SimpleBroker's default)
            config: Optional SimpleBroker configuration dictionary. If omitted,
                :func:`weft._constants.load_config` is used.

        Spec: [CC-2.1], [SB-0.4]
        """
        if not queue_configs:
            raise ValueError("queue_configs cannot be empty")

        config_dict: dict[str, Any] = (
            dict(config) if config is not None else load_config()
        )
        self._config: dict[str, Any] = config_dict

        self._persistent = persistent
        self._yield_strategy = yield_strategy
        self._check_interval = check_interval
        self._inactive_probe_interval = max(0.0, float(inactive_probe_interval))
        self._default_error_handler = default_error_handler_fn
        self._handler: Callable[[str, int], None] | None = None
        self._error_handler: Callable[[Exception, str, int], bool | None] | None = None

        # Establish primary queue and shared broker target
        first_queue_name = next(iter(queue_configs.keys()))
        shared_target = _resolve_db_target(
            db,
            resolve_context_broker_target(Path.cwd(), config=self._config),
        )
        # Direct Queue ok here: MultiQueueWatcher is creating its owned primary
        # handle; see runtime-and-context-patterns.md section 2.
        initial_queue = Queue(
            first_queue_name,
            db_path=shared_target,
            persistent=persistent,
            config=self._config,
        )

        super().__init__(
            initial_queue,
            stop_event=stop_event,
            polling_strategy=polling_strategy,
            config=self._config,
        )
        _detach_queue_stop_event(initial_queue)

        self._db_path = initial_queue.db_target

        # Build runtime configs for each queue
        self._queues: dict[str, QueueRuntimeConfig] = {}
        for queue_name, raw_config in queue_configs.items():
            handler_obj = raw_config.get("handler")
            if not callable(handler_obj):
                raise TypeError(
                    f"handler for queue '{queue_name}' must be callable, "
                    f"got {type(handler_obj).__name__}"
                )
            handler = cast(Callable[[str, int, QueueMessageContext], None], handler_obj)

            mode_value = raw_config.get("mode", QueueMode.READ)
            mode = (
                mode_value
                if isinstance(mode_value, QueueMode)
                else QueueMode(str(mode_value))
            )

            if queue_name == first_queue_name:
                queue_obj = initial_queue
            else:
                # Direct Queue ok here: MultiQueueWatcher owns watched queue
                # handles by design; see runtime-and-context-patterns.md section 2.
                queue_obj = Queue(
                    queue_name,
                    db_path=self._db_path,
                    persistent=persistent,
                    config=self._config,
                )

            _detach_queue_stop_event(queue_obj)

            error_handler_obj = raw_config.get("error_handler")
            if error_handler_obj is not None and not callable(error_handler_obj):
                raise TypeError(
                    f"error_handler for queue '{queue_name}' must be callable, "
                    f"got {type(error_handler_obj).__name__}"
                )
            error_handler = (
                cast(Callable[[Exception, str, int], bool | None], error_handler_obj)
                if error_handler_obj is not None
                else None
            )

            reserved_name_obj = raw_config.get("reserved_queue")
            reserved_name: str | None
            if reserved_name_obj is None:
                reserved_name = None
            elif isinstance(reserved_name_obj, str):
                reserved_name = reserved_name_obj
            else:
                raise TypeError(
                    f"reserved_queue for '{queue_name}' must be a string, "
                    f"got {type(reserved_name_obj).__name__}"
                )

            if mode is QueueMode.RESERVE and not reserved_name:
                raise ValueError(
                    f"Queue '{queue_name}' configured in reserve mode must supply 'reserved_queue'"
                )

            priority_obj = raw_config.get("priority", QUEUE_PRIORITY_NORMAL)
            if not isinstance(priority_obj, int):
                raise TypeError(
                    f"priority for '{queue_name}' must be an int, "
                    f"got {type(priority_obj).__name__}"
                )

            runtime_config = QueueRuntimeConfig(
                name=queue_name,
                queue=queue_obj,
                handler=handler,
                mode=mode,
                error_handler=error_handler or default_error_handler_fn,
                reserved_queue_name=reserved_name,
                priority=priority_obj,
            )
            self._queues[queue_name] = runtime_config

        # Processing state
        self._active_queues: list[str] = []
        self._queue_iterator: itertools.cycle[str] = itertools.cycle([])
        self._check_counter = 0
        self._queue_generation = 0
        self._multi_activity_waiter: Any | None = None
        self._multi_activity_waiter_generation: int | None = None
        self._multi_activity_waiter_signature: tuple[str, ...] | None = None
        self._pending_messages_precheck_confirmed = False
        self._next_inactive_probe_at = time.monotonic()
        self._topology_lock = threading.RLock()
        self._topology_mutations: deque[_TopologyMutation] = deque()
        self._topology_pending = threading.Event()
        self._topology_inflight: _TopologyMutation | None = None
        self._topology_owner_thread: threading.Thread | None = None
        self._topology_reserved_thread: threading.Thread | None = None
        self._topology_manual_wait_thread: threading.Thread | None = None
        self._topology_stopping = False
        self._topology_sigint_critical = False
        self._topology_deferred_sigint = False

        logger.debug(
            "MultiQueueWatcher initialized with queues: %s",
            list(self._queues.keys()),
        )
        self._ensure_multi_activity_waiter()

    # ------------------------------------------------------------------ #
    # Public API                                                         #
    # ------------------------------------------------------------------ #

    def list_queues(self) -> list[str]:
        """Return all configured queue names.

        Spec: [CC-2.1]
        """
        with self._topology_lock:
            return list(self._queues.keys())

    def add_queue(
        self,
        queue_name: str,
        handler: Callable[[str, int, QueueMessageContext], None],
        *,
        mode: QueueMode = QueueMode.READ,
        reserved_queue: str | None = None,
        error_handler: Callable[[Exception, str, int], bool | None] | None = None,
        priority: int = QUEUE_PRIORITY_NORMAL,
    ) -> None:
        """Dynamically add a queue to the watcher.

        Spec: [CC-2.1], [SB-0.4], [QUEUE.8]
        """
        self._validate_add_arguments(
            handler=handler,
            mode=mode,
            reserved_queue=reserved_queue,
            error_handler=error_handler,
            priority=priority,
        )
        request = _TopologyMutation(
            kind="add",
            queue_name=queue_name,
            handler=handler,
            mode=mode,
            reserved_queue_name=reserved_queue,
            error_handler=error_handler,
            priority=priority,
        )
        self._submit_topology_mutation(request)

    def remove_queue(self, queue_name: str) -> None:
        """Remove a queue from the watcher.

        Spec: [CC-2.1], [SB-0.4], [QUEUE.8]
        """
        self._submit_topology_mutation(
            _TopologyMutation(kind="remove", queue_name=queue_name)
        )

    def get_queue(self, queue_name: str) -> Queue | None:
        """Return the managed Queue instance for *queue_name* if present.

        Spec: [SB-0.1]
        """
        with self._topology_lock:
            config = self._queues.get(queue_name)
        return config.queue if config else None

    # ------------------------------------------------------------------ #
    # Internal helpers                                                   #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _validate_add_arguments(
        *,
        handler: Callable[[str, int, QueueMessageContext], None],
        mode: QueueMode,
        reserved_queue: str | None,
        error_handler: Callable[[Exception, str, int], bool | None] | None,
        priority: int,
    ) -> None:
        """Validate one public add request before it can have effects."""
        if not callable(handler):
            raise TypeError(f"handler must be callable, got {type(handler).__name__}")
        if error_handler is not None and not callable(error_handler):
            raise TypeError(
                f"error_handler must be callable, got {type(error_handler).__name__}"
            )
        if mode is QueueMode.RESERVE and reserved_queue is None:
            raise ValueError("reserve mode requires reserved_queue")
        if not isinstance(priority, int):
            raise TypeError(f"priority must be an int, got {type(priority).__name__}")

    def _open_runtime_config(self, request: _TopologyMutation) -> QueueRuntimeConfig:
        """Open the queue facade for an already validated add request."""
        handler = request.handler
        if handler is None:  # pragma: no cover - internal invariant
            raise RuntimeError("add mutation is missing its handler")
        # Direct Queue ok here: MultiQueueWatcher owns dynamically watched queue
        # handles by design; see runtime-and-context-patterns.md section 2.
        queue_obj = Queue(
            request.queue_name,
            db_path=self._db_path,
            persistent=self._persistent,
            config=self._config,
        )
        _detach_queue_stop_event(queue_obj)
        return QueueRuntimeConfig(
            name=request.queue_name,
            queue=queue_obj,
            handler=handler,
            mode=request.mode,
            error_handler=request.error_handler or self._default_error_handler,
            reserved_queue_name=request.reserved_queue_name,
            priority=request.priority,
        )

    def _submit_topology_mutation(self, request: _TopologyMutation) -> None:
        """Apply before drive start or synchronously submit to the drive owner."""
        current = threading.current_thread()
        with self._topology_lock:
            if self._topology_stopping or self._stop_event.is_set():
                raise RuntimeError("watcher topology is stopping")
            if self._topology_manual_wait_thread is not None:
                raise RuntimeError("watcher topology is owned by a manual wait")
            if self._topology_owner_thread is current:
                raise RuntimeError("drive owner cannot mutate topology during dispatch")

            if (
                self._topology_owner_thread is None
                and self._topology_reserved_thread is None
            ):
                self._apply_topology_mutation_before_start_locked(request)
                return

            self._topology_mutations.append(request)
            self._topology_pending.set()
            self._strategy.notify_activity()

        request.done.wait()
        if request.error is not None:
            raise request.error

    def _apply_topology_mutation_before_start_locked(
        self,
        request: _TopologyMutation,
    ) -> None:
        """Apply one mutation synchronously while no drive can exist."""
        if request.kind == "add":
            if request.queue_name in self._queues:
                raise ValueError(f"Queue '{request.queue_name}' already exists")
            self._queues[request.queue_name] = self._open_runtime_config(request)
            self._pending_messages_precheck_confirmed = True
        elif request.kind == "remove":
            if request.queue_name not in self._queues:
                raise ValueError(f"Queue '{request.queue_name}' not found")
            del self._queues[request.queue_name]
            self._active_queues = [
                name for name in self._active_queues if name != request.queue_name
            ]
            self._queue_iterator = itertools.cycle(self._active_queues)
        else:  # pragma: no cover - internal invariant
            raise RuntimeError(f"unknown topology mutation: {request.kind}")
        self._queue_generation += 1
        self._reset_multi_activity_waiter()

    def _create_candidate_activity_waiter(
        self,
        mapping: Mapping[str, QueueRuntimeConfig],
    ) -> tuple[Any | None, tuple[str, ...]]:
        """Build the optional native waiter for an exact candidate mapping."""
        wait_configs = self._activity_wait_configs(mapping=mapping)
        signature = tuple(config.name for config in wait_configs)
        if not wait_configs:
            return None, signature
        try:
            waiter = create_activity_waiter_for_queues(
                [config.queue for config in wait_configs],
                stop_event=self._stop_event,
            )
        except (BrokerError, OSError, RuntimeError, TypeError, ValueError):
            logger.debug(
                "Multi-queue activity waiter unavailable; falling back to polling",
                exc_info=True,
            )
            waiter = None
        return waiter, signature

    @staticmethod
    def _close_candidate_resource_once(resource: Any | None) -> None:
        """Close one rollback-owned resource without masking the cause."""
        if resource is None:
            return
        try:
            resource.close()
        except Exception:  # pragma: no cover - defensive backend cleanup
            logger.debug("Failed to close candidate topology resource", exc_info=True)

    @staticmethod
    def _close_activity_waiter_once(waiter: Any | None) -> None:
        """Close one displaced waiter once at its owner boundary."""
        if waiter is None:
            return
        try:
            waiter.close()
        except Exception:  # pragma: no cover - defensive backend cleanup
            logger.debug("Failed to close multi-queue activity waiter", exc_info=True)

    def _publish_topology_locked(
        self,
        *,
        mapping: dict[str, QueueRuntimeConfig],
        generation: int,
        signature: tuple[str, ...],
        waiter: Any | None,
        active_queues: list[str],
        queue_iterator: itertools.cycle[str],
        force_discovery: bool,
    ) -> None:
        """Publish prebuilt topology state after strategy replacement."""
        self._queues = mapping
        self._queue_generation = generation
        self._multi_activity_waiter = waiter
        self._multi_activity_waiter_generation = generation
        self._multi_activity_waiter_signature = signature
        self._active_queues = active_queues
        self._queue_iterator = queue_iterator
        if force_discovery:
            self._pending_messages_precheck_confirmed = True

    def _apply_topology_mutation_on_owner(self, request: _TopologyMutation) -> None:
        """Build, replace, and publish one mutation on the drive owner.

        Spec: [CC-2.1], [SB-0.4], [QUEUE.8]
        """
        candidate_config: QueueRuntimeConfig | None = None
        candidate_waiter: Any | None = None
        candidate_installed = False
        candidate_rollback_owned = False
        topology_published = False
        strategy_changed = False
        prior_installed_waiter: Any | None = None
        displaced_waiter: Any | None = None

        with self._topology_lock:
            if self._topology_stopping or self._stop_event.is_set():
                raise RuntimeError("watcher topology is stopping")
            generation = self._queue_generation
            prior_mapping = self._queues
            prior_cached_waiter = self._multi_activity_waiter
            strategy_had_native = self._strategy.uses_native_activity()
            prior_installed_waiter = (
                prior_cached_waiter if strategy_had_native else None
            )
            if request.kind == "add" and request.queue_name in prior_mapping:
                raise ValueError(f"Queue '{request.queue_name}' already exists")
            if request.kind == "remove" and request.queue_name not in prior_mapping:
                raise ValueError(f"Queue '{request.queue_name}' not found")

        try:
            candidate_mapping = dict(prior_mapping)
            if request.kind == "add":
                candidate_config = self._open_runtime_config(request)
                candidate_mapping[request.queue_name] = candidate_config
            else:
                del candidate_mapping[request.queue_name]

            candidate_waiter, signature = self._create_candidate_activity_waiter(
                candidate_mapping
            )
            candidate_rollback_owned = (
                candidate_waiter is not None
                and candidate_waiter is not prior_cached_waiter
            )
            active_queues = [
                name for name in self._active_queues if name in candidate_mapping
            ]
            queue_iterator = itertools.cycle(active_queues)

            with self._topology_lock:
                if self._topology_stopping or self._stop_event.is_set():
                    raise RuntimeError("watcher topology is stopping")
                if self._queue_generation != generation:
                    raise _TopologyDriveError(
                        RuntimeError("topology generation changed outside drive owner")
                    )

                self._topology_sigint_critical = True
                try:
                    strategy_changed = candidate_waiter is not prior_installed_waiter
                    displaced_waiter = self._strategy.replace_activity_waiter(
                        candidate_waiter
                    )
                    candidate_installed = (
                        strategy_changed and candidate_waiter is not None
                    )
                    try:
                        self._publish_topology_locked(
                            mapping=candidate_mapping,
                            generation=generation + 1,
                            signature=signature,
                            waiter=candidate_waiter,
                            active_queues=active_queues,
                            queue_iterator=queue_iterator,
                            force_discovery=request.kind == "add",
                        )
                        topology_published = True
                    except BaseException:
                        if strategy_changed:
                            restored_candidate = self._strategy.replace_activity_waiter(
                                displaced_waiter
                            )
                            if candidate_rollback_owned:
                                self._close_activity_waiter_once(restored_candidate)
                            candidate_rollback_owned = False
                            candidate_installed = False
                        raise
                except Exception as exc:
                    raise _TopologyDriveError(exc) from exc

            close_candidates: list[Any] = []
            for waiter in (prior_cached_waiter, displaced_waiter):
                if (
                    waiter is not None
                    and waiter is not candidate_waiter
                    and all(waiter is not seen for seen in close_candidates)
                ):
                    close_candidates.append(waiter)
            for waiter in close_candidates:
                self._close_activity_waiter_once(waiter)
        finally:
            if not topology_published:
                if candidate_waiter is not None and not candidate_installed:
                    if candidate_rollback_owned:
                        self._close_candidate_resource_once(candidate_waiter)
                if candidate_config is not None:
                    self._close_candidate_resource_once(candidate_config.queue)

    def _apply_pending_topology_mutations(self) -> None:
        """Complete queued mutations in FIFO order on the drive owner."""
        if threading.current_thread() is not self._topology_owner_thread:
            return
        if not self._topology_pending.is_set():
            return
        while True:
            with self._topology_lock:
                if not self._topology_mutations:
                    self._topology_pending.clear()
                    return
                request = self._topology_mutations.popleft()
                self._topology_inflight = request
            retry_error: Exception | None = None
            fatal_error: BaseException | None = None
            try:
                self._apply_topology_mutation_on_owner(request)
            except _TopologyDriveError as exc:
                request.error = exc.cause
                retry_error = exc.cause
            except Exception as exc:
                request.error = exc
            except BaseException as exc:
                request.error = RuntimeError(
                    "watcher drive exited during topology mutation"
                )
                with self._topology_lock:
                    while self._topology_mutations:
                        pending = self._topology_mutations.popleft()
                        pending.error = RuntimeError(
                            "watcher drive exited during topology mutation"
                        )
                        pending.done.set()
                fatal_error = exc
            finally:
                with self._topology_lock:
                    if self._topology_inflight is request:
                        self._topology_inflight = None
                    if not self._topology_mutations:
                        self._topology_pending.clear()
                request.done.set()
            self._finish_topology_sigint_critical()
            if fatal_error is not None:
                raise fatal_error
            if retry_error is not None:
                raise retry_error

    def _finish_topology_sigint_critical(self) -> None:
        """Deliver a SIGINT deferred across an atomic topology transaction."""
        self._topology_sigint_critical = False
        if not self._topology_deferred_sigint:
            return
        self._topology_deferred_sigint = False
        self.stop(join=False)
        raise KeyboardInterrupt

    def _sigint_handler(self, signum: int, frame: Any) -> None:
        """Defer SIGINT only while waiter replacement is half-published."""
        if self._topology_sigint_critical:
            self._topology_deferred_sigint = True
            self._stop_event.set()
            self._strategy.notify_activity()
            return
        super()._sigint_handler(signum, frame)

    def _queue_counts_as_wait_activity(self, config: QueueRuntimeConfig) -> bool:
        """Return whether *config* should wake ``wait_for_activity``."""

        del config
        return True

    def _activity_wait_configs(
        self,
        *,
        mapping: Mapping[str, QueueRuntimeConfig] | None = None,
    ) -> list[QueueRuntimeConfig]:
        """Return queue configs that should wake ``wait_for_activity``."""

        return [
            config
            for config in (self._queues if mapping is None else mapping).values()
            if self._queue_counts_as_wait_activity(config)
        ]

    def _activity_wait_queues(self) -> list[Queue]:
        """Return queues watched by the multi-queue activity waiter."""

        return [config.queue for config in self._activity_wait_configs()]

    def _reset_multi_activity_waiter(self) -> None:
        """Close the caller-owned multi-queue waiter if one is active."""
        waiter = self._multi_activity_waiter
        self._multi_activity_waiter = None
        self._multi_activity_waiter_generation = None
        self._multi_activity_waiter_signature = None
        if waiter is None:
            return

        self._strategy.detach_activity_waiter(expected=waiter)
        self._close_activity_waiter_once(waiter)

    def _mark_pending_messages_prechecked(self) -> None:
        """Force the next drain to run broad inactive-queue discovery."""

        self._pending_messages_precheck_confirmed = True

    def _mark_queue_active(self, queue_name: str) -> None:
        """Mark one configured queue as active based on explicit caller evidence."""

        if queue_name not in self._queues:
            raise ValueError(f"Queue '{queue_name}' is not configured")
        if queue_name in self._active_queues:
            return
        self._active_queues.append(queue_name)
        self._queue_iterator = itertools.cycle(self._active_queues)

    def _ensure_multi_activity_waiter(self) -> Any | None:
        """Create or return the SimpleBroker multi-queue activity waiter."""
        wait_configs = self._activity_wait_configs()
        signature = tuple(config.name for config in wait_configs)
        if (
            self._multi_activity_waiter_generation == self._queue_generation
            and self._multi_activity_waiter_signature == signature
        ):
            return self._multi_activity_waiter

        self._reset_multi_activity_waiter()
        self._multi_activity_waiter_generation = self._queue_generation
        self._multi_activity_waiter_signature = signature
        if not wait_configs:
            return None

        try:
            self._multi_activity_waiter = create_activity_waiter_for_queues(
                [config.queue for config in wait_configs],
                stop_event=self._stop_event,
            )
        except (BrokerError, OSError, RuntimeError, TypeError, ValueError):
            logger.debug(
                "Multi-queue activity waiter unavailable; falling back to polling",
                exc_info=True,
            )
            self._multi_activity_waiter = None
        return self._multi_activity_waiter

    def _create_activity_waiter(self, queue: Queue) -> Any | None:
        """Supply Weft's multi-queue waiter to SimpleBroker strategy startup."""
        del queue
        self._apply_pending_topology_mutations()
        return self._ensure_multi_activity_waiter()

    def run_in_thread(self) -> threading.Thread:
        """Reserve and start exactly one background drive thread.

        Spec: [CC-2.1], [QUEUE.8]
        """
        with self._topology_lock:
            if self._topology_stopping or self._stop_event.is_set():
                raise RuntimeError("cannot start a stopped watcher")
            if self._topology_manual_wait_thread is not None:
                raise RuntimeError("cannot start a drive during a manual wait")
            if (
                self._topology_owner_thread is not None
                or self._topology_reserved_thread is not None
            ):
                raise RuntimeError("watcher already has a drive owner")
            thread = threading.Thread(target=self.run_forever, daemon=True)
            self._topology_reserved_thread = thread
            self._thread = weakref.ref(thread)
            try:
                thread.start()
            except BaseException:
                if self._topology_reserved_thread is thread:
                    self._topology_reserved_thread = None
                thread_ref = self._thread
                if thread_ref is not None and thread_ref() is thread:
                    self._thread = None
                raise
            return thread

    def run_forever(self) -> None:
        """Claim the drive owner around SimpleBroker's inherited retry loop.

        Spec: [CC-2.1], [SB-0.4], [QUEUE.8]
        """
        current = threading.current_thread()
        with self._topology_lock:
            reservation = self._topology_reserved_thread
            if (
                self._topology_stopping or self._stop_event.is_set()
            ) and reservation is not current:
                raise RuntimeError("cannot start a stopped watcher")
            if self._topology_owner_thread is not None:
                raise RuntimeError("watcher already has a drive owner")
            if self._topology_manual_wait_thread is not None:
                raise RuntimeError("cannot start a drive during a manual wait")
            if reservation is not None and reservation is not current:
                raise RuntimeError("watcher drive is reserved by another thread")
            self._topology_owner_thread = current
            if reservation is current:
                self._topology_reserved_thread = None
            else:
                self._thread = weakref.ref(current)
        try:
            super().run_forever()
        finally:
            with self._topology_lock:
                self._reset_multi_activity_waiter()
                if self._topology_inflight is not None:
                    request = self._topology_inflight
                    request.error = RuntimeError("watcher drive stopped")
                    request.done.set()
                    self._topology_inflight = None
                if self._topology_owner_thread is current:
                    self._topology_owner_thread = None
                while self._topology_mutations:
                    request = self._topology_mutations.popleft()
                    request.error = RuntimeError("watcher drive stopped")
                    request.done.set()
                self._topology_pending.clear()
                thread_ref = self._thread
                if thread_ref is not None and thread_ref() is current:
                    self._thread = None

    def _wait_for_activity_body(self, timeout: float | None) -> None:
        """Execute the shared broker wait without standalone ownership policy.

        Native waiters are hints only. Callers must still use the ordinary
        pending/drain path after this method returns.

        Spec: [CC-2.1], [SB-0.4]
        """
        if timeout is None or timeout <= 0:
            return

        waiter = self._ensure_multi_activity_waiter()

        if waiter is not None:
            try:
                if waiter.wait(timeout):
                    self._mark_pending_messages_prechecked()
                return
            except (BrokerError, OSError, RuntimeError, TypeError, ValueError):
                logger.debug(
                    "Multi-queue activity waiter failed; falling back to polling",
                    exc_info=True,
                )
                self._reset_multi_activity_waiter()

        if self._has_pending_messages():
            self._mark_pending_messages_prechecked()
            return

        self._stop_event.wait(timeout)

    def wait_for_activity(self, timeout: float | None) -> None:
        """Run one manual wait while excluding drive and topology ownership.

        Spec: [CC-2.1], [SB-0.4], [QUEUE.8]
        """
        if timeout is None or timeout <= 0:
            return
        current = threading.current_thread()
        with self._topology_lock:
            if self._topology_stopping or self._stop_event.is_set():
                return
            if (
                self._topology_owner_thread is not None
                or self._topology_reserved_thread is not None
            ):
                raise RuntimeError("manual wait cannot overlap a background drive")
            if self._topology_manual_wait_thread is not None:
                raise RuntimeError("watcher already has a manual wait owner")
            self._topology_manual_wait_thread = current

        deferred_stop = False
        try:
            self._wait_for_activity_body(timeout)
        finally:
            with self._topology_lock:
                if self._topology_manual_wait_thread is current:
                    if self._topology_stopping or self._stop_event.is_set():
                        self._reset_multi_activity_waiter()
                        deferred_stop = True
                    self._topology_manual_wait_thread = None
            if deferred_stop:
                super().stop(join=False)

    def stop(self, *, join: bool = True, timeout: float = 2.0) -> None:
        """Stop the watcher and close its multi-queue activity waiter.

        Spec: [CC-2.1], [QUEUE.8]
        """
        with self._topology_lock:
            self._topology_stopping = True
            self._stop_event.set()
            self._strategy.notify_activity()
            while self._topology_mutations:
                request = self._topology_mutations.popleft()
                request.error = RuntimeError("watcher topology is stopping")
                request.done.set()
            if self._topology_inflight is None:
                self._topology_pending.clear()
            no_owner = (
                self._topology_owner_thread is None
                and self._topology_reserved_thread is None
                and self._topology_manual_wait_thread is None
            )
            if no_owner:
                self._reset_multi_activity_waiter()
            manual_wait_active = self._topology_manual_wait_thread is not None
        if manual_wait_active:
            return
        super().stop(join=join, timeout=timeout)

    def _has_pending_messages(self) -> bool:
        """Return ``True`` when any configured queue still has pending messages.

        Spec: [CC-2.1]
        """
        self._apply_pending_topology_mutations()
        return any(
            self._queue_counts_as_wait_activity(config)
            and self._queue_has_pending(config.queue)
            for config in self._queues.values()
        )

    def _queue_has_pending(self, queue: Queue) -> bool:
        """Return pending state without querying stopped queue connections."""
        if self._stop_event.is_set():
            return False
        try:
            return queue.has_pending()
        except BrokerError:
            if self._stop_event.is_set():
                return False
            raise

    def _update_active_queues(self) -> None:
        """Refresh the round-robin iterator with queues that still have work pending.

        Spec: [CC-2.1]
        """
        if self._stop_event.is_set():
            self._active_queues = []
            self._queue_iterator = itertools.cycle([])
            return

        still_active: list[str] = [
            name
            for name in self._active_queues
            if self._queue_has_pending(self._queues[name].queue)
        ]

        now = time.monotonic()
        precheck_confirmed = self._pending_messages_precheck_confirmed
        discovery_due = now >= self._next_inactive_probe_at
        should_probe_all = precheck_confirmed or discovery_due
        if should_probe_all:
            for name, config in self._queues.items():
                if (
                    name not in still_active
                    and self._queue_counts_as_wait_activity(config)
                    and self._queue_has_pending(config.queue)
                ):
                    still_active.append(name)
            self._pending_messages_precheck_confirmed = False
            self._next_inactive_probe_at = now + self._inactive_probe_interval

        if set(still_active) != set(self._active_queues):
            self._active_queues = still_active
            self._queue_iterator = (
                itertools.cycle(self._active_queues)
                if self._active_queues
                else itertools.cycle([])
            )

        self._check_counter += 1

    def _fetch_next_message(self, config: QueueRuntimeConfig) -> tuple[str, int] | None:
        """Fetch the next message for a queue based on its configured processing mode.

        Spec: [CC-2.1], [SB-0.3]
        """
        if config.mode is QueueMode.READ:
            return cast(
                tuple[str, int] | None,
                config.queue.read_one(with_timestamps=True),
            )
        if config.mode is QueueMode.PEEK:
            return cast(
                tuple[str, int] | None,
                config.queue.peek_one(with_timestamps=True),
            )
        if config.mode is QueueMode.RESERVE:
            if not config.reserved_queue_name:
                raise RuntimeError(
                    f"Queue '{config.name}' configured for reserve mode missing reserved queue"
                )
            return cast(
                tuple[str, int] | None,
                config.queue.move_one(
                    config.reserved_queue_name,
                    with_timestamps=True,
                ),
            )
        raise ValueError(f"Unsupported queue mode: {config.mode}")

    @staticmethod
    def _make_handler_wrapper(
        handler: Callable[[str, int, QueueMessageContext], None],
        context: QueueMessageContext,
    ) -> Callable[[str, int], None]:
        """Wrap a queue handler so the watcher can invoke it with the expected signature.

        Spec: [CC-2.1]
        """

        def wrapper(message: str, timestamp: int) -> None:
            handler(message, timestamp, context)

        return wrapper

    def _active_queue_priorities(self) -> set[int]:
        """Return priorities for active queues.

        Spec: [CC-2.1], [CC-2.5]
        """
        return {self._queues[name].priority for name in self._active_queues}

    def _process_queue_message(
        self,
        queue_name: str,
        inactive_candidates: set[str],
    ) -> bool:
        """Process one message for one active queue.

        Spec: [CC-2.1], [CC-2.5]
        """
        config = self._queues[queue_name]
        result = self._fetch_next_message(config)
        if not result:
            if config.mode is not QueueMode.PEEK:
                inactive_candidates.add(queue_name)
            return False

        body, timestamp = result
        context = QueueMessageContext(
            queue_name=queue_name,
            queue=config.queue,
            mode=config.mode,
            timestamp=timestamp,
            reserved_queue_name=config.reserved_queue_name,
        )

        handler_wrapper = self._make_handler_wrapper(config.handler, context)
        original_handler = self._handler
        original_error_handler = self._error_handler

        self._handler = handler_wrapper
        self._error_handler = config.error_handler

        try:
            self._dispatch(body, timestamp, config=self._config)
        finally:
            self._handler = original_handler
            self._error_handler = original_error_handler

        if self._stop_event.is_set():
            inactive_candidates.add(queue_name)
        elif not self._queue_has_pending(config.queue):
            inactive_candidates.add(queue_name)

        return True

    def _remove_inactive_queues(self, inactive_candidates: set[str]) -> None:
        """Remove inactive queue names from the active scheduling set."""
        if not inactive_candidates:
            return

        self._active_queues = [
            q for q in self._active_queues if q not in inactive_candidates
        ]
        self._queue_iterator = (
            itertools.cycle(self._active_queues)
            if self._active_queues
            else itertools.cycle([])
        )

    def _drain_round_robin_pass(
        self,
        *,
        queue_names: Sequence[str] | None = None,
    ) -> int:
        """Process one round-robin scheduling pass.

        Spec: [CC-2.1], [CC-2.5]
        """
        messages_processed = 0
        inactive_candidates: set[str] = set()

        if queue_names is None:
            iterations = len(self._active_queues)
            selected_queue_names: list[str] = []
        else:
            iterations = len(queue_names)
            selected_queue_names = list(queue_names)

        for index in range(iterations):
            if self._stop_event.is_set():
                break

            if queue_names is None:
                try:
                    queue_name = next(self._queue_iterator)
                except StopIteration:
                    break
            else:
                queue_name = selected_queue_names[index]
                if queue_name not in self._active_queues:
                    continue

            if self._process_queue_message(queue_name, inactive_candidates):
                messages_processed += 1

            if self._stop_event.is_set():
                break

        self._remove_inactive_queues(inactive_candidates)
        return messages_processed

    def _pending_non_peek_priorities(self) -> set[int]:
        """Return priorities for pending queues that can be drained repeatedly."""
        priorities: set[int] = set()
        for queue_name in self._active_queues:
            config = self._queues[queue_name]
            if config.mode is QueueMode.PEEK:
                continue
            if self._queue_has_pending(config.queue):
                priorities.add(config.priority)
        return priorities

    def _drain_priority_queues(self) -> int:
        """Drain the highest-priority non-PEEK queues before one normal pass.

        Spec: [CC-2.1], [CC-2.5]
        """
        messages_processed = 0
        priorities = self._pending_non_peek_priorities()
        if priorities:
            priority = min(priorities)
            while not self._stop_event.is_set():
                queue_names = [
                    name
                    for name in self._active_queues
                    if self._queues[name].priority == priority
                    and self._queues[name].mode is not QueueMode.PEEK
                ]
                if not queue_names:
                    break

                processed = self._drain_round_robin_pass(queue_names=queue_names)
                messages_processed += processed
                if processed == 0:
                    break

                self._update_active_queues()
                if priority not in self._pending_non_peek_priorities():
                    break

        if self._stop_event.is_set():
            return messages_processed

        self._update_active_queues()
        lower_priority_queue_names = [
            name
            for name in self._active_queues
            if self._queues[name].mode is not QueueMode.PEEK
        ]
        if lower_priority_queue_names:
            messages_processed += self._drain_round_robin_pass(
                queue_names=lower_priority_queue_names
            )

        if self._active_queues and not self._stop_event.is_set():
            peek_queue_names = [
                name
                for name in self._active_queues
                if self._queues[name].mode is QueueMode.PEEK
            ]
            if peek_queue_names:
                messages_processed += self._drain_round_robin_pass(
                    queue_names=peek_queue_names
                )

        return messages_processed

    def _drain_queue(self) -> None:
        """Process one scheduling pass across all active queues.

        Spec: [CC-2.1], [CC-2.5]
        """
        self._apply_pending_topology_mutations()
        self._update_active_queues()
        if not self._active_queues:
            return

        if len(self._active_queue_priorities()) <= 1:
            messages_processed = self._drain_round_robin_pass()
        else:
            messages_processed = self._drain_priority_queues()

        if messages_processed > 0:
            self._strategy.notify_activity()
