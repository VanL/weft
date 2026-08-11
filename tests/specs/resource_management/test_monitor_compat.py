"""Spec checks for ResourceMonitor APIs (RM-5.1)."""

from __future__ import annotations

import pytest

from weft.core import resource_monitor
from weft.core.resource_monitor import (
    BaseResourceMonitor,
    ResourceMetrics,
    ResourceMonitor,
    load_resource_monitor,
)


class AlternateMonitor(BaseResourceMonitor):
    """Monitor using short method names exercised by runner loops."""

    def __init__(
        self,
        *,
        limits: object | None = None,
        polling_interval: float = 1.0,
    ) -> None:
        super().__init__(limits=limits, polling_interval=polling_interval)
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

    def check_limits(self) -> tuple[bool, str | None]:
        return True, None

    def last_metrics(self) -> ResourceMetrics | None:
        return None


class ConstructorTypeErrorMonitor(BaseResourceMonitor):
    """Custom monitor whose documented constructor fails internally."""

    attempts = 0

    def __init__(
        self,
        *,
        limits: object | None = None,
        polling_interval: float = 1.0,
    ) -> None:
        type(self).attempts += 1
        del limits, polling_interval
        raise TypeError("constructor body defect")

    def start(self, pid: int) -> None:
        del pid

    def stop(self) -> None:
        return None

    def snapshot(self) -> ResourceMetrics:
        return ResourceMetrics()

    def check_limits(self) -> tuple[bool, str | None]:
        return True, None

    def last_metrics(self) -> ResourceMetrics | None:
        return None


def test_loader_does_not_retry_constructor_body_type_error() -> None:
    ConstructorTypeErrorMonitor.attempts = 0

    with pytest.raises(TypeError, match="constructor body defect"):
        load_resource_monitor(
            f"{__name__}.ConstructorTypeErrorMonitor",
            limits=object(),
            polling_interval=0.25,
        )

    assert ConstructorTypeErrorMonitor.attempts == 1


def test_loader_passes_the_documented_constructor_keywords() -> None:
    limits = object()

    monitor = load_resource_monitor(
        f"{__name__}.AlternateMonitor",
        limits=limits,
        polling_interval=0.25,
    )

    assert monitor.limits is limits
    assert monitor.polling_interval == 0.25


@pytest.mark.parametrize("removed_argument", ["db_path", "config"])
def test_loader_rejects_removed_constructor_context_arguments(
    removed_argument: str,
) -> None:
    with pytest.raises(
        TypeError,
        match=rf"unexpected keyword argument '{removed_argument}'",
    ):
        load_resource_monitor(
            f"{__name__}.AlternateMonitor",
            **{removed_argument: object()},
        )


def test_resource_monitor_does_not_open_broker_queue_for_metrics() -> None:
    monitor = ResourceMonitor()
    monitor.stop()
    monitor.stop()

    assert not hasattr(monitor, "metrics_queue")


def test_psutil_resource_monitor_alias_is_removed() -> None:
    assert not hasattr(resource_monitor, "PsutilResourceMonitor")


def test_resource_monitor_exposes_only_the_current_method_family() -> None:
    monitor = ResourceMonitor()

    assert not hasattr(monitor, "start_monitoring")
    assert not hasattr(monitor, "stop_monitoring")
    assert not hasattr(monitor, "get_current_metrics")
    assert not hasattr(monitor, "get_max_metrics")
    assert not hasattr(monitor, "close")
