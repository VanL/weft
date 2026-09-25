"""Public Weft client surface built on shared command capabilities.

Spec references:
- docs/specifications/09-Implementation_Plan.md [IP-1]
- docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]
- docs/specifications/05-Message_Flow_and_State.md [MF-1], [MF-5]
- docs/specifications/14-Python_API_Surfaces.md [PY-1], [PY-3]
"""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping, Sequence
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

from simplebroker import BrokerSession
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
    from weft.commands.types import PreparedSubmissionRequest


class _ClientLifecycleState(Enum):
    """Retained submission ownership states [PY-1]."""

    BOUNDED = auto()
    RETAINED_ACTIVE = auto()
    CLEANUP_PENDING = auto()


def _stable_exception_message(failure: BaseException) -> str:
    """Render literal string arguments without invoking custom formatting."""

    string_args = [argument for argument in failure.args if type(argument) is str]
    return ": ".join(string_args) if string_args else "<message unavailable>"


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
        self._lifecycle_state = _ClientLifecycleState.BOUNDED
        self._owner_pid: int | None = None
        self._owner_thread: threading.Thread | None = None
        self._submission_session: BrokerSession | None = None

    def __enter__(self) -> Self:
        """Activate lazy same-thread retained submission reuse [PY-1]."""

        self._reset_after_fork()
        if self._lifecycle_state is _ClientLifecycleState.CLEANUP_PENDING:
            raise RuntimeError(
                "WeftClient cleanup is pending. Retry close() on the owner thread."
            )
        if self._lifecycle_state is _ClientLifecycleState.RETAINED_ACTIVE:
            raise RuntimeError("WeftClient is already active")
        self._lifecycle_state = _ClientLifecycleState.RETAINED_ACTIVE
        self._owner_pid = os.getpid()
        self._owner_thread = threading.current_thread()
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        exc: BaseException | None,
        _traceback: object,
    ) -> None:
        try:
            self.close()
        except Exception as close_failure:
            if exc is None:
                raise
            failure_notes = tuple(getattr(close_failure, "__notes__", ()))
            exc.add_note(
                "Additional WeftClient cleanup failure: "
                f"{type(close_failure).__qualname__}: "
                f"{_stable_exception_message(close_failure)}"
            )
            for note in failure_notes:
                exc.add_note(f"Additional WeftClient cleanup diagnostic: {note}")

    def close(self) -> None:
        """Release a retained submission session on its owner thread [PY-1]."""

        self._reset_after_fork()
        if self._lifecycle_state is _ClientLifecycleState.BOUNDED:
            return
        self._require_owner("close")
        session = self._submission_session
        if session is not None:
            try:
                session.close()
            except BaseException:
                self._lifecycle_state = _ClientLifecycleState.CLEANUP_PENDING
                raise
        self._submission_session = None
        self._owner_pid = None
        self._owner_thread = None
        self._lifecycle_state = _ClientLifecycleState.BOUNDED

    def _reset_after_fork(self) -> None:
        owner_pid = self._owner_pid
        if owner_pid is None or owner_pid == os.getpid():
            return
        session = self._submission_session
        if session is not None:
            session.close()
        self._submission_session = None
        self._owner_pid = None
        self._owner_thread = None
        self._lifecycle_state = _ClientLifecycleState.BOUNDED

    def _require_owner(self, operation: str) -> None:
        if (
            self._owner_pid != os.getpid()
            or self._owner_thread is not threading.current_thread()
        ):
            raise RuntimeError(
                f"Active WeftClient {operation} must run on its owner thread"
            )

    def _submit_prepared(self, prepared: PreparedSubmissionRequest) -> Task:
        self._reset_after_fork()
        session: BrokerSession | None = None
        if self._lifecycle_state is _ClientLifecycleState.CLEANUP_PENDING:
            self._require_owner("submission")
            raise RuntimeError(
                "WeftClient cleanup is pending. Retry close() before submission."
            )
        if self._lifecycle_state is _ClientLifecycleState.RETAINED_ACTIVE:
            self._require_owner("submission")
            runtime_root = submission._resolve_submission_runtime_root(
                prepared.taskspec,
                self.context,
            )
            if runtime_root == self.context.root.resolve():
                if self._submission_session is None:
                    self._submission_session = self.context.session()
                session = self._submission_session
        outcome = submission._submit_prepared_outcome(
            self.context,
            prepared,
            session=session,
        )
        return Task(
            self,
            outcome.receipt.tid,
            context=outcome.runtime_context,
        )

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
        prepared = submission._prepare_command(
            self.context,
            command,
            payload=payload,
            shell=shell,
            **overrides,
        )
        return self._submit_prepared(prepared)

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
