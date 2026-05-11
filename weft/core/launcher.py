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
import threading
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


def _is_foreground_serve_task(task: Any) -> bool:
    taskspec = getattr(task, "taskspec", None)
    metadata = getattr(taskspec, "metadata", None)
    return isinstance(metadata, dict) and metadata.get("foreground_serve") is True


def _parent_process_changed(initial_parent_pid: int) -> bool:
    current_parent_pid = os.getppid()
    return initial_parent_pid > 1 and current_parent_pid != initial_parent_pid


def _wake_task_stop_event(task: Any) -> None:
    stop_event = getattr(task, "_stop_event", None)
    if stop_event is not None and hasattr(stop_event, "set"):
        stop_event.set()


def _request_parent_loss_shutdown(task: Any) -> None:
    handler = getattr(task, "handle_termination_signal", None)
    if callable(handler):
        handler(signal.SIGTERM)
        _wake_task_stop_event(task)
        return
    if hasattr(task, "should_stop"):
        task.should_stop = True
    _wake_task_stop_event(task)


def _start_parent_loss_watcher(
    task: Any,
    *,
    initial_parent_pid: int,
    poll_interval: float,
) -> threading.Event:
    parent_lost = threading.Event()
    wake_interval = min(max(poll_interval, 0.05), 0.2)

    def _watch_parent() -> None:
        while not parent_lost.is_set():
            if _parent_process_changed(initial_parent_pid):
                parent_lost.set()
                _wake_task_stop_event(task)
                return
            time.sleep(wake_interval)

    threading.Thread(target=_watch_parent, daemon=True).start()
    return parent_lost


def _redirect_standard_streams_to_devnull() -> None:
    """Prevent spawned task children from inheriting manager stdio pipes."""

    devnull_fd = os.open(os.devnull, os.O_RDWR)
    try:
        for fd in (0, 1, 2):
            try:
                os.dup2(devnull_fd, fd)
            except OSError:  # pragma: no cover - platform fd edge
                continue
    finally:
        if devnull_fd > 2:
            os.close(devnull_fd)


def _task_process_entry(
    task_cls_path: str,
    db_path: BrokerTarget | str,
    spec_json: str,
    config: dict[str, Any] | None,
    poll_interval: float,
    hard_exit_on_return: bool = False,
    detach_stdio: bool = False,
) -> None:
    if detach_stdio:
        _redirect_standard_streams_to_devnull()

    task_cls = _load_task_class(task_cls_path)
    spec = TaskSpec.model_validate_json(spec_json)
    task = task_cls(db_path, spec, config=config)
    _install_signal_handlers(task)
    initial_parent_pid = os.getppid()
    stop_with_parent = _is_foreground_serve_task(task)
    parent_lost = (
        _start_parent_loss_watcher(
            task,
            initial_parent_pid=initial_parent_pid,
            poll_interval=poll_interval,
        )
        if stop_with_parent
        else None
    )

    try:
        while True:
            if stop_with_parent and (
                (parent_lost is not None and parent_lost.is_set())
                or _parent_process_changed(initial_parent_pid)
            ):
                _request_parent_loss_shutdown(task)
            task.process_once()
            status = task.taskspec.state.status
            if status in TERMINAL_TASK_STATUSES or task.should_stop:
                break
            wait_timeout = poll_interval
            next_wait_timeout = getattr(task, "next_wait_timeout", None)
            if callable(next_wait_timeout):
                candidate_timeout = next_wait_timeout()
                if candidate_timeout is not None:
                    wait_timeout = max(0.0, float(candidate_timeout))
            wait_for_activity = getattr(task, "wait_for_activity", None)
            if callable(wait_for_activity):
                wait_for_activity(timeout=wait_timeout)
            else:
                time.sleep(wait_timeout)
    finally:
        stop = getattr(task, "stop", None)
        if callable(stop):
            stop(join=False)
        else:
            task.cleanup()

    if hard_exit_on_return and os.name != "nt":
        os._exit(0)


def launch_task_process(
    task_cls: type[Any],
    db_path: BrokerTarget | str,
    spec: TaskSpec,
    *,
    config: dict[str, Any] | None = None,
    poll_interval: float = TASK_PROCESS_POLL_INTERVAL,
    detach_stdio: bool = True,
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
            True,
            detach_stdio,
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
            _wake_task_stop_event(task)
            return
        if hasattr(task, "should_stop"):
            task.should_stop = True
        _wake_task_stop_event(task)

    candidate_signals = [signal.SIGTERM, signal.SIGINT]
    sigusr1 = getattr(signal, "SIGUSR1", None)
    if sigusr1 is not None:
        candidate_signals.append(sigusr1)

    for signum in candidate_signals:
        try:
            signal.signal(signum, _handle_signal)
        except (OSError, ValueError):  # pragma: no cover - platform/thread guard
            continue
