"""Queue-native wait helpers for CLI lifecycle and result surfaces.

Spec references:
- docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/10-CLI_Interface.md [CLI-1.1.1], [CLI-1.2]
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping, Sequence
from typing import Any

from simplebroker import Queue, QueueWatcher, create_activity_waiter_for_queues
from simplebroker.ext import BrokerError
from weft._constants import (
    QUEUE_CHANGE_MONITOR_JOIN_TIMEOUT_SECONDS,
    QUEUE_CHANGE_MONITOR_WAITER_TIMEOUT_SECONDS,
    resolve_runtime_config,
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
        self._state_lock = threading.Lock()
        self._closed = False
        self._failure: Exception | None = None
        self._watchers: list[QueueWatcher] = []
        self._watcher_threads: list[threading.Thread] = []
        self._multi_waiter: Any | None = None
        self._monitor_thread: threading.Thread | None = None
        self._config = resolve_runtime_config(config)

        try:
            if self._start_multi_queue_waiter(list(queues)):
                return

            for queue in queues:
                watcher = QueueWatcher(
                    queue,
                    self._handle_queue_activity,
                    stop_event=self._stop_event,
                    peek=True,
                    after_timestamp=queue.last_ts,
                    config=self._config,
                )
                self._watchers.append(watcher)
                thread = threading.Thread(
                    target=self._run_watcher,
                    args=(watcher,),
                    daemon=True,
                )
                self._watcher_threads.append(thread)
                thread.start()
        except BaseException:
            self.close()
            raise

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

        try:
            while not self._stop_event.is_set():
                if waiter.wait(QUEUE_CHANGE_MONITOR_WAITER_TIMEOUT_SECONDS):
                    self.wake()
        except Exception as failure:  # Report ordinary thread failure to observer.
            logger.debug("Multi-queue activity monitor failed", exc_info=True)
            self._record_failure(failure)
        finally:
            # Fatal exceptions stay at the thread boundary. Its death must still
            # wake the observer; close suppresses this expected-exit diagnostic.
            self._record_failure(
                RuntimeError("Multi-queue activity monitor stopped unexpectedly")
            )
            with self._state_lock:
                owns_waiter = self._multi_waiter is waiter
                if owns_waiter:
                    self._multi_waiter = None
            if owns_waiter:
                self._close_waiter(waiter)

    def _run_watcher(self, watcher: QueueWatcher) -> None:
        """Keep failure and thread custody around the broker's public run owner."""
        try:
            watcher.run_forever()
        except Exception as failure:  # Report ordinary thread failure to observer.
            logger.debug("Queue watcher observation failed", exc_info=True)
            self._record_failure(failure)
        finally:
            # Uncaught fatal exceptions retain thread-local semantics while the
            # observer learns that its wake source has died.
            self._record_failure(RuntimeError("Queue watcher stopped unexpectedly"))

    def _record_failure(self, failure: Exception) -> None:
        with self._state_lock:
            if self._closed:
                return
            if self._failure is None:
                self._failure = failure
        self.wake()

    def _raise_failure(self) -> None:
        with self._state_lock:
            failure = self._failure
        if failure is not None:
            raise BrokerError("Queue activity observation failed") from failure

    def wake(self) -> None:
        """Wake observation without broker effects; safe across threads and close.

        Wake sources record durable contents, failure or cancellation first;
        callers recheck that state after the coalesced Event wake [MF-5].
        """
        self._activity_event.set()

    def _handle_queue_activity(self, _message: str, _timestamp: int) -> None:
        self.wake()

    def wait(self, timeout: float | None) -> bool:
        """Wait for activity or timeout; close wakes and returns True.

        Raises BrokerError from a latched observer failure, on every wait.
        """
        self._raise_failure()
        if self._stop_event.is_set():
            return True
        triggered = self._activity_event.wait(timeout=timeout)
        if triggered:
            self._activity_event.clear()
        self._raise_failure()
        return triggered

    @staticmethod
    def _close_waiter(waiter: Any) -> None:
        try:
            waiter.close()
        except (BrokerError, OSError, RuntimeError):
            logger.debug("Failed to close multi-queue activity monitor", exc_info=True)

    def close(self) -> None:
        """Stop watcher threads and release broker-side wait resources."""
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._stop_event.set()
            waiter = self._multi_waiter
            self._multi_waiter = None
            monitor_thread = self._monitor_thread
            self._monitor_thread = None
            watchers = self._watchers
            self._watchers = []
            watcher_threads = self._watcher_threads
            self._watcher_threads = []
        self.wake()
        if waiter is not None:
            self._close_waiter(waiter)
        if (
            monitor_thread is not None
            and monitor_thread.is_alive()
            and monitor_thread is not threading.current_thread()
        ):
            monitor_thread.join(QUEUE_CHANGE_MONITOR_JOIN_TIMEOUT_SECONDS)
        for watcher in watchers:
            try:
                watcher.stop(join=False)
            except Exception:  # pragma: no cover - watcher teardown best effort
                logger.debug("Failed to stop queue watcher cleanly", exc_info=True)
        for thread in watcher_threads:
            if thread.is_alive() and thread is not threading.current_thread():
                thread.join(QUEUE_CHANGE_MONITOR_JOIN_TIMEOUT_SECONDS)
