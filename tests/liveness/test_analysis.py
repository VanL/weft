"""Tests for authority-aware point-in-time liveness analysis."""

from __future__ import annotations

import json

import pytest

from weft.ext import RunnerHandle
from weft.helpers import tid_short_form
from weft.liveness import analysis, registry
from weft.liveness.models import HostProcessObservation

pytestmark = [pytest.mark.shared]


def _payload(
    handle: RunnerHandle | None, *, terminal: object = False
) -> dict[str, object]:
    payload: dict[str, object] = {
        "full": "1779000000000000001",
        "short": tid_short_form("1779000000000000001"),
        "terminal": terminal,
    }
    if handle is not None:
        payload["runtime_handle"] = handle.to_dict()
    return payload


def _handle(
    *,
    authority: str,
    processes: list[dict[str, object]] | None = None,
    runner: str = "example",
) -> RunnerHandle:
    return RunnerHandle(
        runner=runner,
        kind="process",
        id="runtime-1",
        control={"authority": authority},
        observations={"host_processes": processes or []},
    )


def test_host_pid_analysis_uses_multi_process_truth_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations = iter(
        [
            HostProcessObservation("stale", "identity_mismatch"),
            HostProcessObservation("live", "identity_match"),
        ]
    )
    monkeypatch.setattr(
        analysis,
        "inspect_host_process",
        lambda *_args, **_kwargs: next(observations),
    )
    handle = _handle(
        authority="host-pid",
        processes=[
            {"pid": 11, "create_time": 1.0},
            {"pid": 12, "create_time": 2.0},
        ],
    )

    result = analysis.analyze_liveness("1779000000000000001", _payload(handle))

    assert result.evidence == "live"
    assert result.attempted is True


def test_host_pid_analysis_requires_exact_identity_and_propagates_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        analysis,
        "inspect_host_process",
        lambda *_args, **_kwargs: HostProcessObservation("unknown", "access_denied"),
    )
    handle = _handle(
        authority="host-pid",
        processes=[{"pid": 11, "create_time": 1.0}],
    )

    assert (
        analysis.analyze_liveness("1779000000000000001", _payload(handle)).evidence
        == "unknown"
    )
    assert (
        analysis.analyze_liveness(
            "1779000000000000001", _payload(handle, terminal=True)
        ).evidence
        == "stale"
    )


def test_extension_authority_ignores_live_host_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "_runtime_liveness_probes", {})
    registry.register_runtime_liveness_probe("example", lambda *_args: "stale")
    monkeypatch.setattr(
        analysis,
        "inspect_host_process",
        lambda *_args: HostProcessObservation("live", "identity_match"),
    )
    handle = _handle(
        authority="runner",
        processes=[{"pid": 11, "create_time": 1.0}],
    )

    assert (
        analysis.analyze_liveness("1779000000000000001", _payload(handle)).evidence
        == "stale"
    )


def test_extension_probe_failure_is_not_completed_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "_runtime_liveness_probes", {})

    def failing_probe(
        _handle: RunnerHandle, _budget: float
    ) -> registry.RuntimeLiveness:
        raise RuntimeError("provider unavailable")

    registry.register_runtime_liveness_probe("example", failing_probe)
    handle = _handle(authority="runner")

    result = analysis.analyze_liveness("1779000000000000001", _payload(handle))

    assert result.evidence == "unknown"
    assert result.attempted is False
    assert result.reason == "extension_probe_failed"


def test_invalid_or_missing_handle_fails_toward_unknown_unless_terminal() -> None:
    assert (
        analysis.analyze_liveness("1779000000000000001", _payload(None)).evidence
        == "unknown"
    )
    invalid = _payload(None)
    invalid["runtime_handle"] = {"runner": "bad"}
    assert (
        analysis.analyze_liveness("1779000000000000001", invalid).evidence == "unknown"
    )
    assert (
        analysis.analyze_liveness(
            "1779000000000000001", _payload(None, terminal=True)
        ).evidence
        == "stale"
    )


