"""Shared helpers for executing TaskSpec targets.

The :class:`Consumer` task (and future task variants) need to execute either
Python callables or external processes based on the TaskSpec configuration.
This module centralizes the decoding logic so multiple task types can reuse the
same behaviour without duplicating code.

Spec references: docs/specifications/01-Core_Components.md [CC-2.3], [CC-3]
and docs/specifications/02-TaskSpec.md [TS-1].
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from weft._constants import WORK_ENVELOPE_START


def decode_work_message(message: str) -> Any:
    """Deserialize an inbox payload, tolerating plain-text bodies (Spec: [CC-2.3], [MF-2])."""
    if not message:
        return {}
    try:
        payload = json.loads(message)
        if payload == WORK_ENVELOPE_START:
            return {}
        return payload
    except json.JSONDecodeError:
        if message == WORK_ENVELOPE_START:
            return {}
        return message


def prepare_call_arguments(
    spec_args: Sequence[Any] | None,
    spec_kwargs: Mapping[str, Any] | None,
    work_item: Any,
) -> tuple[list[Any], dict[str, Any]]:
    """Merge TaskSpec arguments with overrides supplied in the work item (Spec: [CC-3], [TS-1])."""
    args = list(spec_args or [])
    kwargs = dict(spec_kwargs or {})

    if isinstance(work_item, dict):
        if "args" in work_item:
            args = list(work_item["args"])
        elif "payload" in work_item and not args:
            args = [work_item["payload"]]

        if "kwargs" in work_item and isinstance(work_item["kwargs"], dict):
            kwargs.update(work_item["kwargs"])

        if "payload" in work_item and "input" not in kwargs and not args:
            args = [work_item["payload"]]
    elif work_item is not None and not args:
        args = [work_item]

    return args, kwargs


def execute_function_target(
    function_target: str,
    work_item: Any,
    *,
    args: Sequence[Any] | None = None,
    kwargs: Mapping[str, Any] | None = None,
) -> Any:
    """Import and execute the function referenced by ``module:function`` (Spec: [CC-3], [TS-1])."""
    module_name, func_name = function_target.rsplit(":", 1)
    module = importlib.import_module(module_name)
    func = getattr(module, func_name)

    call_args, call_kwargs = prepare_call_arguments(args, kwargs, work_item)
    return func(*call_args, **call_kwargs)


def execute_command_target(
    process_target: Sequence[str],
    work_item: Any,
    *,
    env: Mapping[str, str] | None = None,
    working_dir: str | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Execute an external command described by the TaskSpec (Spec: [CC-3], [TS-1], [RM-5])."""
    args = list(process_target)
    stdin_data = None

    if isinstance(work_item, dict):
        extra_args = work_item.get("args")
        if isinstance(extra_args, Iterable):
            args.extend(str(item) for item in extra_args)
        stdin_data = work_item.get("stdin")
    elif isinstance(work_item, str) and work_item:
        stdin_data = work_item

    env_vars = os.environ.copy()
    if env:
        env_vars.update(env)

    return subprocess.run(
        args,
        input=stdin_data,
        capture_output=True,
        text=True,
        cwd=working_dir or None,
        env=env_vars,
        timeout=timeout,
        check=False,
    )


def serialize_result(result: Any) -> str:
    """Serialize execution results, favouring JSON where possible (Spec: [CC-3], [MF-5])."""
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result)
    except TypeError:
        return str(result)


__all__ = [
    "decode_work_message",
    "prepare_call_arguments",
    "execute_function_target",
    "execute_command_target",
    "serialize_result",
]
