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
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from weft.core.taskspec import TaskSpec

from .base import BaseTask


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

        return self._start_service_lane_with_submitter(
            lane,
            work,
            func,
            submitter=self._submit_worker_lane,
        )

    def _start_service_call_lane(
        self,
        lane: str,
        work: Any,
        func: Callable[[], Any],
    ) -> threading.Thread:
        """Start broker-free single-flight work on a service lane."""

        return self._start_service_lane_with_submitter(
            lane,
            work,
            func,
            submitter=self._submit_worker_call,
        )

    def _start_service_lane_with_submitter(
        self,
        lane: str,
        work: Any,
        func: Callable[[], Any],
        *,
        submitter: Callable[[str, Callable[[], Any]], threading.Thread],
    ) -> threading.Thread:
        """Record single-flight service work and submit it to a worker lane."""

        if lane in self._service_lane_work_items:
            raise RuntimeError(f"Service lane {lane!r} already has work in flight")
        self._service_lane_work_items[lane] = work
        try:
            return submitter(lane, func)
        except Exception:
            self._service_lane_work_items.pop(lane, None)
            raise

    def _pop_service_lane_work(self, lane: str) -> Any | None:
        """Clear and return the current work item for a service lane."""

        return self._service_lane_work_items.pop(lane, None)

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
