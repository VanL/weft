"""Tests for keyed control-channel PING/PONG probing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft._constants import (
    SERVICE_STATUS_DRAINING,
    TERMINAL_ENVELOPE_TYPE,
    WEFT_MANAGER_OUTBOX_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
)
from weft.context import WeftContext, build_context
from weft.core.control_probe import (
    coerce_pong_response,
    pong_proves_dispatch_eligible,
    reply_bears_request_id,
    send_keyed_ping_probe,
)

pytestmark = [pytest.mark.shared]


def _write_ctrl_out_rows(
    ctx: WeftContext,
    ctrl_out_name: str,
    rows: list[str],
) -> None:
    queue = ctx.queue(ctrl_out_name, persistent=False)
    try:
        for row in rows:
            queue.write(row)
    finally:
        queue.close()


def _peek_ctrl_out_bodies(ctx: WeftContext, ctrl_out_name: str) -> list[str]:
    queue = ctx.queue(ctrl_out_name, persistent=False)
    try:
        return [str(item) for item in queue.peek_generator()]
    finally:
        queue.close()


def test_reply_ownership_accepts_malformed_body_only_with_one_exact_request_id() -> (
    None
):
    assert (
        reply_bears_request_id(
            '{"command":"PING","command":"STATUS","request_id":"owned"}',
            request_id="owned",
        )
        is True
    )
    assert (
        reply_bears_request_id(
            '{"request_id":"other","request_id":"owned"}',
            request_id="owned",
        )
        is False
    )
    assert (
        reply_bears_request_id('[["request_id","owned"]]', request_id="owned") is False
    )


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


def test_send_keyed_ping_probe_consumes_matched_pong(tmp_path: Path) -> None:
    """A matched keyed PONG is retired from ctrl_out by the probe itself.

    Single-reader contract: the prober that issued the request_id owns the
    matched reply's lifecycle and deletes it by exact message id on match.

    Spec: [MF-3], [MANAGER.8]
    """
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1775622400000000104"
    ctrl_in_name = f"T{tid}.ctrl_in"
    ctrl_out_name = f"T{tid}.ctrl_out"
    request_id = "probe-request-consumed"
    _write_ctrl_out_rows(
        ctx,
        ctrl_out_name,
        [
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
        ],
    )

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
    assert _peek_ctrl_out_bodies(ctx, ctrl_out_name) == []


def test_send_keyed_ping_probe_leaves_bystander_rows_untouched(
    tmp_path: Path,
) -> None:
    """Non-matching pongs and terminal envelopes survive a probe untouched.

    The single-reader contract only covers replies keyed to this probe's
    request_id; messages owned by other ctrl_out readers (other probes'
    replies, terminal-envelope scanners) must stay visible after the probe
    completes, including after its timeout sweep.

    Spec: [MF-3]
    """
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1775622400000000105"
    ctrl_in_name = f"T{tid}.ctrl_in"
    ctrl_out_name = f"T{tid}.ctrl_out"
    bystander_pong = json.dumps(
        {
            "command": "PING",
            "status": "ok",
            "message": "PONG",
            "tid": tid,
            "request_id": "other-request",
            "task_status": "running",
        }
    )
    terminal_envelope = json.dumps(
        {
            "type": TERMINAL_ENVELOPE_TYPE,
            "source": "task",
            "tid": tid,
            "status": "completed",
            "timestamp": 1,
        }
    )
    _write_ctrl_out_rows(ctx, ctrl_out_name, [bystander_pong, terminal_envelope])

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
    assert _peek_ctrl_out_bodies(ctx, ctrl_out_name) == [
        bystander_pong,
        terminal_envelope,
    ]


def test_send_keyed_ping_probe_timeout_sweeps_rows_bearing_its_request_id(
    tmp_path: Path,
) -> None:
    """After the probe returns, no row bearing its request_id remains.

    The sweep runs once at timeout-return, so the swept row must already be
    in ctrl_out while the probe waits. A reply keyed to this probe's
    request_id that cannot coerce to a matched PONG (missing task_status)
    exercises exactly that window: the probe cannot match it, times out, and
    must still retire it. A reply keyed to another request_id survives.

    Spec: [MF-3]
    """
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1775622400000000106"
    ctrl_in_name = f"T{tid}.ctrl_in"
    ctrl_out_name = f"T{tid}.ctrl_out"
    request_id = "swept-request"
    own_unmatchable_reply = json.dumps(
        {
            "command": "PING",
            "status": "ok",
            "message": "PONG",
            "tid": tid,
            "request_id": request_id,
        }
    )
    bystander_pong = json.dumps(
        {
            "command": "PING",
            "status": "ok",
            "message": "PONG",
            "tid": tid,
            "request_id": "other-request",
            "task_status": "running",
        }
    )
    _write_ctrl_out_rows(ctx, ctrl_out_name, [own_unmatchable_reply, bystander_pong])

    result = send_keyed_ping_probe(
        ctx,
        tid=tid,
        ctrl_in_name=ctrl_in_name,
        ctrl_out_name=ctrl_out_name,
        request_id=request_id,
        timeout=0.0,
    )

    assert result.error is None
    assert result.matched is None
    assert result.timed_out is True
    remaining = [json.loads(body) for body in _peek_ctrl_out_bodies(ctx, ctrl_out_name)]
    assert [entry.get("request_id") for entry in remaining] == ["other-request"]


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


@pytest.mark.parametrize(
    "task_status",
    [
        "created",
        "spawning",
        "running",
        "completed",
        "failed",
        "timeout",
        "cancelled",
        "killed",
    ],
)
def test_coerce_pong_response_accepts_each_exact_task_status(
    task_status: str,
) -> None:
    payload = {
        "command": "PING",
        "status": "ok",
        "message": "PONG",
        "tid": "1775622400000000103",
        "request_id": "probe-request-3",
        "task_status": task_status,
    }

    assert (
        coerce_pong_response(
            json.dumps(payload),
            tid="1775622400000000103",
            request_id="probe-request-3",
        )
        == payload
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("command", "ping"),
        ("command", " PING "),
        ("status", "OK"),
        ("status", " ok "),
        ("message", "pong"),
        ("tid", 1775622400000000103),
        ("request_id", 3),
        ("task_status", ""),
        ("task_status", "waiting"),
        ("task_status", 3),
        ("task_status", []),
    ],
)
def test_coerce_pong_response_requires_exact_core_fields(
    field: str,
    value: object,
) -> None:
    payload: dict[str, object] = {
        "command": "PING",
        "status": "ok",
        "message": "PONG",
        "tid": "1775622400000000103",
        "request_id": "probe-request-3",
        "task_status": "running",
    }
    payload[field] = value

    assert (
        coerce_pong_response(
            json.dumps(payload),
            tid="1775622400000000103",
            request_id="probe-request-3",
        )
        is None
    )


def test_coerce_pong_response_rejects_conflicting_duplicate_core_fields() -> None:
    raw = (
        '{"command":"STATUS","command":"PING",'
        '"status":"error","status":"ok",'
        '"message":"NOPE","message":"PONG",'
        '"tid":"other","tid":"1775622400000000103",'
        '"request_id":"other","request_id":"probe-request-3",'
        '"task_status":"failed","task_status":"running"}'
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
        "outbox": WEFT_MANAGER_OUTBOX_QUEUE,
        "weft_context": _ROOT,
        "should_stop": False,
    }
    payload.update(overrides)
    return payload


def _gate(payload: dict[str, object], record: dict[str, object] | None = None) -> bool:
    return pong_proves_dispatch_eligible(
        payload,
        record={"weft_context": _ROOT} if record is None else record,
        ctrl_in_name=_CTRL_IN,
        ctrl_out_name=_CTRL_OUT,
        outbox_name=WEFT_MANAGER_OUTBOX_QUEUE,
        root_context=_ROOT,
    )


@pytest.mark.parametrize("task_status", ["created", "spawning", "running"])
def test_pong_gate_accepts_fully_specified_manager(task_status: str) -> None:
    assert _gate(_eligible_payload(task_status=task_status)) is True


@pytest.mark.parametrize(
    "field",
    [
        "task_status",
        "should_stop",
        "role",
        "requests",
        "ctrl_in",
        "ctrl_out",
        "outbox",
        "weft_context",
    ],
)
def test_pong_gate_rejects_each_missing_authority_field(field: str) -> None:
    payload = _eligible_payload()
    del payload[field]

    assert _gate(payload) is False


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
        [],
    ],
)
def test_pong_gate_rejects_terminal_or_stopping_status(status: object) -> None:
    assert _gate(_eligible_payload(task_status=status)) is False


def test_pong_gate_rejects_should_stop_and_mismatches() -> None:
    assert _gate(_eligible_payload(should_stop=True)) is False
    assert _gate(_eligible_payload(role="worker")) is False
    assert _gate(_eligible_payload(requests="other.queue")) is False
    assert _gate(_eligible_payload(ctrl_in="other.ctrl_in")) is False
    assert _gate(_eligible_payload(ctrl_out="other.ctrl_out")) is False
    assert _gate(_eligible_payload(outbox="other.outbox")) is False
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
