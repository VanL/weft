"""Real launch publication barriers shared by Manager dispatch contract tests."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import partial
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import Any

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


@dataclass
class SettledLaunches:
    """Actual submission and publication evidence for same-turn dispatch tests."""

    submitted: list[str] = field(default_factory=list)
    reserved: list[int] = field(default_factory=list)
    applied: list[str] = field(default_factory=list)
    publication_timeouts: list[str] = field(default_factory=list)
    maximum_creators: int = 0
    processes: list[BaseProcess] = field(default_factory=list)
    _creators: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def create_process(
        self, create: Callable[..., BaseProcess], *args: Any, **kwargs: Any
    ) -> BaseProcess:
        """Observe actual concurrent creation and retain children for cleanup."""
        with self._lock:
            self._creators += 1
            self.maximum_creators = max(self.maximum_creators, self._creators)
        try:
            process = create(*args, **kwargs)
            with self._lock:
                self.processes.append(process)
            return process
        finally:
            with self._lock:
                self._creators -= 1


@contextmanager
def settled_launches(
    manager: Manager,
    monkeypatch: pytest.MonkeyPatch,
    releases: list[Path],
    *,
    after_receipt: Callable[[str], None] | None = None,
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
        if after_receipt is not None:
            after_receipt(tid)

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
