"""Decorator surface for Django-owned Weft background functions."""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Mapping
from functools import update_wrapper
from typing import Any, cast

from weft_django.conf import get_request_id_provider
from weft_django.registry import infer_app_label, register_task


def _callable_ref(func: Callable[..., Any]) -> str:
    return f"{func.__module__}:{func.__name__}"


def _split_import_ref(ref: str) -> tuple[str, str]:
    module_name, sep, object_name = ref.partition(":")
    if not sep or not module_name or not object_name:
        raise ValueError("import ref must use 'module:object' format")
    return module_name, object_name


def _default_name(func: Callable[..., Any]) -> str:
    module_name, object_name = _split_import_ref(_callable_ref(func))
    return f"{module_name}.{object_name}"


def _json_validate_call(args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
    try:
        json.dumps({"args": args, "kwargs": kwargs})
    except TypeError as exc:
        raise ValueError(
            "Decorated Django task arguments must be JSON-serializable"
        ) from exc


class RegisteredWeftTask:
    """Runtime wrapper returned by `@weft_task`."""

    def __init__(
        self,
        func: Callable[..., Any],
        *,
        name: str | None = None,
        description: str | None = None,
        timeout: float | None = None,
        memory_mb: int | None = None,
        cpu_percent: int | None = None,
        stream_output: bool | None = None,
        runner: str | None = None,
        runner_options: Mapping[str, Any] | None = None,
        working_dir: str | None = None,
        env: Mapping[str, str] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        if inspect.iscoroutinefunction(func):
            raise TypeError("@weft_task does not support async callables in v1")
        if inspect.isgeneratorfunction(func):
            raise TypeError("@weft_task does not support generator callables in v1")
        if runner not in (None, "", "host"):
            raise ValueError("Decorated Django tasks only support runner='host' in v1")

        self.func = func
        self.name = name or _default_name(func)
        self.description = description
        self.timeout = timeout
        self.memory_mb = memory_mb
        self.cpu_percent = cpu_percent
        self.stream_output = stream_output
        self.runner = "host"
        self.runner_options = dict(runner_options or {})
        self.working_dir = working_dir
        self.env = dict(env or {})
        self.metadata = dict(metadata or {})
        self.callable_ref = _callable_ref(func)
        self.app_label = infer_app_label(func.__module__)
        update_wrapper(self, func)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.func(*args, **kwargs)

    def build_envelope(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        _json_validate_call(args, kwargs)
        request_id = None
        request_id_provider = get_request_id_provider()
        if request_id_provider is not None:
            request_id = request_id_provider()
        return {
            "task_name": self.name,
            "tid": None,
            "call": {
                "args": list(args),
                "kwargs": kwargs,
            },
            "headers": {},
            "submitted_by": None,
            "request_id": request_id,
        }

    def as_taskspec_for_call(
        self,
        *args: Any,
        _overrides: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        from weft_django.client import build_registered_task_taskspec

        envelope = self.build_envelope(*args, **kwargs)
        return build_registered_task_taskspec(
            self,
            envelope=envelope,
            overrides=_overrides,
            embed_envelope=True,
        )

    def enqueue(
        self,
        *args: Any,
        _overrides: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        from weft_django.client import submit_registered_task

        return submit_registered_task(
            self,
            args=args,
            kwargs=kwargs,
            overrides=_overrides,
        )

    def enqueue_on_commit(
        self,
        *args: Any,
        _overrides: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        from weft_django.client import submit_registered_task_on_commit

        return submit_registered_task_on_commit(
            self,
            args=args,
            kwargs=kwargs,
            overrides=_overrides,
        )


def weft_task(
    *,
    name: str | None = None,
    description: str | None = None,
    timeout: float | None = None,
    memory_mb: int | None = None,
    cpu_percent: int | None = None,
    stream_output: bool | None = None,
    runner: str | None = None,
    runner_options: Mapping[str, Any] | None = None,
    working_dir: str | None = None,
    env: Mapping[str, str] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> Callable[[Callable[..., Any]], RegisteredWeftTask]:
    """Register a synchronous Django function as a named Weft task."""

    def _decorator(func: Callable[..., Any]) -> RegisteredWeftTask:
        task = RegisteredWeftTask(
            func,
            name=name,
            description=description,
            timeout=timeout,
            memory_mb=memory_mb,
            cpu_percent=cpu_percent,
            stream_output=stream_output,
            runner=runner,
            runner_options=runner_options,
            working_dir=working_dir,
            env=env,
            metadata=metadata,
        )
        return cast(RegisteredWeftTask, register_task(task))

    return _decorator
