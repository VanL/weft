"""Bounded runner-boundary diagnostic payload helpers.

Runner diagnostics explain process/session startup and runner-boundary failures.
They are operational metadata attached to existing task-log/result surfaces, not
task lifecycle truth.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3.4]
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/06-Resource_Management.md [RM-5.2]
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from typing import Any

from weft._constants import (
    RUNNER_DIAGNOSTICS_MESSAGE_MAX_CHARS,
    RUNNER_DIAGNOSTICS_TRACEBACK_MAX_CHARS,
)


def runner_diagnostics(
    *,
    phase: str,
    runner: str | None = None,
    target_type: str | None = None,
    pid: int | None = None,
    exitcode: int | None = None,
    alive: bool | None = None,
    duration_seconds: float | None = None,
    timeout_seconds: float | None = None,
    message: str | None = None,
    exception_type: str | None = None,
    traceback_text: str | None = None,
    last_handshake: str | None = None,
    include_python: bool = False,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a bounded JSON-safe runner diagnostic payload.

    Spec: docs/specifications/01-Core_Components.md [CC-3.4]
    """

    payload: dict[str, Any] = {
        "phase": _clean_text(phase) or "unknown",
        "platform": sys.platform,
    }
    _set_clean_text(payload, "runner", runner)
    _set_clean_text(payload, "target_type", target_type)
    _set_int(payload, "pid", pid)
    _set_int(payload, "exitcode", exitcode)
    if alive is not None:
        payload["alive"] = bool(alive)
    _set_float(payload, "duration_seconds", duration_seconds)
    _set_float(payload, "timeout_seconds", timeout_seconds)
    _set_clean_text(
        payload,
        "message",
        message,
        max_chars=RUNNER_DIAGNOSTICS_MESSAGE_MAX_CHARS,
    )
    _set_clean_text(payload, "exception_type", exception_type)
    _set_clean_text(
        payload,
        "traceback_tail",
        traceback_text,
        max_chars=RUNNER_DIAGNOSTICS_TRACEBACK_MAX_CHARS,
        tail=True,
    )
    _set_clean_text(payload, "last_handshake", last_handshake)
    if include_python:
        payload["python"] = sys.executable

    if extra is not None:
        for key, value in extra.items():
            normalized_key = _clean_text(str(key))
            if not normalized_key or normalized_key in payload:
                continue
            normalized_value = _json_safe(value)
            if normalized_value is not None:
                payload[normalized_key] = normalized_value

    return payload


def merge_runner_diagnostics(
    *diagnostics: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Merge diagnostic fragments into a JSON-safe payload."""

    merged: dict[str, Any] = {}
    for fragment in diagnostics:
        if fragment is None:
            continue
        for key, value in fragment.items():
            normalized_key = _clean_text(str(key))
            if not normalized_key:
                continue
            normalized_value = _json_safe(value)
            if normalized_value is not None:
                merged[normalized_key] = normalized_value
    return merged or None


def diagnostic_summary(diagnostics: Mapping[str, Any] | None) -> str | None:
    """Return a compact human-readable diagnostic summary."""

    if not diagnostics:
        return None
    parts: list[str] = []
    for key in ("phase", "pid", "exitcode", "alive", "last_handshake"):
        value = diagnostics.get(key)
        if value is not None:
            parts.append(f"{key}={value}")
    message = diagnostics.get("message")
    if isinstance(message, str) and message:
        parts.append(f"message={message}")
    return ", ".join(parts) if parts else None


def _set_clean_text(
    payload: dict[str, Any],
    key: str,
    value: str | None,
    *,
    max_chars: int = RUNNER_DIAGNOSTICS_MESSAGE_MAX_CHARS,
    tail: bool = False,
) -> None:
    cleaned = _clean_text(value, max_chars=max_chars, tail=tail)
    if cleaned:
        payload[key] = cleaned


def _set_int(payload: dict[str, Any], key: str, value: int | None) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        payload[key] = value


def _set_float(payload: dict[str, Any], key: str, value: float | None) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, int | float):
        payload[key] = float(value)


def _clean_text(
    value: str | None,
    *,
    max_chars: int = RUNNER_DIAGNOSTICS_MESSAGE_MAX_CHARS,
    tail: bool = False,
) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) <= max_chars:
        return text
    if max_chars <= 1:
        return text[-max_chars:] if tail else text[:max_chars]
    if tail:
        return "..." + text[-(max_chars - 3) :]
    return text[: max_chars - 3] + "..."


def _json_safe(value: Any) -> Any | None:
    if value is None:
        return None
    if isinstance(value, str):
        return _clean_text(value)
    if isinstance(value, bool | int | float):
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, nested in value.items():
            clean_key = _clean_text(str(key))
            if not clean_key:
                continue
            clean_value = _json_safe(nested)
            if clean_value is not None:
                result[clean_key] = clean_value
        return result or None
    if isinstance(value, list | tuple):
        items = [_json_safe(item) for item in value]
        return [item for item in items if item is not None]
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return _clean_text(str(value))
    return value


__all__ = [
    "diagnostic_summary",
    "merge_runner_diagnostics",
    "runner_diagnostics",
]
