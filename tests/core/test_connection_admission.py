"""Connection-budget boundaries required by [MA-1.8]."""

from __future__ import annotations

import pytest

from tests.core.test_manager import make_manager_spec
from tests.helpers.typing import BrokerEnv
from weft._constants import load_config
from weft.core.admission import AdmissionController, postgres_accounted_usage
from weft.core.manager import Manager

pytestmark = pytest.mark.shared


@pytest.mark.parametrize(
    ("used", "blocked"),
    [
        (254, frozenset()),
        (255, frozenset({"public"})),
        (262, frozenset({"public"})),
        (263, frozenset({"public", "child"})),
        (292, frozenset({"public", "child"})),
        (293, frozenset({"public", "child", "internal"})),
        (None, frozenset({"public", "child", "internal"})),
    ],
)
def test_postgres_candidate_must_fit_entire_budget(
    used: int | None, blocked: frozenset[str]
) -> None:
    """A partial free slot must not launch an eight-connection task."""
    decision = AdmissionController(300, 0.1, False).evaluate(
        used=used, backend="postgres", now=0.0
    )
    assert decision.blocked_lanes == blocked


def test_sqlite_children_share_public_capacity() -> None:
    """Child priority does not introduce the PostgreSQL budget on SQLite."""
    controller = AdmissionController(10, 0.1, False)
    assert (
        controller.evaluate(used=6, backend="sqlite", now=0.0).blocked_lanes
        == frozenset()
    )
    assert controller.evaluate(
        used=7, backend="sqlite", now=1.0
    ).blocked_lanes == frozenset({"public", "child"})


@pytest.mark.parametrize(
    ("total", "by_owner", "expected"),
    [
        (2, {}, 18),
        (6, {"a": 4}, 18),
        (14, {"a": 8, "b": 4}, 18),
        (12, {"a": 10}, 20),
        (9, {"unknown": 7}, 25),
        (0, {}, 16),
    ],
)
def test_unopened_budgets_preserve_unrelated_and_over_budget_pressure(
    total: int, by_owner: dict[str, int], expected: int
) -> None:
    """A/B tasks cannot discount outside pressure or spend closed capacity."""
    assert postgres_accounted_usage(total, by_owner, [("a", 8), ("b", 8)]) == expected


@pytest.mark.parametrize("value", [0, -1, True, "0", "no", 1.5])
def test_connection_budget_rejects_invalid_configuration(value: object) -> None:
    with (
        pytest.warns(UserWarning, match="WEFT_ADMISSION_TASK_CONNECTION_BUDGET"),
        pytest.raises(ValueError),
    ):
        load_config({"WEFT_ADMISSION_TASK_CONNECTION_BUDGET": value})


@pytest.mark.parametrize(("budget", "root_allowed"), [(4, True), (8, False)])
def test_configured_budget_changes_manager_launch_capacity(
    broker_env: BrokerEnv, budget: int, root_allowed: bool
) -> None:
    """The config declaration must affect the actual Manager policy owner."""
    target, queues = broker_env
    unique_tid = str(queues("admission-policy-ids").generate_timestamp())
    manager = Manager(
        target,
        make_manager_spec(unique_tid, idle_timeout=0.0),
        config=load_config(
            {
                "WEFT_ADMISSION_MAX_CONNECTIONS": 30,
                "WEFT_ADMISSION_RESERVE_FRACTION": 0.0,
                "WEFT_ADMISSION_TASK_CONNECTION_BUDGET": budget,
                "WEFT_TASK_MONITOR_ENABLED": False,
                "WEFT_LIVENESS_MONITOR_ENABLED": False,
            }
        ),
    )
    try:
        decision = manager._admission_controller.evaluate(
            used=15, backend="postgres", now=0.0
        )
        assert ("public" not in decision.blocked_lanes) is root_allowed
    finally:
        manager.stop(join=False)
        manager.cleanup()
