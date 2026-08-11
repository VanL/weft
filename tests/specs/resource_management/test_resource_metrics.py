"""Spec checks for ResourceMetrics helpers (RM-5.1)."""

from __future__ import annotations

from types import SimpleNamespace

import psutil
import pytest

from weft.core.resource_monitor import ResourceMetrics, ResourceMonitor
from weft.core.taskspec import LimitsSection


def test_resource_metrics_to_dict_rounding() -> None:
    metrics = ResourceMetrics(
        timestamp=123,
        memory_mb=12.3456,
        cpu_percent=7.891,
        open_files=4,
        connections=1,
    )
    payload = metrics.to_dict()
    assert payload == {
        "timestamp": 123,
        "memory_mb": 12.35,
        "cpu_percent": 7.9,
        "open_files": 4,
        "connections": 1,
    }


def test_resource_metrics_exceeds_limits() -> None:
    limits = LimitsSection(memory_mb=10, cpu_percent=5, max_fds=2, max_connections=1)
    metrics = ResourceMetrics(
        timestamp=0,
        memory_mb=11.0,
        cpu_percent=6.0,
        open_files=3,
        connections=2,
    )
    violations = metrics.exceeds_limits(limits)
    assert set(violations) == {"memory", "cpu", "fds", "connections"}


def test_connection_count_uses_net_connections_when_available() -> None:
    class FakeProcess:
        def net_connections(self) -> list[object]:
            return [object(), object()]

        def connections(self) -> list[object]:
            raise AssertionError("deprecated connections() fallback was called")

    assert ResourceMonitor._connection_count(FakeProcess()) == 2


def test_connection_count_does_not_use_deprecated_fallback_after_access_denied() -> (
    None
):
    class FakeProcess:
        def net_connections(self) -> list[object]:
            raise psutil.AccessDenied(pid=123, name="fake")

    assert ResourceMonitor._connection_count(FakeProcess()) == 0


def test_connection_count_does_not_use_connections_when_current_api_is_missing() -> (
    None
):
    class FakeProcess:
        def connections(self) -> list[object]:
            raise AssertionError("deprecated connections() fallback was called")

    assert ResourceMonitor._connection_count(FakeProcess()) == 0


def test_check_limits_fails_open_when_the_process_disappears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monitor = ResourceMonitor(limits=LimitsSection(memory_mb=1))
    monkeypatch.setattr(
        monitor,
        "snapshot",
        lambda: (_ for _ in ()).throw(RuntimeError("process exited")),
    )

    assert monitor.check_limits() == (True, None)


def test_check_limits_propagates_unexpected_metric_collection_defects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monitor = ResourceMonitor(limits=LimitsSection(memory_mb=1))
    monkeypatch.setattr(
        monitor,
        "snapshot",
        lambda: (_ for _ in ()).throw(ValueError("unexpected metric defect")),
    )

    with pytest.raises(ValueError, match="unexpected metric defect"):
        monitor.check_limits()


def test_cpu_limit_requires_four_of_five_samples_not_first_or_five_of_five(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monitor = ResourceMonitor(limits=LimitsSection(cpu_percent=50))
    cpu_samples = iter([100.0, 100.0, 0.0, 100.0, 100.0])

    def sample() -> ResourceMetrics:
        metrics = ResourceMetrics(cpu_percent=next(cpu_samples))
        monitor.history.append(metrics)
        return metrics

    monkeypatch.setattr(monitor, "snapshot", sample)

    outcomes = [monitor.check_limits() for _ in range(5)]

    assert outcomes[:4] == [(True, None)] * 4
    assert outcomes[4] == (False, "CPU 80.0% > 50% (sustained)")


def test_cpu_limit_does_not_fire_for_only_three_of_five_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monitor = ResourceMonitor(limits=LimitsSection(cpu_percent=50))
    cpu_samples = iter([100.0, 0.0, 100.0, 0.0, 100.0])

    def sample() -> ResourceMetrics:
        metrics = ResourceMetrics(cpu_percent=next(cpu_samples))
        monitor.history.append(metrics)
        return metrics

    monkeypatch.setattr(monitor, "snapshot", sample)

    assert [monitor.check_limits() for _ in range(5)] == [(True, None)] * 5


def test_snapshot_aggregates_root_and_recursive_child_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recursive_calls: list[bool] = []

    class FakeProcess:
        def __init__(
            self,
            pid: int,
            *,
            memory_mb: float,
            open_files: int,
            connections: int,
        ) -> None:
            self.pid = pid
            self._memory_mb = memory_mb
            self._open_files = open_files
            self._connections = connections

        def children(self, *, recursive: bool) -> list[FakeProcess]:
            recursive_calls.append(recursive)
            return [child, grandchild] if recursive else [child]

        def cpu_times(self) -> SimpleNamespace:
            return SimpleNamespace(user=0.0, system=0.0)

        def memory_info(self) -> SimpleNamespace:
            return SimpleNamespace(rss=self._memory_mb * 1024 * 1024)

        def num_fds(self) -> int:
            return self._open_files

        def net_connections(self) -> list[object]:
            return [object()] * self._connections

    child = FakeProcess(2, memory_mb=2.0, open_files=3, connections=2)
    grandchild = FakeProcess(3, memory_mb=4.0, open_files=5, connections=3)
    root = FakeProcess(1, memory_mb=1.0, open_files=1, connections=1)
    monkeypatch.setattr(psutil, "Process", lambda pid: root)
    monitor = ResourceMonitor()

    monitor.start(root.pid)
    metrics = monitor.snapshot()

    assert recursive_calls == [True, True]
    assert metrics.memory_mb == pytest.approx(7.0)
    assert metrics.open_files == 9
    assert metrics.connections == 6


def test_connection_limit_is_enforced_on_first_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monitor = ResourceMonitor(limits=LimitsSection(max_connections=1))
    monkeypatch.setattr(
        monitor,
        "snapshot",
        lambda: ResourceMetrics(connections=2),
    )

    assert monitor.history == []
    assert monitor.check_limits() == (False, "Connections 2 > 1")
