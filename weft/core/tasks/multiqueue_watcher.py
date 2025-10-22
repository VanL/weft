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
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, cast

from simplebroker import Queue
from simplebroker.db import BrokerDB
from simplebroker.watcher import (
    BaseWatcher,
    PollingStrategy,
    default_error_handler,
)
from weft._constants import load_config

logger = logging.getLogger(__name__)


class QueueMode(str, Enum):
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


def _resolve_db_path(db: BrokerDB | str | Path | None, fallback: str) -> str:
    """Derive a database path shared across Queue instances (Spec: [SB-0.1], [SB-0.4])."""
    if isinstance(db, BrokerDB):
        return str(db.db_path)
    if isinstance(db, (str, Path)):
        return str(db)
    return fallback


class MultiQueueWatcher(BaseWatcher):
    """Monitor multiple queues with per-queue processing semantics (Spec: [CC-2.1], [SB-0.4])."""

    def __init__(
        self,
        queue_configs: Mapping[str, Mapping[str, object]],
        *,
        db: BrokerDB | str | Path | None = None,
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
            db: BrokerDB instance or filesystem path to the SQLite database
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

        # Establish primary queue and shared database path
        first_queue_name = next(iter(queue_configs.keys()))
        initial_queue = Queue(
            first_queue_name,
            db_path=_resolve_db_path(db, "broker.db"),
            persistent=persistent,
            config=self._config,
        )

        super().__init__(
            initial_queue,
            stop_event=stop_event,
            polling_strategy=polling_strategy,
            config=self._config,
        )

        db_path_obj = getattr(initial_queue, "_db_path", None)
        if db_path_obj is not None:
            db_path = str(db_path_obj)
        else:
            db_path = _resolve_db_path(db, "broker.db")
        self._db_path = db_path

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
                    db_path=db_path,
                    persistent=persistent,
                    config=self._config,
                )
            )

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

            runtime_config = QueueRuntimeConfig(
                name=queue_name,
                queue=queue_obj,
                handler=handler,
                mode=mode,
                error_handler=error_handler or default_error_handler_fn,
                reserved_queue_name=reserved_name,
            )
            self._queues[queue_name] = runtime_config

        # Processing state
        self._active_queues: list[str] = []
        self._queue_iterator: itertools.cycle[str] = itertools.cycle([])
        self._check_counter = 0

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

        queue_obj = Queue(
            queue_name,
            db_path=self._db_path,
            persistent=self._persistent,
            config=self._config,
        )

        self._queues[queue_name] = QueueRuntimeConfig(
            name=queue_name,
            queue=queue_obj,
            handler=handler,
            mode=mode,
            error_handler=error_handler or self._default_error_handler,
            reserved_queue_name=reserved_queue,
        )

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

    def get_queue(self, queue_name: str) -> Queue | None:
        """Return the managed Queue instance for *queue_name* if present.

        Spec: [SB-0.1]
        """
        config = self._queues.get(queue_name)
        return config.queue if config else None

    # ------------------------------------------------------------------ #
    # Internal helpers                                                   #
    # ------------------------------------------------------------------ #
    def _has_pending_messages(self) -> bool:
        """Return ``True`` when any configured queue still has pending messages.

        Spec: [CC-2.1]
        """
        return any(config.queue.has_pending() for config in self._queues.values())

    def _update_active_queues(self) -> None:
        """Refresh the round-robin iterator with queues that still have work pending.

        Spec: [CC-2.1]
        """
        still_active: list[str] = [
            name
            for name in self._active_queues
            if self._queues[name].queue.has_pending()
        ]

        if self._check_counter % self._check_interval == 0:
            for name, config in self._queues.items():
                if name not in still_active and config.queue.has_pending():
                    still_active.append(name)

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

    def _drain_queue(self) -> None:
        """Process one scheduling pass across all active queues.

        Spec: [CC-2.1], [CC-2.5]
        """
        self._update_active_queues()
        if not self._active_queues:
            return

        messages_processed = 0
        inactive_candidates: set[str] = set()

        for _ in range(len(self._active_queues)):
            try:
                queue_name = next(self._queue_iterator)
            except StopIteration:
                break

            config = self._queues[queue_name]
            result = self._fetch_next_message(config)
            if not result:
                if config.mode is not QueueMode.PEEK:
                    inactive_candidates.add(queue_name)
                continue

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
                messages_processed += 1
            finally:
                self._handler = original_handler
                self._error_handler = original_error_handler

            if not config.queue.has_pending():
                inactive_candidates.add(queue_name)

        if messages_processed > 0:
            self._strategy.notify_activity()

        if inactive_candidates:
            self._active_queues = [
                q for q in self._active_queues if q not in inactive_candidates
            ]
            self._queue_iterator = (
                itertools.cycle(self._active_queues)
                if self._active_queues
                else itertools.cycle([])
            )
