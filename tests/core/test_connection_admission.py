"""Measured admission boundaries [MA-1.8], without prospective task debt."""

from __future__ import annotations

import pytest

from weft._constants import load_config
from weft.core.admission import AdmissionController


@pytest.mark.parametrize("backend", ["postgres", "sqlite"])
@pytest.mark.parametrize(
    ("used", "blocked"),
    [
        (269, frozenset()),
        (270, frozenset({"public"})),
        (299, frozenset({"public"})),
        (300, frozenset({"public", "internal"})),
        (None, frozenset({"public", "internal"})),
    ],
)
def test_launch_uses_measured_capacity_without_task_budget(
    backend: str, used: int | None, blocked: frozenset[str]
) -> None:
    """The final unreserved slot permits a launch; only actual usage counts."""
    decision = AdmissionController(300, 0.1, False).evaluate(
        used=used, backend=backend, now=0.0
    )
    assert decision.blocked_lanes == blocked


@pytest.mark.parametrize("value", [0, 8, "8"])
def test_removed_connection_budget_is_an_actionable_configuration_error(
    value: object,
) -> None:
    """Old deployment config must explain how to migrate rather than lose policy."""
    with pytest.raises(
        ValueError, match="WEFT_ADMISSION_TASK_CONNECTION_BUDGET.*removed"
    ):
        load_config({"WEFT_ADMISSION_TASK_CONNECTION_BUDGET": value})


@pytest.mark.parametrize("origin", ["environment", "override"])
@pytest.mark.parametrize("value", [1, 5, "bad"])
def test_retired_frontier_setting_is_an_actionable_configuration_error(
    monkeypatch: pytest.MonkeyPatch, origin: str, value: object
) -> None:
    """Both configuration boundaries reject the retired capacity policy."""
    monkeypatch.delenv("WEFT_SCHEDULER_MAX_FRONTIERS", raising=False)
    overrides: dict[str, object] = {}
    if origin == "environment":
        monkeypatch.setenv("WEFT_SCHEDULER_MAX_FRONTIERS", str(value))
    else:
        overrides["WEFT_SCHEDULER_MAX_FRONTIERS"] = value
    with pytest.raises(ValueError, match="WEFT_SCHEDULER_MAX_FRONTIERS.*removed"):
        load_config(overrides)
