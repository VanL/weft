"""Resource monitoring utilities for task execution.

Implements monitoring and limit checks from
docs/specifications/06-Resource_Management.md [RM-0] – [RM-5.1].
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from importlib import import_module
from typing import Any

_psutil_module: Any | None
try:  # pragma: no cover - psutil optional at import time
    import psutil as _psutil_module
except Exception:  # pragma: no cover
    _psutil_module = None

psutil: Any | None = _psutil_module

PsutilProcess = Any


@dataclass(slots=True)
class ResourceMetrics:
    """Snapshot of process resource utilisation (Spec: [RM-5], [RM-5.1])."""

    timestamp: int
    memory_mb: float
    cpu_percent: float
    open_files: int
    connections: int


class BaseResourceMonitor(ABC):
    """Abstract base class for resource monitors (Spec: [RM-5])."""

    def __init__(self) -> None:
        self._pid: int | None = None

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
    def check_limits(self, limits: Any) -> tuple[bool, str | None]:
        """Return (ok, message) after comparing metrics with limits (Spec: [RM-1], [RM-2], [RM-3], [RM-4])."""

    @abstractmethod
    def last_metrics(self) -> ResourceMetrics | None:
        """Return the most recent metric snapshot, if available (Spec: [RM-5.1])."""


class PsutilResourceMonitor(BaseResourceMonitor):
    """Default psutil-based resource monitor (Spec: [RM-5.1])."""

    def __init__(self) -> None:
        super().__init__()
        self._process: PsutilProcess | None = None if psutil else None
        self._history: list[ResourceMetrics] = []
        self._history_max = 20
        self._last_metrics: ResourceMetrics | None = None

    def start(self, pid: int) -> None:
        if psutil is None:  # pragma: no cover
            raise RuntimeError("psutil is required for resource monitoring")
        self._process = psutil.Process(pid)
        self._pid = pid
        self._process.cpu_percent(interval=None)

    def stop(self) -> None:
        self._process = None
        self._pid = None
        self._history.clear()

    def snapshot(self) -> ResourceMetrics:
        if psutil is None:
            raise RuntimeError("psutil is required for resource monitoring")
        if self._process is None:
            raise RuntimeError("Resource monitor not started")

        memory_info = self._process.memory_info()
        memory_mb = memory_info.rss / (1024 * 1024)

        try:
            cpu_percent = self._process.cpu_percent(interval=0.0)
        except (psutil.NoSuchProcess, psutil.ZombieProcess):  # pragma: no cover
            cpu_percent = 0.0

        try:
            open_files = self._process.num_fds()
        except (AttributeError, psutil.AccessDenied):  # pragma: no cover
            try:
                open_files = len(self._process.open_files())
            except (psutil.AccessDenied, AttributeError):
                open_files = 0

        try:
            connections = len(self._process.net_connections())
        except (psutil.AccessDenied, AttributeError):  # pragma: no cover
            connections = 0

        metrics = ResourceMetrics(
            timestamp=time.time_ns(),
            memory_mb=memory_mb,
            cpu_percent=cpu_percent,
            open_files=open_files,
            connections=connections,
        )
        self._history.append(metrics)
        if len(self._history) > self._history_max:
            self._history.pop(0)
        self._last_metrics = metrics
        return metrics

    def _average_cpu(self, samples: int = 5) -> float:
        if not self._history:
            return 0.0
        window = self._history[-samples:]
        return sum(metric.cpu_percent for metric in window) / len(window)

    def check_limits(self, limits: Any) -> tuple[bool, str | None]:
        if limits is None:
            return True, None

        metrics = self.snapshot()
        violations: list[str] = []

        memory_limit = getattr(limits, "memory_mb", None)
        if memory_limit and metrics.memory_mb > memory_limit:
            violations.append(f"Memory {metrics.memory_mb:.1f}MB > {memory_limit}MB")

        cpu_limit = getattr(limits, "cpu_percent", None)
        if cpu_limit and self._average_cpu() > cpu_limit:
            violations.append(f"CPU {self._average_cpu():.1f}% > {cpu_limit}%")

        fd_limit = getattr(limits, "max_fds", None)
        if fd_limit and metrics.open_files > fd_limit:
            violations.append(f"Open files {metrics.open_files} > {fd_limit}")

        conn_limit = getattr(limits, "max_connections", None)
        if conn_limit and metrics.connections > conn_limit:
            violations.append(f"Connections {metrics.connections} > {conn_limit}")

        if violations:
            return False, "; ".join(violations)
        return True, None

    def last_metrics(self) -> ResourceMetrics | None:
        return self._last_metrics


class ResourceMonitor(PsutilResourceMonitor):
    """Default monitor exported under the spec-required name (Spec: [RM-5.1])."""

    pass


def load_resource_monitor(class_path: str) -> BaseResourceMonitor:
    """Load a monitor implementation by dotted path (Spec: [RM-5])."""
    module_name, class_name = class_path.rsplit(".", 1)
    module = import_module(module_name)
    monitor_cls = getattr(module, class_name)
    if not isinstance(monitor_cls, type) or not issubclass(
        monitor_cls, BaseResourceMonitor
    ):
        raise TypeError(f"{class_path} must reference a BaseResourceMonitor subclass")
    return monitor_cls()


__all__ = [
    "BaseResourceMonitor",
    "PsutilResourceMonitor",
    "ResourceMonitor",
    "ResourceMetrics",
    "load_resource_monitor",
]
