"""Spec checks for ResourceMetrics helpers (RM-5.1)."""

from __future__ import annotations

import warnings

import pytest

from weft.core import resource_monitor
from weft.core.resource_monitor import PsutilResourceMonitor, ResourceMetrics
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

    assert PsutilResourceMonitor._connection_count(FakeProcess()) == 2


def test_connection_count_does_not_use_deprecated_fallback_after_access_denied() -> (
    None
):
    psutil = resource_monitor.psutil
    if psutil is None:
        pytest.skip("psutil is unavailable")

    class FakeProcess:
        def net_connections(self) -> list[object]:
            raise psutil.AccessDenied(pid=123, name="fake")

        def connections(self) -> list[object]:
            warnings.warn(
                "connections() is deprecated; use net_connections() instead",
                DeprecationWarning,
                stacklevel=2,
            )
            return [object()]

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        assert PsutilResourceMonitor._connection_count(FakeProcess()) == 0


def test_connection_count_keeps_legacy_connections_fallback() -> None:
    class FakeProcess:
        def connections(self) -> list[object]:
            return [object()]

    assert PsutilResourceMonitor._connection_count(FakeProcess()) == 1
