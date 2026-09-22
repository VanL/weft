"""Canonical pure policy for ``weft.state.tasks.<tid>``.

This module owns malformed/superseded history reduction, unknown-deadline
reduction, durable-row decoding, and the conservative payload probe retained
by SQLite admission. It opens no broker/store state and applies no deletion;
the broker-aware LivenessMonitor is the sole exact-delete executor.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.6], [OBS.13], [OBS.13.7]
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from weft.core.queue_window import (
    DecodedQueueWindowRow,
    QueueWindowRow,
    is_old_enough,
)
from weft.ext import RunnerHandle
from weft.helpers import handle_has_live_host_process


@dataclass(frozen=True, slots=True)
class UnknownDeadlineState:
    """In-memory attempted-unknown deadline for one runtime generation."""

    generation: str
    deadline_monotonic: float
    paused_at_monotonic: float | None


@dataclass(frozen=True, slots=True)
class MappingHistoryDecision:
    """Pure malformed/superseded-row decision for one reconciliation step."""

    adopt_candidate: bool
    retire_message_ids: tuple[int, ...]


def reduce_mapping_history(
    *,
    current_message_id: int | None,
    candidate_message_id: int,
    malformed_reason: str | None,
    now_ns: int,
    min_age_seconds: float,
) -> MappingHistoryDecision:
    """Select exact malformed or superseded IDs without touching broker state."""

    candidate_old = is_old_enough(
        candidate_message_id,
        now_ns,
        min_age_seconds,
    )
    if malformed_reason is not None:
        return MappingHistoryDecision(
            False,
            (candidate_message_id,) if candidate_old else (),
        )
    if current_message_id is None:
        return MappingHistoryDecision(True, ())
    if current_message_id > candidate_message_id:
        return MappingHistoryDecision(
            False,
            (candidate_message_id,) if candidate_old else (),
        )
    current_old = is_old_enough(current_message_id, now_ns, min_age_seconds)
    return MappingHistoryDecision(
        True,
        (current_message_id,) if current_old else (),
    )


def reduce_unknown_deadline(
    state: UnknownDeadlineState | None,
    *,
    generation: str,
    outcome: Literal["live", "stale", "unknown", "not_attempted"],
    now_monotonic: float,
    timeout_seconds: float,
) -> tuple[UnknownDeadlineState | None, bool]:
    """Reduce one probe outcome into deadline state and retirement authority.

    Not-attempted time is excluded by pausing once and shifting the original
    deadline forward on the next completed unknown observation.

    Spec: [LIVENESS.R4]
    """

    if timeout_seconds <= 0.0:
        raise ValueError("timeout_seconds must be positive")
    if state is not None and state.generation != generation:
        state = None
    if outcome == "live":
        return None, False
    if outcome == "stale":
        return None, True
    if outcome == "not_attempted":
        if state is None or state.paused_at_monotonic is not None:
            return state, False
        return (
            UnknownDeadlineState(
                generation=state.generation,
                deadline_monotonic=state.deadline_monotonic,
                paused_at_monotonic=now_monotonic,
            ),
            False,
        )
    if state is None:
        return (
            UnknownDeadlineState(
                generation=generation,
                deadline_monotonic=now_monotonic + timeout_seconds,
                paused_at_monotonic=None,
            ),
            False,
        )
    if state.paused_at_monotonic is not None:
        if now_monotonic < state.paused_at_monotonic:
            raise ValueError("monotonic time moved backwards during deadline pause")
        state = UnknownDeadlineState(
            generation=generation,
            deadline_monotonic=(
                state.deadline_monotonic + now_monotonic - state.paused_at_monotonic
            ),
            paused_at_monotonic=None,
        )
    return state, now_monotonic >= state.deadline_monotonic


def decode_tid_state_row(
    row: QueueWindowRow, *, expected_tid: str | None = None
) -> DecodedQueueWindowRow:
    """Decode a snapshot and optionally bind its full ID to the queue suffix.

    Spec: docs/specifications/07-System_Invariants.md [OBS.6]
    """

    try:
        payload = json.loads(row.body)
    except json.JSONDecodeError:
        return DecodedQueueWindowRow(
            raw=row,
            payload=None,
            malformed_reason="invalid_json",
        )
    if not isinstance(payload, dict):
        return DecodedQueueWindowRow(
            raw=row,
            payload=None,
            malformed_reason="json_not_object",
        )
    if not valid_tid_state_payload(payload):
        return DecodedQueueWindowRow(
            raw=row,
            payload=payload,
            malformed_reason="invalid_tid_state_shape",
        )
    if expected_tid is not None and payload["full"] != expected_tid:
        return DecodedQueueWindowRow(
            raw=row, payload=payload, malformed_reason="task_state_tid_mismatch"
        )
    return DecodedQueueWindowRow(raw=row, payload=payload)


def valid_tid_state_payload(payload: Mapping[str, Any]) -> bool:
    """Return whether a TID mapping payload has the required Weft shape."""

    full = payload.get("full")
    short = payload.get("short")
    return (
        isinstance(full, str) and bool(full) and isinstance(short, str) and bool(short)
    )


def mapping_row_is_live(payload: Mapping[str, Any] | None) -> bool:
    """Return whether a mapping payload's own liveness probe finds a live owner.

    Positive scoped host-process liveness always wins. Without that proof, a
    valid ``terminal is True`` hint makes the row dead. Other undecidable
    payloads (no runtime handle, or a handle with no probeable host PIDs --
    e.g. external/non-host runtime handles) are treated as live: undecidable
    means skip, never delete. This is the only liveness evidence this policy
    consults; it never looks past the row payload.

    Public because SQLite Manager admission retains these conservative
    live-or-undecidable counting semantics independently of LivenessMonitor's
    timeout-based retirement.

    Spec: [OBS.13.7]
    """

    if payload is None:
        return True
    handle_payload = payload.get("runtime_handle")
    if isinstance(handle_payload, Mapping):
        try:
            handle = RunnerHandle.from_dict(handle_payload)
        except ValueError:
            handle = None
        if handle is not None and handle.scoped_host_processes():
            return handle_has_live_host_process(handle)
    return payload.get("terminal") is not True
