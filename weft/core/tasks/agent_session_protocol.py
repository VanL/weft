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

from weft.core.agent_runtime import AgentExecutionResult

AGENT_SESSION_PROTOCOL_VERSION = 1


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


def is_ready_response(payload: Mapping[str, Any]) -> bool:
    """Return ``True`` when *payload* is a private ready response."""
    return (
        payload.get("version") == AGENT_SESSION_PROTOCOL_VERSION
        and payload.get("type") == "ready"
    )


def make_startup_error_response(error: str) -> dict[str, Any]:
    """Build a private startup-error response payload."""
    return {
        "version": AGENT_SESSION_PROTOCOL_VERSION,
        "type": "startup_error",
        "error": error,
    }


def startup_error_message(payload: Mapping[str, Any]) -> str | None:
    """Return the startup error string when *payload* is a startup error."""
    if (
        payload.get("version") != AGENT_SESSION_PROTOCOL_VERSION
        or payload.get("type") != "startup_error"
    ):
        return None
    error = payload.get("error")
    if not isinstance(error, str) or not error:
        return "Agent session failed to start"
    return error


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
    "make_ready_response",
    "make_result_response",
    "make_startup_error_response",
    "make_stop_request",
    "parse_request_type",
    "parse_result_response",
    "startup_error_message",
    "is_ready_response",
]
