"""Private protocol helpers for persistent agent sessions.

This protocol is local to the private request queue and framed response
connection between the task parent and dedicated agent-session subprocess. It
must not leak to public task inbox or outbox queues.

Spec references:
- docs/specifications/13-Agent_Runtime.md [AR-6]
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from weft._constants import AGENT_SESSION_PROTOCOL_VERSION
from weft.core.agents.runtime import AgentExecutionResult
from weft.core.targets import (
    PreparedTaskResult,
    prepare_task_result,
    prepared_result_from_payload,
    prepared_result_to_payload,
)


def make_execute_request(work_item: Any) -> dict[str, Any]:
    """Build a private execute request payload."""
    return {
        "version": AGENT_SESSION_PROTOCOL_VERSION,
        "type": "execute",
        "work_item": work_item,
    }


def make_stop_request() -> dict[str, Any]:
    """Build a private stop request payload."""
    return {
        "version": AGENT_SESSION_PROTOCOL_VERSION,
        "type": "stop",
    }


def parse_request_type(payload: Mapping[str, Any]) -> str | None:
    """Return the request type when the payload matches the private protocol."""
    version = payload.get("version")
    request_type = payload.get("type")
    if type(version) is not int or version != AGENT_SESSION_PROTOCOL_VERSION:
        return None
    if not isinstance(request_type, str) or not request_type:
        return None
    if request_type not in {"execute", "stop"}:
        return None
    return request_type


def make_ready_response() -> dict[str, Any]:
    """Build a private ready response payload."""
    return {
        "version": AGENT_SESSION_PROTOCOL_VERSION,
        "type": "ready",
    }


def make_booted_response() -> dict[str, Any]:
    """Build a private worker-booted response payload."""
    return {
        "version": AGENT_SESSION_PROTOCOL_VERSION,
        "type": "booted",
    }


def response_type(payload: Mapping[str, Any]) -> str | None:
    """Return the response type when the payload matches the private protocol."""

    version = payload.get("version")
    if type(version) is not int or version != AGENT_SESSION_PROTOCOL_VERSION:
        return None
    candidate = payload.get("type")
    if not isinstance(candidate, str) or not candidate:
        return None
    if candidate not in {"booted", "ready", "startup_error", "result"}:
        return None
    required = {"version", "type"}
    if candidate == "startup_error":
        required.add("error")
        if not isinstance(payload.get("error"), str) or not payload["error"]:
            return None
    elif candidate == "result":
        required.update(("status", "error", "result"))
        if not _valid_result_fields(payload):
            return None
    optional = {"diagnostics"} if candidate in {"startup_error", "result"} else set()
    if not required <= set(payload) or set(payload) - required - optional:
        return None
    if "diagnostics" in payload and not isinstance(payload["diagnostics"], dict):
        return None
    return candidate


def _valid_result_fields(payload: Mapping[str, Any]) -> bool:
    """Check the result discriminant before accepting its prepared record."""
    status, error = payload.get("status"), payload.get("error")
    if status == "ok":
        return error is None and isinstance(payload.get("result"), dict)
    return (
        status == "error"
        and payload.get("result") is None
        and isinstance(error, str)
        and bool(error)
    )


def is_booted_response(payload: Mapping[str, Any]) -> bool:
    """Return ``True`` when *payload* is a private booted response."""
    return response_type(payload) == "booted"


def is_ready_response(payload: Mapping[str, Any]) -> bool:
    """Return ``True`` when *payload* is a private ready response."""
    return response_type(payload) == "ready"


def make_startup_error_response(
    error: str,
    *,
    diagnostics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a private startup-error response payload."""
    payload: dict[str, Any] = {
        "version": AGENT_SESSION_PROTOCOL_VERSION,
        "type": "startup_error",
        "error": error,
    }
    if diagnostics is not None:
        payload["diagnostics"] = dict(diagnostics)
    return payload


def startup_error_message(payload: Mapping[str, Any]) -> str | None:
    """Return the startup error string when *payload* is a startup error."""
    if response_type(payload) != "startup_error":
        return None
    error: str = payload["error"]
    return error


def startup_error_diagnostics(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return startup diagnostic payload when present."""

    if response_type(payload) != "startup_error":
        return None
    diagnostics = payload.get("diagnostics")
    return dict(diagnostics) if isinstance(diagnostics, Mapping) else None


def make_result_response(
    *,
    status: str,
    result: AgentExecutionResult | PreparedTaskResult | None = None,
    error: str | None = None,
    diagnostics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a private execution response payload."""
    prepared = prepare_task_result(result) if status == "ok" else None
    if prepared is not None and prepared.kind != "agent":
        raise TypeError("agent session success requires a prepared agent result")
    payload: dict[str, Any] = {
        "version": AGENT_SESSION_PROTOCOL_VERSION,
        "type": "result",
        "status": status,
        "error": error,
        "result": (
            prepared_result_to_payload(prepared) if prepared is not None else None
        ),
    }
    if diagnostics is not None:
        payload["diagnostics"] = dict(diagnostics)
    return payload


def parse_result_response(
    payload: Mapping[str, Any],
) -> (
    tuple[
        str,
        PreparedTaskResult | None,
        str | None,
        dict[str, Any] | None,
    ]
    | None
):
    """Parse a private execution response payload."""
    if response_type(payload) != "result":
        return None

    status = payload["status"]
    error = payload.get("error")
    diagnostics = payload.get("diagnostics")
    normalized_diagnostics = (
        dict(diagnostics) if isinstance(diagnostics, Mapping) else None
    )

    result_payload = payload.get("result")
    if result_payload is None:
        return status, None, error, normalized_diagnostics
    result = prepared_result_from_payload(result_payload)
    if result.kind != "agent":
        raise ValueError("agent session success requires a prepared agent result")
    return (
        status,
        result,
        error,
        normalized_diagnostics,
    )


__all__ = [  # noqa: RUF022 approved [TS-3.1] [RUFF-SUP-249] exception
    "make_execute_request",
    "make_booted_response",
    "make_ready_response",
    "make_result_response",
    "make_startup_error_response",
    "make_stop_request",
    "parse_request_type",
    "parse_result_response",
    "response_type",
    "startup_error_diagnostics",
    "startup_error_message",
    "is_booted_response",
    "is_ready_response",
]
