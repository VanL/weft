"""Runner calibration for the Windows time budget of two stress tests.

Ruling D2 and its addendum (owner, 2026-11-03) in
``docs/plans/2026-11-02-test-suite-structure-plan.md`` keep the Manager-reuse
stress tests at full load on every platform and allow Windows, and only
Windows, a scaled *time* budget: process spawning and disk access are far
slower on Windows than on macOS and Linux, a legitimate platform difference,
not a load reduction.

The shape mirrors SimpleBroker's test calibration
(``tests/performance_calibration.py`` and
``tests/helper_scripts/timing.py::scale_timeout_for_calibration``), which Weft
does not import: measure named costs on the current runner, divide them by
committed reference timings, and relax a budget by that ratio on a slower
runner, never tightening it on a faster one. Weft measures the two costs the
owner names (spawn-context process creation and a SimpleBroker SQLite write
cycle), takes the larger ratio, and applies it on Windows only, capped at
``RUNNER_TIME_FACTOR_CEILING``.

Spec: docs/specifications/08-Testing_Strategy.md [TS-0] (contention stop
condition; this helper sizes time, never load).
"""

from __future__ import annotations

import multiprocessing
import sqlite3
import statistics
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import Final

from simplebroker import Queue
from simplebroker.ext import BrokerError

REFERENCE_SPAWN_MEDIAN_S: Final[float | None] = None  # unmeasured
"""Linux reference median, in seconds, of one spawn-context process start-to-join.

To be measured on GitHub-hosted ``ubuntu-latest`` through ``Test`` workflow
dispatch runs (the ``cli`` jobs for Python 3.12 and 3.14), as plan
``2026-11-02-test-suite-structure-plan.md`` Task 2 Step 4 describes; the
docstring then names the runner, the Python versions, the dispatch run IDs,
and the date. While ``None`` the factor is 1.0 on every platform.
"""

REFERENCE_SQLITE_MEDIAN_S: Final[float | None] = None  # unmeasured
"""Linux reference median, in seconds, of one SimpleBroker SQLite write cycle.

One cycle opens a persistent ``Queue`` in a fresh temporary directory under
SimpleBroker's default ``SYNC_MODE=FULL``, writes one message, closes it, and
removes the directory. Measured as ``REFERENCE_SPAWN_MEDIAN_S`` is; while
``None`` the factor is 1.0 on every platform.
"""

RUNNER_TIME_FACTOR_CEILING: Final[float] = 20.0
"""Upper bound of the Windows time factor.

The upper end of the published Windows-versus-Linux process-creation gap. A
session-time storage stall cannot inflate a budget past it. The owner may
change it.
"""

RUNNER_PROBE_REPETITIONS: Final[int] = 5
"""Samples per probe; the probe reports their median."""

RUNNER_PROBE_JOIN_TIMEOUT_S: Final[float] = 30.0
"""Bound on one probe child's join, and on the five disk-probe cycles together.

The probe runs in ``pytest_configure``, before the test protocol, so the
installed pytest-timeout timer does not cover it. An unbounded ``join()`` on a
stalled child would hang every session before the error fallback or the
ceiling could apply.
"""

RUNNER_PROBE_KILL_JOIN_TIMEOUT_S: Final[float] = 5.0
"""Bound on each join after terminating or killing a stalled probe child."""

_PROBE_QUEUE_NAME: Final[str] = "weft.calibration.probe"
_PROBE_PROCESS_NAME: Final[str] = "weft-runner-calibration-probe"
_WINDOWS_PLATFORM: Final[str] = "win32"


def _return_immediately() -> None:
    """Spawn-probe child target: start, import, and exit."""


_SPAWN_PROBE_TARGET: Callable[[], None] = _return_immediately
"""Child target of the spawn probe; a test replaces it to stall the child."""


@dataclass(frozen=True, slots=True)
class RunnerCalibration:
    """Medians measured by ``probe_runner`` on the current runner, in seconds."""

    spawn_median_s: float
    sqlite_median_s: float
    error: str | None = None


def _stop_child(process: BaseProcess) -> None:
    """Terminate, then kill, a probe child that outlived its join bound."""

    process.terminate()
    process.join(RUNNER_PROBE_KILL_JOIN_TIMEOUT_S)
    if process.is_alive():
        process.kill()
        process.join(RUNNER_PROBE_KILL_JOIN_TIMEOUT_S)


def _spawn_samples() -> list[float]:
    context = multiprocessing.get_context("spawn")
    samples: list[float] = []
    for _ in range(RUNNER_PROBE_REPETITIONS):
        process = context.Process(target=_SPAWN_PROBE_TARGET, name=_PROBE_PROCESS_NAME)
        started = time.perf_counter()
        process.start()
        process.join(timeout=RUNNER_PROBE_JOIN_TIMEOUT_S)
        elapsed = time.perf_counter() - started
        if process.is_alive():
            _stop_child(process)
            raise TimeoutError(
                f"spawn probe child still alive after {RUNNER_PROBE_JOIN_TIMEOUT_S}s"
            )
        samples.append(elapsed)
    return samples


