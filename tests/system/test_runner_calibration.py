"""Tests for the runner calibration that sizes the Windows stress-test budgets.

Ruling D2 and its addendum (owner, 2026-11-03) in
``docs/plans/2026-11-02-test-suite-structure-plan.md`` Task 2: full load on
every platform; Windows alone gets a calibrated time factor. A constructed
``RunnerCalibration`` with chosen medians stands in for the measurement; the
reference baselines are patched on the helper module so each ratio is chosen
by the test. Every test passes ``platform=`` explicitly, so the tests behave
identically on Linux, macOS, and Windows.

Spec: docs/specifications/08-Testing_Strategy.md [TS-0].
"""

from __future__ import annotations

import functools
import multiprocessing
import time

import pytest

from tests.cli.test_cli_run import (
    MANAGER_REUSE_STATUS_TIMEOUT_S,
    MANAGER_REUSE_SUBMIT_TIMEOUT_S,
    MANAGER_REUSE_TASK_COMPLETION_TIMEOUT_S,
    _manager_reuse_timeouts,
)
from tests.helpers import runner_calibration
from tests.helpers.runner_calibration import (
    RUNNER_TIME_FACTOR_CEILING,
    RunnerCalibration,
    probe_runner,
    windows_time_factor,
)

pytestmark = [pytest.mark.shared]

SPAWN_REFERENCE_S = 0.2
SQLITE_REFERENCE_S = 0.01


@pytest.fixture
def references(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the helper measured-looking reference baselines."""

    monkeypatch.setattr(
        runner_calibration, "REFERENCE_SPAWN_MEDIAN_S", SPAWN_REFERENCE_S
    )
    monkeypatch.setattr(
        runner_calibration, "REFERENCE_SQLITE_MEDIAN_S", SQLITE_REFERENCE_S
    )


def _calibration(spawn_ratio: float, sqlite_ratio: float) -> RunnerCalibration:
    return RunnerCalibration(
        spawn_median_s=SPAWN_REFERENCE_S * spawn_ratio,
        sqlite_median_s=SQLITE_REFERENCE_S * sqlite_ratio,
    )


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_factor_is_pinned_off_windows(references: None, platform: str) -> None:
    """macOS and Linux get no time factor, however slow the runner measures."""

    assert windows_time_factor(_calibration(30.0, 30.0), platform=platform) == 1.0


def test_factor_never_tightens_a_budget_on_a_fast_windows_runner(
    references: None,
) -> None:
    assert windows_time_factor(_calibration(0.5, 0.25), platform="win32") == 1.0


@pytest.mark.parametrize(
    ("spawn_ratio", "sqlite_ratio"),
    [(6.0, 3.0), (3.0, 6.0)],
)
def test_factor_takes_the_larger_ratio(
    references: None, spawn_ratio: float, sqlite_ratio: float
) -> None:
    """Either cost can bound the stress tests, so neither is averaged away."""

    factor = windows_time_factor(
        _calibration(spawn_ratio, sqlite_ratio), platform="win32"
    )

    assert factor == pytest.approx(6.0)


def test_factor_is_capped_at_the_ceiling(references: None) -> None:
    factor = windows_time_factor(_calibration(50.0, 1.0), platform="win32")

    assert factor == RUNNER_TIME_FACTOR_CEILING


def test_factor_is_neutral_while_a_reference_is_unmeasured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner_calibration, "REFERENCE_SPAWN_MEDIAN_S", None)
    monkeypatch.setattr(
        runner_calibration, "REFERENCE_SQLITE_MEDIAN_S", SQLITE_REFERENCE_S
    )

    assert windows_time_factor(_calibration(10.0, 10.0), platform="win32") == 1.0


def test_factor_is_neutral_when_the_probe_reports_an_error(references: None) -> None:
    calibration = RunnerCalibration(
        spawn_median_s=SPAWN_REFERENCE_S * 10.0,
        sqlite_median_s=SQLITE_REFERENCE_S * 10.0,
        error="TimeoutError: probe stalled",
    )

    assert windows_time_factor(calibration, platform="win32") == 1.0


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_manager_reuse_budgets_scale_linux_budgets_by_the_factor(
    references: None, platform: str
) -> None:
    """The stress tests' budgets are the Linux budgets times the factor only."""

    calibration = _calibration(4.0, 2.0)
    factor = windows_time_factor(calibration, platform=platform)

    budgets = _manager_reuse_timeouts(calibration, platform=platform)

    assert budgets == (
        MANAGER_REUSE_SUBMIT_TIMEOUT_S * factor,
        MANAGER_REUSE_STATUS_TIMEOUT_S * factor,
        MANAGER_REUSE_TASK_COMPLETION_TIMEOUT_S * factor,
    )
    if platform != "win32":
        assert factor == 1.0


@pytest.mark.timeout(60)
def test_probe_bounds_a_stalled_spawn_child(
    monkeypatch: pytest.MonkeyPatch, references: None
) -> None:
    """A stalled probe child cannot hang the session or outlive the probe."""

    monkeypatch.setattr(runner_calibration, "RUNNER_PROBE_JOIN_TIMEOUT_S", 0.5)
    monkeypatch.setattr(
        runner_calibration, "_SPAWN_PROBE_TARGET", functools.partial(time.sleep, 60)
    )

    started = time.monotonic()
    calibration = probe_runner()
    elapsed = time.monotonic() - started

    assert elapsed < 15.0
    assert calibration.error is not None
    assert "TimeoutError" in calibration.error
    assert not [
        child
        for child in multiprocessing.active_children()
        if child.name == runner_calibration._PROBE_PROCESS_NAME
    ]
    assert windows_time_factor(calibration, platform="win32") == 1.0
