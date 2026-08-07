"""Lazy public facade for core Weft runtime components."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from weft.helpers import format_tid, parse_tid

    from .callable import ManagedProcessResult, make_callable
    from .launcher import launch_task_process
    from .manager import Manager
    from .resource_monitor import (
        BaseResourceMonitor,
        PsutilResourceMonitor,
        ResourceMonitor,
    )
    from .targets import (
        decode_work_message,
        execute_command_target,
        execute_function_target,
        prepare_call_arguments,
        serialize_result,
    )
    from .tasks import Consumer, Monitor, Observer, SelectiveConsumer
    from .tasks.runner import TaskRunner
    from .taskspec import (
        IOSection,
        LimitsSection,
        RunnerSection,
        SpecSection,
        StateSection,
        TaskSpec,
        validate_taskspec,
    )

__all__ = [  # noqa: RUF022 approved [TS-3.1] [RUFF-SUP-247] exception
    "Consumer",
    "Observer",
    "SelectiveConsumer",
    "Monitor",
    "TaskRunner",
    "Manager",
    "launch_task_process",
    "ResourceMonitor",
    "PsutilResourceMonitor",
    "BaseResourceMonitor",
    "make_callable",
    "ManagedProcessResult",
    "decode_work_message",
    "prepare_call_arguments",
    "execute_function_target",
    "execute_command_target",
    "serialize_result",
    "TaskSpec",
    "SpecSection",
    "LimitsSection",
    "RunnerSection",
    "IOSection",
    "StateSection",
    "validate_taskspec",
    "format_tid",
    "parse_tid",
]

_LAZY_EXPORTS = {
    "Consumer": ("weft.core.tasks", "Consumer"),
    "Observer": ("weft.core.tasks", "Observer"),
    "SelectiveConsumer": ("weft.core.tasks", "SelectiveConsumer"),
    "Monitor": ("weft.core.tasks", "Monitor"),
    "TaskRunner": ("weft.core.tasks.runner", "TaskRunner"),
    "Manager": ("weft.core.manager", "Manager"),
    "launch_task_process": ("weft.core.launcher", "launch_task_process"),
    "ResourceMonitor": ("weft.core.resource_monitor", "ResourceMonitor"),
    "PsutilResourceMonitor": (
        "weft.core.resource_monitor",
        "PsutilResourceMonitor",
    ),
    "BaseResourceMonitor": ("weft.core.resource_monitor", "BaseResourceMonitor"),
    "make_callable": ("weft.core.callable", "make_callable"),
    "ManagedProcessResult": ("weft.core.callable", "ManagedProcessResult"),
    "decode_work_message": ("weft.core.targets", "decode_work_message"),
    "prepare_call_arguments": ("weft.core.targets", "prepare_call_arguments"),
    "execute_function_target": ("weft.core.targets", "execute_function_target"),
    "execute_command_target": ("weft.core.targets", "execute_command_target"),
    "serialize_result": ("weft.core.targets", "serialize_result"),
    "TaskSpec": ("weft.core.taskspec", "TaskSpec"),
    "SpecSection": ("weft.core.taskspec", "SpecSection"),
    "LimitsSection": ("weft.core.taskspec", "LimitsSection"),
    "RunnerSection": ("weft.core.taskspec", "RunnerSection"),
    "IOSection": ("weft.core.taskspec", "IOSection"),
    "StateSection": ("weft.core.taskspec", "StateSection"),
    "validate_taskspec": ("weft.core.taskspec", "validate_taskspec"),
    "format_tid": ("weft.helpers", "format_tid"),
    "parse_tid": ("weft.helpers", "parse_tid"),
}


def __getattr__(name: str) -> Any:
    """Load a public core export on first access."""

    try:
        module_name, attribute_name = _LAZY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Return eager and lazy public names."""

    return sorted({*globals(), *__all__})
