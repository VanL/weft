"""Structured foreground manager operational log helpers.

Spec references:
- docs/specifications/03-Manager_Architecture.md [MA-3]
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Mapping, Sequence
from typing import Any

from weft._constants import (
    MANAGER_SERVE_LOG_ACTIVE_CONFIG_KEY,
    MANAGER_SERVE_LOG_CANDIDATE_LIMIT,
    MANAGER_SERVE_LOG_COMPONENTS,
    MANAGER_SERVE_LOG_EVENT_MAX_CHARS,
    MANAGER_SERVE_LOG_LEVEL_ORDER,
    MANAGER_SERVE_LOG_SCHEMA,
    MANAGER_SERVE_LOG_SCHEMA_VERSION,
    WEFT_MANAGER_SERVE_LOG_LEVEL,
    WEFT_MANAGER_SERVE_LOG_LEVEL_DEFAULT,
)


def serve_log_level(config: Mapping[str, Any]) -> str:
    """Return the normalized foreground manager operational-log level."""

    if not bool(config.get(MANAGER_SERVE_LOG_ACTIVE_CONFIG_KEY, False)):
        return WEFT_MANAGER_SERVE_LOG_LEVEL_DEFAULT
    level = (
        str(
            config.get(
                WEFT_MANAGER_SERVE_LOG_LEVEL, WEFT_MANAGER_SERVE_LOG_LEVEL_DEFAULT
            )
        )
        .strip()
        .lower()
    )
    return (
        level
        if level in MANAGER_SERVE_LOG_LEVEL_ORDER
        else WEFT_MANAGER_SERVE_LOG_LEVEL_DEFAULT
    )


def serve_log_allows(config: Mapping[str, Any], required_level: str) -> bool:
    """Return whether *config* allows an event with *required_level*."""

    configured = serve_log_level(config)
    required = required_level.strip().lower()
    return MANAGER_SERVE_LOG_LEVEL_ORDER.get(
        configured, 0
    ) >= MANAGER_SERVE_LOG_LEVEL_ORDER.get(required, 999)


def truncate_serve_log_value(value: Any, *, max_items: int | None = None) -> Any:
    """Return a JSON-safe bounded representation of *value*."""

    item_limit = max_items or MANAGER_SERVE_LOG_CANDIDATE_LIMIT
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) <= MANAGER_SERVE_LOG_EVENT_MAX_CHARS:
            return value
        suffix = "...[truncated]"
        return value[: MANAGER_SERVE_LOG_EVENT_MAX_CHARS - len(suffix)] + suffix
    if isinstance(value, Mapping):
        return {
            str(key): truncate_serve_log_value(val, max_items=item_limit)
            for key, val in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        values = [
            truncate_serve_log_value(item, max_items=item_limit)
            for item in list(value)[:item_limit]
        ]
        if len(value) > item_limit:
            values.append({"truncated_count": len(value) - item_limit})
        return values
    return truncate_serve_log_value(str(value), max_items=item_limit)


def build_serve_log_record(
    *,
    config: Mapping[str, Any],
    event: str,
    component: str,
    manager_tid: str,
    manager_tid_short: str,
    required_level: str,
    severity: str = "info",
    weft_context: str | None = None,
    runtime_handle_id: str | None = None,
    pid: int | None = None,
    loop_iteration: int | None = None,
    fields: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one bounded JSONL operational-log record."""

    normalized_component = (
        component if component in MANAGER_SERVE_LOG_COMPONENTS else "manager"
    )
    record: dict[str, Any] = {
        "schema": MANAGER_SERVE_LOG_SCHEMA,
        "schema_version": MANAGER_SERVE_LOG_SCHEMA_VERSION,
        "event": event,
        "component": normalized_component,
        "timestamp_ns": time.time_ns(),
        "manager_tid": manager_tid,
        "manager_tid_short": manager_tid_short,
        "weft_context": weft_context,
        "runtime_handle_id": runtime_handle_id,
        "pid": pid,
        "loop_iteration": loop_iteration,
        "severity": severity,
        "configured_level": serve_log_level(config),
        "required_level": required_level,
    }
    if fields:
        record.update(truncate_serve_log_value(dict(fields)))
    return record


def emit_serve_log_record(record: Mapping[str, Any]) -> None:
    """Best-effort JSONL write to process stderr."""

    try:
        print(
            json.dumps(record, ensure_ascii=False, sort_keys=True),
            file=sys.stderr,
            flush=True,
        )
    except Exception:
        return
