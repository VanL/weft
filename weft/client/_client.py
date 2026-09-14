"""Public Weft client surface built on shared command capabilities.

Spec references:
- docs/specifications/09-Implementation_Plan.md [IP-1]
- docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]
- docs/specifications/05-Message_Flow_and_State.md [MF-1], [MF-5]
- docs/specifications/14-Python_API_Surfaces.md [PY-1], [PY-3]
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

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

if TYPE_CHECKING:
    from weft.client import TaskSpec


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
        self._context_explicit = (
            context is not None
            or path is not None
            or bool(self.context.config.get("CONTEXT"))
        )
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
        fallback_root: str | Path | None = None,
        autostart: bool | None = None,
    ) -> WeftClient:
        """Request core resolution with an optional discovery anchor [PY-1]."""
        client = cls(
            build_context(
                spec_context=spec_context,
                fallback_root=fallback_root,
                autostart=autostart,
            )
        )
        client._context_explicit = spec_context is not None or bool(
            client.context.config.get("CONTEXT")
        )
        return client

    @classmethod
    def from_weft_context(cls, context: WeftContext) -> WeftClient:
        return cls(context)

    def submit(
        self,
        taskspec: TaskSpec | Mapping[str, Any],
        *,
        payload: Any = None,
        **overrides: Any,
    ) -> Task:
        return self.prepare(taskspec, payload=payload, **overrides).submit()

    def prepare(
        self,
        taskspec: TaskSpec | Mapping[str, Any],
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
        spec_args: Sequence[str] = (),
        payload: Any = None,
        stdin_text: str | None = None,
        **overrides: Any,
    ) -> Task:
        return self.prepare_spec(
            reference,
            spec_args=spec_args,
            payload=payload,
            stdin_text=stdin_text,
            **overrides,
        ).submit()

    def prepare_spec(
        self,
        reference: str | Path,
        *,
        spec_args: Sequence[str] = (),
        payload: Any = None,
        stdin_text: str | None = None,
        **overrides: Any,
    ) -> PreparedSubmission:
        request = submission.prepare_spec(
            self.context,
            reference,
            spec_args=spec_args,
            payload=payload,
            stdin_text=stdin_text,
            context_explicit=self._context_explicit,
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


def connect(
    spec_context: str | Path | None = None,
    *,
    path: str | Path | None = None,
    autostart: bool | None = None,
) -> WeftClient:
    """Resolve a Weft context and return a bound public client adapter."""

    if spec_context is not None and path is not None:
        raise ValueError("Pass either spec_context or path, not both")
    return WeftClient.from_context(
        path if path is not None else spec_context,
        autostart=autostart,
    )


def normalize_taskspec_payload(
    taskspec: TaskSpec | Mapping[str, Any], **overrides: Any
) -> dict[str, Any]:
    """Return the validated, normalized TaskSpec payload for a submission.

    Args:
        taskspec: TaskSpec or JSON-compatible mapping; a mapping without a
            `tid` is validated as a template.
        **overrides: Public submit overrides. Every keyword is an override
            name, so `payload` is rejected like any other unknown name.

    Returns:
        A fresh JSON-compatible mapping: the pre-transport snapshot
        `WeftClient.prepare` would hold for the same inputs.

    Raises:
        TypeError: If an override name is outside the public vocabulary.
        ValueError: If an override value or the resulting TaskSpec is invalid.

    Note:
        Needs no client or context: builds no context, reads no configuration,
        resolves no project root, opens no broker, and writes nothing.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-1], [PY-3]
    """

    request = submission.prepare_definition(taskspec, overrides)
    payload: dict[str, Any] = request.taskspec.model_dump(mode="json")
    return payload
