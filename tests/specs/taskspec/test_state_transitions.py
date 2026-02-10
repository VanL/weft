"""Spec checks for TaskSpec state transitions (STATE.1/STATE.2)."""

from __future__ import annotations

import pytest

from tests.taskspec import fixtures


def test_mark_started_sets_spawning_state() -> None:
    taskspec = fixtures.create_minimal_taskspec()
    taskspec.mark_started()
    assert taskspec.state.status == "spawning"
    assert taskspec.state.started_at is not None


def test_mark_running_promotes_from_created() -> None:
    taskspec = fixtures.create_minimal_taskspec()
    taskspec.mark_running()
    assert taskspec.state.status == "running"
    assert taskspec.state.started_at is not None


def test_invalid_transition_raises() -> None:
    taskspec = fixtures.create_minimal_taskspec()
    with pytest.raises(ValueError):
        taskspec.set_status("completed")


def test_terminal_state_is_immutable() -> None:
    taskspec = fixtures.create_minimal_taskspec()
    taskspec.mark_running()
    taskspec.mark_completed()
    with pytest.raises(ValueError):
        taskspec.set_status("running")
