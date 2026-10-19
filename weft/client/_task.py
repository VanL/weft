"""Task handle for the public Python client.

Spec references:
- docs/specifications/09-Implementation_Plan.md [IP-1]
- docs/specifications/05-Message_Flow_and_State.md [MF-3], [MF-5]
- docs/specifications/14-Python_API_Surfaces.md [PY-2]
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from weft._constants import (
    LIVENESS_RUNTIME_PROBE_TIMEOUT_SECONDS,
    TASK_PING_TIMEOUT_SECONDS,
)
from weft.commands import events
from weft.commands import result as result_cmd
from weft.commands import tasks as task_ops
from weft.commands.submission import TaskSpec
from weft.commands.types import (
    RuntimeLivenessSnapshot,
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
    context: WeftContext | None = None
    _accepted_taskspec: TaskSpec | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def snapshot(self) -> TaskSnapshot | None:
        """Project fresh state while lending known routing [MF-5], [PY-2]."""
        return task_ops._task_snapshot(
            self.tid,
            context=self._context,
            include_submission_custody=True,
            accepted_taskspec_payload=(
                self._accepted_taskspec.model_dump(mode="json")
                if self._accepted_taskspec is not None
                else None
            ),
        )

    def runtime_liveness(
        self, *, timeout: float = LIVENESS_RUNTIME_PROBE_TIMEOUT_SECONDS
    ) -> RuntimeLivenessSnapshot:
        """Explicitly probe the published runtime identity [PY-2]."""
        return task_ops.task_runtime_liveness(
            self.tid, timeout=timeout, context=self._context
        )

    def terminal_snapshot(self, timeout: float = 0.0) -> TaskTerminalSnapshot:
        if self._accepted_taskspec is None:
            return task_ops.task_terminal_snapshot(
                self.tid, timeout=timeout, context=self._context
            )
        return task_ops._task_terminal_snapshot(
            self.tid,
            timeout=timeout,
            context=self._context,
            accepted_taskspec_payload=self._accepted_taskspec.model_dump(mode="json"),
        )

    def ping(self, *, timeout: float = TASK_PING_TIMEOUT_SECONDS) -> dict[str, Any]:
        return task_ops.task_ping(self.tid, timeout=timeout, context=self._context)

    def result(self, timeout: float | None = None) -> TaskResult:
        return result_cmd.await_task_result(
            self._context,
            self.tid,
            timeout=timeout,
            _accepted_taskspec=self._accepted_taskspec,
        )

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
            _accepted_taskspec=self._accepted_taskspec,
        )

    def follow(self, *, timeout: float | None = None) -> Iterator[TaskEvent]:
        yield from events.follow_task_events(
            self._context,
            self.tid,
            timeout=timeout,
            _accepted_taskspec=self._accepted_taskspec,
        )

    def stop(self) -> None:
        task_ops.stop_task(self.tid, context=self._context)

    def kill(self) -> None:
        task_ops.kill_task(self.tid, context=self._context)

    @property
    def _context(self) -> WeftContext:
        return self.context or self.client.context


def _task_with_configuration(
    client: ClientContextHandle,
    tid: str,
    context: WeftContext,
    taskspec: TaskSpec,
) -> Task:
    """Bind immutable accepted configuration before exposing the handle.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2];
    docs/specifications/05-Message_Flow_and_State.md [MF-5].
    """
    task = Task(client, tid, context)
    object.__setattr__(task, "_accepted_taskspec", taskspec)
    return task
