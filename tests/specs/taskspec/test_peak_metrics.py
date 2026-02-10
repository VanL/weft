"""Spec checks for peak metric tracking in TaskSpec state (TS-1)."""

from __future__ import annotations

from tests.taskspec import fixtures


def test_update_metrics_tracks_peak_values() -> None:
    taskspec = fixtures.create_minimal_taskspec()

    taskspec.update_metrics(memory=1.0, cpu=10, fds=2, net_connections=1)
    assert taskspec.state.peak_memory == 1.0
    assert taskspec.state.peak_cpu == 10
    assert taskspec.state.peak_fds == 2
    assert taskspec.state.peak_net_connections == 1

    taskspec.update_metrics(memory=0.5, cpu=5, fds=1, net_connections=0)
    assert taskspec.state.peak_memory == 1.0
    assert taskspec.state.peak_cpu == 10
    assert taskspec.state.peak_fds == 2
    assert taskspec.state.peak_net_connections == 1

    taskspec.update_metrics(memory=2.5, cpu=25, fds=4, net_connections=3)
    assert taskspec.state.peak_memory == 2.5
    assert taskspec.state.peak_cpu == 25
    assert taskspec.state.peak_fds == 4
    assert taskspec.state.peak_net_connections == 3

    assert not hasattr(taskspec.state, "max_memory")
