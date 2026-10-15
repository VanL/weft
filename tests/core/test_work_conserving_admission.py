"""Measured capacity, rather than a family count, gates launches [MA-1.8]."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from simplebroker.ext import BrokerError
from tests.core.test_manager import make_manager_spec
from tests.helpers.typing import BrokerEnv
from weft._constants import load_config, resolve_runtime_config
from weft.core.admission import AdmissionController
from weft.core.family_scheduling import PendingRequest
from weft.core.manager import Manager

pytestmark = pytest.mark.shared


@pytest.fixture
def manager(broker_env: BrokerEnv) -> Iterator[Manager]:
    target, queues = broker_env
    task = Manager(
        target,
        make_manager_spec(str(queues("uncapped-ids").generate_timestamp())),
        config=load_config(
            {
                "WEFT_ADMISSION_MAX_CONNECTIONS": 0,
                "WEFT_TASK_MONITOR_ENABLED": False,
                "WEFT_LIVENESS_MONITOR_ENABLED": False,
                "WEFT_HEARTBEAT_ENABLED": False,
                "WEFT_AUTOSTART_TASKS": False,
            }
        ),
    )
    try:
        yield task
    finally:
        task.stop(join=False)
        task.cleanup()


def test_manager_offers_sixth_independent_family(manager: Manager) -> None:
    scheduler = manager._family_scheduler
    for request_id in range(100, 112):
        scheduler.add_pending(PendingRequest(request_id, str(request_id), None))
    for _ in range(5):
        candidate = scheduler.select()
        assert candidate is not None
        scheduler.remove_pending(candidate.request_id)
        scheduler.launched(candidate)
    candidate = scheduler.select()
    assert candidate is not None, "eligible work was hidden behind five live paths"
    assert candidate.request_id == 105


def test_manager_records_more_than_five_live_paths(manager: Manager) -> None:
    scheduler = manager._family_scheduler
    for request_id in range(100, 112):
        scheduler.launched(PendingRequest(request_id, str(request_id), None))
    assert len(scheduler.frontiers) == 12


def test_postgres_default_uses_native_capacity(
    manager: Manager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(manager, "_admission_backend_name", lambda: "postgres")
    monkeypatch.setattr(
        manager,
        "_read_postgres_connection_stats",
        lambda: {"max_connections": 300},
    )
    monkeypatch.setattr(
        manager,
        "_read_postgres_connection_usage",
        lambda: {"total": 270, "by_owner": {}},
    )
    assert not manager._admission_allows_queue(manager._queue_names["inbox"])


def test_native_capacity_is_refreshed_without_caching_configured_zero(
    manager: Manager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(manager, "_admission_backend_name", lambda: "postgres")
    ceilings = iter((300, 100))
    monkeypatch.setattr(
        manager,
        "_read_postgres_connection_stats",
        lambda: {"max_connections": next(ceilings)},
    )
    monkeypatch.setattr(
        manager,
        "_read_postgres_connection_usage",
        lambda: {"total": 95, "by_owner": {}},
    )
    source = manager._queue_names["inbox"]
    assert manager._admission_allows_queue(source)
    assert not manager._admission_allows_queue(source)
    assert manager._admission_max_connections == 0


def test_failed_native_capacity_defers_without_observing_usage_or_moving_work(
    manager: Manager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(manager, "_admission_backend_name", lambda: "postgres")

    def unavailable() -> dict[str, int]:
        raise BrokerError("capacity unavailable")

    def unexpected_usage() -> None:
        pytest.fail("usage was read after capacity observation failed")

    monkeypatch.setattr(manager, "_read_postgres_connection_stats", unavailable)
    monkeypatch.setattr(manager, "_read_postgres_connection_usage", unexpected_usage)
    source = manager._queue(manager._queue_names["inbox"])
    request_id = int(source.write("accepted work"))
    assert not manager._admission_allows_queue(source.name)
    assert source.peek_one(exact_timestamp=request_id) == "accepted work"
    assert manager._queue(manager._queue_names["reserved"]).peek_one() is None
    assert manager._admission_blocked_lanes == {"public", "internal"}


@pytest.mark.parametrize("backend", ["postgres", "sqlite"])
def test_explicit_capacity_never_reads_native_ceiling(
    manager: Manager, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    manager._admission_max_connections = 100
    manager._admission_controller = AdmissionController(100, 0.1, False)
    monkeypatch.setattr(manager, "_admission_backend_name", lambda: backend)

    def unexpected_stats() -> None:
        pytest.fail("explicit capacity read native metadata")

    monkeypatch.setattr(manager, "_read_postgres_connection_stats", unexpected_stats)
    monkeypatch.setattr(
        manager,
        "_read_postgres_connection_usage",
        lambda: {"total": 90, "by_owner": {}},
    )
    if backend == "postgres":
        assert not manager._admission_allows_queue(manager._queue_names["inbox"])
    else:
        assert manager._observe_admission_usage() is not None


def test_sqlite_zero_disables_resource_gate(
    manager: Manager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(manager, "_admission_backend_name", lambda: "sqlite")

    def unexpected_observation() -> None:
        pytest.fail("disabled SQLite gate observed usage")

    monkeypatch.setattr(manager, "_observe_admission_usage", unexpected_observation)
    assert manager._admission_allows_queue(manager._queue_names["inbox"])


def test_native_capacity_update_preserves_drain_wait_history() -> None:
    controller = AdmissionController(300, 0.1, False)
    controller.evaluate(used=300, backend="postgres", now=100.0)
    controller.evaluate(used=290, backend="postgres", now=110.0)
    controller.set_max_connections(100)
    unknown = controller.evaluate(used=None, backend="postgres", now=135.0)
    assert unknown.elapsed_seconds == 35.0
    assert unknown.idle_seconds == 25.0
    decision = controller.evaluate(used=290, backend="postgres", now=140.0)
    assert decision.capacity is not None and decision.capacity["internal_limit"] == 100
    assert decision.elapsed_seconds == 40.0
    assert decision.idle_seconds == 30.0
    assert decision.wait_phase == "stalled"


def test_restored_postgres_request_at_zero_schedules_existing_retry(
    manager: Manager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(manager, "_admission_backend_name", lambda: "postgres")
    manager._schedule_admission_retry_for_source(manager._queue_names["inbox"])
    assert manager._admission_retry_after_ns > 0
    assert manager._admission_blocked_lanes == {"public"}


def test_old_serialized_config_requires_coordinated_process_upgrade() -> None:
    """A stale key surviving JSON transport is rejected at the runtime Mapping."""
    restored = resolve_runtime_config(
        '{"prefix":"WEFT","values":{"SCHEDULER_MAX_FRONTIERS":5}}'
    )
    with pytest.raises(ValueError, match="WEFT_SCHEDULER_MAX_FRONTIERS.*removed"):
        resolve_runtime_config(dict(restored))
