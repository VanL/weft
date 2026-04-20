"""Heartbeat service helpers.

Spec references:
- docs/specifications/03-Manager_Architecture.md [MA-1.1], [MA-1.2]
- docs/specifications/05-Message_Flow_and_State.md [MF-3.1], [MF-6]
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any

from weft._constants import (
    DEFAULT_FUNCTION_TARGET,
    INTERNAL_HEARTBEAT_ENDPOINT_NAME,
    INTERNAL_RUNTIME_ENDPOINT_NAME_KEY,
    INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT,
    INTERNAL_RUNTIME_TASK_CLASS_KEY,
    MANAGER_REGISTRY_POLL_INTERVAL,
    MANAGER_STARTUP_TIMEOUT_SECONDS,
)
from weft.context import WeftContext
from weft.core.endpoints import ResolvedEndpoint, resolve_endpoint
from weft.core.spawn_requests import submit_spawn_request


def _heartbeat_service_taskspec_payload(context: WeftContext) -> dict[str, Any]:
    return {
        "name": "heartbeat-service",
        "spec": {
            "type": "function",
            "function_target": DEFAULT_FUNCTION_TARGET,
            "persistent": True,
            "weft_context": str(context.root),
        },
        "metadata": {
            "role": "heartbeat_service",
            INTERNAL_RUNTIME_TASK_CLASS_KEY: INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT,
            INTERNAL_RUNTIME_ENDPOINT_NAME_KEY: INTERNAL_HEARTBEAT_ENDPOINT_NAME,
        },
    }


def _ensure_manager_running(context: WeftContext) -> None:
    from weft.core.manager_runtime import _ensure_manager

    _ensure_manager(context, verbose=False)


def _write_heartbeat_request(
    context: WeftContext,
    *,
    resolved: ResolvedEndpoint,
    payload: Mapping[str, Any],
) -> int | None:
    queue = context.queue(resolved.record.inbox, persistent=False)
    try:
        queue.write(json.dumps(dict(payload), ensure_ascii=False))
        return None
    finally:
        queue.close()


def ensure_heartbeat_service(
    context: WeftContext,
    *,
    startup_timeout: float = MANAGER_STARTUP_TIMEOUT_SECONDS,
) -> ResolvedEndpoint:
    """Ensure the built-in heartbeat service is live and return its endpoint."""

    resolved = resolve_endpoint(context, INTERNAL_HEARTBEAT_ENDPOINT_NAME)
    if resolved is not None:
        return resolved

    _ensure_manager_running(context)

    resolved = resolve_endpoint(context, INTERNAL_HEARTBEAT_ENDPOINT_NAME)
    if resolved is None:
        submit_spawn_request(
            context.broker_target,
            taskspec=_heartbeat_service_taskspec_payload(context),
            work_payload=None,
            config=context.broker_config,
            inherited_weft_context=str(context.root),
            allow_internal_runtime=True,
        )

    deadline = time.monotonic() + startup_timeout
    while time.monotonic() < deadline:
        resolved = resolve_endpoint(context, INTERNAL_HEARTBEAT_ENDPOINT_NAME)
        if resolved is not None:
            return resolved
        time.sleep(MANAGER_REGISTRY_POLL_INTERVAL)

    raise RuntimeError(
        "heartbeat service did not publish a live endpoint before startup timeout"
    )


def upsert_heartbeat(
    context: WeftContext,
    *,
    heartbeat_id: str,
    interval_seconds: int,
    destination_queue: str,
    message: Any,
) -> int | None:
    """Register or replace one runtime-scoped heartbeat."""

    resolved = ensure_heartbeat_service(context)
    return _write_heartbeat_request(
        context,
        resolved=resolved,
        payload={
            "action": "upsert",
            "heartbeat_id": heartbeat_id,
            "interval_seconds": interval_seconds,
            "destination_queue": destination_queue,
            "message": message,
        },
    )


def cancel_heartbeat(
    context: WeftContext,
    *,
    heartbeat_id: str,
) -> int | None:
    """Cancel one runtime-scoped heartbeat registration."""

    resolved = ensure_heartbeat_service(context)
    return _write_heartbeat_request(
        context,
        resolved=resolved,
        payload={
            "action": "cancel",
            "heartbeat_id": heartbeat_id,
        },
    )


__all__ = [
    "cancel_heartbeat",
    "ensure_heartbeat_service",
    "upsert_heartbeat",
]
