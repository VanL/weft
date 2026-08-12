"""Direct contract tests for the shared reactor test driver.

Spec: docs/specifications/08-Testing_Strategy.md [TS-0].
"""

from __future__ import annotations

import pytest

from tests.helpers import reactor_driver
from tests.helpers.reactor_driver import drive_until

pytestmark = [pytest.mark.shared]


def test_drive_until_runs_one_turn_before_matching_observation() -> None:
    """The shared frame begins with one owner turn, then observes evidence."""

    calls: list[object] = []

    def step() -> None:
        calls.append("step")

    def observe() -> str:
        calls.append("observe")
        return "ready"

    def wait(*, timeout: float) -> None:
        calls.append(("wait", timeout))

    result = drive_until(
        observe,
        lambda evidence: evidence == "ready",
        step=step,
        wait=wait,
        timeout=1.0,
        diagnostics=lambda: calls.append("diagnostics"),
    )

    assert result == "ready"
    assert calls == ["step", "observe"]


def test_drive_until_allows_only_the_turn_paired_with_a_boundary_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wait reaching the deadline gets its paired turn and nothing later."""

    monotonic_values = iter((0.0, 0.5, 2.0))
    monkeypatch.setattr(
        reactor_driver.time,
        "monotonic",
        lambda: next(monotonic_values),
    )
    calls: list[object] = []

    with pytest.raises(AssertionError, match="turns=2"):
        drive_until(
            lambda: calls.append("observe"),
            lambda _evidence: False,
            step=lambda: calls.append("step"),
            wait=lambda *, timeout: calls.append(("wait", timeout)),
            timeout=1.0,
            wait_slice=0.75,
            pending_work=(lambda: calls.append("pending") or False,),
        )

    assert calls == [
        "step",
        "observe",
        ("wait", 0.5),
        "step",
        "observe",
        "pending",
    ]


def test_drive_until_clips_wait_and_pairs_it_with_the_next_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bounded owner wait is followed by one turn before observation."""

    monotonic_values = iter((10.0, 10.75))
    monkeypatch.setattr(
        reactor_driver.time,
        "monotonic",
        lambda: next(monotonic_values),
    )
    calls: list[object] = []
    turns = 0

    def step() -> None:
        nonlocal turns
        turns += 1
        calls.append("step")

    def observe() -> int:
        calls.append("observe")
        return turns

    def wait(*, timeout: float) -> None:
        calls.append(("wait", timeout))

    result = drive_until(
        observe,
        lambda evidence: evidence == 2,
        step=step,
        wait=wait,
        timeout=1.0,
        wait_slice=0.5,
    )

    assert result == 2
    assert calls == ["step", "observe", ("wait", 0.25), "step", "observe"]


def test_drive_until_applies_one_ready_result_after_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ready worker evidence authorizes one final non-waiting turn."""

    monotonic_values = iter((0.0, 2.0))
    monkeypatch.setattr(
        reactor_driver.time,
        "monotonic",
        lambda: next(monotonic_values),
    )
    calls: list[str] = []
    completed = False
    pending = False

    def step() -> None:
        nonlocal completed, pending
        calls.append("step")
        if pending:
            pending = False
            completed = True
        else:
            pending = True

    def observe() -> bool:
        calls.append("observe")
        return completed

    def has_pending_work() -> bool:
        calls.append("pending")
        return pending

    def wait(*, timeout: float) -> None:
        raise AssertionError(f"unexpected wait after deadline: {timeout}")

    result = drive_until(
        observe,
        bool,
        step=step,
        wait=wait,
        timeout=1.0,
        pending_work=(has_pending_work,),
    )

    assert result is True
    assert calls == ["step", "observe", "pending", "step", "observe"]


def test_drive_until_reports_boundary_evidence_after_failed_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed final turn reports turns, evidence, and lazy diagnostics."""

    monotonic_values = iter((0.0, 2.0))
    monkeypatch.setattr(
        reactor_driver.time,
        "monotonic",
        lambda: next(monotonic_values),
    )
    turns = 0
    diagnostic_calls = 0

    def step() -> None:
        nonlocal turns
        turns += 1

    def diagnostics() -> dict[str, str]:
        nonlocal diagnostic_calls
        diagnostic_calls += 1
        return {"worker": "still-running"}

    with pytest.raises(AssertionError) as exc_info:
        drive_until(
            lambda: "missing",
            lambda evidence: evidence == "ready",
            step=step,
            wait=lambda *, timeout: None,
            timeout=1.0,
            pending_work=(lambda: True,),
            diagnostics=diagnostics,
        )

    message = str(exc_info.value)
    assert "timeout=1.0" in message
    assert "turns=2" in message
    assert "latest='missing'" in message
    assert "diagnostics={'worker': 'still-running'}" in message
    assert turns == 2
    assert diagnostic_calls == 1


