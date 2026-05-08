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
    HEARTBEAT_ENDPOINT_PROBE_TIMEOUT,
    INTERNAL_HEARTBEAT_ENDPOINT_NAME,
    INTERNAL_RUNTIME_ENDPOINT_NAME_KEY,
    INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT,
    INTERNAL_RUNTIME_TASK_CLASS_KEY,
    INTERNAL_SERVICE_KEY_HEARTBEAT,
    INTERNAL_SERVICE_KEY_METADATA_KEY,
    MANAGER_REGISTRY_POLL_INTERVAL,
    MANAGER_STARTUP_TIMEOUT_SECONDS,
    TERMINAL_TASK_STATUSES,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
)
from weft.context import WeftContext
from weft.core.control_probe import send_keyed_ping_probe
from weft.core.endpoints import ResolvedEndpoint, resolve_endpoint
from weft.ext import RunnerHandle
from weft.helpers import handle_has_live_host_process, iter_queue_json_entries


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


def _latest_heartbeat_task_status(
    context: WeftContext,
    *,
    tid: str,
) -> str | None:
    queue = context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    latest_status: str | None = None
    latest_timestamp = -1
    try:
        for payload, timestamp in iter_queue_json_entries(queue):
            if payload.get("tid") != tid or timestamp < latest_timestamp:
                continue
            taskspec_dump = payload.get("taskspec")
            if not isinstance(taskspec_dump, dict):
                continue
            metadata = taskspec_dump.get("metadata") or {}
            if not isinstance(metadata, dict):
                continue
            is_heartbeat = (
                metadata.get(INTERNAL_SERVICE_KEY_METADATA_KEY)
                == INTERNAL_SERVICE_KEY_HEARTBEAT
                or metadata.get(INTERNAL_RUNTIME_ENDPOINT_NAME_KEY)
                == INTERNAL_HEARTBEAT_ENDPOINT_NAME
                or metadata.get(INTERNAL_RUNTIME_TASK_CLASS_KEY)
                == INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT
            )
            if not is_heartbeat:
                continue
            status = payload.get("status")
            if isinstance(status, str):
                latest_status = status
                latest_timestamp = timestamp
    finally:
        queue.close()
    return latest_status


def _heartbeat_runtime_handle_is_live(
    context: WeftContext,
    *,
    tid: str,
) -> bool:
    queue = context.queue(WEFT_TID_MAPPINGS_QUEUE, persistent=False)
    latest_payload: dict[str, Any] | None = None
    latest_timestamp = -1
    try:
        for payload, timestamp in iter_queue_json_entries(queue):
            if payload.get("full") != tid or timestamp < latest_timestamp:
                continue
            latest_payload = payload
            latest_timestamp = timestamp
    finally:
        queue.close()
    if latest_payload is None:
        return False
    handle_payload = latest_payload.get("runtime_handle")
    if not isinstance(handle_payload, Mapping):
        return False
    try:
        handle = RunnerHandle.from_dict(handle_payload)
    except ValueError:
        return False
    return handle_has_live_host_process(handle)


def _heartbeat_endpoint_is_live(
    context: WeftContext,
    *,
    resolved: ResolvedEndpoint,
) -> bool:
    record = resolved.record
    if record.status != "active":
        return False

    latest_status = _latest_heartbeat_task_status(context, tid=record.tid)
    if latest_status in TERMINAL_TASK_STATUSES:
        return False

    if record.ctrl_in and record.ctrl_out:
        probe = send_keyed_ping_probe(
            context,
            tid=record.tid,
            ctrl_in_name=record.ctrl_in,
            ctrl_out_name=record.ctrl_out,
            timeout=HEARTBEAT_ENDPOINT_PROBE_TIMEOUT,
        )
        if probe.matched is not None:
            return True

    if latest_status is None:
        return False
    return _heartbeat_runtime_handle_is_live(context, tid=record.tid)


def ensure_heartbeat_service(
    context: WeftContext,
    *,
    startup_timeout: float = MANAGER_STARTUP_TIMEOUT_SECONDS,
) -> ResolvedEndpoint:
    """Ensure the built-in heartbeat service is live and return its endpoint."""

    resolved = resolve_endpoint(context, INTERNAL_HEARTBEAT_ENDPOINT_NAME)
    if resolved is not None and _heartbeat_endpoint_is_live(context, resolved=resolved):
        return resolved

    _ensure_manager_running(context)

    deadline = time.monotonic() + startup_timeout
    while time.monotonic() < deadline:
        resolved = resolve_endpoint(context, INTERNAL_HEARTBEAT_ENDPOINT_NAME)
        if resolved is not None and _heartbeat_endpoint_is_live(
            context,
            resolved=resolved,
        ):
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