def test_runtime_generation_uses_only_contract_fields_and_canonical_process_order() -> (
    None
):
    first = _payload(
        RunnerHandle(
            runner="example",
            kind="process",
            id="runtime-1",
            control={"authority": "host-pid"},
            observations={
                "host_processes": [
                    {"pid": 12, "create_time": 2.0},
                    {"pid": 11, "create_time": 1.0},
                ],
                "diagnostic": "one",
            },
            metadata={"noise": 1},
        )
    )
    second = json.loads(json.dumps(first))
    second["runtime_handle"]["observations"]["host_processes"].reverse()
    second["runtime_handle"]["observations"]["diagnostic"] = "two"
    second["runtime_handle"]["metadata"] = {"noise": 2}

    assert analysis.runtime_generation(first) == analysis.runtime_generation(second)

    second["runtime_handle"]["id"] = "runtime-2"
    assert analysis.runtime_generation(first) != analysis.runtime_generation(second)


@pytest.mark.parametrize(
    ("provider_result", "outcome", "raw_reason", "legacy_reason", "attempted"),
    [
        (None, "unavailable", "extension_probe_unavailable", "extension_unknown", True),
        ("unknown", "completed", "extension_unknown", "extension_unknown", True),
        (
            "invalid",
            "failed",
            "extension_probe_failed",
            "extension_probe_failed",
            False,
        ),
        ("raises", "failed", "extension_probe_failed", "extension_probe_failed", False),
        ([], "failed", "extension_probe_failed", "extension_probe_failed", False),
        ({}, "failed", "extension_probe_failed", "extension_probe_failed", False),
        ("stale", "completed", "extension_stale", "extension_stale", True),
        ("live", "completed", "extension_live", "extension_live", True),
    ],
)
def test_raw_extension_evidence_preserves_legacy_retirement_policy(
    monkeypatch: pytest.MonkeyPatch,
    provider_result: object,
    outcome: str,
    raw_reason: str,
    legacy_reason: str,
    attempted: bool,
) -> None:
    """Raw provider facts must not inherit a terminal-hint conclusion."""
    monkeypatch.setattr(registry, "_runtime_liveness_probes", {})
    if provider_result is not None:

        def probe(_handle: RunnerHandle, _budget: float) -> registry.RuntimeLiveness:
            if provider_result == "raises":
                raise RuntimeError("provider failure")
            return provider_result  # type: ignore[return-value]

        registry.register_runtime_liveness_probe("example", probe)
    handle = _handle(authority="runner")
    raw = analysis.probe_runtime_liveness("1779000000000000001", handle)
    expected_evidence = (
        provider_result if provider_result in ("live", "stale") else "unknown"
    )
    assert (raw.evidence, raw.outcome, raw.reason) == (
        expected_evidence,
        outcome,
        raw_reason,
    )
    legacy = analysis.analyze_liveness("1779000000000000001", _payload(handle))
    assert (legacy.evidence, legacy.reason, legacy.attempted) == (
        expected_evidence,
        legacy_reason,
        attempted,
    )
    terminal = analysis.analyze_liveness(
        "1779000000000000001", _payload(handle, terminal=True)
    )
    assert (terminal.evidence, terminal.reason, terminal.attempted) == (
        ("live", "extension_live", True)
        if provider_result == "live"
        else ("stale", "terminal_hint", True)
    )


@pytest.mark.parametrize(
    ("authority", "processes", "reason", "outcome"),
    [
        (
            "host-pid",
            [{"pid": 11, "create_time": 1.0}],
            "host_identity_unresolved",
            "completed",
        ),
        ("host-pid", [], "missing_exact_host_identity", "completed"),
        ("unsupported", [], "unknown_control_authority", "unsupported"),
    ],
)
def test_raw_host_unknown_does_not_inherit_terminal_hint(
    monkeypatch: pytest.MonkeyPatch,
    authority: str,
    processes: list[dict[str, object]],
    reason: str,
    outcome: str,
) -> None:
    """A terminal lifecycle cannot establish runtime death from an unresolved PID."""
    monkeypatch.setattr(
        analysis,
        "inspect_host_process",
        lambda *_args, **_kwargs: HostProcessObservation(
            "unknown", "process_unresolved"
        ),
    )
    handle = _handle(authority=authority, processes=processes)
    raw = analysis.probe_runtime_liveness("1779000000000000001", handle)
    assert (raw.evidence, raw.reason, raw.outcome) == ("unknown", reason, outcome)
    nonterminal = analysis.analyze_liveness("1779000000000000001", _payload(handle))
    assert (nonterminal.evidence, nonterminal.reason, nonterminal.attempted) == (
        "unknown",
        reason,
        True,
    )
    legacy = analysis.analyze_liveness(
        "1779000000000000001", _payload(handle, terminal=True)
    )
    assert (legacy.evidence, legacy.reason, legacy.attempted) == (
        "stale",
        "terminal_hint",
        True,
    )
