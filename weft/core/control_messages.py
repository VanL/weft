"""Canonical task control-envelope wire shape.

This module owns control request encoding and parsing, not task or manager
control policy.

Spec references:
- docs/specifications/01-Core_Components.md [CC-2.4]
- docs/specifications/05-Message_Flow_and_State.md [MF-3]
- docs/specifications/07-System_Invariants.md [QUEUE.2a]
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

from weft._constants import (
    CONTROL_COMMANDS,
)


@dataclass(frozen=True, slots=True)
class ControlRequest:
    """One canonical task control request [CC-2.4], [MF-3]."""

    command: str
    request_id: str | None = None


def _validate_control_request(request: ControlRequest) -> None:
    if not isinstance(request.command, str) or request.command not in CONTROL_COMMANDS:
        raise ValueError(f"Unsupported control command: {request.command!r}")
    if request.request_id is not None and (
        not isinstance(request.request_id, str) or not request.request_id.strip()
    ):
        raise ValueError("request_id must contain a non-whitespace character")


def encode_control_message(command: str, *, request_id: str | None = None) -> str:
    """Encode one canonical control request.

    Raises:
        ValueError: If the command or request ID is outside the wire contract.

    Spec: [QUEUE.2a]
    """

    request = ControlRequest(command=command, request_id=request_id)
    _validate_control_request(request)
    payload = {"command": command}
    if request_id is not None:
        payload["request_id"] = request_id
    return json.dumps(payload)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError(f"Duplicate control-envelope key: {key}")
        payload[key] = value
    return payload


def decode_control_object(raw: str) -> dict[str, Any] | None:
    """Decode one duplicate-free JSON object from the control channel.

    Spec: [MF-3], [QUEUE.2a]
    """

    try:
        payload = json.loads(raw, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return cast(dict[str, Any], payload)


def parse_control_request(raw: str) -> ControlRequest | None:
    """Parse a strict canonical control request, returning None when invalid.

    Spec: [QUEUE.2a]
    """

    payload = decode_control_object(raw)
    if payload is None:
        return None
    keys = set(payload)
    if keys not in ({"command"}, {"command", "request_id"}):
        return None
    command = payload["command"]
    request_id = payload.get("request_id")
    if not isinstance(command, str):
        return None
    if "request_id" in payload and not isinstance(request_id, str):
        return None
    request = ControlRequest(command=command, request_id=request_id)
    try:
        _validate_control_request(request)
    except ValueError:
        return None
    return request
