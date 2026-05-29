"""Tests for keyed control-channel PING/PONG probing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft._constants import (
    SERVICE_STATUS_DRAINING,
    WEFT_MANAGER_OUTBOX_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
)
from weft.context import build_context
from weft.core.control_probe import (
    coerce_pong_response,
    pong_proves_dispatch_eligible,
    send_keyed_ping_probe,
)
from weft.core.manager_runtime import _matched_pong_proves_manager_record

pytestmark = [pytest.mark.shared]


def test_send_keyed_ping_probe_matches_expected_pong(tmp_path: Path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1775622400000000101"
    ctrl_in_name = f"T{tid}.ctrl_in"
    ctrl_out_name = f"T{tid}.ctrl_out"
    request_id = "probe-request-1"

    ctrl_out = ctx.queue(ctrl_out_name, persistent=False)
    try:
        ctrl_out.write(
            json.dumps(
                {
                    "command": "PING",
                    "status": "ok",
                    "message": "PONG",
                    "tid": tid,
                    "request_id": request_id,
                    "task_status": "running",
                }
            )
        )
    finally:
        ctrl_out.close()

    result = send_keyed_ping_probe(
        ctx,
        tid=tid,
        ctrl_in_name=ctrl_in_name,
        ctrl_out_name=ctrl_out_name,
        request_id=request_id,
        timeout=0.0,
    )

    assert result.error is None
    assert result.timed_out is False
    assert result.matched is not None
    assert result.matched.request_id == request_id
    assert result.matched.payload["task_status"] == "running"
    assert result.matched.observed_at is not None

    ctrl_in = ctx.queue(ctrl_in_name, persistent=True)
    try:
        ping_payload = json.loads(str(ctrl_in.read_one()))
    finally:
        ctrl_in.close()
    assert ping_payload == {"command": "PING", "request_id": request_id}


def test_send_keyed_ping_probe_ignores_unmatched_pongs(tmp_path: Path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1775622400000000102"
    ctrl_in_name = f"T{tid}.ctrl_in"
    ctrl_out_name = f"T{tid}.ctrl_out"

    ctrl_out = ctx.queue(ctrl_out_name, persistent=False)
    try:
        ctrl_out.write("not-json")
        ctrl_out.write(
            json.dumps(
                {
                    "command": "PING",
                    "status": "ok",
                    "message": "PONG",
                    "tid": tid,
                    "request_id": "other-request",
                    "task_status": "running",
                }
            )
        )
        ctrl_out.write(
            json.dumps(
                {
                    "command": "PING",
                    "status": "ok",
                    "message": "PONG",
                    "tid": "different-tid",
                    "request_id": "wanted-request",
                    "task_status": "running",
                }
            )
        )
    finally:
        ctrl_out.close()

    result = send_keyed_ping_probe(
        ctx,
        tid=tid,
        ctrl_in_name=ctrl_in_name,
        ctrl_out_name=ctrl_out_name,
        request_id="wanted-request",
        timeout=0.0,
    )

    assert result.error is None
    assert result.matched is None
    assert result.timed_out is True


def test_coerce_pong_response_rejects_payload_without_task_status() -> None:
    raw = json.dumps(
        {
            "command": "PING",
            "status": "ok",
            "message": "PONG",
            "tid": "1775622400000000103",
            "request_id": "probe-request-3",
        }
    )

    assert (
        coerce_pong_response(
            raw,
            tid="1775622400000000103",
            request_id="probe-request-3",
        )
        is None
    )


_CTRL_IN = "weft.manager.ctrl_in"
_CTRL_OUT = "weft.manager.ctrl_out"
_ROOT = "/projects/demo"


def _eligible_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "task_status": "running",
        "role": "manager",
        "requests": WEFT_SPAWN_REQUESTS_QUEUE,
        "ctrl_in": _CTRL_IN,
        "ctrl_out": _CTRL_OUT,
        "weft_context": _ROOT,
    }
    payload.update(overrides)
    return payload


def _gate(payload: dict[str, object], record: dict[str, object] | None = None) -> bool:
    return pong_proves_dispatch_eligible(
        payload,
        record={"weft_context": _ROOT} if record is None else record,
        ctrl_in_name=_CTRL_IN,
        ctrl_out_name=_CTRL_OUT,
        root_context=_ROOT,
    )


def test_pong_gate_accepts_fully_specified_manager() -> None:
    assert _gate(_eligible_payload()) is True


def test_pong_gate_accepts_absent_manager_selection_fields() -> None:
    # Absent role/requests/ctrl/context are legacy-accepted; only mismatch rejects.
    assert _gate({"task_status": "running"}) is True
    assert _gate({}) is True


@pytest.mark.parametrize(
    "status",
    [
        SERVICE_STATUS_DRAINING,
        "stopping",
        "cancelled",
        "completed",
        "failed",
        "timeout",
        "killed",
    ],
)
def test_pong_gate_rejects_terminal_or_stopping_status(status: str) -> None:
    assert _gate(_eligible_payload(task_status=status)) is False


def test_pong_gate_rejects_should_stop_and_mismatches() -> None:
    assert _gate(_eligible_payload(should_stop=True)) is False
    assert _gate(_eligible_payload(role="worker")) is False
    assert _gate(_eligible_payload(requests="other.queue")) is False
    assert _gate(_eligible_payload(ctrl_in="other.ctrl_in")) is False
    assert _gate(_eligible_payload(ctrl_out="other.ctrl_out")) is False
    assert _gate(_eligible_payload(weft_context="/projects/other")) is False


def test_pong_gate_empty_record_context_falls_back_to_root() -> None:
    # Decision: an empty weft_context means absent -> fall back to root_context.
    record = {"weft_context": ""}
    assert _gate(_eligible_payload(weft_context=_ROOT), record) is True
    assert _gate(_eligible_payload(weft_context=""), record) is False


def test_pong_gate_nonempty_record_context_overrides_root() -> None:
    record = {"weft_context": "/projects/other"}
    assert _gate(_eligible_payload(weft_context="/projects/other"), record) is True
    assert _gate(_eligible_payload(weft_context=_ROOT), record) is False


def test_runtime_predicate_converges_with_gate_on_empty_context(
    tmp_path: Path,
) -> None:
    """Runtime PONG predicate agrees with the shared gate, incl. empty context.

    Both the in-process manager (via the gate) and the runtime delegate to
    ``pong_proves_dispatch_eligible``. This locks the empty-context convergence:
    the runtime previously treated an empty record ``weft_context`` as
    authoritative and would have rejected this root-matching payload.

    Spec: [MA-1] item 4, [MANAGER.8]
    """
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    root_str = str(ctx.root)
    payload = {
        "task_status": "running",
        "role": "manager",
        "requests": WEFT_SPAWN_REQUESTS_QUEUE,
        "ctrl_in": _CTRL_IN,
        "ctrl_out": _CTRL_OUT,
        "outbox": WEFT_MANAGER_OUTBOX_QUEUE,
        "weft_context": root_str,
    }
    record = {"weft_context": ""}

    assert (
        pong_proves_dispatch_eligible(
            payload,
            record=record,
            ctrl_in_name=_CTRL_IN,
            ctrl_out_name=_CTRL_OUT,
            root_context=root_str,
        )
        is True
    )
    assert (
        _matched_pong_proves_manager_record(
            payload,
            context=ctx,
            record=record,
            ctrl_in_name=_CTRL_IN,
            ctrl_out_name=_CTRL_OUT,
        )
        is True
    )
    # Runtime keeps its own outbox nuance after the shared gate passes.
    assert (
        _matched_pong_proves_manager_record(
            dict(payload, outbox="other.outbox"),
            context=ctx,
            record=record,
            ctrl_in_name=_CTRL_IN,
            ctrl_out_name=_CTRL_OUT,
        )
        is False
    )
