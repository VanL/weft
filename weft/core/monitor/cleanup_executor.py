"""Bounded local executor for TaskMonitor runtime cleanup jobs.

This module is intentionally policy-free. It runs caller-provided cleanup
callables under a small worker cap and returns deterministic result ordering.

Spec references:
- docs/specifications/01-Core_Components.md [CC-2.3]
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.13]
"""

from __future__ import annotations

import queue
import threading
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

CleanupJobKind = Literal["terminal_control", "reserved", "dead_tid"]
CleanupJobResource = Literal["task_queue_delete"]


@dataclass(frozen=True, slots=True)
class CleanupJob:
    """One cleanup job selected by TaskMonitor policy code."""

    kind: CleanupJobKind
    identity: str
    run: Callable[[], Any]
    resource: CleanupJobResource = "task_queue_delete"


@dataclass(frozen=True, slots=True)
class CleanupJobResult:
    """Result wrapper for one cleanup job attempt."""

    job_index: int
    kind: CleanupJobKind
    identity: str
    value: Any | None = None
    success: bool = True
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CleanupExecutorResult:
    """Aggregate result from one bounded cleanup executor run."""

    results: tuple[CleanupJobResult, ...] = ()
    workers_configured: int = 1
    jobs_started: int = 0
    jobs_completed: int = 0
    jobs_pending: int = 0
    jobs_by_kind: Mapping[str, int] | None = None
    jobs_pending_by_kind: Mapping[str, int] | None = None

    @property
    def success(self) -> bool:
        """Return whether every started job completed without an exception."""

        return all(result.success for result in self.results)


def run_cleanup_jobs(
    jobs: Sequence[CleanupJob],
    *,
    max_workers: int,
    deadline_reached: Callable[[], bool],
) -> CleanupExecutorResult:
    """Run cleanup jobs under a bounded worker cap.

    The calling thread counts as one worker. With ``max_workers=3`` this
    function starts at most two helper threads and runs one job inline.

    Spec: [CC-2.3], [MF-5]
    """

    if max_workers <= 0:
        raise ValueError("max_workers must be positive")
    if not jobs:
        return CleanupExecutorResult(
            workers_configured=max_workers,
            jobs_by_kind={},
            jobs_pending_by_kind={},
        )

    work_queue: queue.Queue[tuple[int, CleanupJob]] = queue.Queue()
    for index, job in enumerate(jobs):
        work_queue.put((index, job))

    result_lock = threading.Lock()
    results: list[CleanupJobResult] = []
    jobs_started = 0
    jobs_completed = 0
    jobs_pending = 0
    jobs_pending_by_kind: Counter[str] = Counter()

    def worker() -> None:
        nonlocal jobs_started
        nonlocal jobs_completed
        nonlocal jobs_pending
        while True:
            try:
                index, job = work_queue.get_nowait()
            except queue.Empty:
                return
            if deadline_reached():
                with result_lock:
                    jobs_pending += 1
                    jobs_pending_by_kind[job.kind] += 1
                continue
            with result_lock:
                jobs_started += 1
            try:
                value = job.run()
            except Exception as exc:  # pragma: no cover - exercised by tests
                result = CleanupJobResult(
                    job_index=index,
                    kind=job.kind,
                    identity=job.identity,
                    success=False,
                    errors=(str(exc),),
                )
            else:
                result = CleanupJobResult(
                    job_index=index,
                    kind=job.kind,
                    identity=job.identity,
                    value=value,
                )
            with result_lock:
                results.append(result)
                jobs_completed += 1

    helper_count = max(0, min(max_workers, len(jobs)) - 1)
    threads = [threading.Thread(target=worker) for _ in range(helper_count)]
    for thread in threads:
        thread.start()
    worker()
    for thread in threads:
        thread.join()

    while True:
        try:
            _index, pending_job = work_queue.get_nowait()
        except queue.Empty:
            break
        jobs_pending += 1
        jobs_pending_by_kind[pending_job.kind] += 1

    ordered_results = tuple(sorted(results, key=lambda result: result.job_index))
    jobs_by_kind: Counter[str] = Counter(str(result.kind) for result in ordered_results)
    return CleanupExecutorResult(
        results=ordered_results,
        workers_configured=max_workers,
        jobs_started=jobs_started,
        jobs_completed=jobs_completed,
        jobs_pending=jobs_pending,
        jobs_by_kind=dict(jobs_by_kind),
        jobs_pending_by_kind=dict(jobs_pending_by_kind),
    )


__all__ = [
    "CleanupExecutorResult",
    "CleanupJob",
    "CleanupJobKind",
    "CleanupJobResource",
    "CleanupJobResult",
    "run_cleanup_jobs",
]
