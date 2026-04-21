"""Task handle for the public Python client.

Spec references:
- docs/specifications/13C-Using_Weft_With_Django.md [DJ-2.1], [DJ-2.2]
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from weft.commands import events
from weft.commands import result as result_cmd
from weft.commands import tasks as task_ops
from weft.commands.types import TaskEvent, TaskResult, TaskSnapshot
from weft.context import WeftContext

from ._types import ClientContextHandle


@dataclass(frozen=True, slots=True)
class Task:
    """Lazy handle for one Weft task id."""

    client: ClientContextHandle
    tid: str

    def snapshot(self) -> TaskSnapshot | None:
        return task_ops.task_snapshot(self.tid, context=self._context)

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
