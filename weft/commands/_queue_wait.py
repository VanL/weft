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

from simplebroker import Queue
from simplebroker.watcher import QueueWatcher
from weft._constants import load_config

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
        self._config = dict(config) if config is not None else load_config()

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
        for watcher in self._watchers:
            try:
                watcher.stop(join=True)
            except Exception:
                logger.debug("Failed to stop queue watcher cleanly", exc_info=True)
        self._watchers.clear()
