"""Mechanism tests for the private framed terminal transport."""

from __future__ import annotations

import multiprocessing

import pytest

from weft.core.terminal_handoff_transport import (
    TerminalHandoffTransportError,
    receive_terminal_payload,
    send_terminal_payload,
)

pytestmark = [pytest.mark.shared]


def test_terminal_transport_round_trips_one_frame() -> None:
    """A one-way pipe preserves one synchronously serialized payload."""

    receiver, sender = multiprocessing.get_context("spawn").Pipe(duplex=False)
    try:
        assert send_terminal_payload(sender, {"result": [1, 2]}) is True
        sender.close()
        assert receive_terminal_payload(receiver) == {"result": [1, 2]}
        assert receiver.poll(1.0)
        with pytest.raises(EOFError):
            receive_terminal_payload(receiver)
    finally:
        receiver.close()
        sender.close()


def test_terminal_transport_serialization_fallback_is_the_only_frame() -> None:
    """A pre-write serialization failure may emit one fixed fallback frame."""

    receiver, sender = multiprocessing.get_context("spawn").Pipe(duplex=False)
    try:
        original_sent = send_terminal_payload(
            sender,
            lambda: None,
            serialization_failure_factory=lambda failure: {
                "error": failure.cause,
            },
        )
        sender.close()

        assert original_sent is False
        payload = receive_terminal_payload(receiver)
        assert isinstance(payload, dict)
        assert "lambda" in payload["error"]
        assert receiver.poll(1.0)
        with pytest.raises(EOFError):
            receive_terminal_payload(receiver)
    finally:
        receiver.close()
        sender.close()


def test_terminal_transport_write_failure_does_not_retry() -> None:
    """A failed first write cannot be followed by a second frame."""

    class FailingSender:
        def __init__(self) -> None:
            self.calls = 0

        def send_bytes(self, _frame: object) -> None:
            self.calls += 1
            raise BrokenPipeError("closed")

    sender = FailingSender()
    fallback_calls = 0

    def _unexpected_fallback(_failure: object) -> dict[str, str]:
        nonlocal fallback_calls
        fallback_calls += 1
        return {"error": "fallback"}

    with pytest.raises(TerminalHandoffTransportError, match="delivery failed"):
        send_terminal_payload(  # type: ignore[arg-type]
            sender,
            {"result": "ok"},
            serialization_failure_factory=_unexpected_fallback,
        )

    assert sender.calls == 1
    assert fallback_calls == 0


def test_terminal_transport_rejects_malformed_frame() -> None:
    """Receiver decode failure is explicit transport evidence."""

    receiver, sender = multiprocessing.get_context("spawn").Pipe(duplex=False)
    try:
        sender.send_bytes(b"not-a-pickle")
        sender.close()
        with pytest.raises(TerminalHandoffTransportError, match="decode failed"):
            receive_terminal_payload(receiver)
    finally:
        receiver.close()
        sender.close()
