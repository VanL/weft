"""Spec checks for ResourceMetrics helpers (RM-5.1)."""

from __future__ import annotations

from weft.core.resource_monitor import ResourceMetrics
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
