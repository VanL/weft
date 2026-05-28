"""Task handle for the public Python client.

Spec references:
- docs/specifications/09-Implementation_Plan.md [IP-1]
- docs/specifications/05-Message_Flow_and_State.md [MF-3], [MF-5]
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from weft._constants import TASK_PING_TIMEOUT_SECONDS
from weft.commands import events
from weft.commands import result as result_cmd
from weft.commands import tasks as task_ops
from weft.commands.types import (
    TaskEvent,
    TaskResult,
    TaskSnapshot,
    TaskTerminalSnapshot,
)
from weft.context import WeftContext

from ._types import ClientContextHandle


@dataclass(frozen=True, slots=True)
class Task:
    """Lazy handle for one Weft task id."""

    client: ClientContextHandle
    tid: str

    def snapshot(self) -> TaskSnapshot | None:
        return task_ops.task_snapshot(self.tid, context=self._context)

    def terminal_snapshot(self, timeout: float = 0.0) -> TaskTerminalSnapshot:
        return task_ops.task_terminal_snapshot(
            self.tid,
            timeout=timeout,
            context=self._context,
        )

    def ping(self, *, timeout: float = TASK_PING_TIMEOUT_SECONDS) -> dict[str, Any]:
        return task_ops.task_ping(self.tid, timeout=timeout, context=self._context)

    def result(self, timeout: float | None = None) -> TaskResult:
        return result_cmd.await_task_result(self._context, self.tid, timeout=timeout)

    def events(
        self,
        *,
        follow: bool = False,
        timeout: float | None = None,
    ) -> Iterator[TaskEvent]:
        if follow:
            yield from events.iter_task_events(
                self._context,
                self.tid,
                follow=True,
                timeout=timeout,
            )
            return
        yield from events.iter_task_events(
            self._context,
            self.tid,
            follow=False,
            timeout=timeout,
        )

    def realtime_events(
        self,
        *,
        follow: bool = True,
        cancel_event: Any | None = None,
        timeout: float | None = None,
    ) -> Iterator[TaskEvent]:
        yield from events.iter_task_realtime_events(
            self._context,
            self.tid,
            follow=follow,
            cancel_event=cancel_event,
            timeout=timeout,
        )

    def follow(self, *, timeout: float | None = None) -> Iterator[TaskEvent]:
        yield from events.follow_task_events(
            self._context,
            self.tid,
            timeout=timeout,
        )

    def stop(self) -> None:
        task_ops.stop_task(self.tid, context=self._context)

    def kill(self) -> None:
        task_ops.kill_task(self.tid, context=self._context)

    @property
    def _context(self) -> WeftContext:
        return self.client.context
