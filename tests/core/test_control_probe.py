"""Tests for keyed control-channel PING/PONG probing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft.context import build_context
from weft.core.control_probe import coerce_pong_response, send_keyed_ping_probe

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
