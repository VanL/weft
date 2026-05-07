"""Lifecycle monitor task primitive.

This module provides the task-shaped non-consuming scanner used by the
foreground lifecycle monitor command. Classification and archive writing stay
in the command layer.

Spec references:
- docs/specifications/01-Core_Components.md [CC-2.1], [CC-2.3]
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from weft._constants import (
    QUEUE_CTRL_IN_SUFFIX,
    QUEUE_CTRL_OUT_SUFFIX,
    QUEUE_OUTBOX_SUFFIX,
    WEFT_GLOBAL_LOG_QUEUE,
)
from weft.core.taskspec import IOSection, SpecSection, StateSection, TaskSpec
from weft.helpers import iter_queue_entries

from .base import BaseTask
from .multiqueue_watcher import QueueMessageContext

LifecycleMonitorCallback = Callable[[str, str, int], None]


def make_lifecycle_monitor_taskspec(tid: str | None = None) -> TaskSpec:
    """Create the private synthetic TaskSpec for a foreground monitor run."""

    monitor_tid = tid or str(time.time_ns())
    prefix = f"T{monitor_tid}"
    return TaskSpec(
        tid=monitor_tid,
        name="lifecycle-monitor",
        spec=SpecSection(
            type="function",
            function_target="weft.core.tasks.lifecycle_monitor:noop_monitor_target",
            enable_process_title=False,
        ),
        io=IOSection(
            inputs={"inbox": WEFT_GLOBAL_LOG_QUEUE},
            outputs={"outbox": f"{prefix}.{QUEUE_OUTBOX_SUFFIX}"},
            control={
                "ctrl_in": f"{prefix}.{QUEUE_CTRL_IN_SUFFIX}",
                "ctrl_out": f"{prefix}.{QUEUE_CTRL_OUT_SUFFIX}",
            },
        ),
        state=StateSection(),
        metadata={"internal": True, "weft_runtime": "lifecycle_monitor"},
    )


def noop_monitor_target() -> None:
    """No-op target used only to satisfy the private synthetic TaskSpec."""


class LifecycleMonitorTask(BaseTask):
    """Task-shaped non-consuming lifecycle log scanner.

    The command layer owns summary construction and archive/checkpoint writes.
    This class only provides queue wiring and a callback-oriented peek surface.
    """

    def __init__(
        self,
        db: Path | str | Any,
        taskspec: TaskSpec,
        observer: LifecycleMonitorCallback,
        *,
        stop_event: threading.Event | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        self._lifecycle_observer = observer
        super().__init__(db=db, taskspec=taskspec, stop_event=stop_event, config=config)

    def _build_queue_configs(self) -> dict[str, dict[str, Any]]:
        """Configure task log and control queues in peek mode."""

        return {
            WEFT_GLOBAL_LOG_QUEUE: self._peek_queue_config(self._handle_work_message),
            self._queue_names["ctrl_in"]: self._peek_queue_config(
                self._handle_control_message
            ),
        }

    def scan_once(
        self,
        *,
        since_timestamp: int | None = None,
        limit: int | None = None,
    ) -> int:
        """Invoke the callback for task-log entries without consuming them.

        Spec: [CC-2.3], [MF-5]
        """

        queue = self._queue(WEFT_GLOBAL_LOG_QUEUE)
        count = 0
        for message, timestamp in iter_queue_entries(
            queue,
            since_timestamp=since_timestamp,
        ):
            self._lifecycle_observer(WEFT_GLOBAL_LOG_QUEUE, message, timestamp)
            count += 1
            if limit is not None and count >= limit:
                break
        return count

    def _handle_work_message(
        self,
        message: str,
        timestamp: int,
        context: QueueMessageContext,
    ) -> None:
        self._lifecycle_observer(context.queue_name, message, timestamp)

    def _cleanup_reserved_if_needed(self) -> None:
        """Lifecycle monitors never create reserved messages."""

        return
