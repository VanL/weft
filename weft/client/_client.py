"""Public Weft client surface built on shared ops."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from weft.commands import submission
from weft.context import WeftContext, build_context

from ._namespaces import (
    ManagersNamespace,
    QueuesNamespace,
    SpecsNamespace,
    SystemNamespace,
    TasksNamespace,
)
from ._task import Task


class WeftClient:
    """Python adapter over the shared Weft capability layer."""

    def __init__(
        self,
        context: WeftContext | None = None,
        *,
        path: str | Path | None = None,
    ) -> None:
        if context is not None and path is not None:
            raise ValueError("Pass either context or path, not both")
        self.context = context or build_context(spec_context=path)
        self.tasks = TasksNamespace(self)
        self.queues = QueuesNamespace(self)
        self.managers = ManagersNamespace(self)
        self.specs = SpecsNamespace(self)
        self.system = SystemNamespace(self)

    @classmethod
    def from_context(
        cls,
        spec_context: str | Path | None = None,
        *,
        autostart: bool | None = None,
    ) -> WeftClient:
        return cls(build_context(spec_context=spec_context, autostart=autostart))

    @classmethod
    def from_weft_context(cls, context: WeftContext) -> WeftClient:
        return cls(context)

    def submit(
        self,
        taskspec: Any,
        *,
        payload: Any = None,
        **overrides: Any,
    ) -> Task:
        receipt = submission.submit(
            self.context,
            taskspec,
            payload=payload,
            **overrides,
        )
        return Task(self, receipt.tid)

    def submit_spec(
        self,
        reference: str | Path,
        *,
        payload: Any = None,
        **overrides: Any,
    ) -> Task:
        receipt = submission.submit_spec(
            self.context,
            reference,
            payload=payload,
            **overrides,
        )
        return Task(self, receipt.tid)

    def submit_pipeline(
        self,
        reference: str | Path,
        *,
        payload: Any = None,
        **overrides: Any,
    ) -> Task:
        receipt = submission.submit_pipeline(
            self.context,
            reference,
            payload=payload,
            **overrides,
        )
        return Task(self, receipt.tid)

    def submit_command(
        self,
        command: list[str] | tuple[str, ...] | str,
        *,
        payload: Any = None,
        shell: bool = False,
        **overrides: Any,
    ) -> Task:
        receipt = submission.submit_command(
            self.context,
            command,
            payload=payload,
            shell=shell,
            **overrides,
        )
        return Task(self, receipt.tid)

    def task(self, tid: str) -> Task:
        return Task(self, submission.normalize_tid(tid))
