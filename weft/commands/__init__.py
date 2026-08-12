"""Lazy public command facade.

Spec: docs/specifications/14-Python_API_Surfaces.md [PY-1], [PY-2].
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_COMMAND_EXPORTS: dict[str, tuple[str, str]] = {
    "cmd_init": ("weft.commands.init", "cmd_init"),
    "cmd_status": ("weft.commands.system", "cmd_status"),
    "cmd_result": ("weft.commands.result", "cmd_result"),
    "cmd_run": ("weft.commands.run", "execute_run"),
    **{
        name: ("weft.commands.queue", name)
        for name in (
            "cmd_queue_read", "cmd_queue_write", "cmd_queue_peek",
            "cmd_queue_move", "cmd_queue_list", "cmd_queue_exists",
            "cmd_queue_stats", "cmd_queue_resolve", "cmd_queue_watch",
            "cmd_queue_delete", "cmd_queue_broadcast", "cmd_queue_alias_add",
            "cmd_queue_alias_list", "cmd_queue_alias_remove",
        )
    },
    **{
        name: ("weft.commands.specs", name)
        for name in (
            "cmd_spec_create", "cmd_spec_list", "cmd_spec_show",
            "cmd_spec_delete", "cmd_spec_validate", "cmd_spec_generate",
        )
    },
    **{
        name: ("weft.commands.tasks", name)
        for name in (
            "cmd_task_list", "cmd_task_status", "cmd_task_ping",
            "cmd_task_stop", "cmd_task_kill", "cmd_task_tid",
        )
    },
    "cmd_manager_start": ("weft.commands.manager", "cmd_manager_start"),
    "cmd_manager_serve": ("weft.commands.serve", "cmd_manager_serve"),
    "cmd_manager_stop": ("weft.commands.manager", "cmd_manager_stop"),
    "cmd_manager_list": ("weft.commands.manager", "cmd_manager_list"),
    "cmd_manager_status": ("weft.commands.manager", "cmd_manager_status"),
    "cmd_system_tidy": ("weft.commands.tidy", "cmd_system_tidy"),
    "cmd_system_task_monitor": ("weft.commands.task_monitor", "cmd_system_task_monitor"),
    "cmd_system_prune": ("weft.commands.prune", "cmd_system_prune"),
    "cmd_system_dump": ("weft.commands.dump", "cmd_system_dump"),
    "cmd_system_builtins": ("weft.commands.builtins", "cmd_system_builtins"),
    "cmd_system_load": ("weft.commands.load", "cmd_system_load"),
}

_TYPE_NAMES = (
    "BuiltinSpecRecord", "CommandStream", "EndpointResolution", "InitResult",
    "ManagerSnapshot", "QueueAliasRecord", "QueueBroadcastReceipt",
    "QueueDeleteReceipt", "QueueEntry", "QueueInfo", "QueueMoveResult",
    "QueueWriteReceipt", "RunExecutionResult", "RunSession",
    "RunSpecDescription", "ServiceSnapshot", "SpecMutationResult", "SpecRecord",
    "SpecValidationResult", "SubmittedTaskReceipt", "SystemDumpResult",
    "SystemLoadResult", "SystemPruneResult", "SystemStatusSnapshot",
    "SystemTidyResult", "TaskControlResult", "TaskEvent", "TaskPingResult",
    "TaskResult", "TaskSnapshot",
)
_TYPE_EXPORTS = {name: ("weft.commands.types", name) for name in _TYPE_NAMES}
_TYPE_EXPORTS.update(
    {
        "TaskMonitorConfig": ("weft.commands.task_monitor", "TaskMonitorConfig"),
        "TaskMonitorResult": ("weft.commands.task_monitor", "TaskMonitorResult"),
        "TaskMonitorRecord": ("weft.commands.task_monitor", "TaskMonitorRecord"),
        "TaskMonitorSummary": ("weft.commands.task_monitor", "TaskMonitorSummary"),
    }
)

_ERROR_NAMES = (
    "CommandError", "CommandExecutionError", "CommandTimeoutError",
    "CommandUsageError", "ControlRejected", "InvalidTID", "ManagerNotRunning",
    "ManagerStartFailed", "SpecNotFound", "SubmissionError",
    "SubmissionManagerError", "SubmissionValidationError", "TaskNotFound",
    "WeftError",
)
_ERROR_EXPORTS = {name: ("weft._exceptions", name) for name in _ERROR_NAMES}

_EXPORTS = {**_COMMAND_EXPORTS, **_TYPE_EXPORTS, **_ERROR_EXPORTS}
__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    """Resolve and cache one declared facade export."""

    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Return the declared facade inventory."""

    return sorted(set(globals()) | set(__all__))


assert set(_EXPORTS) == set(__all__)
