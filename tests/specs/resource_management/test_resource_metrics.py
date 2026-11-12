"""Spec checks for ResourceMetrics helpers (RM-5.1)."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from tests.helpers.process_tree import spawn_tree
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


def test_raced_empty_snapshot_does_not_replace_last_live_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A process exit during collection preserves the last valid sample."""

    class ExitedProcess:
        def memory_info(self) -> object:
            raise psutil.NoSuchProcess(pid=123, name="exited")

    previous = ResourceMetrics(memory_mb=12.5)
    monitor = ResourceMonitor(limits=LimitsSection(memory_mb=100))
    monitor._last_metrics = previous
    monkeypatch.setattr(monitor, "_process_tree", lambda: [ExitedProcess()])

    assert monitor.check_limits() == (True, None)
    assert monitor.last_metrics() is previous
    assert monitor.history == []


class _ZeroReadingProcess:
    """A process as Linux psutil reads it once exited and not yet reaped.

    Linux does not raise for such a process: resident memory, CPU times, open
    descriptors and connections all read as zero (``/proc/<pid>/statm`` is
    ``0 0 0 0 0 0 0``). macOS and Windows raise instead.
    """

    pid = 123

    def memory_info(self) -> object:
        return SimpleNamespace(rss=0)

    def cpu_times(self) -> object:
        return SimpleNamespace(user=0.0, system=0.0)

    def num_fds(self) -> int:
        return 0

    def net_connections(self) -> list[object]:
        return []


def test_exited_unreaped_root_reading_does_not_replace_last_live_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A zero reading of an exited root is not a sample of the running work.

    Sibling of ``test_raced_empty_snapshot_does_not_replace_last_live_metrics``
    for the platform where psutil reports zeros instead of raising.

    Spec: [RM-5.1]
    """

    previous = ResourceMetrics(memory_mb=12.5)
    monitor = ResourceMonitor(limits=LimitsSection(memory_mb=100))
    monitor._last_metrics = previous
    monkeypatch.setattr(monitor, "_process_tree", lambda: [_ZeroReadingProcess()])

    assert monitor.check_limits() == (True, None)
    assert monitor.last_metrics() is previous
    assert monitor.history == []


def test_exited_unreaped_member_does_not_hide_the_live_part_of_the_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tree with one live process still yields a sample of that process.

    Spec: [RM-5.1]
    """

    class LiveProcess(_ZeroReadingProcess):
        pid = 456

        def memory_info(self) -> object:
            return SimpleNamespace(rss=8 * 1024 * 1024)

        def num_fds(self) -> int:
            return 3

    monitor = ResourceMonitor(limits=LimitsSection(memory_mb=100))
    monkeypatch.setattr(
        monitor, "_process_tree", lambda: [_ZeroReadingProcess(), LiveProcess()]
    )

    assert monitor.check_limits() == (True, None)
    metrics = monitor.last_metrics()
    assert metrics is not None
    assert metrics.memory_mb == 8.0
    assert metrics.open_files == 3
    assert monitor.history == [metrics]


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="only Linux reads an exited, unreaped process as zeros",
)
def test_real_exited_unreaped_root_does_not_replace_last_live_metrics() -> None:
    """Real child, real zombie, real psutil: the live sample survives.

    Spec: [RM-5.1]
    """

    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"],
        stdin=subprocess.PIPE,
    )
    monitor = ResourceMonitor(limits=LimitsSection(memory_mb=4096))
    try:
        monitor.start(child.pid)
        assert monitor.check_limits() == (True, None)
        live = monitor.last_metrics()
        assert live is not None and live.memory_mb > 0

        assert child.stdin is not None
        child.stdin.close()
        # Nothing reaps the child until the finally block, so it stays a
        # zombie once it has exited.
        root = psutil.Process(child.pid)
        deadline = time.monotonic() + 15.0
        while root.status() != psutil.STATUS_ZOMBIE:
            assert time.monotonic() < deadline, "child did not exit"
            time.sleep(0.002)

        assert monitor.check_limits() == (True, None)
        assert monitor.last_metrics() is live
    finally:
        if child.stdin is not None:
            child.stdin.close()
        child.wait(timeout=30)
        monitor.stop()


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
    allowed, reason = outcomes[4]
    assert allowed is False
    assert (
        reason is not None and "CPU" in reason and "80.0" in reason and "50" in reason
    )


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


def test_snapshot_counts_a_grandchilds_memory(tmp_path: Path) -> None:
    """A snapshot sums the whole tree, not only the monitored root.

    The grandchild alone holds 128 MiB; root plus child stay far below that.
    """

    tree = spawn_tree(tmp_path, depth=2, fanout=1, leaf_memory_mb=128)
    monitor = ResourceMonitor()
    try:
        monitor.start(tree.root.pid)
        metrics = monitor.snapshot()
    finally:
        monitor.stop()
        tree.kill()

    assert metrics.memory_mb >= 128


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
    allowed, reason = monitor.check_limits()
    assert allowed is False
    assert (
        reason is not None
        and "Connections" in reason
        and "2" in reason
        and "1" in reason
    )
