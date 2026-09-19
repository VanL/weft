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
    ["STATUS", "STOP", "KILL", "PAUSE", "RESUME"],
)
def test_control_request_round_trip_preserves_each_command(command: str) -> None:
    encoded = encode_control_message(command, request_id="  probe-1  ")

    assert json.loads(encoded) == {
        "command": command,
        "request_id": "  probe-1  ",
    }
    assert parse_control_request(encoded) == ControlRequest(command, "  probe-1  ")


def test_ping_control_request_requires_and_preserves_reply_route() -> None:
    encoded = encode_control_message(
        "PING",
        request_id="  probe-1  ",
        reply_to="  T123.ctrl_in  ",
    )

    assert json.loads(encoded) == {
        "command": "PING",
        "request_id": "  probe-1  ",
        "reply_to": "  T123.ctrl_in  ",
    }
    assert parse_control_request(encoded) == ControlRequest(
        "PING",
        "  probe-1  ",
        "  T123.ctrl_in  ",
    )


def test_non_ping_control_request_without_request_id_has_only_command_key() -> None:
    encoded = encode_control_message("STATUS")

    assert json.loads(encoded) == {"command": "STATUS"}
    assert parse_control_request(encoded) == ControlRequest("STATUS")


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
        '{"command":"PING"}',
        '{"command":"PING","request_id":"x"}',
        '{"command":"PING","reply_to":"T1.ctrl_in"}',
        '{"command":"PING","request_id":null}',
        '{"command":"PING","request_id":1}',
        '{"command":"PING","request_id":""}',
        '{"command":"PING","request_id":"   "}',
        '{"command":"PING","request_id":"x","reply_to":null}',
        '{"command":"PING","request_id":"x","reply_to":1}',
        '{"command":"PING","request_id":"x","reply_to":""}',
        '{"command":"PING","request_id":"x","reply_to":"   "}',
        '{"command":"STATUS","reply_to":"T1.ctrl_in"}',
        '{"command":"STATUS","request_id":"x","reply_to":"T1.ctrl_in"}',
        '{"command":"PING","command":"STOP"}',
        '{"command":"PING","request_id":"a","request_id":"b"}',
        '{"command":"PING","request_id":"x","reply_to":"a","reply_to":"b"}',
    ],
)
def test_parse_control_request_rejects_every_noncanonical_shape(raw: str) -> None:
    assert parse_control_request(raw) is None


@pytest.mark.parametrize(
    ("command", "request_id", "reply_to"),
    [
        ("ping", None, None),
        (" PING ", None, None),
        ("DANCE", None, None),
        ("PING", None, "T1.ctrl_in"),
        ("PING", "request", None),
        ("PING", "", "T1.ctrl_in"),
        ("PING", "   ", "T1.ctrl_in"),
        ("PING", "request", ""),
        ("PING", "request", "   "),
        ("STATUS", None, "T1.ctrl_in"),
        ("STATUS", "request", "T1.ctrl_in"),
    ],
)
def test_encode_control_message_rejects_noncanonical_values(
    command: str,
    request_id: str | None,
    reply_to: str | None,
) -> None:
    with pytest.raises(ValueError):
        encode_control_message(command, request_id=request_id, reply_to=reply_to)
