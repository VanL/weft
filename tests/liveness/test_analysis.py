"""Tests for authority-aware point-in-time liveness analysis."""

from __future__ import annotations

import json

import pytest

from weft.ext import RunnerHandle
from weft.helpers import tid_short_form
from weft.liveness import analysis
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
    monkeypatch.setattr(
        analysis,
        "attempt_runtime_liveness_from_registered_probe",
        lambda *_args, **_kwargs: "stale",
    )
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
    monkeypatch.setattr(
        analysis,
        "attempt_runtime_liveness_from_registered_probe",
        lambda *_args, **_kwargs: None,
    )
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
