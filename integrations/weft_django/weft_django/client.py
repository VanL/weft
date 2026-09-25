"""Django-facing client helpers layered over the public `weft.client` surface.

Spec: docs/specifications/13C-Using_Weft_With_Django.md [DJ-8.1], [DJ-8.4], [DJ-13.2].
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Self

from django.db import transaction

from weft.client import (
    ControlRejected,
    PreparedSubmission,
    Task,
    TaskEvent,
    TaskNotFound,
    TaskResult,
    TaskSnapshot,
    TaskTerminalSnapshot,
    WeftClient,
    normalize_taskspec_payload,
)
from weft_django.conf import (
    get_context_fallback_root,
    get_default_task_settings,
    get_explicit_context,
    merge_metadata,
)
from weft_django.lifecycle import get_current_client
from weft_django.registry import get_task


@dataclass(frozen=True, slots=True)
class WeftSubmission:
    """Thin Django-facing wrapper over a submitted Weft task."""

    task: Task
    name: str

    @property
    def tid(self) -> str:
        return self.task.tid

    def snapshot(self) -> TaskSnapshot | None:
        return self.task.snapshot()

    def terminal_snapshot(self, timeout: float = 0.0) -> TaskTerminalSnapshot:
        return self.task.terminal_snapshot(timeout=timeout)

    def status(self) -> str | None:
        snapshot = self.terminal_snapshot()
        if snapshot is None:
            return None
        return snapshot.status

    def wait(self, timeout: float | None = None) -> TaskResult:
        return self.task.result(timeout=timeout)

    def result(self, timeout: float | None = None) -> TaskResult:
        return self.task.result(timeout=timeout)

    def stop(self) -> None:
        self.task.stop()

    def kill(self) -> None:
        self.task.kill()

    def events(self, *, follow: bool = False) -> Iterator[TaskEvent]:
        yield from self.task.events(follow=follow)


@dataclass(slots=True)
class WeftDeferredSubmission:
    """Handle for submission deferred until transaction commit."""

    name: str
    task: WeftSubmission | None = None

    @property
    def tid(self) -> str | None:
        return self.task.tid if self.task is not None else None

    def bind(self, task: WeftSubmission) -> WeftSubmission:
        self.task = task
        return task

    def _require_task(self) -> WeftSubmission:
        if self.task is None:
            raise RuntimeError(
                "Submission has not been bound yet. Wait for the outer transaction to commit."
            )
        return self.task

    def status(self) -> str | None:
        return self._require_task().status()

    def wait(self, timeout: float | None = None) -> TaskResult:
        return self._require_task().wait(timeout=timeout)

    def result(self, timeout: float | None = None) -> TaskResult:
        return self._require_task().result(timeout=timeout)

    def stop(self) -> None:
        self._require_task().stop()

    def kill(self) -> None:
        self._require_task().kill()

    def events(self, *, follow: bool = False) -> Iterator[TaskEvent]:
        yield from self._require_task().events(follow=follow)


class DjangoWeftClient:
    """Small Django-native wrapper over the public Weft client."""

    def __init__(self, core_client: WeftClient) -> None:
        self.core_client = core_client

    def __enter__(self) -> Self:
        self.core_client.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        self.core_client.__exit__(exc_type, exc, traceback)

    def close(self) -> None:
        self.core_client.close()

    def submit_registered_task(
        self,
        task: Any,
        *,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        overrides: Mapping[str, Any] | None = None,
        envelope: Mapping[str, Any] | None = None,
    ) -> WeftSubmission:
        _validate_decorated_task_overrides(overrides)
        built_envelope = dict(envelope or task.build_envelope(*args, **kwargs))
        taskspec_payload = build_registered_task_taskspec(
            task,
            envelope=built_envelope,
            embed_envelope=False,
        )
        submission_name = _effective_submission_name(
            task,
            overrides,
            default=task.name,
        )
        task_handle = self.core_client.submit(
            taskspec_payload,
            payload={"payload": built_envelope},
            **_submit_kwargs(overrides),
        )
        return _maybe_wait(_wrap_task(task_handle, name=submission_name), overrides)

    def submit_registered_task_on_commit(
        self,
        task: Any,
        *,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        overrides: Mapping[str, Any] | None = None,
    ) -> WeftDeferredSubmission:
        if overrides and overrides.get("wait"):
            raise ValueError("enqueue_on_commit(..., wait=True) is not supported")
        _validate_decorated_task_overrides(overrides)
        envelope = task.build_envelope(*args, **kwargs)
        prepared = self.core_client.prepare(
            build_registered_task_taskspec(
                task,
                envelope=envelope,
                embed_envelope=False,
            ),
            payload={"payload": envelope},
            **_submit_kwargs(overrides),
        )
        deferred_name = _effective_submission_name(task, overrides, default=task.name)
        deferred = WeftDeferredSubmission(name=deferred_name)

        def _submit() -> None:
            deferred.bind(_submit_prepared(prepared, name=deferred_name))

        transaction.on_commit(_submit)
        return deferred

    def submit_taskspec(
        self,
        taskspec: Any,
        *,
        payload: Any = None,
        **overrides: Any,
    ) -> WeftSubmission:
        _reject_legacy_payload_names(overrides)
        task = self.core_client.submit(
            taskspec,
            payload=payload,
            **_submit_kwargs(overrides),
        )
        return _maybe_wait(
            _wrap_task(
                task,
                name=_effective_submission_name(taskspec, overrides, default="task"),
            ),
            overrides or None,
        )

    def submit_taskspec_on_commit(
        self,
        taskspec: Any,
        *,
        payload: Any = None,
        **overrides: Any,
    ) -> WeftDeferredSubmission:
        _reject_legacy_payload_names(overrides)
        if overrides.get("wait"):
            raise ValueError(
                "submit_taskspec_on_commit(..., wait=True) is not supported"
            )
        deferred_name = _effective_submission_name(taskspec, overrides, default="task")
        prepared = self.core_client.prepare(
            taskspec,
            payload=payload,
            **_submit_kwargs(overrides),
        )
        deferred = WeftDeferredSubmission(name=deferred_name)

        def _submit() -> None:
            deferred.bind(_submit_prepared(prepared, name=deferred_name))

        transaction.on_commit(_submit)
        return deferred

    def submit_spec_reference(
        self,
        reference: str | Any,
        *,
        spec_args: Sequence[str] = (),
        stdin_text: str | None = None,
        payload: Any = None,
        **overrides: Any,
    ) -> WeftSubmission:
        _reject_legacy_payload_names(overrides)
        task = self.core_client.submit_spec(
            reference,
            spec_args=spec_args,
            stdin_text=stdin_text,
            payload=payload,
            **_submit_kwargs(overrides),
        )
        return _maybe_wait(
            _wrap_task(
                task,
                name=_effective_submission_name(
                    reference, overrides, default=str(reference)
                ),
            ),
            overrides or None,
        )

    def submit_spec_reference_on_commit(
        self,
        reference: str | Any,
        *,
        spec_args: Sequence[str] = (),
        stdin_text: str | None = None,
        payload: Any = None,
        **overrides: Any,
    ) -> WeftDeferredSubmission:
        _reject_legacy_payload_names(overrides)
        if overrides.get("wait"):
            raise ValueError(
                "submit_spec_reference_on_commit(..., wait=True) is not supported"
            )
        deferred_name = _effective_submission_name(
            reference, overrides, default=str(reference)
        )
        prepared = self.core_client.prepare_spec(
            reference,
            spec_args=spec_args,
            stdin_text=stdin_text,
            payload=payload,
            **_submit_kwargs(overrides),
        )
        deferred = WeftDeferredSubmission(name=deferred_name)

        def _submit() -> None:
            deferred.bind(_submit_prepared(prepared, name=deferred_name))

        transaction.on_commit(_submit)
        return deferred

    def submit_pipeline_reference(
        self,
        reference: str | Any,
        *,
        payload: Any = None,
        **overrides: Any,
    ) -> WeftSubmission:
        _reject_legacy_payload_names(overrides)
        task = self.core_client.submit_pipeline(
            reference,
            payload=payload,
            **_submit_kwargs(overrides),
        )
        return _maybe_wait(
            _wrap_task(
                task,
                name=_effective_submission_name(
                    reference, overrides, default=str(reference)
                ),
            ),
            overrides or None,
        )

    def submit_pipeline_reference_on_commit(
        self,
        reference: str | Any,
        *,
        payload: Any = None,
        **overrides: Any,
    ) -> WeftDeferredSubmission:
        _reject_legacy_payload_names(overrides)
        if overrides.get("wait"):
            raise ValueError(
                "submit_pipeline_reference_on_commit(..., wait=True) is not supported"
            )
        deferred_name = _effective_submission_name(
            reference, overrides, default=str(reference)
        )
        prepared = self.core_client.prepare_pipeline(
            reference,
            payload=payload,
            **_submit_kwargs(overrides),
        )
        deferred = WeftDeferredSubmission(name=deferred_name)

        def _submit() -> None:
            deferred.bind(_submit_prepared(prepared, name=deferred_name))

        transaction.on_commit(_submit)
        return deferred

    def task(self, tid: str, *, name: str | None = None) -> WeftSubmission:
        return WeftSubmission(self.core_client.task(tid), name=name or tid)

    def status(self, tid: str) -> TaskTerminalSnapshot | None:
        try:
            return self.core_client.task(tid).terminal_snapshot()
        except ValueError:
            return None

    def terminal_snapshot(
        self,
        tid: str,
        timeout: float = 0.0,
    ) -> TaskTerminalSnapshot | None:
        try:
            return self.core_client.task(tid).terminal_snapshot(timeout=timeout)
        except ValueError:
            return None

    def snapshot(self, tid: str) -> TaskSnapshot | None:
        try:
            return self.core_client.task(tid).snapshot()
        except ValueError:
            return None

    def result(self, tid: str, timeout: float | None = None) -> TaskResult:
        return self.core_client.task(tid).result(timeout=timeout)

    def stop(self, tid: str) -> None:
        self.core_client.task(tid).stop()

    def kill(self, tid: str) -> None:
        self.core_client.task(tid).kill()


def get_core_client() -> WeftClient:
    """Request a resolved context from Weft using Django's settings inputs.

    Spec: docs/specifications/13C-Using_Weft_With_Django.md [DJ-13.2]
    """

    return WeftClient.from_context(
        get_explicit_context(), fallback_root=get_context_fallback_root()
    )


def get_client() -> DjangoWeftClient:
    return DjangoWeftClient(get_core_client())


def _current_client() -> DjangoWeftClient:
    """Select the request-owned client or a bounded one-shot client."""

    return get_current_client(get_client)


def _submit_kwargs(overrides: Mapping[str, Any] | None) -> dict[str, Any]:
    if not overrides:
        return {}
    return {key: value for key, value in dict(overrides).items() if key != "wait"}


def _wrap_task(task: Task, *, name: str) -> WeftSubmission:
    return WeftSubmission(task=task, name=name)


def _submit_prepared(
    prepared: PreparedSubmission,
    *,
    name: str,
) -> WeftSubmission:
    return _wrap_task(prepared.submit(), name=name)


def _maybe_wait(
    submission: WeftSubmission,
    overrides: Mapping[str, Any] | None,
) -> WeftSubmission:
    if overrides and overrides.get("wait"):
        submission.result()
    return submission


def _submission_name(candidate: Any, *, default: str) -> str:
    if isinstance(candidate, Mapping):
        name = candidate.get("name")
        if isinstance(name, str) and name.strip():
            return name
        return default

    name = getattr(candidate, "name", None)
    if isinstance(name, str) and name.strip():
        return name
    return default


def _effective_submission_name(
    candidate: Any,
    overrides: Mapping[str, Any] | None,
    *,
    default: str,
) -> str:
    if overrides and overrides.get("name"):
        return str(overrides["name"])
    return _submission_name(candidate, default=default)


def _reject_legacy_payload_names(overrides: Mapping[str, Any]) -> None:
    legacy_names = sorted({"work_payload", "input"} & set(overrides))
    if legacy_names:
        joined = ", ".join(f"{name}=..." for name in legacy_names)
        raise TypeError(f"Use payload=... for native submissions, not {joined}")


def _validate_decorated_task_overrides(overrides: Mapping[str, Any] | None) -> None:
    if not overrides:
        return
    runner = overrides.get("runner")
    if runner not in (None, "", "host"):
        raise ValueError("Decorated Django tasks only support runner='host' in v1")


def _build_limits(
    *,
    memory_mb: int | None,
    cpu_percent: int | None,
) -> dict[str, Any] | None:
    limits: dict[str, Any] = {}
    if memory_mb is not None:
        limits["memory_mb"] = memory_mb
    if cpu_percent is not None:
        limits["cpu_percent"] = cpu_percent
    return limits or None


def build_registered_task_taskspec(
    task: Any,
    *,
    envelope: Mapping[str, Any],
    embed_envelope: bool,
) -> dict[str, Any]:
    """Build a portable declaration, copying only an explicit Django context.

    Spec: docs/specifications/13C-Using_Weft_With_Django.md [DJ-8.1], [DJ-13.2]
    """

    default_task_settings = get_default_task_settings()
    metadata = merge_metadata(
        default_task_settings.get("metadata"),
        task.metadata,
    )
    if task.description:
        metadata.setdefault("description", task.description)
    metadata.setdefault("django_app_label", task.app_label)
    metadata.setdefault("callable_ref", task.callable_ref)

    timeout = (
        task.timeout
        if task.timeout is not None
        else default_task_settings.get("timeout")
    )
    stream_output = (
        task.stream_output
        if task.stream_output is not None
        else default_task_settings.get("stream_output", False)
    )
    runner = task.runner or default_task_settings.get("runner", "host")
    if runner != "host":
        raise ValueError("Decorated Django tasks only support runner='host' in v1")
    runner_options = merge_metadata(
        default_task_settings.get("runner_options"),
        task.runner_options,
    )
    working_dir = task.working_dir or default_task_settings.get("working_dir")
    env = merge_metadata(default_task_settings.get("env"), task.env)
    memory_mb = (
        task.memory_mb
        if task.memory_mb is not None
        else default_task_settings.get("memory_mb")
    )
    cpu_percent = (
        task.cpu_percent
        if task.cpu_percent is not None
        else default_task_settings.get("cpu_percent")
    )
    limits = _build_limits(memory_mb=memory_mb, cpu_percent=cpu_percent)
    spec_payload: dict[str, Any] = {
        "name": task.name,
        "spec": {
            "type": "function",
            "function_target": "weft_django.worker:run_registered_task",
            "runner": {
                "name": "host",
                "options": runner_options,
            },
            "args": [{"payload": dict(envelope)}] if embed_envelope else [],
            "keyword_args": {},
            "env": env,
            "working_dir": working_dir,
            "stream_output": bool(stream_output),
        },
        "metadata": metadata,
    }
    context_override = get_explicit_context()
    if context_override is not None:
        spec_payload["spec"]["weft_context"] = str(context_override)
    if timeout is not None:
        spec_payload["spec"]["timeout"] = timeout
    if limits is not None:
        spec_payload["spec"]["limits"] = limits
    return spec_payload


def submit_registered_task(
    task: Any,
    *,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    overrides: Mapping[str, Any] | None = None,
    envelope: Mapping[str, Any] | None = None,
) -> WeftSubmission:
    return _current_client().submit_registered_task(
        task,
        args=args,
        kwargs=kwargs,
        overrides=overrides,
        envelope=envelope,
    )


def submit_registered_task_on_commit(
    task: Any,
    *,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    overrides: Mapping[str, Any] | None = None,
) -> WeftDeferredSubmission:
    """Capture the client and decorated call before registering the callback.

    Spec: docs/specifications/13C-Using_Weft_With_Django.md [DJ-8.4]
    """

    return _current_client().submit_registered_task_on_commit(
        task,
        args=args,
        kwargs=kwargs,
        overrides=overrides,
    )


def export_registered_task_taskspec(
    task: Any,
    *,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validated TaskSpec payload for one decorated-task call.

    Applies the core submit-override contract through
    `weft.client.normalize_taskspec_payload`; builds no Weft context, reads no
    Weft configuration, opens no broker, writes nothing (README "Composition
    Export"). Overrides travel unchanged so `wait` and `payload` reach core and
    raise like any other name outside the override vocabulary.

    Spec: docs/specifications/13C-Using_Weft_With_Django.md [DJ-8.1]
    """

    _validate_decorated_task_overrides(overrides)
    envelope = task.build_envelope(*args, **kwargs)
    base_payload = build_registered_task_taskspec(
        task,
        envelope=envelope,
        embed_envelope=True,
    )
    return normalize_taskspec_payload(base_payload, **dict(overrides or {}))


