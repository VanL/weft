"""Strict canonical control-envelope wire tests [QUEUE.2a]."""

from __future__ import annotations

import json

import pytest

from weft.core.control_messages import (
    ControlRequest,
    encode_control_message,
    parse_control_request,
)

pytestmark = pytest.mark.shared


@pytest.mark.parametrize(
    "command",
    ["PING", "STATUS", "STOP", "KILL", "PAUSE", "RESUME"],
)
def test_control_request_round_trip_preserves_each_command(command: str) -> None:
    encoded = encode_control_message(command, request_id="  probe-1  ")

    assert json.loads(encoded) == {
        "command": command,
        "request_id": "  probe-1  ",
    }
    assert parse_control_request(encoded) == ControlRequest(command, "  probe-1  ")


def test_control_request_without_request_id_has_only_command_key() -> None:
    encoded = encode_control_message("PING")

    assert json.loads(encoded) == {"command": "PING"}
    assert parse_control_request(encoded) == ControlRequest("PING")


@pytest.mark.parametrize(
    "raw",
    [
        "PING",
        "STATUS",
        "STOP",
        "KILL",
        "PAUSE",
        "RESUME",
        "",
        "not json",
        "null",
        "[]",
        '"PING"',
        "{}",
        '{"request_id":"x"}',
        '{"command":"ping"}',
        '{"command":" PING "}',
        '{"command":"DANCE"}',
        '{"command":1}',
        '{"command":"PING","extra":true}',
        '{"command":"PING","request_id":null}',
        '{"command":"PING","request_id":1}',
        '{"command":"PING","request_id":""}',
        '{"command":"PING","request_id":"   "}',
        '{"command":"PING","command":"STOP"}',
        '{"command":"PING","request_id":"a","request_id":"b"}',
    ],
)
def test_parse_control_request_rejects_every_noncanonical_shape(raw: str) -> None:
    assert parse_control_request(raw) is None


@pytest.mark.parametrize(
    ("command", "request_id"),
    [
        ("ping", None),
        (" PING ", None),
        ("DANCE", None),
        ("PING", ""),
        ("PING", "   "),
    ],
)
def test_encode_control_message_rejects_noncanonical_values(
    command: str,
    request_id: str | None,
) -> None:
    with pytest.raises(ValueError):
        encode_control_message(command, request_id=request_id)
