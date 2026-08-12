"""Lazy public command facade.

Spec: docs/specifications/14-Python_API_Surfaces.md [PY-1], [PY-2].
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_command_exports: dict[str, tuple[str, str]] = {
    "cmd_init": ("weft.commands.init", "cmd_init"),
    "cmd_status": ("weft.commands.system", "cmd_status"),
    "cmd_result": ("weft.commands.result", "cmd_result"),
    "cmd_run": ("weft.commands.run", "cmd_run"),
    **{
        name: ("weft.commands.queue", name)
        for name in (
            "cmd_queue_read",
            "cmd_queue_write",
            "cmd_queue_peek",
            "cmd_queue_move",
            "cmd_queue_list",
            "cmd_queue_exists",
            "cmd_queue_stats",
            "cmd_queue_resolve",
            "cmd_queue_watch",
            "cmd_queue_delete",
            "cmd_queue_broadcast",
            "cmd_queue_alias_add",
            "cmd_queue_alias_list",
            "cmd_queue_alias_remove",
        )
    },
    **{
        name: ("weft.commands.specs", name)
        for name in (
            "cmd_spec_create",
            "cmd_spec_list",
            "cmd_spec_show",
            "cmd_spec_delete",
            "cmd_spec_validate",
            "cmd_spec_generate",
        )
    },
    **{
        name: ("weft.commands.tasks", name)
        for name in (
            "cmd_task_list",
            "cmd_task_status",
            "cmd_task_ping",
            "cmd_task_stop",
            "cmd_task_kill",
            "cmd_task_tid",
        )
    },
    "cmd_manager_start": ("weft.commands.manager", "cmd_manager_start"),
    "cmd_manager_serve": ("weft.commands.serve", "cmd_manager_serve"),
    "cmd_manager_stop": ("weft.commands.manager", "cmd_manager_stop"),
    "cmd_manager_list": ("weft.commands.manager", "cmd_manager_list"),
    "cmd_manager_status": ("weft.commands.manager", "cmd_manager_status"),
    "cmd_system_tidy": ("weft.commands.tidy", "cmd_system_tidy"),
    "cmd_system_task_monitor": (
        "weft.commands.task_monitor",
        "cmd_system_task_monitor",
    ),
    "cmd_system_prune": ("weft.commands.prune", "cmd_system_prune"),
    "cmd_system_dump": ("weft.commands.dump", "cmd_system_dump"),
    "cmd_system_builtins": ("weft.commands.builtins", "cmd_system_builtins"),
    "cmd_system_load": ("weft.commands.load", "cmd_system_load"),
}

_type_names = (
    "BuiltinSpecRecord",
    "CommandStream",
    "EndpointResolution",
    "InitResult",
    "ManagerSnapshot",
    "QueueAliasRecord",
    "QueueBroadcastReceipt",
    "QueueDeleteReceipt",
    "QueueEntry",
    "QueueInfo",
    "QueueMoveResult",
    "QueueWriteReceipt",
    "RunExecutionResult",
    "RunSession",
    "RunSpecDescription",
    "ServiceSnapshot",
    "SpecMutationResult",
    "SpecRecord",
    "SpecValidationResult",
    "SubmittedTaskReceipt",
    "SystemDumpResult",
    "SystemLoadResult",
    "SystemPruneResult",
    "SystemStatusSnapshot",
    "SystemTidyResult",
    "TaskControlResult",
    "TaskEvent",
    "TaskPingResult",
    "TaskResult",
    "TaskSnapshot",
)
_type_exports = {name: ("weft.commands.types", name) for name in _type_names}
_type_exports.update(
    {
        "TaskMonitorConfig": ("weft.commands.task_monitor", "TaskMonitorConfig"),
        "TaskMonitorResult": ("weft.commands.task_monitor", "TaskMonitorResult"),
        "TaskMonitorRecord": ("weft.commands.task_monitor", "TaskMonitorRecord"),
        "TaskMonitorSummary": ("weft.commands.task_monitor", "TaskMonitorSummary"),
    }
)

_error_names = (
    "CommandError",
    "CommandExecutionError",
    "CommandTimeoutError",
    "CommandUsageError",
    "ControlRejected",
    "InvalidTID",
    "ManagerNotRunning",
    "ManagerStartFailed",
    "SpecNotFound",
    "SubmissionError",
    "SubmissionManagerError",
    "SubmissionValidationError",
    "TaskNotFound",
    "WeftError",
)
_error_exports = {name: ("weft._exceptions", name) for name in _error_names}

_exports = {**_command_exports, **_type_exports, **_error_exports}
__all__ = list(_exports)


def __getattr__(name: str) -> Any:
    """Resolve and cache one declared facade export."""

    target = _exports.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Return the declared facade inventory."""

    return sorted(set(globals()) | set(__all__))


assert set(_exports) == set(__all__)
