"""Resource monitoring utilities for task execution.

Implements monitoring and limit checks from
docs/specifications/06-Resource_Management.md [RM-0] – [RM-5.1]. The
process tree is enumerated by ``weft.core.process_tree``, whose cost follows
the task's own tree rather than the host's process count, and
:func:`sample_delay` is the one sampling clock every runner loop uses.
"""

from __future__ import annotations

import logging
import sys
import time
from abc import ABC, abstractmethod
from importlib import import_module
from typing import Any

import psutil

from weft._constants import DEFAULT_POLLING_INTERVAL, RESOURCE_LIMIT_FIELDS
from weft.core.process_tree import descendant_processes
from weft.ext import ResourceMetrics

logger = logging.getLogger(__name__)

PsutilProcess = Any


def limits_configured(limits: Any | None) -> bool:
    """Return whether ``limits`` sets any resource limit (Spec: [RM-5.1])."""
    return limits is not None and any(
        getattr(limits, name, None) is not None for name in RESOURCE_LIMIT_FIELDS
    )


def sample_delay(limits: Any | None, *, interval: float, elapsed: float) -> float:
    """Return the delay from one resource sample to the next.

    A configured limit is checked on every sample, so the monitor samples
    every ``interval``. Without one, samples serve only the reported metrics:
    the delay equals the time already monitored, never less than
    ``interval``, so samples fall at one, two, four, ... intervals. The last
    sample before the runtime exits therefore lies in the latter half of the
    run, and the number of samples grows with the logarithm of the run's
    length instead of linearly (the doubling schedule for an unknown end).

    Args:
        limits: The task's limits section, or ``None``.
        interval: The monitor polling interval in seconds.
        elapsed: Seconds since this monitoring began.

    Spec: [RM-5.1]
    """
    if limits_configured(limits):
        return interval
    return max(interval, elapsed)


class BaseResourceMonitor(ABC):
    """Abstract base class for resource monitors (Spec: [RM-5])."""

    def __init__(
        self,
        *,
        limits: Any | None = None,
        polling_interval: float = DEFAULT_POLLING_INTERVAL,
    ) -> None:
        self._pid: int | None = None
        self.limits = limits
        self.polling_interval = polling_interval

    @abstractmethod
    def start(self, pid: int) -> None:
        """Begin monitoring the given process id (Spec: [RM-5.1])."""

    @abstractmethod
    def stop(self) -> None:
        """Stop monitoring and release resources (Spec: [RM-5.1])."""

    @abstractmethod
    def snapshot(self) -> ResourceMetrics:
        """Return the current resource utilisation snapshot (Spec: [RM-5.1])."""

    @abstractmethod
    def check_limits(self) -> tuple[bool, str | None]:
        """Compare current metrics with the configured limits.

        Spec: [RM-1], [RM-2], [RM-3], [RM-4]
        """

    @abstractmethod
    def last_metrics(self) -> ResourceMetrics | None:
        """Return the most recent metric snapshot, if available (Spec: [RM-5.1])."""


