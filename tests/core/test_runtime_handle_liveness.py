"""Tests for runtime-handle liveness registry helpers."""

from __future__ import annotations

import pytest

import weft.runtime_liveness as runtime_liveness
from weft.ext import RunnerHandle

pytestmark = [pytest.mark.shared]


def _runtime_handle(*, runner: str = "example") -> RunnerHandle:
    return RunnerHandle(
        runner=runner,
        kind="supervised-process",
        id="runtime-1",
        control={"authority": "external-supervisor"},
        observations={},
        metadata={},
    )


def test_runtime_liveness_registry_returns_unknown_for_missing_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_liveness, "_RUNTIME_LIVENESS_PROBES", {})

    assert (
        runtime_liveness.runtime_liveness_from_registered_probe(_runtime_handle())
        == "unknown"
    )


def test_runtime_liveness_registry_returns_registered_probe_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_liveness, "_RUNTIME_LIVENESS_PROBES", {})

    runtime_liveness.register_runtime_liveness_probe("example", lambda handle: "stale")

    assert (
        runtime_liveness.runtime_liveness_from_registered_probe(_runtime_handle())
        == "stale"
    )


def test_runtime_liveness_registry_returns_unknown_when_probe_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_liveness, "_RUNTIME_LIVENESS_PROBES", {})

    def failing_probe(handle: RunnerHandle) -> runtime_liveness.RuntimeLiveness:
        del handle
        raise RuntimeError("boom")

    runtime_liveness.register_runtime_liveness_probe("example", failing_probe)

    assert (
        runtime_liveness.runtime_liveness_from_registered_probe(_runtime_handle())
        == "unknown"
    )


def test_runtime_liveness_registry_replaces_existing_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_liveness, "_RUNTIME_LIVENESS_PROBES", {})

    runtime_liveness.register_runtime_liveness_probe("example", lambda handle: "live")
    runtime_liveness.register_runtime_liveness_probe("example", lambda handle: "stale")

    assert (
        runtime_liveness.runtime_liveness_from_registered_probe(_runtime_handle())
        == "stale"
    )
