from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from weft.core.taskspec import TaskSpec

from .base import BaseTask
from .multiqueue_watcher import QueueMessageContext


class Observer(BaseTask):
    """Task that peeks at messages without consuming them (Spec: [CC-2.3], [MF-5])."""

    def __init__(
        self,
        db: Path | str | Any,
        taskspec: TaskSpec,
        observer: Callable[[str, int], None],
        *,
        stop_event: threading.Event | None = None,
    ) -> None:
        self._observer = observer
        super().__init__(db=db, taskspec=taskspec, stop_event=stop_event)

    def _build_queue_configs(self) -> dict[str, dict[str, Any]]:
        """Peek at inbox and control queues without consuming messages.

        Spec: [CC-2.3], [MF-3]
        """
        return {
            self._queue_names["inbox"]: self._peek_queue_config(
                self._handle_work_message
            ),
            self._queue_names["ctrl_in"]: self._peek_queue_config(
                self._handle_control_message
            ),
        }

    def _handle_work_message(
        self, message: str, timestamp: int, context: QueueMessageContext
    ) -> None:
        """Surface messages to the caller without acknowledging them.

        Spec: [CC-2.3], [MF-5]
        """
        self._observer(message, timestamp)

    def _cleanup_reserved_if_needed(self) -> None:
        """Observers never create reserved messages, so no cleanup is required.

        Spec: [CC-2.3]
        """
        return


class SamplingObserver(Observer):
    """Observer that samples messages based on elapsed time (Spec: [CC-2.3], [MF-5])."""

    def __init__(
        self,
        db: Path | str | Any,
        taskspec: TaskSpec,
        observer: Callable[[str, int], None],
        *,
        interval_seconds: float,
        stop_event: threading.Event | None = None,
    ) -> None:
        super().__init__(
            db=db, taskspec=taskspec, observer=observer, stop_event=stop_event
        )
        self._sampling_interval = max(0.0, interval_seconds)
        self._last_sample_time: float | None = None

    def _handle_work_message(
        self,
        message: str,
        timestamp: int,
        context: QueueMessageContext,
    ) -> None:
        """Forward messages when the configured interval has elapsed since the last sample.

        Spec: [CC-2.3], [MF-5]
        """
        now = time.monotonic()
        if (
            self._last_sample_time is None
            or (now - self._last_sample_time) >= self._sampling_interval
        ):
            self._observer(message, timestamp)
            self._last_sample_time = now


# ~
