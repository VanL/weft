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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from simplebroker import BrokerTarget, Queue, create_activity_waiter_for_queues
from simplebroker.ext import BrokerError
from simplebroker.watcher import (
    BaseWatcher,
    PollingStrategy,
    _StopLoop,
    default_error_handler,
)
from weft._constants import QUEUE_PRIORITY_NORMAL, load_config
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
            check_interval: How often inactive queues are probed for new work
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
        self._default_error_handler = default_error_handler_fn
        self._handler: Callable[[str, int], None] | None = None
        self._error_handler: Callable[[Exception, str, int], bool | None] | None = None

        # Establish primary queue and shared broker target
        first_queue_name = next(iter(queue_configs.keys()))
        shared_target = _resolve_db_target(
            db,
            resolve_context_broker_target(Path.cwd(), config=self._config),
        )
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

            queue_obj = (
                initial_queue
                if queue_name == first_queue_name
                else Queue(
                    queue_name,
                    db_path=self._db_path,
                    persistent=persistent,
                    config=self._config,
                )
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
        self._pending_messages_precheck_confirmed = False

        logger.debug(
            "MultiQueueWatcher initialized with queues: %s",
            list(self._queues.keys()),
        )

    # ------------------------------------------------------------------ #
    # Public API                                                         #
    # ------------------------------------------------------------------ #

    def list_queues(self) -> list[str]:
        """Return all configured queue names.

        Spec: [CC-2.1]
        """
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

        Spec: [CC-2.1], [SB-0.4]
        """
        if queue_name in self._queues:
            raise ValueError(f"Queue '{queue_name}' already exists")
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

        queue_obj = Queue(
            queue_name,
            db_path=self._db_path,
            persistent=self._persistent,
            config=self._config,
        )

        _detach_queue_stop_event(queue_obj)

        self._queues[queue_name] = QueueRuntimeConfig(
            name=queue_name,
            queue=queue_obj,
            handler=handler,
            mode=mode,
            error_handler=error_handler or self._default_error_handler,
            reserved_queue_name=reserved_queue,
            priority=priority,
        )
        self._queue_generation += 1
        self._reset_multi_activity_waiter()

    def remove_queue(self, queue_name: str) -> None:
        """Remove a queue from the watcher.

        Spec: [CC-2.1]
        """
        if queue_name not in self._queues:
            raise ValueError(f"Queue '{queue_name}' not found")
        del self._queues[queue_name]
        if queue_name in self._active_queues:
            self._active_queues = [q for q in self._active_queues if q != queue_name]
            self._queue_iterator = (
                itertools.cycle(self._active_queues)
                if self._active_queues
                else itertools.cycle([])
            )
        self._queue_generation += 1
        self._reset_multi_activity_waiter()

    def get_queue(self, queue_name: str) -> Queue | None:
        """Return the managed Queue instance for *queue_name* if present.

        Spec: [SB-0.1]
        """
        config = self._queues.get(queue_name)
        return config.queue if config else None

    # ------------------------------------------------------------------ #
    # Internal helpers                                                   #
    # ------------------------------------------------------------------ #
    def _activity_wait_queues(self) -> list[Queue]:
        """Return queues watched by the multi-queue activity waiter."""
        return [config.queue for config in self._queues.values()]

    def _reset_multi_activity_waiter(self) -> None:
        """Close the caller-owned multi-queue waiter if one is active."""
        waiter = self._multi_activity_waiter
        self._multi_activity_waiter = None
        self._multi_activity_waiter_generation = None
        if waiter is None:
            return

        if getattr(self._strategy, "_activity_waiter", None) is waiter:
            self._strategy._activity_waiter = None

        try:
            cast(Any, waiter).close()
        except (BrokerError, OSError, RuntimeError):
            logger.debug("Failed to close multi-queue activity waiter", exc_info=True)

    def _mark_pending_messages_prechecked(self) -> None:
        """Force the next drain to probe every configured queue."""

        self._pending_messages_precheck_confirmed = True

    def _ensure_multi_activity_waiter(self) -> Any | None:
        """Create or return the SimpleBroker multi-queue activity waiter."""
        if self._multi_activity_waiter_generation == self._queue_generation:
            return self._multi_activity_waiter

        self._reset_multi_activity_waiter()
        try:
            self._multi_activity_waiter = create_activity_waiter_for_queues(
                self._activity_wait_queues(),
                stop_event=self._stop_event,
            )
        except (BrokerError, OSError, RuntimeError, TypeError, ValueError):
            logger.debug(
                "Multi-queue activity waiter unavailable; falling back to polling",
                exc_info=True,
            )
            self._multi_activity_waiter = None
        self._multi_activity_waiter_generation = self._queue_generation
        return self._multi_activity_waiter

    def _start_strategy_for_configured_queues(self) -> None:
        """Start the polling strategy with a multi-queue activity waiter."""
        queue = self._get_queue_for_data_version()

        def data_version_getter(q: Queue = queue) -> int | None:
            return q.get_data_version()

        try:
            queue.refresh_last_ts()
        except (BrokerError, OSError, RuntimeError):
            logger.debug("Initial last_ts refresh failed", exc_info=True)

        def on_data_version_change(q: Queue = queue) -> None:
            q.refresh_last_ts()

        self._strategy.start(
            data_version_getter,
            on_data_version_change=on_data_version_change,
            activity_waiter=self._ensure_multi_activity_waiter(),
        )

    def wait_for_activity(self, timeout: float | None) -> None:
        """Wait for possible queue activity without consuming messages.

        Native waiters are hints only. Callers must still use the ordinary
        pending/drain path after this method returns.

        Spec: [CC-2.1], [SB-0.4]
        """
        if timeout is None or timeout <= 0:
            if self._has_pending_messages():
                self._mark_pending_messages_prechecked()
                return
            return

        waiter = self._ensure_multi_activity_waiter()
        if waiter is not None:
            if self._has_pending_messages():
                self._mark_pending_messages_prechecked()
                return
            try:
                waiter.wait(timeout)
                return
            except (BrokerError, OSError, RuntimeError, TypeError, ValueError):
                logger.debug(
                    "Multi-queue activity waiter failed; falling back to polling",
                    exc_info=True,
                )
                self._reset_multi_activity_waiter()

        self._stop_event.wait(timeout)

    def stop(self, *, join: bool = True, timeout: float = 2.0) -> None:
        """Stop the watcher and close its multi-queue activity waiter."""
        self._reset_multi_activity_waiter()
        super().stop(join=join, timeout=timeout)

    def _run_with_retries(self, max_retries: int = 3) -> None:
        """Run with SimpleBroker retry semantics and a multi-queue waiter."""
        retry_count = 0
        start_time = time.monotonic()

        while retry_count < max_retries:
            self._check_retry_timeout(start_time, retry_count)

            try:
                # SimpleBroker 3.3.2 has no protected waiter factory hook, so
                # this is the narrow override that swaps in the multi-queue API.
                if hasattr(self._strategy, "start"):
                    self._start_strategy_for_configured_queues()

                self._in_initial_drain = True
                try:
                    self._drain_queue()
                finally:
                    self._in_initial_drain = False

                self._process_messages()
                break
            except _StopLoop:
                break
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                retry_count += 1
                if not self._handle_retry(exc, retry_count, max_retries):
                    break

    def _has_pending_messages(self) -> bool:
        """Return ``True`` when any configured queue still has pending messages.

        Spec: [CC-2.1]
        """
        return any(
            self._queue_has_pending(config.queue) for config in self._queues.values()
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

        precheck_confirmed = self._pending_messages_precheck_confirmed
        should_probe_all = precheck_confirmed or (
            self._check_counter % self._check_interval == 0
        )
        if should_probe_all:
            for name, config in self._queues.items():
                if name not in still_active and self._queue_has_pending(config.queue):
                    still_active.append(name)
            self._pending_messages_precheck_confirmed = False

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
        self._update_active_queues()
        if not self._active_queues:
            return

        if len(self._active_queue_priorities()) <= 1:
            messages_processed = self._drain_round_robin_pass()
        else:
            messages_processed = self._drain_priority_queues()

        if messages_processed > 0:
            self._strategy.notify_activity()
