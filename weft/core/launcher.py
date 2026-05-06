"""Utilities for launching task processes.

Spec references:
- docs/specifications/03-Manager_Architecture.md [MA-1.2], [MA-3]
"""

from __future__ import annotations

import importlib
import json
import multiprocessing
import os
import signal
import sys
import time
from multiprocessing.process import BaseProcess
from typing import Any, cast

from simplebroker import BrokerTarget
from weft._constants import TASK_PROCESS_POLL_INTERVAL, TERMINAL_TASK_STATUSES

from .taskspec import TaskSpec, apply_bundle_root_to_taskspec_payload


def _load_task_class(path: str) -> type[Any]:
    module_name, class_name = path.rsplit(".", 1)
    module = importlib.import_module(module_name)
    return cast(type[Any], getattr(module, class_name))


def _backend_needs_process_hard_exit(db_path: BrokerTarget | str) -> bool:
    """Return whether backend helper threads can keep a finished task alive."""
    backend_name = getattr(db_path, "backend_name", None)
    if isinstance(backend_name, str) and backend_name == "postgres":
        return True
    return str(db_path).startswith(("postgresql://", "postgres://"))


def _is_foreground_serve_task(task: Any) -> bool:
    taskspec = getattr(task, "taskspec", None)
    metadata = getattr(taskspec, "metadata", None)
    return isinstance(metadata, dict) and metadata.get("foreground_serve") is True


def _parent_process_changed(initial_parent_pid: int) -> bool:
    current_parent_pid = os.getppid()
    return initial_parent_pid > 1 and current_parent_pid != initial_parent_pid


def _request_parent_loss_shutdown(task: Any) -> None:
    handler = getattr(task, "handle_termination_signal", None)
    if callable(handler):
        handler(signal.SIGTERM)
        return
    if hasattr(task, "should_stop"):
        task.should_stop = True


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
    initial_parent_pid = os.getppid()
    stop_with_parent = _is_foreground_serve_task(task)

    try:
        while True:
            if stop_with_parent and _parent_process_changed(initial_parent_pid):
                _request_parent_loss_shutdown(task)
            task.process_once()
            status = task.taskspec.state.status
            if status in TERMINAL_TASK_STATUSES or task.should_stop:
                break
            wait_for_activity = getattr(task, "wait_for_activity", None)
            if callable(wait_for_activity):
                wait_for_activity(timeout=poll_interval)
            else:
                time.sleep(poll_interval)
    finally:
        stop = getattr(task, "stop", None)
        if callable(stop):
            stop(join=False)
        else:
            task.cleanup()

    if task.__class__.__module__.startswith(
        "weft."
    ) and _backend_needs_process_hard_exit(db_path):
        os._exit(0)


def launch_task_process(
    task_cls: type[Any],
    db_path: BrokerTarget | str,
    spec: TaskSpec,
    *,
    config: dict[str, Any] | None = None,
    poll_interval: float = TASK_PROCESS_POLL_INTERVAL,
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