def submit_taskspec(
    taskspec: Any,
    *,
    payload: Any = None,
    **overrides: Any,
) -> WeftSubmission:
    return _current_client().submit_taskspec(taskspec, payload=payload, **overrides)


def submit_taskspec_on_commit(
    taskspec: Any,
    *,
    payload: Any = None,
    **overrides: Any,
) -> WeftDeferredSubmission:
    """Capture a native TaskSpec and its client before registering the callback.

    Spec: docs/specifications/13C-Using_Weft_With_Django.md [DJ-8.4]
    """

    return _current_client().submit_taskspec_on_commit(
        taskspec, payload=payload, **overrides
    )


def submit_spec_reference(
    reference: str | Any,
    *,
    spec_args: Sequence[str] = (),
    stdin_text: str | None = None,
    payload: Any = None,
    **overrides: Any,
) -> WeftSubmission:
    return _current_client().submit_spec_reference(
        reference,
        spec_args=spec_args,
        stdin_text=stdin_text,
        payload=payload,
        **overrides,
    )


def submit_spec_reference_on_commit(
    reference: str | Any,
    *,
    spec_args: Sequence[str] = (),
    stdin_text: str | None = None,
    payload: Any = None,
    **overrides: Any,
) -> WeftDeferredSubmission:
    """Resolve and prepare a task reference before registering the callback.

    Spec: docs/specifications/13C-Using_Weft_With_Django.md [DJ-8.4]
    """

    return _current_client().submit_spec_reference_on_commit(
        reference,
        spec_args=spec_args,
        stdin_text=stdin_text,
        payload=payload,
        **overrides,
    )


