"""Utilities for launching task processes.

Spec references:
- docs/specifications/03-Manager_Architecture.md [MA-1.2], [MA-3]
"""

from __future__ import annotations

import importlib
import json
import multiprocessing
import signal
import sys
import time
from multiprocessing.process import BaseProcess
from typing import Any, cast

from simplebroker import BrokerTarget

from .taskspec import TaskSpec, apply_bundle_root_to_taskspec_payload

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
    db_path: BrokerTarget | str,
    spec_json: str,
    config: dict[str, Any] | None,
    poll_interval: float,
) -> None:
    task_cls = _load_task_class(task_cls_path)
    spec = TaskSpec.model_validate_json(spec_json)
    task = task_cls(db_path, spec, config=config)
    _install_signal_handlers(task)

    try:
        while True:
            task.process_once()
            status = task.taskspec.state.status
            if status in TERMINAL_STATES or task.should_stop:
                break
            time.sleep(poll_interval)
    finally:
        stop = getattr(task, "stop", None)
        if callable(stop):
            stop(join=False)
        else:
            task.cleanup()


def launch_task_process(
    task_cls: type[Any],
    db_path: BrokerTarget | str,
    spec: TaskSpec,
    *,
    config: dict[str, Any] | None = None,
    poll_interval: float = 0.05,
) -> BaseProcess:
    """Launch *task_cls* in a new spawn-process and return the Process object.

    Spec: [MA-1.2], [MA-3]
    """

    ctx = multiprocessing.get_context("spawn")
    ctx.set_executable(sys.executable)
    task_cls_path = f"{task_cls.__module__}.{task_cls.__qualname__}"
    process = ctx.Process(
        target=_task_process_entry,
        args=(
            task_cls_path,
            db_path,
            json.dumps(
                apply_bundle_root_to_taskspec_payload(
                    spec.model_dump(mode="json"),
                    spec.get_bundle_root(),
                )
            ),
            config,
            poll_interval,
        ),
        daemon=False,
    )
    process.start()
    return process


__all__ = ["launch_task_process"]


def _install_signal_handlers(task: Any) -> None:
    """Install signal handlers for spawned task processes."""

    def _handle_signal(signum: int, _frame: Any) -> None:
        handler = getattr(task, "handle_termination_signal", None)
        if callable(handler):
            handler(signum)
            return
        if hasattr(task, "should_stop"):
            task.should_stop = True

    candidate_signals = [signal.SIGTERM, signal.SIGINT]
    sigusr1 = getattr(signal, "SIGUSR1", None)
    if sigusr1 is not None:
        candidate_signals.append(sigusr1)

    for signum in candidate_signals:
        try:
            signal.signal(signum, _handle_signal)
        except (OSError, ValueError):  # pragma: no cover - platform/thread guard
            continue
