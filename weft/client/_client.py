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
from ._prepared import PreparedSubmission
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
        return self.prepare(taskspec, payload=payload, **overrides).submit()

    def prepare(
        self,
        taskspec: Any,
        *,
        payload: Any = None,
        **overrides: Any,
    ) -> PreparedSubmission:
        request = submission.prepare(
            self.context,
            taskspec,
            payload=payload,
            **overrides,
        )
        return PreparedSubmission(self, request)

    def submit_spec(
        self,
        reference: str | Path,
        *,
        payload: Any = None,
        **overrides: Any,
    ) -> Task:
        return self.prepare_spec(reference, payload=payload, **overrides).submit()

    def prepare_spec(
        self,
        reference: str | Path,
        *,
        payload: Any = None,
        **overrides: Any,
    ) -> PreparedSubmission:
        request = submission.prepare_spec(
            self.context,
            reference,
            payload=payload,
            **overrides,
        )
        return PreparedSubmission(self, request)

    def submit_pipeline(
        self,
        reference: str | Path,
        *,
        payload: Any = None,
        **overrides: Any,
    ) -> Task:
        return self.prepare_pipeline(reference, payload=payload, **overrides).submit()

    def prepare_pipeline(
        self,
        reference: str | Path,
        *,
        payload: Any = None,
        **overrides: Any,
    ) -> PreparedSubmission:
        request = submission.prepare_pipeline(
            self.context,
            reference,
            payload=payload,
            **overrides,
        )
        return PreparedSubmission(self, request)

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
