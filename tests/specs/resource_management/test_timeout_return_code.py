"""Spec checks for timeout return codes (RM error handling)."""

from __future__ import annotations

from tests.taskspec import fixtures


def test_mark_timeout_sets_return_code_124() -> None:
    taskspec = fixtures.create_minimal_taskspec()
    taskspec.mark_running()
    taskspec.mark_timeout(error="too slow")
    assert taskspec.state.status == "timeout"
    assert taskspec.state.return_code == 124
