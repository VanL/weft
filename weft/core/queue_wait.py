"""Queue-native wait helpers for CLI lifecycle and result surfaces.

Spec references:
- docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]
- docs/specifications/10-CLI_Interface.md [CLI-1.1.1], [CLI-1.2]
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping, Sequence
from typing import Any

from simplebroker import Queue, create_activity_waiter_for_queues
from simplebroker.ext import BrokerError
from simplebroker.watcher import QueueWatcher
from weft._constants import (
    QUEUE_CHANGE_MONITOR_JOIN_TIMEOUT_SECONDS,
    QUEUE_CHANGE_MONITOR_WAITER_TIMEOUT_SECONDS,
    load_config,
)

logger = logging.getLogger(__name__)


class QueueChangeMonitor:
    """Wait for new messages on one or more queues using SimpleBroker watchers."""

    def __init__(
        self,
        queues: Sequence[Queue],
        *,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        self._activity_event = threading.Event()
        self._stop_event = threading.Event()
        self._watchers: list[QueueWatcher] = []
        self._multi_waiter: Any | None = None
        self._monitor_thread: threading.Thread | None = None
        self._config = dict(config) if config is not None else load_config()

        if self._start_multi_queue_waiter(list(queues)):
            return

        for queue in queues:
            watcher = QueueWatcher(
                queue,
                self._handle_queue_activity,
                stop_event=self._stop_event,
                peek=True,
                since_timestamp=queue.last_ts,
                config=self._config,
            )
            watcher.run_in_thread()
            self._watchers.append(watcher)

    def _start_multi_queue_waiter(self, queues: list[Queue]) -> bool:
        if not queues:
            return False
        try:
            waiter = create_activity_waiter_for_queues(
                queues,
                stop_event=self._stop_event,
            )
        except (BrokerError, OSError, RuntimeError, TypeError, ValueError):
            logger.debug(
                "Multi-queue activity monitor unavailable; falling back to watchers",
                exc_info=True,
            )
            return False
        if waiter is None:
            return False

        self._multi_waiter = waiter
        self._monitor_thread = threading.Thread(
            target=self._run_multi_waiter,
            daemon=True,
        )
        self._monitor_thread.start()
        return True

    def _run_multi_waiter(self) -> None:
        waiter = self._multi_waiter
        if waiter is None:
            return

        while not self._stop_event.is_set():
            try:
                if waiter.wait(QUEUE_CHANGE_MONITOR_WAITER_TIMEOUT_SECONDS):
                    self._activity_event.set()
            except (BrokerError, OSError, RuntimeError, TypeError, ValueError):
                logger.debug("Multi-queue activity monitor failed", exc_info=True)
                self._activity_event.set()
                try:
                    waiter.close()
                except (BrokerError, OSError, RuntimeError):
                    logger.debug(
                        "Failed to close failed multi-queue activity monitor",
                        exc_info=True,
                    )
                if self._multi_waiter is waiter:
                    self._multi_waiter = None
                break

    def _handle_queue_activity(self, _message: str, _timestamp: int) -> None:
        self._activity_event.set()

    def wait(self, timeout: float | None) -> bool:
        """Block until any watched queue changes or the timeout expires."""
        triggered = self._activity_event.wait(timeout=timeout)
        if triggered:
            self._activity_event.clear()
        return triggered

    def close(self) -> None:
        """Stop watcher threads and release broker-side wait resources."""
        self._stop_event.set()
        waiter = self._multi_waiter
        self._multi_waiter = None
        if waiter is not None:
            try:
                waiter.close()
            except (BrokerError, OSError, RuntimeError):
                logger.debug(
                    "Failed to close multi-queue activity monitor", exc_info=True
                )
        if (
            self._monitor_thread is not None
            and self._monitor_thread.is_alive()
            and self._monitor_thread is not threading.current_thread()
        ):
            self._monitor_thread.join(QUEUE_CHANGE_MONITOR_JOIN_TIMEOUT_SECONDS)
        self._monitor_thread = None
        for watcher in self._watchers:
            try:
                watcher.stop(join=True)
            except Exception:  # pragma: no cover - watcher teardown best effort
                logger.debug("Failed to stop queue watcher cleanly", exc_info=True)
        self._watchers.clear()
