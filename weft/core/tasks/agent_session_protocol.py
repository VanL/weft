"""Private protocol helpers for persistent agent sessions.

This protocol is local to the multiprocessing queues connecting the task parent
and the dedicated agent-session subprocess. It must not leak to public task
inbox or outbox queues.

Spec references:
- docs/specifications/13-Agent_Runtime.md [AR-6]
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from weft._constants import AGENT_SESSION_PROTOCOL_VERSION
from weft.core.agents.runtime import AgentExecutionResult


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
    if version != AGENT_SESSION_PROTOCOL_VERSION:
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

    if payload.get("version") != AGENT_SESSION_PROTOCOL_VERSION:
        return None
    candidate = payload.get("type")
    if not isinstance(candidate, str) or not candidate:
        return None
    if candidate not in {"booted", "ready", "startup_error", "result"}:
        return None
    return candidate


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
    error = payload.get("error")
    if not isinstance(error, str) or not error:
        return "Agent session failed to start"
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
    result: AgentExecutionResult | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Build a private execution response payload."""
    payload: dict[str, Any] = {
        "version": AGENT_SESSION_PROTOCOL_VERSION,
        "type": "result",
        "status": status,
        "error": error,
    }
    if result is not None:
        payload["result"] = result.to_private_payload()
    return payload


def parse_result_response(
    payload: Mapping[str, Any],
) -> tuple[str, AgentExecutionResult | None, str | None] | None:
    """Parse a private execution response payload."""
    if (
        payload.get("version") != AGENT_SESSION_PROTOCOL_VERSION
        or payload.get("type") != "result"
    ):
        return None

    status = payload.get("status")
    if not isinstance(status, str) or not status:
        return None

    error = payload.get("error")
    if error is not None and not isinstance(error, str):
        return None

    result_payload = payload.get("result")
    if result_payload is None:
        return status, None, error
    if not isinstance(result_payload, Mapping):
        return None
    return status, AgentExecutionResult.from_private_payload(result_payload), error


__all__ = [
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
