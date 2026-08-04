"""Synchronous private transport helpers for terminal handoff payloads.

The helpers preserve a strict framing rule: serialize before the first write,
then write exactly one frame. They do not own endpoint lifetime.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3.5]
- docs/specifications/07-System_Invariants.md [EXEC.5], [EXEC.8]
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing.connection import Connection
from multiprocessing.reduction import ForkingPickler
from typing import Any

from weft._constants import TERMINAL_HANDOFF_ERROR_CAUSE_MAX_CHARS


class TerminalHandoffTransportError(RuntimeError):
    """Raised when a private terminal frame cannot be delivered or decoded."""


@dataclass(frozen=True, slots=True)
class TerminalPayloadSerializationFailure:
    """Bounded detail supplied to a pre-write fallback factory."""

    cause: str


def bounded_terminal_handoff_cause(exc: BaseException) -> str:
    """Return a bounded single-line description of a transport exception."""

    text = " ".join(str(exc).split()) or type(exc).__name__
    return text[:TERMINAL_HANDOFF_ERROR_CAUSE_MAX_CHARS]


def send_terminal_payload(
    sender: Connection,
    payload: Any,
    *,
    serialization_failure_factory: Callable[[TerminalPayloadSerializationFailure], Any]
    | None = None,
) -> bool:
    """Synchronously serialize and send one framed private payload.

    A fallback is permitted only when serialization fails before `send_bytes`
    starts. A write failure is terminal and never causes a second frame.
    Returns true when the original payload was sent and false when the fallback
    was sent.
    """

    used_fallback = False
    try:
        frame = ForkingPickler.dumps(payload)
    except Exception as exc:
        if serialization_failure_factory is None:
            raise TerminalHandoffTransportError(
                "terminal payload serialization failed: "
                f"{bounded_terminal_handoff_cause(exc)}"
            ) from exc
        fallback = serialization_failure_factory(
            TerminalPayloadSerializationFailure(
                cause=bounded_terminal_handoff_cause(exc)
            )
        )
        used_fallback = True
        try:
            frame = ForkingPickler.dumps(fallback)
        except Exception as fallback_exc:  # pragma: no cover - fixed envelopes
            raise TerminalHandoffTransportError(
                "terminal serialization fallback failed: "
                f"{bounded_terminal_handoff_cause(fallback_exc)}"
            ) from fallback_exc

    try:
        sender.send_bytes(frame)
    except (BrokenPipeError, EOFError, OSError) as exc:
        raise TerminalHandoffTransportError(
            f"terminal payload delivery failed: {bounded_terminal_handoff_cause(exc)}"
        ) from exc
    return not used_fallback


def receive_terminal_payload(receiver: Connection) -> Any:
    """Receive and decode one framed private terminal payload.

    `EOFError` remains distinct receiver-visible channel seal evidence.
    Decode errors become explicit transport failures.
    """

    try:
        frame = receiver.recv_bytes()
    except EOFError:
        raise
    except OSError as exc:
        raise TerminalHandoffTransportError(
            f"terminal payload receive failed: {bounded_terminal_handoff_cause(exc)}"
        ) from exc

    try:
        return ForkingPickler.loads(frame)
    except Exception as exc:
        raise TerminalHandoffTransportError(
            f"terminal payload decode failed: {bounded_terminal_handoff_cause(exc)}"
        ) from exc


__all__ = [
    "TerminalHandoffTransportError",
    "TerminalPayloadSerializationFailure",
    "bounded_terminal_handoff_cause",
    "receive_terminal_payload",
    "send_terminal_payload",
]