class ResourceMonitor(BaseResourceMonitor):
    """Default psutil-based resource monitor (Spec: [RM-5.1])."""

    def __init__(
        self,
        *,
        limits: Any | None = None,
        polling_interval: float = DEFAULT_POLLING_INTERVAL,
    ) -> None:
        super().__init__(
            limits=limits,
            polling_interval=polling_interval,
        )
        self._process: PsutilProcess | None = None
        self.history: list[ResourceMetrics] = []
        self.max_history = 100
        self._last_metrics: ResourceMetrics | None = None
        self._last_cpu_sample_at: float | None = None
        self._last_cpu_times: dict[int, float] = {}

    def start(self, pid: int) -> None:
        self._process = psutil.Process(pid)
        self._pid = pid
        self._last_cpu_sample_at = time.monotonic()
        self._last_cpu_times = self._snapshot_cpu_times(self._process_tree())

    def stop(self) -> None:
        self._process = None
        self._pid = None
        self.history.clear()
        self._last_cpu_sample_at = None
        self._last_cpu_times.clear()

    def _process_tree(self) -> list[PsutilProcess]:
        if self._process is None:
            raise RuntimeError("Resource monitor not started")

        processes: list[PsutilProcess] = []
        seen_pids: set[int] = set()

        def _add_process(process: PsutilProcess) -> None:
            try:
                pid = int(process.pid)
            except (AttributeError, TypeError, ValueError):
                return
            if pid in seen_pids:
                return
            seen_pids.add(pid)
            processes.append(process)

        try:
            _add_process(self._process)
            for child in descendant_processes(self._process):
                _add_process(child)
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            return []

        return processes

    @staticmethod
    def _snapshot_cpu_times(processes: list[PsutilProcess]) -> dict[int, float]:
        cpu_times: dict[int, float] = {}
        for process in processes:
            try:
                pid = int(process.pid)
            except (AttributeError, TypeError, ValueError):
                continue
            try:
                times = process.cpu_times()
            except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
                continue
            cpu_times[pid] = float(times.user + times.system)
        return cpu_times

    @staticmethod
    def _open_file_count(process: PsutilProcess) -> int:
        try:
            return int(process.num_fds())
        except (AttributeError, psutil.AccessDenied):
            try:
                return int(process.num_handles())
            except (AttributeError, psutil.AccessDenied):
                try:
                    return len(process.open_files())
                except (psutil.AccessDenied, AttributeError):
                    return 0

    @staticmethod
    def _connection_count(process: PsutilProcess) -> int:
        try:
            return len(process.net_connections())
        except (
            psutil.NoSuchProcess,
            psutil.ZombieProcess,
            psutil.AccessDenied,
            AttributeError,
        ):
            return 0

    def snapshot(self) -> ResourceMetrics:
        processes = self._process_tree()
        if not processes:
            raise RuntimeError("Resource monitor not started")
        now = time.monotonic()
        current_cpu_times = self._snapshot_cpu_times(processes)

        memory_mb = 0.0
        measured_processes = 0
        open_files = 0
        connections = 0
        for process in processes:
            try:
                memory_mb += process.memory_info().rss / (1024 * 1024)
            except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
                continue
            measured_processes += 1

            open_files += self._open_file_count(process)
            connections += self._connection_count(process)

        if measured_processes == 0:
            raise RuntimeError(
                "Resource monitor process tree disappeared during sample"
            )

        cpu_percent = 0.0
        if self._last_cpu_sample_at is not None:
            elapsed = now - self._last_cpu_sample_at
            if elapsed > 0:
                total_cpu_delta = 0.0
                for pid, current_total in current_cpu_times.items():
                    previous_total = self._last_cpu_times.get(pid, 0.0)
                    total_cpu_delta += max(0.0, current_total - previous_total)
                cpu_percent = (total_cpu_delta / elapsed) * 100.0
        self._last_cpu_sample_at = now
        self._last_cpu_times = current_cpu_times

        metrics = ResourceMetrics(
            timestamp=time.time_ns(),
            memory_mb=memory_mb,
            cpu_percent=cpu_percent,
            open_files=open_files,
            connections=connections,
        )
        self.history.append(metrics)
        if len(self.history) > self.max_history:
            self.history.pop(0)
        self._last_metrics = metrics
        return metrics

    def _get_average_cpu(self, samples: int = 10) -> float:
        if not self.history:
            return 0.0
        window = self.history[-samples:]
        return sum(metric.cpu_percent for metric in window) / len(window)

    def _is_sustained_cpu_violation(self) -> bool:
        if not self.limits or not getattr(self.limits, "cpu_percent", None):
            return False
        if len(self.history) < 5:
            return False
        limit = getattr(self.limits, "cpu_percent", None)
        if limit is None:
            return False
        recent_samples = self.history[-5:]
        violations = sum(1 for metric in recent_samples if metric.cpu_percent > limit)
        return violations >= 4

    def check_limits(self) -> tuple[bool, str | None]:
        if self.limits is None:
            return True, None

        try:
            metrics = self.snapshot()
        except RuntimeError:  # pragma: no cover - process may have exited
            return True, None

        violations: list[str] = []

        memory_limit = getattr(self.limits, "memory_mb", None)
        if memory_limit and metrics.memory_mb > memory_limit:
            violations.append(f"Memory {metrics.memory_mb:.1f}MB > {memory_limit}MB")

        cpu_limit = getattr(self.limits, "cpu_percent", None)
        if cpu_limit and self._is_sustained_cpu_violation():
            avg_cpu = self._get_average_cpu(samples=5)
            violations.append(f"CPU {avg_cpu:.1f}% > {cpu_limit}% (sustained)")

        fd_limit = getattr(self.limits, "max_fds", None)
        if fd_limit and metrics.open_files > fd_limit:
            descriptor_type = "handles" if sys.platform == "win32" else "files"
            violations.append(
                f"Open {descriptor_type} {metrics.open_files} > {fd_limit}"
            )

        conn_limit = getattr(self.limits, "max_connections", None)
        if conn_limit and metrics.connections > conn_limit:
            violations.append(f"Connections {metrics.connections} > {conn_limit}")

        if violations:
            return False, "; ".join(violations)
        return True, None

    def last_metrics(self) -> ResourceMetrics | None:
        return self._last_metrics


def load_resource_monitor(
    class_path: str,
    *,
    limits: Any | None = None,
    polling_interval: float | None = None,
) -> BaseResourceMonitor:
    """Load a monitor implementation by dotted path (Spec: [RM-5])."""
    module_name, class_name = class_path.rsplit(".", 1)
    module = import_module(module_name)
    monitor_cls = getattr(module, class_name)
    if not isinstance(monitor_cls, type) or not issubclass(
        monitor_cls, BaseResourceMonitor
    ):
        raise TypeError(f"{class_path} must reference a BaseResourceMonitor subclass")
    return monitor_cls(
        limits=limits,
        polling_interval=(
            DEFAULT_POLLING_INTERVAL if polling_interval is None else polling_interval
        ),
    )


__all__ = [
    "BaseResourceMonitor",
    "ResourceMetrics",
    "ResourceMonitor",
    "limits_configured",
    "load_resource_monitor",
    "sample_delay",
]