def _sqlite_samples() -> list[float]:
    deadline = time.monotonic() + RUNNER_PROBE_JOIN_TIMEOUT_S
    samples: list[float] = []
    for _ in range(RUNNER_PROBE_REPETITIONS):
        started = time.perf_counter()
        with tempfile.TemporaryDirectory(
            prefix="weft-calibration-", ignore_cleanup_errors=True
        ) as directory:
            queue = Queue(
                _PROBE_QUEUE_NAME,
                db_path=str(Path(directory) / "probe.db"),
                persistent=True,
            )
            try:
                queue.write("probe")
            finally:
                queue.close()
        samples.append(time.perf_counter() - started)
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"disk probe exceeded {RUNNER_PROBE_JOIN_TIMEOUT_S}s "
                f"after {len(samples)} cycle(s)"
            )
    return samples


def probe_runner() -> RunnerCalibration:
    """Measure process-creation and SQLite write cost on this runner.

    Returns:
        The two medians. When either probe fails or exceeds its bound, the
        result carries the error text and every factor derived from it is
        1.0, as SimpleBroker's ``_machine_performance_ratio`` falls back to a
        neutral ratio.
    """

    spawn_median = 0.0
    sqlite_median = 0.0
    try:
        spawn_median = statistics.median(_spawn_samples())
        sqlite_median = statistics.median(_sqlite_samples())
    except (OSError, RuntimeError, sqlite3.Error, BrokerError) as exc:
        # Best-effort probe at the session boundary: a failed or stalled
        # probe (TimeoutError is an OSError) yields the neutral factor.
        return RunnerCalibration(
            spawn_median_s=spawn_median,
            sqlite_median_s=sqlite_median,
            error=f"{type(exc).__name__}: {exc}",
        )
    return RunnerCalibration(spawn_median_s=spawn_median, sqlite_median_s=sqlite_median)


def _ratios(calibration: RunnerCalibration) -> tuple[float, float] | None:
    """Return (spawn ratio, SQLite ratio), or None when they cannot be formed."""

    spawn_reference = REFERENCE_SPAWN_MEDIAN_S
    sqlite_reference = REFERENCE_SQLITE_MEDIAN_S
    if calibration.error is not None:
        return None
    if spawn_reference is None or sqlite_reference is None:
        return None
    return (
        calibration.spawn_median_s / spawn_reference,
        calibration.sqlite_median_s / sqlite_reference,
    )


def _uncapped_factor(calibration: RunnerCalibration) -> float | None:
    ratios = _ratios(calibration)
    if ratios is None:
        return None
    spawn_ratio, sqlite_ratio = ratios
    return max(1.0, spawn_ratio, sqlite_ratio)


def windows_time_factor(
    calibration: RunnerCalibration, *, platform: str = sys.platform
) -> float:
    """Return the factor that scales a Linux time budget on this runner.

    Ruling D2 (owner, 2026-11-03): "MacOS should not get any breaks. Windows
    should run at full concurrency as well, but we have observed that its
    process spawning and disk accesses are far slower than Mac and Linux.
    Accordingly we allow for a scaled time for Windows to account for a
    legitimate platform difference." The addendum makes the factor a
    calibrated runner ratio. It scales time only and never load.

    Args:
        calibration: The session's probe result.
        platform: The platform the budget is for; tests pass it explicitly.

    Returns:
        1.0 off Windows, on a probe error, or while a reference baseline is
        unmeasured. Otherwise the larger of the two runner ratios, floored
        at 1.0 (a fast runner never tightens a budget) and capped at
        ``RUNNER_TIME_FACTOR_CEILING``.
    """

    if platform != _WINDOWS_PLATFORM:
        return 1.0
    uncapped = _uncapped_factor(calibration)
    if uncapped is None:
        return 1.0
    return min(RUNNER_TIME_FACTOR_CEILING, uncapped)


def scale_linux_budget(
    linux_budget: float,
    calibration: RunnerCalibration,
    *,
    platform: str = sys.platform,
) -> float:
    """Return a Linux time budget scaled for this runner; load never changes."""

    return linux_budget * windows_time_factor(calibration, platform=platform)


def describe_calibration(
    calibration: RunnerCalibration, *, platform: str = sys.platform
) -> str:
    """Return the session header line for the calibration."""

    ratios = _ratios(calibration)
    spawn_ratio, sqlite_ratio = ("n/a", "n/a")
    if ratios is not None:
        spawn_ratio, sqlite_ratio = (f"{ratio:.2f}" for ratio in ratios)
    uncapped = _uncapped_factor(calibration)
    factor = windows_time_factor(calibration, platform=platform)
    if calibration.error is not None:
        status = f"error: {calibration.error}"
    elif platform != _WINDOWS_PLATFORM:
        status = "pinned"
    elif uncapped is None:
        status = "unmeasured"
    elif uncapped > RUNNER_TIME_FACTOR_CEILING:
        status = "capped"
        factor = uncapped
    else:
        status = "applied"
    return (
        "weft runner calibration: "
        f"spawn_median={calibration.spawn_median_s:.4f}s "
        f"sqlite_median={calibration.sqlite_median_s:.4f}s "
        f"spawn_ratio={spawn_ratio} sqlite_ratio={sqlite_ratio} "
        f"factor={factor:.2f} platform={platform} {status}"
    )
