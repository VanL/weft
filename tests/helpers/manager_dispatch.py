"""Real launch publication barriers shared by Manager dispatch contract tests."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import partial
from multiprocessing.process import BaseProcess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import psutil
import pytest

import weft.core.manager as manager_mod
from weft._constants import MANAGER_CHILD_LAUNCH_WORKER_LANE
from weft.core.manager import Manager, _ManagerChildLaunchResult
from weft.core.tasks.multiqueue_watcher import QueueMode, QueueRuntimeConfig
from weft.core.tasks.service import ServiceWorkerEvent
from weft.core.taskspec import TaskSpec


def join_waiting_processes(processes: list[BaseProcess]) -> None:
    """Close this fixture's real process producers before broker teardown."""
    for process in processes:
        process.join(timeout=30.0)
        if process.is_alive():
            process.terminate()
            process.join(timeout=10.0)
        if process.is_alive():
            process.kill()
            process.join(timeout=10.0)


def retire_dispatch_workers(manager: Manager, releases: list[Path]) -> None:
    """Stop producers before taking a final inventory of their real children."""
    for release in releases:
        release.touch()
    with manager._worker_lock:
        workers = tuple(manager._worker_threads)
    for worker in workers:
        worker.join(timeout=30.0)
    if any(worker.is_alive() for worker in workers):
        manager.stop(join=False)
        manager.cleanup()
        for worker in workers:
            worker.join(timeout=30.0)
    assert not [worker.name for worker in workers if worker.is_alive()]


def observe_child_exit(manager: Manager, tid: str, *, timeout: float = 10.0) -> None:
    """Deliver a real child's exit sentinel the way a Manager reactor turn does.

    Tests that place a real process in the child table register its sentinel
    here (registration is idempotent), wait for the adapter to report the
    exit, and drain it, so cleanup sees the exit evidence a turn would.

    Spec: docs/specifications/03-Manager_Architecture.md [MA-1.6a]
    """
    child = manager._child_processes[tid]
    manager._register_child_sentinel(tid, child)
    deadline = time.monotonic() + timeout
    while child.sentinel_observed_ns <= 0:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"exit sentinel of child {tid} was not observed")
        manager._child_sentinel_adapter.wait(remaining)
        manager._drain_child_sentinel_events()


@dataclass
class SettledLaunches:
    """Actual submission and publication evidence for same-turn dispatch tests."""

    submitted: list[str] = field(default_factory=list)
    reserved: list[int] = field(default_factory=list)
    applied: list[str] = field(default_factory=list)
    publication_timeouts: list[str] = field(default_factory=list)
    processes: list[BaseProcess] = field(default_factory=list)

    def create_process(
        self, create: Callable[..., BaseProcess], *args: Any, **kwargs: Any
    ) -> BaseProcess:
        """Retain each actual child for cleanup after its worker retires."""
        process = create(*args, **kwargs)
        self.processes.append(process)
        return process


@contextmanager
def settled_launches(
    manager: Manager,
    monkeypatch: pytest.MonkeyPatch,
    releases: list[Path],
) -> Iterator[SettledLaunches]:
    """Publish real receipts before returning to the inherited result drain."""
    evidence = SettledLaunches()
    events: dict[str, threading.Event] = {}
    lock = threading.Lock()
    real_publish = manager._publish_worker_result
    real_launch = manager._launch_child_task
    real_apply = manager._handle_child_launch_result
    real_create = manager_mod.launch_task_process
    real_fetch = manager._fetch_next_message

    def fetch(config: QueueRuntimeConfig) -> tuple[str, int] | None:
        message = real_fetch(config)
        if message is not None and config.mode is QueueMode.RESERVE:
            evidence.reserved.append(message[1])
        return message

    def publish(
        lane: str, *, value: Any = None, error: BaseException | None = None
    ) -> bool:
        accepted = real_publish(lane, value=value, error=error)
        if (
            accepted
            and lane == MANAGER_CHILD_LAUNCH_WORKER_LANE
            and isinstance(value, ServiceWorkerEvent)
            and value.kind in {"result", "error"}
        ):
            tid = value.request_id
            with lock:
                event = events.setdefault(tid, threading.Event())
            event.set()
        return accepted

    def launch(spec: TaskSpec, message: Any | None, **kwargs: Any) -> bool:
        assert spec.tid is not None
        with lock:
            event = events.setdefault(spec.tid, threading.Event())
        accepted = real_launch(spec, message, **kwargs)
        if accepted:
            evidence.submitted.append(spec.tid)
            # Handler exceptions can be caught by delivery. Record, then assert
            # outside the handler so a failed test barrier cannot disappear.
            if not event.wait(timeout=30.0):
                evidence.publication_timeouts.append(spec.tid)
        return accepted

    def apply(result: _ManagerChildLaunchResult) -> None:
        real_apply(result)
        tid = result.request.child_spec.tid
        assert tid is not None
        evidence.applied.append(tid)

    with monkeypatch.context() as patch:
        patch.setattr(manager, "_fetch_next_message", fetch)
        patch.setattr(
            manager_mod,
            "launch_task_process",
            partial(evidence.create_process, real_create),
        )
        patch.setattr(manager, "_publish_worker_result", publish)
        patch.setattr(manager, "_launch_child_task", launch)
        patch.setattr(manager, "_handle_child_launch_result", apply)
        try:
            yield evidence
        finally:
            retire_dispatch_workers(manager, releases)
            join_waiting_processes(evidence.processes)


def dispatch_witness_body(ready: Path, release: Path) -> str:
    """Use the existing real target as the child-entry and release barrier."""
    return json.dumps(
        {
            "spec": {
                "type": "function",
                "function_target": (
                    "tests.tasks.sample_targets:signal_ready_and_wait_for_release"
                ),
            },
            "inbox_message": {
                "kwargs": {
                    "ready_path": str(ready),
                    "release_path": str(release),
                }
            },
        }
    )


PINNED_HOST_MEMORY_BYTES = 64 * 2**30
"""Total host memory reported while a test controls the memory gate [MA-1.8]."""


def pin_host_memory(
    monkeypatch: pytest.MonkeyPatch,
    *,
    used: int = 0,
    total: int = PINNED_HOST_MEMORY_BYTES,
) -> None:
    """Control the host memory every Manager observation reads [MA-1.8].

    Host memory is an external dependency shared with every other process on
    the machine. The patch replaces psutil's reading only, so the Manager's
    own observation, memory gate, startup window and refusal stay real.
    """
    monkeypatch.setattr(
        psutil,
        "virtual_memory",
        lambda: SimpleNamespace(total=total, available=total - used),
    )
