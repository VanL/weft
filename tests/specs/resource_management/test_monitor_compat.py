"""Spec checks for backward-compatible ResourceMonitor APIs (RM-5.1)."""

from __future__ import annotations

from weft.core.resource_monitor import BaseResourceMonitor, ResourceMetrics


class LegacyMonitor(BaseResourceMonitor):
    """Legacy-style monitor implementing start/stop/snapshot only."""

    def __init__(self) -> None:
        super().__init__()
        self.started_pid: int | None = None
        self.stopped = False

    def start(self, pid: int) -> None:
        self.started_pid = pid

    def stop(self) -> None:
        self.stopped = True

    def snapshot(self) -> ResourceMetrics:
        return ResourceMetrics(
            timestamp=123,
            memory_mb=1.0,
            cpu_percent=0.0,
            open_files=0,
            connections=0,
        )

    def check_limits(self, limits=None) -> tuple[bool, str | None]:
        return True, None

    def last_metrics(self) -> ResourceMetrics | None:
        return None


def test_legacy_monitor_methods_are_honored() -> None:
    monitor = LegacyMonitor()
    monitor.start_monitoring(42)
    assert monitor.started_pid == 42

    metrics = monitor.get_current_metrics()
    assert metrics.timestamp == 123

    monitor.stop_monitoring()
    assert monitor.stopped is True
