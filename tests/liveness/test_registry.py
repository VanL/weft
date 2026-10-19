"""Tests for the process-local runtime liveness registry."""

from __future__ import annotations

import pytest

from weft.ext import RunnerHandle
from weft.liveness import registry

pytestmark = [pytest.mark.shared]


def _handle(*, runner: str = "example", provider: str | None = None) -> RunnerHandle:
    observations = {}
    if provider is not None:
        observations["liveness_provider"] = provider
    return RunnerHandle(
        runner=runner,
        kind="supervised-process",
        id="runtime-1",
        control={"authority": "external-supervisor"},
        observations=observations,
    )


def test_registry_routes_to_explicit_provider_and_passes_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "_runtime_liveness_probes", {})
    observed: list[tuple[RunnerHandle, float]] = []

    def probe(handle: RunnerHandle, budget: float) -> registry.RuntimeLiveness:
        observed.append((handle, budget))
        return "live"

    registry.register_runtime_liveness_probe(" docker ", probe)

    assert (
        registry.runtime_liveness_from_registered_probe(
            _handle(runner="manager-supervisor", provider=" docker "),
            timeout_seconds=1.25,
        )
        == "live"
    )
    assert observed == [
        (_handle(runner="manager-supervisor", provider=" docker "), 1.25)
    ]


@pytest.mark.parametrize("provider", [None, "", "   ", 42])
def test_registry_falls_back_to_runner_for_invalid_provider(
    monkeypatch: pytest.MonkeyPatch,
    provider: object,
) -> None:
    monkeypatch.setattr(registry, "_runtime_liveness_probes", {})
    registry.register_runtime_liveness_probe(
        "example", lambda _handle, _budget: "stale"
    )

    observations = {} if provider is None else {"liveness_provider": provider}
    handle = RunnerHandle(
        runner="example",
        kind="supervised-process",
        id="runtime-1",
        control={"authority": "external-supervisor"},
        observations=observations,
    )

    assert registry.runtime_liveness_from_registered_probe(handle) == "stale"


def test_registry_miss_exception_and_invalid_result_are_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "_runtime_liveness_probes", {})
    assert registry.runtime_liveness_from_registered_probe(_handle()) == "unknown"

    def raises(_handle: RunnerHandle, _budget: float) -> registry.RuntimeLiveness:
        raise RuntimeError("boom")

    registry.register_runtime_liveness_probe("example", raises)
    assert registry.attempt_runtime_liveness_from_registered_probe(_handle()) is None
    assert registry.runtime_liveness_from_registered_probe(_handle()) == "unknown"

    def invalid(_handle: RunnerHandle, _budget: float) -> str:
        return "invalid"

    # A third-party probe can violate its declared result contract.
    registry.register_runtime_liveness_probe("example", invalid)  # type: ignore[arg-type]
    assert registry.attempt_runtime_liveness_from_registered_probe(_handle()) is None
    assert registry.runtime_liveness_from_registered_probe(_handle()) == "unknown"


def test_registry_rejects_blank_key_and_replaces_existing_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "_runtime_liveness_probes", {})
    with pytest.raises(ValueError, match="non-empty"):
        registry.register_runtime_liveness_probe("  ", lambda _handle, _budget: "live")

    registry.register_runtime_liveness_probe("example", lambda _handle, _budget: "live")
    registry.register_runtime_liveness_probe(
        "example", lambda _handle, _budget: "stale"
    )
    assert registry.runtime_liveness_from_registered_probe(_handle()) == "stale"


@pytest.mark.parametrize("registered_runner_only", [False, True])
def test_raw_registry_presence_alias_routing_and_budget(
    monkeypatch: pytest.MonkeyPatch,
    registered_runner_only: bool,
) -> None:
    """Cold loading must distinguish an absent callback from completed unknown."""
    monkeypatch.setattr(registry, "_runtime_liveness_probes", {})
    handle = _handle(runner="manager-supervisor", provider=" docker ")
    if registered_runner_only:
        registry.register_runtime_liveness_probe(
            "manager-supervisor", lambda *_args: "live"
        )
    assert registry.has_runtime_liveness_probe(handle) is False
    missing = registry.probe_registered_runtime(handle, 1.25)
    assert (missing.evidence, missing.outcome) == ("unknown", "unavailable")
    observed: list[tuple[RunnerHandle, float]] = []

    def probe(selected: RunnerHandle, budget: float) -> registry.RuntimeLiveness:
        observed.append((selected, budget))
        return "unknown"

    registry.register_runtime_liveness_probe("docker", probe)
    assert registry.has_runtime_liveness_probe(handle) is True
    result = registry.probe_registered_runtime(handle, 1.25)
    assert (result.evidence, result.outcome, result.reason) == (
        "unknown",
        "completed",
        "extension_unknown",
    )
    assert observed == [(handle, 1.25)]
