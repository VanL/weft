"""Literal result settlement rules from [MF-5], independent of queue I/O.

The shared policy consumes acquired evidence. Acquisition, source selection,
last drains and raw-value assembly have separate real-owner tests. Fixed
absolute clocks here distinguish caller deadlines from the original grace,
without introducing a fake broker or copying the policy algorithm.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Literal

import pytest

from weft._constants import WEFT_COMPLETED_RESULT_GRACE_SECONDS, WRAPPER_LOST_ERROR
from weft.commands import _result_wait as result_wait
from weft.core.task_evidence import TaskEvidenceSnapshot

pytestmark = [pytest.mark.shared]


def _terminal(
    status: str,
    *,
    source: str = "log",
    classification: str = "terminal_log",
    error: str | None = None,
    value: Any | None = None,
) -> TaskEvidenceSnapshot:
    """Supply already-selected evidence to its actual settlement owner."""
    return TaskEvidenceSnapshot(
        tid="1780000000000000099",
        status=status,
        classification=classification,
        source=source,
        terminal=True,
        error=error,
        value=value,
    )


_COMPLETED = _terminal("completed")
_WRAPPER_LOST = _terminal(
    "failed", source="ctrl_out", classification="wrapper_lost", error=WRAPPER_LOST_ERROR
)


def _collection_facts(
    fields: dict[str, Any],
) -> tuple[result_wait._CollectedResult, result_wait.ResultMaterialization]:
    """Build acquired configuration and literal collection facts, without I/O."""
    mode = fields.get("mode", "one_shot")
    observed_at = fields.get("observed_at")
    count = fields.get("final_count", 0)
    framed = fields.get("stream_output", False)
    output = fields.get("output_evidence", _COMPLETED if count and not framed else None)
    terminal = fields.get("terminal")
    config = (
        None
        if mode is None
        else result_wait.task_evidence.TaskObservationConfig(
            mode,
            bool(framed),
            "test.inbox",
            "test.outbox",
            "test.ctrl_in",
            "test.ctrl_out",
            "test.status" if mode == "pipeline" else None,
        )
    )
    materialized = result_wait.ResultMaterialization(
        config,
        terminal_evidence=terminal,
        terminal_observed_monotonic=observed_at if terminal is not None else None,
        output_evidence=output,
    )
    collected = result_wait._CollectedResult(
        scope=fields.get("scope", "task"),
        deadline=fields.get("deadline"),
        final_count=count,
        stream_output=framed,
        first_final_seen=fields.get("first_final_seen", observed_at if count else None),
        pending_changed_at=fields.get(
            "pending_changed_at", observed_at if count else None
        ),
        latest_pending=fields.get("latest_pending"),
        pending_ordinary_count=fields.get("pending_ordinary_count", 0),
        boundary=(100, observed_at or 0.0) if fields.get("boundary_seen") else None,
    )
    return collected, materialized


@pytest.mark.parametrize(
    ("fields", "now", "expected"),
    [
        pytest.param(
            {
                "mode": "one_shot",
                "scope": "batch",
                "terminal": _WRAPPER_LOST,
                "observed_at": 101.0,
                "first_final_seen": 100.0,
                "pending_ordinary_count": 2,
                "deadline": 110.0,
            },
            101.0,
            {"action": "wait", "wait_until": 101.5},
            id="withdrawn-one-shot-window-cannot-age-wrapper-grace",
        ),
        pytest.param(
            {
                "mode": "persistent",
                "scope": "batch",
                "terminal": _WRAPPER_LOST,
                "observed_at": 101.0,
                "pending_changed_at": 100.0,
                "pending_ordinary_count": 0,
                "deadline": 110.0,
            },
            101.0,
            {"action": "wait", "wait_until": 101.5},
            id="emptied-persistent-window-cannot-age-wrapper-grace",
        ),
        pytest.param(
            {
                "mode": "persistent",
                "scope": "batch",
                "terminal": _WRAPPER_LOST,
                "observed_at": 101.0,
                "first_final_seen": 100.0,
                "pending_changed_at": 100.25,
                "stream_output": True,
                "pending_ordinary_count": 0,
                "deadline": 110.0,
            },
            101.0,
            {"action": "wait", "wait_until": 101.5},
            id="revoked-ordinary-before-partial-cannot-age-wrapper-grace",
        ),
        pytest.param(
            {
                "mode": "one_shot",
                "scope": "batch",
                "terminal": _WRAPPER_LOST,
                "observed_at": 101.0,
                "first_final_seen": 100.0,
                "final_count": 1,
                "deadline": 110.0,
            },
            101.0,
            {"action": "result", "status": "failed", "error": WRAPPER_LOST_ERROR},
            id="actual-final-keeps-original-clock-before-wrapper-loss",
        ),
        pytest.param(
            {
                "mode": "persistent",
                "scope": "batch",
                "terminal": _WRAPPER_LOST,
                "observed_at": 101.0,
                "pending_changed_at": 100.0,
                "latest_pending": 120,
                "stream_output": True,
                "deadline": 110.0,
            },
            101.0,
            {"action": "wait", "wait_until": 101.5},
            id="partial-stream-cannot-age-wrapper-lost-grace",
        ),
        pytest.param(
            {
                "mode": "persistent",
                "scope": "batch",
                "final_count": 1,
                "observed_at": 10.5,
                "latest_pending": 120,
                "stream_output": True,
                "deadline": 12.0,
            },
            10.5,
            {"action": "wait", "wait_until": 12.0},
            id="framed-final-needs-proof-without-quiet-wake",
        ),
        pytest.param(
            {}, 10.0, {"action": "wait", "wait_until": None}, id="queued-indefinite"
        ),
        pytest.param(
            {
                "scope": "task",
                "final_count": 1,
                "output_evidence": None,
                "deadline": 10.0,
            },
            10.0,
            {"action": "timeout"},
            id="readonly-ambiguous-ordinary-needs-proof",
        ),
        pytest.param(
            {"deadline": 12.0},
            10.0,
            {"action": "wait", "wait_until": 12.0},
            id="queued-caller-deadline",
        ),
        pytest.param(
            {"deadline": 10.0}, 10.0, {"action": "timeout"}, id="queued-zero-budget"
        ),
        pytest.param(
            {"mode": None, "deadline": 12.0, "final_count": 1},
            10.0,
            {"action": "wait", "wait_until": 12.0},
            id="unknown-mode-output-needs-proof",
        ),
        pytest.param(
            {"terminal": _COMPLETED, "observed_at": 10.5, "deadline": 12.0},
            10.0,
            {"action": "wait", "wait_until": 11.0},
            id="completion-grace-before-caller",
        ),
        pytest.param(
            {"terminal": _COMPLETED, "observed_at": 12.5, "deadline": 12.0},
            10.0,
            {"action": "wait", "wait_until": 12.0},
            id="caller-before-completion-grace",
        ),
        pytest.param(
            {"terminal": _COMPLETED, "observed_at": 10.5},
            10.0,
            {"action": "wait", "wait_until": 11.0},
            id="completion-grace-without-caller",
        ),
        pytest.param(
            {"terminal": _COMPLETED, "observed_at": 10.5},
            11.0,
            {"action": "result", "status": "completed", "error": None},
            id="empty-completion-at-original-grace",
        ),
        pytest.param(
            {"terminal": _COMPLETED, "observed_at": 10.5, "deadline": 11.0},
            11.0,
            {"action": "result", "status": "completed"},
            id="equal-grace-and-caller-empty-completion",
        ),
        pytest.param(
            {"terminal": _COMPLETED, "observed_at": 12.5, "deadline": 12.0},
            12.0,
            {"action": "timeout"},
            id="empty-completion-caller-expires-first",
        ),
        pytest.param(
            {"terminal": _COMPLETED, "observed_at": 10.5, "deadline": 10.0},
            10.0,
            {"action": "timeout"},
            id="empty-completion-zero-budget-before-grace",
        ),
        pytest.param(
            {"terminal": _COMPLETED, "observed_at": 10.5, "final_count": 1},
            10.0,
            {"action": "result", "status": "completed"},
            id="final-null-or-emitted-presence-is-not-absence",
        ),
        pytest.param(
            {
                "terminal": _COMPLETED,
                "observed_at": 10.5,
                "deadline": 10.0,
                "final_count": 1,
            },
            10.0,
            {"action": "result", "status": "completed"},
            id="fresh-final-output-at-zero-budget",
        ),
        pytest.param(
            {
                "terminal": _terminal("completed", value="diagnostic snapshot value"),
                "observed_at": 10.5,
            },
            10.0,
            {"action": "wait", "wait_until": 11.0},
            id="snapshot-value-does-not-replace-final-presence",
        ),
        pytest.param(
            {"final_count": 1, "observed_at": 10.5, "deadline": 12.0},
            10.0,
            {"action": "wait", "wait_until": 11.0},
            id="one-shot-final-only-original-quiet-grace",
        ),
        pytest.param(
            {"final_count": 1, "observed_at": 10.5},
            11.0,
            {"action": "result", "status": "completed"},
            id="one-shot-final-only-at-quiet-grace",
        ),
        pytest.param(
            {"final_count": 1, "observed_at": 12.5, "deadline": 12.0},
            12.0,
            {"action": "result", "status": "completed"},
            id="one-shot-final-only-at-caller-before-grace",
        ),
        pytest.param(
            {"final_count": 1, "deadline": 10.0},
            10.0,
            {"action": "result", "status": "completed"},
            id="one-shot-final-only-zero-budget",
        ),
        pytest.param(
            {"final_count": 1, "stream_output": True, "deadline": 12.0},
            12.0,
            {"action": "timeout"},
            id="ineligible-stream-or-ambiguous-output-at-deadline",
        ),
        pytest.param(
            {
                "mode": "persistent",
                "scope": "batch",
                "boundary_seen": True,
                "final_count": 1,
                "deadline": 12.0,
            },
            10.0,
            {"action": "result", "status": "completed"},
            id="persistent-explicit-boundary-final-batch",
        ),
        pytest.param(
            {
                "mode": "persistent",
                "scope": "batch",
                "boundary_seen": True,
                "final_count": 1,
                "deadline": 10.0,
            },
            10.0,
            {"action": "result", "status": "completed"},
            id="persistent-boundary-final-batch-at-deadline",
        ),
        pytest.param(
            {
                "mode": "persistent",
                "scope": "batch",
                "boundary_seen": True,
                "observed_at": 10.5,
                "deadline": 12.0,
            },
            10.0,
            {"action": "wait", "wait_until": 11.0},
            id="persistent-empty-boundary-awaits-original-grace",
        ),
        pytest.param(
            {
                "mode": "persistent",
                "scope": "batch",
                "boundary_seen": True,
                "observed_at": 10.5,
                "deadline": 12.0,
            },
            11.0,
            {"action": "result", "status": "completed"},
            id="persistent-empty-boundary-completes-at-original-grace",
        ),
        pytest.param(
            {
                "mode": "persistent",
                "scope": "batch",
                "boundary_seen": True,
                "observed_at": 10.5,
                "deadline": 10.0,
            },
            10.0,
            {"action": "timeout"},
            id="persistent-empty-boundary-caller-before-grace",
        ),
        pytest.param(
            {
                "mode": "persistent",
                "scope": "task",
                "boundary_seen": True,
                "final_count": 1,
                "deadline": 12.0,
            },
            10.0,
            {"action": "wait", "wait_until": 12.0},
            id="persistent-batch-is-not-task-completion",
        ),
        pytest.param(
            {
                "mode": "persistent",
                "scope": "batch",
                "final_count": 1,
                "observed_at": 10.5,
                "deadline": 12.0,
            },
            10.0,
            {"action": "wait", "wait_until": 11.0},
            id="persistent-nonstream-quiet-batch-awaits-grace",
        ),
        pytest.param(
            {
                "mode": "persistent",
                "scope": "batch",
                "final_count": 1,
                "observed_at": 10.5,
            },
            11.0,
            {"action": "result", "status": "completed"},
            id="persistent-nonstream-quiet-batch-at-grace",
        ),
        pytest.param(
            {
                "mode": "persistent",
                "scope": "batch",
                "final_count": 1,
                "observed_at": 12.5,
                "deadline": 12.0,
            },
            12.0,
            {"action": "result", "status": "completed"},
            id="persistent-nonstream-batch-last-drain-at-caller",
        ),
        pytest.param(
            {
                "mode": "persistent",
                "scope": "batch",
                "final_count": 1,
                "stream_output": True,
                "deadline": 12.0,
            },
            10.0,
            {"action": "wait", "wait_until": 12.0},
            id="persistent-emitted-stream-needs-boundary",
        ),
        pytest.param(
            {
                "mode": "persistent",
                "scope": "batch",
                "final_count": 1,
                "stream_output": True,
                "deadline": 12.0,
            },
            12.0,
            {"action": "timeout"},
            id="persistent-emitted-stream-no-boundary-at-caller",
        ),
        pytest.param(
            {
                "mode": "persistent",
                "scope": "task",
                "terminal": _COMPLETED,
                "observed_at": 10.5,
            },
            11.0,
            {"action": "result", "status": "completed"},
            id="persistent-task-lifecycle-empty-at-grace",
        ),
        pytest.param(
            {
                "mode": "pipeline",
                "final_count": 1,
                "observed_at": 10.5,
                "deadline": 12.0,
            },
            11.0,
            {"action": "wait", "wait_until": 12.0},
            id="pipeline-quiet-output-needs-lifecycle-proof",
        ),
        pytest.param(
            {"mode": "pipeline", "final_count": 1, "deadline": 12.0},
            12.0,
            {"action": "timeout"},
            id="pipeline-intermediate-output-at-caller",
        ),
        pytest.param(
            {
                "mode": "pipeline",
                "terminal": _COMPLETED,
                "final_count": 1,
                "observed_at": 10.5,
            },
            10.0,
            {"action": "result", "status": "completed"},
            id="pipeline-lifecycle-with-final-output",
        ),
        pytest.param(
            {"mode": "pipeline", "terminal": _COMPLETED, "observed_at": 10.5},
            11.0,
            {"action": "result", "status": "completed"},
            id="pipeline-lifecycle-empty-at-original-grace",
        ),
        pytest.param(
            {
                "mode": "pipeline",
                "terminal": _COMPLETED,
                "observed_at": 12.5,
                "deadline": 12.0,
            },
            12.0,
            {"action": "timeout"},
            id="pipeline-lifecycle-empty-caller-first",
        ),
    ],
)
def test_settlement_uses_original_clocks_and_mode_completion_rules(
    fields: dict[str, Any], now: float, expected: dict[str, Any]
) -> None:
    """Apply the literal mode/deadline table after acquisition and the last drain."""
    collected, evidence = _collection_facts(fields)
    decision = collected.settle(evidence, now=now)
    for field, value in expected.items():
        assert getattr(decision, field) == value, field


@pytest.mark.parametrize("status", ["failed", "timeout", "cancelled", "killed"])
@pytest.mark.parametrize("final_output_seen", [False, True])
@pytest.mark.parametrize("mode", ["one_shot", "persistent", "pipeline"])
def test_task_failure_keeps_its_status_and_error_even_with_final_output(
    status: str,
    final_output_seen: bool,
    mode: Literal["one_shot", "persistent", "pipeline"],
) -> None:
    """Selected task failure is never converted into success or observer timeout."""
    collected, evidence = _collection_facts(
        {
            "mode": mode,
            "scope": "batch" if mode == "persistent" else "task",
            "deadline": 10.0,
            "terminal": _terminal(status, error="task-owned failure"),
            "observed_at": 19.5,
            "final_count": int(final_output_seen),
        }
    )
    decision = collected.settle(evidence, now=10.0)
    assert (decision.action, decision.status, decision.error) == (
        "result",
        status,
        "task-owned failure",
    )


@pytest.mark.parametrize("final_output_seen", [False, True])
@pytest.mark.parametrize(
    ("deadline", "now", "expected"),
    [
        pytest.param(
            30.0, 10.0, {"action": "wait", "wait_until": 20.0}, id="hold-original-grace"
        ),
        pytest.param(
            15.0,
            10.0,
            {"action": "wait", "wait_until": 15.0},
            id="caller-bounds-held-verdict",
        ),
        pytest.param(
            30.0,
            20.0,
            {"action": "result", "status": "failed", "error": WRAPPER_LOST_ERROR},
            id="held-verdict-at-original-grace",
        ),
        pytest.param(
            15.0,
            15.0,
            {"action": "result", "status": "failed", "error": WRAPPER_LOST_ERROR},
            id="held-verdict-at-caller-before-grace",
        ),
    ],
)
def test_wrapper_lost_holds_only_original_grace_and_output_cannot_override_it(
    final_output_seen: bool,
    deadline: float,
    now: float,
    expected: dict[str, Any],
) -> None:
    """Late task proof has its original window; readable/emitted values retain failure."""
    collected, evidence = _collection_facts(
        {
            "deadline": deadline,
            "terminal": _WRAPPER_LOST,
            "observed_at": 19.5,
            "final_count": int(final_output_seen),
        }
    )
    decision = collected.settle(evidence, now=now)
    for field, value in expected.items():
        assert getattr(decision, field) == value, field


def test_replacing_wrapper_lost_with_task_completion_keeps_original_grace() -> None:
    """Selected stronger proof changes the verdict, not its visibility clock."""
    collected, held = _collection_facts(
        {
            "deadline": 30.0,
            "terminal": _WRAPPER_LOST,
            "observed_at": 20.0 - WEFT_COMPLETED_RESULT_GRACE_SECONDS,
        }
    )
    initial = collected.settle(held, now=10.0)
    assert (initial.action, initial.wait_until) == ("wait", 20.0)

    # Acquisition supplies stronger proof without changing its visibility clock.
    completed = replace(held, terminal_evidence=_COMPLETED)
    replacement = collected.settle(completed, now=15.0)
    assert (replacement.action, replacement.wait_until) == ("wait", 20.0)
    settled = collected.settle(completed, now=20.0)
    assert (settled.action, settled.status) == ("result", "completed")


@pytest.mark.parametrize("scope", ["batch", "task"], ids=["consuming", "readonly"])
@pytest.mark.parametrize("withdrawal", ["empty", "ambiguous", "direct-replacement"])
def test_replacement_ordinary_row_starts_its_own_quiet_clock(
    scope: Literal["batch", "task"], withdrawal: str
) -> None:
    """A new row cannot inherit a revoked pending value's visibility clock."""
    collected, materialized = _collection_facts(
        {"scope": scope, "deadline": 110.0, "output_evidence": None}
    )
    row_a = ('"A"', 1000)
    row_b = ('"B"', 2000)
    output_a = replace(
        _COMPLETED,
        source="outbox",
        classification="result_without_terminal",
        observed_at=row_a[1],
        value="A",
    )
    output_b = replace(output_a, observed_at=row_b[1], value="B")

    def observe(
        rows: list[tuple[str, int]],
        output: TaskEvidenceSnapshot | None,
        *,
        now: float,
    ) -> result_wait.ResultMaterialization:
        evidence = replace(materialized, output_evidence=output)
        if scope == "task":
            collected.observe_readonly(
                evidence, final_output_seen=bool(rows), stream_framed=False, now=now
            )
        else:
            assert collected.select_claims(rows, evidence, now=now) == (), (
                "pending ordinary rows need their own quiet grace before claim"
            )
        return evidence

    first = observe([row_a], output_a, now=100.0)
    initial = collected.settle(first, now=100.0)
    assert (initial.action, initial.wait_until) == ("wait", 100.5)
    if withdrawal == "empty":
        observe([], None, now=100.25)
    elif withdrawal == "ambiguous":
        observe([row_a, row_b], None, now=100.25)

    replacement = observe([row_b], output_b, now=101.0)
    quiet = collected.settle(replacement, now=101.0)
    assert (quiet.action, quiet.wait_until) == ("wait", 101.5)
    terminal = replace(
        replacement,
        terminal_evidence=_WRAPPER_LOST,
        terminal_observed_monotonic=101.0,
    )
    held = collected.settle(terminal, now=101.0)
    assert (held.action, held.wait_until) == ("wait", 101.5)


def test_replacement_pending_window_keeps_actual_delivered_final_clock() -> None:
    """A decoded streaming final remains proof after later pending-row changes."""
    collected, materialized = _collection_facts(
        {"scope": "batch", "deadline": 110.0, "output_evidence": None}
    )
    collected.select_claims([], materialized, now=100.0)
    collected.record_output(
        result_wait.DecodedOutboxValue("delivered", emitted=True, streamed=True),
        now=100.0,
        show_stderr=False,
    )
    collected.select_claims([], materialized, now=100.25)
    collected.select_claims([('"B"', 2000)], materialized, now=101.0)
    terminal = replace(
        materialized,
        terminal_evidence=_WRAPPER_LOST,
        terminal_observed_monotonic=101.0,
    )
    failed = collected.settle(terminal, now=101.0)
    assert (failed.action, failed.status, failed.error) == (
        "result",
        "failed",
        WRAPPER_LOST_ERROR,
    )
