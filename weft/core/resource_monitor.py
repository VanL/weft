"""Resource monitoring utilities for task execution.

Implements monitoring and limit checks from
docs/specifications/06-Resource_Management.md [RM-0] – [RM-5.1].
"""

from __future__ import annotations

import logging
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from importlib import import_module
from typing import Any

from simplebroker import BrokerTarget, Queue

logger = logging.getLogger(__name__)

_psutil_imported: Any | None
try:  # pragma: no cover - psutil optional at import time
    import psutil as _psutil_imported
except Exception:  # pragma: no cover
    _psutil_imported = None

psutil: Any | None = _psutil_imported

PsutilProcess = Any


@dataclass(slots=True)
class ResourceMetrics:
    """Snapshot of process resource utilisation (Spec: [RM-5], [RM-5.1])."""

    timestamp: int = 0
    memory_mb: float = 0.0
    cpu_percent: float = 0.0
    open_files: int = 0
    connections: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to a JSON-friendly dict (Spec: [RM-5.1])."""
        return {
            "timestamp": self.timestamp,
            "memory_mb": round(self.memory_mb, 2),
            "cpu_percent": round(self.cpu_percent, 1),
            "open_files": self.open_files,
            "connections": self.connections,
        }

    def exceeds_limits(self, limits: Any) -> list[str]:
        """Return a list of limit categories exceeded (Spec: [RM-5.1])."""
        violations: list[str] = []
        if limits is None:
            return violations

        memory_limit = getattr(limits, "memory_mb", None)
        if memory_limit and self.memory_mb > memory_limit:
            violations.append("memory")

        cpu_limit = getattr(limits, "cpu_percent", None)
        if cpu_limit and self.cpu_percent > cpu_limit:
            violations.append("cpu")

        fd_limit = getattr(limits, "max_fds", None)
        if fd_limit and self.open_files > fd_limit:
            violations.append("fds")

        conn_limit = getattr(limits, "max_connections", None)
        if conn_limit and self.connections > conn_limit:
            violations.append("connections")

        return violations


class BaseResourceMonitor(ABC):
    """Abstract base class for resource monitors (Spec: [RM-5])."""

    def __init__(
        self,
        *,
        limits: Any | None = None,
        polling_interval: float = 1.0,
        db_path: BrokerTarget | str | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._pid: int | None = None
        self.limits = limits
        self.polling_interval = polling_interval
        queue_kwargs: dict[str, Any] = {}
        if db_path is not None:
            queue_kwargs["db_path"] = db_path
        if config is not None:
            queue_kwargs["config"] = config
        self.metrics_queue = Queue("weft.metrics", **queue_kwargs)

    def start_monitoring(self, pid: int) -> None:
        """Begin monitoring the given process id (Spec: [RM-5.1])."""
        if type(self).start is BaseResourceMonitor.start:
            raise NotImplementedError(
                "Resource monitor must implement start() or start_monitoring()."
            )
        self.start(pid)

    def stop_monitoring(self) -> None:
        """Stop monitoring and release resources (Spec: [RM-5.1])."""
        if type(self).stop is BaseResourceMonitor.stop:
            raise NotImplementedError(
                "Resource monitor must implement stop() or stop_monitoring()."
            )
        self.stop()

    def get_current_metrics(self) -> ResourceMetrics:
        """Return the current resource utilisation snapshot (Spec: [RM-5.1])."""
        if type(self).snapshot is BaseResourceMonitor.snapshot:
            raise NotImplementedError(
                "Resource monitor must implement snapshot() or get_current_metrics()."
            )
        return self.snapshot()

    @abstractmethod
    def check_limits(self, limits: Any | None = None) -> tuple[bool, str | None]:
        """Return (ok, message) after comparing metrics with limits (Spec: [RM-1], [RM-2], [RM-3], [RM-4])."""

    @abstractmethod
    def last_metrics(self) -> ResourceMetrics | None:
        """Return the most recent metric snapshot, if available (Spec: [RM-5.1])."""

    def start(self, pid: int) -> None:
        """Backward-compatible alias for start_monitoring."""
        if type(self).start_monitoring is BaseResourceMonitor.start_monitoring:
            raise NotImplementedError(
                "Resource monitor must implement start() or start_monitoring()."
            )
        self.start_monitoring(pid)

    def stop(self) -> None:
        """Backward-compatible alias for stop_monitoring."""
        if type(self).stop_monitoring is BaseResourceMonitor.stop_monitoring:
            raise NotImplementedError(
                "Resource monitor must implement stop() or stop_monitoring()."
            )
        self.stop_monitoring()

    def snapshot(self) -> ResourceMetrics:
        """Backward-compatible alias for get_current_metrics."""
        if type(self).get_current_metrics is BaseResourceMonitor.get_current_metrics:
            raise NotImplementedError(
                "Resource monitor must implement snapshot() or get_current_metrics()."
            )
        return self.get_current_metrics()


class PsutilResourceMonitor(BaseResourceMonitor):
    """Default psutil-based resource monitor (Spec: [RM-5.1])."""

    def __init__(
        self,
        *,
        limits: Any | None = None,
        polling_interval: float = 1.0,
        db_path: BrokerTarget | str | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            limits=limits,
            polling_interval=polling_interval,
            db_path=db_path,
            config=config,
        )
        self._process: PsutilProcess | None = None if psutil else None
        self.history: list[ResourceMetrics] = []
        self.max_history = 100
        self._last_metrics: ResourceMetrics | None = None
        self._last_cpu_sample_at: float | None = None
        self._last_cpu_times: dict[int, float] = {}

    def start_monitoring(self, pid: int) -> None:
        if psutil is None:  # pragma: no cover
            raise RuntimeError("psutil is required for resource monitoring")
        self._process = psutil.Process(pid)
        self._pid = pid
        self._last_cpu_sample_at = time.monotonic()
        self._last_cpu_times = self._snapshot_cpu_times(self._process_tree())

    def stop_monitoring(self) -> None:
        self._process = None
        self._pid = None
        self.history.clear()
        self._last_cpu_sample_at = None
        self._last_cpu_times.clear()

    def _process_tree(self) -> list[PsutilProcess]:
        if psutil is None:
            raise RuntimeError("psutil is required for resource monitoring")
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
            for child in self._process.children(recursive=True):
                _add_process(child)
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            return []

        return processes

    @staticmethod
    def _snapshot_cpu_times(processes: list[PsutilProcess]) -> dict[int, float]:
        if psutil is None:
            return {}
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
        if psutil is None:
            return 0
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
        if psutil is None:
            return 0
        try:
            return len(process.net_connections())
        except (psutil.AccessDenied, AttributeError):
            try:
                return len(process.connections())
            except (psutil.AccessDenied, AttributeError):
                return 0

    def get_current_metrics(self) -> ResourceMetrics:
        if psutil is None:
            raise RuntimeError("psutil is required for resource monitoring")
        processes = self._process_tree()
        if not processes:
            raise RuntimeError("Resource monitor not started")
        now = time.monotonic()
        current_cpu_times = self._snapshot_cpu_times(processes)

        memory_mb = 0.0
        open_files = 0
        connections = 0
        for process in processes:
            try:
                memory_mb += process.memory_info().rss / (1024 * 1024)
            except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
                continue

            open_files += self._open_file_count(process)
            connections += self._connection_count(process)

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

        try:
            timestamp = self.metrics_queue.generate_timestamp()
        except Exception:  # pragma: no cover - fallback when queue unavailable
            timestamp = time.time_ns()

        metrics = ResourceMetrics(
            timestamp=timestamp,
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

    def check_limits(self, limits: Any | None = None) -> tuple[bool, str | None]:
        if limits is not None:
            self.limits = limits
        if self.limits is None:
            return True, None

        try:
            metrics = self.get_current_metrics()
        except Exception:  # pragma: no cover - process may have exited
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

    def get_max_metrics(self) -> ResourceMetrics:
        if not self.history:
            return ResourceMetrics()
        return ResourceMetrics(
            timestamp=self.history[-1].timestamp,
            memory_mb=max(m.memory_mb for m in self.history),
            cpu_percent=max(m.cpu_percent for m in self.history),
            open_files=max(m.open_files for m in self.history),
            connections=max(m.connections for m in self.history),
        )

    def last_metrics(self) -> ResourceMetrics | None:
        return self._last_metrics


class ResourceMonitor(PsutilResourceMonitor):
    """Default monitor exported under the spec-required name (Spec: [RM-5.1])."""

    pass


def load_resource_monitor(
    class_path: str,
    *,
    limits: Any | None = None,
    polling_interval: float | None = None,
    db_path: BrokerTarget | str | None = None,
    config: dict[str, Any] | None = None,
) -> BaseResourceMonitor:
    """Load a monitor implementation by dotted path (Spec: [RM-5])."""
    module_name, class_name = class_path.rsplit(".", 1)
    module = import_module(module_name)
    monitor_cls = getattr(module, class_name)
    if not isinstance(monitor_cls, type) or not issubclass(
        monitor_cls, BaseResourceMonitor
    ):
        raise TypeError(f"{class_path} must reference a BaseResourceMonitor subclass")
    kwargs: dict[str, Any] = {}
    if limits is not None:
        kwargs["limits"] = limits
    if polling_interval is not None:
        kwargs["polling_interval"] = polling_interval
    if db_path is not None:
        kwargs["db_path"] = db_path
    if config is not None:
        kwargs["config"] = config
    try:
        return monitor_cls(**kwargs)
    except TypeError:
        return monitor_cls()


__all__ = [
    "BaseResourceMonitor",
    "PsutilResourceMonitor",
    "ResourceMonitor",
    "ResourceMetrics",
    "load_resource_monitor",
]
