"""Utilities for launching task processes."""

from __future__ import annotations

import importlib
import multiprocessing
import time
from multiprocessing.process import BaseProcess
from typing import Any, cast

from .taskspec import TaskSpec

TERMINAL_STATES = {
    "completed",
    "failed",
    "timeout",
    "cancelled",
    "killed",
}


def _load_task_class(path: str) -> type[Any]:
    module_name, class_name = path.rsplit(".", 1)
    module = importlib.import_module(module_name)
    return cast(type[Any], getattr(module, class_name))


def _task_process_entry(
    task_cls_path: str,
    db_path: str,
    spec_json: str,
    config: dict[str, Any] | None,
    poll_interval: float,
) -> None:
    task_cls = _load_task_class(task_cls_path)
    spec = TaskSpec.model_validate_json(spec_json)
    task = task_cls(db_path, spec, config=config)

    try:
        while True:
            task.process_once()
            status = task.taskspec.state.status
            if status in TERMINAL_STATES or task.should_stop:
                break
            time.sleep(poll_interval)
    finally:
        task.cleanup()


def launch_task_process(
    task_cls: type[Any],
    db_path: str,
    spec: TaskSpec,
    *,
    config: dict[str, Any] | None = None,
    poll_interval: float = 0.05,
) -> BaseProcess:
    """Launch *task_cls* in a new spawn-process and return the Process object."""

    ctx = multiprocessing.get_context("spawn")
    task_cls_path = f"{task_cls.__module__}.{task_cls.__qualname__}"
    process = ctx.Process(
        target=_task_process_entry,
        args=(task_cls_path, db_path, spec.model_dump_json(), config, poll_interval),
        daemon=False,
    )
    process.start()
    return process


__all__ = ["launch_task_process"]