def submit_pipeline_reference(
    reference: str | Any,
    *,
    payload: Any = None,
    **overrides: Any,
) -> WeftSubmission:
    return _current_client().submit_pipeline_reference(
        reference, payload=payload, **overrides
    )


def submit_pipeline_reference_on_commit(
    reference: str | Any,
    *,
    payload: Any = None,
    **overrides: Any,
) -> WeftDeferredSubmission:
    """Compile and prepare a pipeline before registering the callback.

    Spec: docs/specifications/13C-Using_Weft_With_Django.md [DJ-8.4]
    """

    return _current_client().submit_pipeline_reference_on_commit(
        reference, payload=payload, **overrides
    )


def _resolve_task(task: str | Any) -> Any:
    if isinstance(task, str):
        return get_task(task)
    return task


def enqueue(
    task: str | Any,
    *args: Any,
    _overrides: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> WeftSubmission:
    return submit_registered_task(
        _resolve_task(task),
        args=args,
        kwargs=kwargs,
        overrides=_overrides,
    )


def enqueue_on_commit(
    task: str | Any,
    *args: Any,
    _overrides: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> WeftDeferredSubmission:
    return submit_registered_task_on_commit(
        _resolve_task(task),
        args=args,
        kwargs=kwargs,
        overrides=_overrides,
    )


def status(tid: str) -> TaskTerminalSnapshot | None:
    return _current_client().status(tid)


def terminal_snapshot(
    tid: str,
    timeout: float = 0.0,
) -> TaskTerminalSnapshot | None:
    return _current_client().terminal_snapshot(tid, timeout=timeout)


def snapshot(tid: str) -> TaskSnapshot | None:
    return _current_client().snapshot(tid)


def result(tid: str, timeout: float | None = None) -> TaskResult:
    return _current_client().result(tid, timeout=timeout)


def stop(tid: str) -> bool:
    try:
        _current_client().stop(tid)
    except (ControlRejected, TaskNotFound, ValueError):
        return False
    return True


def kill(tid: str) -> bool:
    try:
        _current_client().kill(tid)
    except (ControlRejected, TaskNotFound, ValueError):
        return False
    return True
