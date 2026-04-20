"""Django-facing client helpers layered over the public `weft.client` surface."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.db import transaction

from weft.client import Task, TaskResult, TaskSnapshot, WeftClient
from weft_django.conf import (
    get_default_task_settings,
    merge_metadata,
    resolve_context_override,
)
from weft_django.registry import get_task

WeftSubmission = Task


@dataclass(slots=True)
class WeftDeferredSubmission:
    """Handle for submission deferred until transaction commit."""

    name: str
    task: Task | None = None

    @property
    def tid(self) -> str | None:
        return self.task.tid if self.task is not None else None

    def bind(self, task: Task) -> Task:
        self.task = task
        return task


class DjangoWeftClient:
    """Small Django-native wrapper over the public Weft client."""

    def __init__(self, core_client: WeftClient) -> None:
        self.core_client = core_client

    def status(self, tid: str) -> TaskSnapshot | None:
        return self.core_client.task(tid).snapshot()

    def result(self, tid: str, timeout: float | None = None) -> TaskResult:
        return self.core_client.task(tid).result(timeout=timeout)

    def stop(self, tid: str) -> None:
        self.core_client.task(tid).stop()

    def kill(self, tid: str) -> None:
        self.core_client.task(tid).kill()


def get_core_client() -> WeftClient:
    return WeftClient.from_context(resolve_context_override())


def get_client() -> DjangoWeftClient:
    return DjangoWeftClient(get_core_client())


def _submit_kwargs(overrides: Mapping[str, Any] | None) -> dict[str, Any]:
    if not overrides:
        return {}
    return {key: value for key, value in dict(overrides).items() if key != "wait"}


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


def _validate_decorated_task_overrides(overrides: Mapping[str, Any] | None) -> None:
    if not overrides:
        return
    runner = overrides.get("runner")
    if runner not in (None, "", "host"):
        raise ValueError("Decorated Django tasks only support runner='host' in v1")


def _apply_taskspec_payload_overrides(
    payload: Mapping[str, Any],
    overrides: Mapping[str, Any] | None,
    *,
    decorated_task: Any | None = None,
) -> dict[str, Any]:
    def _copy_payload(source: Mapping[str, Any]) -> dict[str, Any]:
        cloned = json.loads(json.dumps(dict(source)))
        if not isinstance(cloned, dict):
            raise ValueError("TaskSpec payload must be a JSON object")
        return cloned

    if not overrides:
        return _copy_payload(payload)

    updated = _copy_payload(payload)
    spec_section = updated.setdefault("spec", {})
    metadata = updated.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        updated["metadata"] = metadata
    if not isinstance(spec_section, dict):
        raise ValueError("TaskSpec spec section must be a mapping")

    if "metadata" in overrides:
        metadata.update(dict(overrides["metadata"] or {}))
    if "description" in overrides and overrides["description"] is not None:
        metadata["description"] = overrides["description"]
    if "tags" in overrides and overrides["tags"] is not None:
        metadata["tags"] = list(overrides["tags"])
    if "timeout" in overrides:
        spec_section["timeout"] = overrides["timeout"]
    if "stream_output" in overrides:
        spec_section["stream_output"] = overrides["stream_output"]
    if "name" in overrides and overrides["name"]:
        updated["name"] = overrides["name"]
    if "env" in overrides:
        env = spec_section.get("env")
        env = env if isinstance(env, dict) else {}
        env.update(dict(overrides["env"] or {}))
        spec_section["env"] = env
    if "working_dir" in overrides:
        spec_section["working_dir"] = overrides["working_dir"]
    if "memory_mb" in overrides or "cpu_percent" in overrides:
        limits = spec_section.get("limits")
        limits = limits if isinstance(limits, dict) else {}
        if "memory_mb" in overrides and overrides["memory_mb"] is not None:
            limits["memory_mb"] = overrides["memory_mb"]
        if "cpu_percent" in overrides and overrides["cpu_percent"] is not None:
            limits["cpu_percent"] = overrides["cpu_percent"]
        spec_section["limits"] = limits
    if "runner" in overrides or "runner_options" in overrides:
        runner = spec_section.get("runner")
        runner = runner if isinstance(runner, dict) else {}
        runner_name = overrides.get("runner", runner.get("name"))
        if decorated_task is not None and runner_name not in (None, "", "host"):
            raise ValueError("Decorated Django tasks only support runner='host' in v1")
        if runner_name:
            runner["name"] = runner_name
        options = runner.get("options")
        options = options if isinstance(options, dict) else {}
        if "runner_options" in overrides:
            options.update(dict(overrides["runner_options"] or {}))
        runner["options"] = options
        spec_section["runner"] = runner
    return updated


def _maybe_wait(task: Task, overrides: Mapping[str, Any] | None) -> Task:
    if overrides and overrides.get("wait"):
        task.result()
    return task


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
    overrides: Mapping[str, Any] | None,
    embed_envelope: bool,
) -> dict[str, Any]:
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
    context_override = resolve_context_override()
    if context_override is not None:
        spec_payload["spec"]["weft_context"] = str(context_override)
    if timeout is not None:
        spec_payload["spec"]["timeout"] = timeout
    if limits is not None:
        spec_payload["spec"]["limits"] = limits
    updated = _apply_taskspec_payload_overrides(
        spec_payload,
        overrides,
        decorated_task=task,
    )
    return updated


def submit_registered_task(
    task: Any,
    *,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    overrides: Mapping[str, Any] | None = None,
    envelope: Mapping[str, Any] | None = None,
) -> Task:
    _validate_decorated_task_overrides(overrides)
    built_envelope = dict(envelope or task.build_envelope(*args, **kwargs))
    taskspec_payload = build_registered_task_taskspec(
        task,
        envelope=built_envelope,
        overrides=None,
        embed_envelope=False,
    )
    task_handle = get_core_client().submit(
        taskspec_payload,
        payload={"payload": built_envelope},
        **_submit_kwargs(overrides),
    )
    return _maybe_wait(task_handle, overrides)


def submit_registered_task_on_commit(
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
    taskspec_payload = build_registered_task_taskspec(
        task,
        envelope=envelope,
        overrides=None,
        embed_envelope=False,
    )
    deferred_name = (
        str(overrides.get("name")) if overrides and overrides.get("name") else task.name
    )
    deferred = WeftDeferredSubmission(name=deferred_name)

    def _submit() -> None:
        deferred.bind(
            get_core_client().submit(
                taskspec_payload,
                payload={"payload": envelope},
                **_submit_kwargs(overrides),
            )
        )

    transaction.on_commit(_submit)
    return deferred


def submit_taskspec(
    taskspec: Any,
    *,
    payload: Any = None,
    **overrides: Any,
) -> Task:
    task = get_core_client().submit(
        taskspec,
        payload=payload,
        **_submit_kwargs(overrides),
    )
    return _maybe_wait(task, overrides or None)


def submit_taskspec_on_commit(
    taskspec: Any,
    *,
    payload: Any = None,
    **overrides: Any,
) -> WeftDeferredSubmission:
    if overrides.get("wait"):
        raise ValueError("submit_taskspec_on_commit(..., wait=True) is not supported")
    deferred_name = (
        str(overrides["name"])
        if overrides.get("name")
        else _submission_name(taskspec, default="task")
    )
    deferred = WeftDeferredSubmission(name=deferred_name)

    def _submit() -> None:
        deferred.bind(
            get_core_client().submit(
                taskspec,
                payload=payload,
                **_submit_kwargs(overrides),
            )
        )

    transaction.on_commit(_submit)
    return deferred


def submit_spec_reference(
    reference: str | Any,
    *,
    payload: Any = None,
    **overrides: Any,
) -> Task:
    task = get_core_client().submit_spec(
        reference,
        payload=payload,
        **_submit_kwargs(overrides),
    )
    return _maybe_wait(task, overrides or None)


def submit_spec_reference_on_commit(
    reference: str | Any,
    *,
    payload: Any = None,
    **overrides: Any,
) -> WeftDeferredSubmission:
    if overrides.get("wait"):
        raise ValueError(
            "submit_spec_reference_on_commit(..., wait=True) is not supported"
        )
    deferred_name = str(overrides["name"]) if overrides.get("name") else str(reference)
    deferred = WeftDeferredSubmission(name=deferred_name)

    def _submit() -> None:
        deferred.bind(
            get_core_client().submit_spec(
                reference,
                payload=payload,
                **_submit_kwargs(overrides),
            )
        )

    transaction.on_commit(_submit)
    return deferred


def submit_pipeline_reference(
    reference: str | Any,
    *,
    payload: Any = None,
    **overrides: Any,
) -> Task:
    task = get_core_client().submit_pipeline(
        reference,
        payload=payload,
        **_submit_kwargs(overrides),
    )
    return _maybe_wait(task, overrides or None)


def submit_pipeline_reference_on_commit(
    reference: str | Any,
    *,
    payload: Any = None,
    **overrides: Any,
) -> WeftDeferredSubmission:
    if overrides.get("wait"):
        raise ValueError(
            "submit_pipeline_reference_on_commit(..., wait=True) is not supported"
        )
    deferred_name = str(overrides["name"]) if overrides.get("name") else str(reference)
    deferred = WeftDeferredSubmission(name=deferred_name)

    def _submit() -> None:
        deferred.bind(
            get_core_client().submit_pipeline(
                reference,
                payload=payload,
                **_submit_kwargs(overrides),
            )
        )

    transaction.on_commit(_submit)
    return deferred


def _resolve_task(task: str | Any) -> Any:
    if isinstance(task, str):
        return get_task(task)
    return task


def enqueue(
    task: str | Any,
    *args: Any,
    _overrides: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> Task:
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


def status(tid: str) -> TaskSnapshot | None:
    try:
        return get_core_client().task(tid).snapshot()
    except ValueError:
        return None


def result(tid: str, timeout: float | None = None) -> TaskResult:
    return get_core_client().task(tid).result(timeout=timeout)


def stop(tid: str) -> bool:
    try:
        get_core_client().task(tid).stop()
    except ValueError:
        return False
    return True


def kill(tid: str) -> bool:
    try:
        get_core_client().task(tid).kill()
    except ValueError:
        return False
    return True
