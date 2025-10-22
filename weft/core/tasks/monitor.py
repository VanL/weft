from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from weft._constants import (
    CONTROL_STOP,
)
from weft.core.taskspec import TaskSpec

from .base import BaseTask
from .multiqueue_watcher import QueueMessageContext


class Monitor(BaseTask):
    """Forward messages to a downstream queue while observing them (Spec: [CC-2.3], [MF-5])."""

    def __init__(
        self,
        db: Path | str | Any,
        taskspec: TaskSpec,
        observer: Callable[[str, int], None],
        *,
        downstream_queue: str | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        self._observer = observer
        self._downstream_queue = downstream_queue
        super().__init__(db=db, taskspec=taskspec, stop_event=stop_event)

    def _build_queue_configs(self) -> dict[str, dict[str, Any]]:
        """Reserve messages, forwarding them to the downstream queue while observing.

        Spec: [CC-2.3], [MF-2], [MF-5]
        """
        target = self._downstream_queue or self._queue_names["outbox"]
        self._downstream_queue = target
        return {
            self._queue_names["inbox"]: self._reserve_queue_config(
                self._handle_work_message,
                reserved_queue=target,
            ),
            self._queue_names["ctrl_in"]: self._peek_queue_config(
                self._handle_control_message
            ),
        }

    def _handle_work_message(
        self, message: str, timestamp: int, context: QueueMessageContext
    ) -> None:
        """Forward the observed payload while preserving the move performed by reserve mode.

        Spec: [CC-2.3], [MF-2], [MF-5]
        """
        self._observer(message, timestamp)
        # message already moved to downstream queue by the watcher

    def _handle_control_command(
        self, command: str, context: QueueMessageContext
    ) -> bool:
        """Allow STOP to cancel the monitor without reserved-queue manipulation.

        Spec: [CC-2.4], [MF-3]
        """
        if command == CONTROL_STOP:
            self.should_stop = True
            self.taskspec.mark_cancelled(reason="STOP command received")
            self._report_state_change(
                event="control_stop", message_id=context.timestamp
            )
            self._update_process_title("cancelled")
            if self._stop_event:
                self._stop_event.set()
            return True
        return False

    def _cleanup_reserved_if_needed(self) -> None:
        """Monitor never allocates its own reserved queue so cleanup is unnecessary.

        Spec: [CC-2.3]
        """
        return


# ~
