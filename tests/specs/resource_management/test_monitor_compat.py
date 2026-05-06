"""Spec checks for backward-compatible ResourceMonitor APIs (RM-5.1)."""

from __future__ import annotations

from weft.core import resource_monitor as resource_monitor_module
from weft.core.resource_monitor import (
    BaseResourceMonitor,
    PsutilResourceMonitor,
    ResourceMetrics,
)


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


def test_psutil_monitor_stop_closes_metrics_queue(monkeypatch) -> None:
    class FakeMetricsQueue:
        def __init__(self, *_args, **_kwargs) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    created: list[FakeMetricsQueue] = []

    def _fake_queue(*args, **kwargs):
        queue = FakeMetricsQueue(*args, **kwargs)
        created.append(queue)
        return queue

    monkeypatch.setattr(resource_monitor_module, "Queue", _fake_queue)

    monitor = PsutilResourceMonitor()
    monitor.stop_monitoring()

    assert created
    assert created[0].closed is True