@pytest.mark.parametrize(
    ("timeout", "wait_slice", "expected"),
    [
        (0.0, 0.02, "timeout must be positive"),
        (-1.0, 0.02, "timeout must be positive"),
        (1.0, 0.0, "wait_slice must be positive"),
        (1.0, -0.01, "wait_slice must be positive"),
    ],
)
def test_drive_until_rejects_invalid_timing_before_callbacks(
    timeout: float,
    wait_slice: float,
    expected: str,
) -> None:
    """Invalid timing cannot claim a reactor or consume evidence."""

    calls: list[str] = []

    with pytest.raises(ValueError, match=expected):
        drive_until(
            lambda: calls.append("observe"),
            lambda _evidence: False,
            step=lambda: calls.append("step"),
            wait=lambda *, timeout: calls.append(f"wait:{timeout}"),
            timeout=timeout,
            wait_slice=wait_slice,
        )

    assert calls == []


def test_drive_until_keeps_timeout_primary_when_diagnostics_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Broken diagnostics are reported without replacing the timeout."""

    monotonic_values = iter((0.0, 2.0))
    monkeypatch.setattr(
        reactor_driver.time,
        "monotonic",
        lambda: next(monotonic_values),
    )

    def diagnostics() -> object:
        raise RuntimeError("snapshot failed")

    with pytest.raises(AssertionError) as exc_info:
        drive_until(
            lambda: "missing",
            lambda _evidence: False,
            step=lambda: None,
            wait=lambda *, timeout: None,
            timeout=1.0,
            diagnostics=diagnostics,
        )

    message = str(exc_info.value)
    assert "reactor evidence did not match before timeout" in message
    assert "diagnostics raised RuntimeError: snapshot failed" in message


def test_drive_until_propagates_fatal_diagnostic_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The diagnostic guard catches ordinary exceptions, not fatal exits."""

    class FatalDiagnostic(BaseException):
        pass

    monotonic_values = iter((0.0, 2.0))
    monkeypatch.setattr(
        reactor_driver.time,
        "monotonic",
        lambda: next(monotonic_values),
    )
    failure = FatalDiagnostic("stop")

    def diagnostics() -> object:
        raise failure

    with pytest.raises(FatalDiagnostic) as exc_info:
        drive_until(
            lambda: "missing",
            lambda _evidence: False,
            step=lambda: None,
            wait=lambda *, timeout: None,
            timeout=1.0,
            diagnostics=diagnostics,
        )

    assert exc_info.value is failure


@pytest.mark.parametrize("stage", ("step", "observe", "matches", "wait", "pending"))
def test_drive_until_propagates_callback_failures(
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    """Only diagnostic failures are folded into the timeout assertion."""

    monotonic_values = iter((0.0, 2.0) if stage == "pending" else (0.0, 0.5))
    monkeypatch.setattr(
        reactor_driver.time,
        "monotonic",
        lambda: next(monotonic_values),
    )
    failure = RuntimeError(stage)

    def fail_at(callback_stage: str) -> None:
        if stage == callback_stage:
            raise failure

    def step() -> None:
        fail_at("step")

    def observe() -> str:
        fail_at("observe")
        return "missing"

    def matches(_evidence: str) -> bool:
        fail_at("matches")
        return False

    def wait(*, timeout: float) -> None:
        del timeout
        fail_at("wait")

    def pending() -> bool:
        fail_at("pending")
        return False

    with pytest.raises(RuntimeError) as exc_info:
        drive_until(
            observe,
            matches,
            step=step,
            wait=wait,
            timeout=1.0,
            pending_work=(pending,),
            diagnostics=lambda: pytest.fail("diagnostics must stay lazy"),
        )

    assert exc_info.value is failure
