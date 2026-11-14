"""Mechanism tests for the private framed terminal transport."""

from __future__ import annotations

import json
import multiprocessing
import os
import pickle
import sys
import time
from multiprocessing.connection import Connection
from pathlib import Path
from types import FrameType, SimpleNamespace
from typing import Any, NoReturn, cast

import pytest

import weft.core.runners.host as host_runner_mod
from tests.helpers.leaked_sender import (
    _leak_sender_to_grandchild,
    kill_pid,
    silent_leaking_producer,
    wait_for_pidfile,
)
from weft._constants import AGENT_SESSION_PROTOCOL_VERSION
from weft.core.runners.agent_session_protocol import (
    parse_result_response,
    response_type,
)
from weft.core.runners.host import HostTaskRunner
from weft.core.runners.outcome import parse_outcome_payload
from weft.core.targets import PreparedTaskResult
from weft.core.terminal_handoff_transport import (
    TerminalHandoffTransportError,
    poll_terminal_payload,
    prepare_terminal_payload,
    receive_terminal_payload,
    send_terminal_payload,
    wait_terminal_payload,
)

pytestmark = [pytest.mark.shared]


def test_terminal_transport_round_trips_one_frame() -> None:
    """A one-way pipe preserves one synchronously serialized payload."""

    receiver, sender = multiprocessing.get_context("spawn").Pipe(duplex=False)
    try:
        assert send_terminal_payload(sender, lambda: {"result": [1, 2]}) is True
        sender.close()
        assert receive_terminal_payload(receiver) == {"result": [1, 2]}
        assert poll_terminal_payload(receiver, 1.0)
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
            lambda: {"result": object()},
            serialization_failure_factory=lambda failure: {
                "error": failure.cause,
            },
        )
        sender.close()

        assert original_sent is False
        payload = receive_terminal_payload(receiver)
        assert isinstance(payload, dict)
        assert "not JSON serializable" in payload["error"]
        assert poll_terminal_payload(receiver, 1.0)
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
        send_terminal_payload(
            sender,  # type: ignore[arg-type]  # partial connection double injects a write failure
            lambda: {"result": "ok"},
            serialization_failure_factory=_unexpected_fallback,
        )

    assert sender.calls == 1
    assert fallback_calls == 0


def test_terminal_transport_rejects_malformed_frame() -> None:
    """Receiver decode failure is explicit transport evidence."""

    receiver, sender = multiprocessing.get_context("spawn").Pipe(duplex=False)
    try:
        sender.send_bytes(b"not-json")
        sender.close()
        with pytest.raises(TerminalHandoffTransportError, match="decode failed"):
            receive_terminal_payload(receiver)
    finally:
        receiver.close()
        sender.close()


def test_terminal_transport_normalizes_windows_broken_pipe_to_eof() -> None:
    """Windows' ended-pipe signal is the same clean seal POSIX exposes as EOF."""

    class WindowsSealedReceiver:
        def poll(self, timeout: float = 0.0) -> bool:
            del timeout
            raise BrokenPipeError(109, "The pipe has been ended")

        def recv_bytes(self) -> bytes:
            raise BrokenPipeError(109, "The pipe has been ended")

    receiver = WindowsSealedReceiver()

    assert poll_terminal_payload(receiver) is True  # type: ignore[arg-type]
    with pytest.raises(EOFError):
        receive_terminal_payload(receiver)  # type: ignore[arg-type]


def test_terminal_transport_uses_independent_json_wire_examples() -> None:
    """Literal bytes fix interoperability independently of matching codecs."""
    receiver, sender = multiprocessing.get_context("spawn").Pipe(duplex=False)
    try:
        send_terminal_payload(sender, lambda: {"result": "\u2603\ud800"})
        assert receiver.recv_bytes() == b'{"result": "\\u2603\\ud800"}'
        sender.send_bytes(b'{"result": [null, true, 42]}')
        assert receive_terminal_payload(receiver) == {"result": [None, True, 42]}
    finally:
        receiver.close()
        sender.close()


@pytest.mark.parametrize("frame", [b"\xff", b"{", b"[]", b"42", b"null"])
def test_terminal_transport_rejects_non_object_or_invalid_json(frame: bytes) -> None:
    receiver, sender = multiprocessing.get_context("spawn").Pipe(duplex=False)
    try:
        sender.send_bytes(frame)
        with pytest.raises(TerminalHandoffTransportError, match="decode failed"):
            receive_terminal_payload(receiver)
    finally:
        receiver.close()
        sender.close()


def test_terminal_transport_never_executes_pickle_reconstruction(
    tmp_path: Path,
) -> None:
    """Receiving an old pickle cannot run its reconstruction callable."""
    marker = tmp_path / "unpickled"

    class Reconstruction:
        def __reduce__(self) -> tuple[Any, tuple[Path]]:
            return Path.touch, (marker,)

    receiver, sender = multiprocessing.get_context("spawn").Pipe(duplex=False)
    try:
        sender.send_bytes(pickle.dumps(Reconstruction()))
        with pytest.raises(TerminalHandoffTransportError, match="decode failed"):
            receive_terminal_payload(receiver)
        assert not marker.exists()
    finally:
        receiver.close()
        sender.close()


def test_terminal_preparation_bounds_an_unprintable_conversion_error() -> None:
    class UnprintableError(Exception):
        def __str__(self) -> str:
            raise ValueError("cannot format")

    calls = 0

    def convert() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise UnprintableError

    frame, original = prepare_terminal_payload(
        convert, serialization_failure_factory=lambda failure: {"error": failure.cause}
    )
    assert calls == 1
    assert original is False
    assert json.loads(frame) == {"error": "UnprintableError"}


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_terminal_sender_rejects_nonfinite_outer_numbers(value: float) -> None:
    with pytest.raises(TerminalHandoffTransportError, match="serialization failed"):
        prepare_terminal_payload(lambda: {"duration": value})


def _literal_outcome() -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(
        '{"type":"outcome","status":"ok","result":{"kind":"value",'
        '"outputs":[{"format":"json","text":"{\\"1\\": 1, \\"1\\": 2}"}],'
        '"agent_execution_json":null},"error":null,"stdout":null,"stderr":null,'
        '"returncode":0,"duration":0.25,"diagnostics":null}'
    )
    return payload


def test_outcome_parser_preserves_opaque_output_text() -> None:
    """The queue path must not lose duplicate keys by parsing inner text."""
    outcome = parse_outcome_payload(_literal_outcome())
    assert isinstance(outcome.value, PreparedTaskResult)
    assert outcome.value.outputs[0].text == '{"1": 1, "1": 2}'
    assert outcome.duration == 0.25


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("type", "result"),
        ("extra", None),
        ("status", "completed"),
        ("error", "contradictory"),
        ("result", None),
        ("stdout", []),
        ("stderr", 0),
        ("returncode", True),
        ("duration", True),
        ("duration", -1),
        ("duration", float("nan")),
        ("duration", float("inf")),
        ("duration", "1"),
        ("diagnostics", []),
    ],
)
def test_outcome_parser_rejects_malformed_fields(field: str, value: Any) -> None:
    payload = _literal_outcome()
    payload[field] = value
    with pytest.raises((TypeError, ValueError)):
        parse_outcome_payload(payload)


def test_outcome_parser_rejects_missing_field_and_error_with_result() -> None:
    payload = _literal_outcome()
    del payload["duration"]
    with pytest.raises(ValueError):
        parse_outcome_payload(payload)
    payload = _literal_outcome()
    payload.update(status="error", error="failed")
    with pytest.raises(ValueError):
        parse_outcome_payload(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"version": True, "type": "ready"},
        {"version": 1, "type": "ready"},
        {"version": AGENT_SESSION_PROTOCOL_VERSION, "type": "ready", "extra": 1},
        {
            "version": AGENT_SESSION_PROTOCOL_VERSION,
            "type": "startup_error",
            "error": "",
        },
        {
            "version": AGENT_SESSION_PROTOCOL_VERSION,
            "type": "result",
            "status": "ok",
            "result": None,
            "error": None,
        },
        {
            "version": AGENT_SESSION_PROTOCOL_VERSION,
            "type": "result",
            "status": "error",
            "result": None,
            "error": None,
        },
    ],
)
def test_session_protocol_rejects_invalid_response_fields(
    payload: dict[str, Any],
) -> None:
    assert response_type(payload) is None
    assert parse_result_response(payload) is None


def test_session_protocol_requires_agent_result() -> None:
    payload = {
        "version": AGENT_SESSION_PROTOCOL_VERSION,
        "type": "result",
        "status": "ok",
        "error": None,
        "result": _literal_outcome()["result"],
    }
    with pytest.raises(ValueError, match="prepared agent"):
        parse_result_response(payload)


def test_wait_terminal_payload_returns_on_producer_sentinel(tmp_path: Path) -> None:
    """A producer's exit wakes the wait while a descendant holds the channel open.

    Spec: [CC-3.5]
    """

    ctx = multiprocessing.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    pidfile = tmp_path / "grandchild.pid"
    process = ctx.Process(target=silent_leaking_producer, args=(sender, str(pidfile)))
    process.start()
    sender.close()
    grandchild_pid: int | None = None
    try:
        grandchild_pid = wait_for_pidfile(pidfile)

        started = time.monotonic()
        ready = wait_terminal_payload(
            receiver, producer_sentinel=process.sentinel, timeout=5.0
        )
        elapsed = time.monotonic() - started

        assert ready is True
        assert elapsed < 2.0
        assert receiver.poll(0) is False
        assert (
            wait_terminal_payload(receiver, producer_sentinel=None, timeout=0.2)
            is False
        )
    finally:
        kill_pid(grandchild_pid)
        process.join(timeout=5.0)
        receiver.close()


def _leaking_function_target(pidfile: str) -> NoReturn:
    """Spawn a child holding the private response pipe open and exit immediately."""
    frame: FrameType | None = sys._getframe(1)
    result_sender: Connection | None = None
    while frame is not None:
        candidate = frame.f_locals.get("result_sender")
        if hasattr(candidate, "send_bytes") and hasattr(candidate, "poll"):
            result_sender = cast(Connection, candidate)
            break
        frame = frame.f_back
    assert result_sender is not None
    multiprocessing.current_process().daemon = False
    _leak_sender_to_grandchild(result_sender, pidfile)
    # The exit instant, so the caller can time the handoff without the two
    # interpreter start-ups before it.
    Path(f"{pidfile}.exited").write_text(repr(time.time()), encoding="utf-8")
    os._exit(7)


def test_host_terminal_handoff_wait_seconds_preserves_timeout_monitor_and_drain_deadlines_without_poll_floor() -> (
    None
):
    """HostTaskRunner._terminal_handoff_wait_seconds omits the 50ms poll floor when cancel_requested is None and a sentinel exists [CC-3.5]."""
    runner_unbounded = HostTaskRunner(
        target_type="function",
        tid="1780000000000000501",
        function_target="builtins:len",
        process_target=None,
        agent=None,
        args=(),
        kwargs={},
        env={},
        working_dir=None,
        timeout=None,
        limits=None,
        monitor_class=None,
        monitor_interval=1.0,
    )
    assert (
        runner_unbounded._terminal_handoff_wait_seconds(
            now=100.0,
            start_time=95.0,
            next_monitor_at=None,
            drain_deadline=None,
            cancel_requested=None,
            has_producer_sentinel=True,
        )
        is None
    )
    assert runner_unbounded._terminal_handoff_wait_seconds(
        now=100.0,
        start_time=95.0,
        next_monitor_at=None,
        drain_deadline=None,
        cancel_requested=lambda: False,
        has_producer_sentinel=True,
    ) == pytest.approx(0.05)
    assert runner_unbounded._terminal_handoff_wait_seconds(
        now=100.0,
        start_time=95.0,
        next_monitor_at=None,
        drain_deadline=100.2,
        cancel_requested=None,
        has_producer_sentinel=False,
    ) == pytest.approx(0.05)
    assert runner_unbounded._terminal_handoff_wait_seconds(
        now=100.0,
        start_time=95.0,
        next_monitor_at=None,
        drain_deadline=100.2,
        cancel_requested=None,
        has_producer_sentinel=True,
    ) == pytest.approx(0.2)

    runner_bounded = HostTaskRunner(
        target_type="function",
        tid="1780000000000000502",
        function_target="builtins:len",
        process_target=None,
        agent=None,
        args=(),
        kwargs={},
        env={},
        working_dir=None,
        timeout=5.0,
        limits=None,
        monitor_class=None,
        monitor_interval=1.0,
    )
    assert runner_bounded._terminal_handoff_wait_seconds(
        now=97.0,
        start_time=95.0,
        next_monitor_at=98.5,
        drain_deadline=None,
        cancel_requested=None,
        has_producer_sentinel=True,
    ) == pytest.approx(1.5)
    assert runner_bounded._terminal_handoff_wait_seconds(
        now=97.0,
        start_time=95.0,
        next_monitor_at=None,
        drain_deadline=None,
        cancel_requested=None,
        has_producer_sentinel=True,
    ) == pytest.approx(3.0)


def test_host_one_shot_terminal_handoff_wakes_on_producer_sentinel_and_expires_drain_when_descendant_holds_pipe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HostTaskRunner.run_with_hooks wakes on process.sentinel and expires drain_deadline when a descendant holds the pipe open [CC-3.5].

    The bound runs from the worker's exit, not from the call: the worker and
    its grandchild are spawned interpreters, and on a loaded Windows runner
    their start-up alone took over three seconds (CI run 42790027897). A
    runner that missed the sentinel would wait for the 30 s control poll.
    """
    monkeypatch.setattr(host_runner_mod, "ACTIVE_CONTROL_POLL_INTERVAL", 30.0)
    pidfile = tmp_path / "descendant.pid"
    runner = HostTaskRunner(
        target_type="function",
        tid="1780000000000000503",
        function_target=(
            "tests.core.test_terminal_handoff_transport:_leaking_function_target"
        ),
        process_target=None,
        agent=None,
        args=(str(pidfile),),
        kwargs={},
        env={},
        working_dir=None,
        timeout=None,
        limits=None,
        monitor_class=None,
        monitor_interval=1.0,
    )
    descendant_pid: int | None = None
    try:
        outcome = runner.run_with_hooks(None, cancel_requested=None)
        returned_at = time.time()
        descendant_pid = wait_for_pidfile(pidfile, timeout=1.0)
        exited_at = float(Path(f"{pidfile}.exited").read_text(encoding="utf-8"))

        assert returned_at - exited_at < 2.0
        assert outcome.status == "error"
        assert outcome.returncode == 7
        assert (
            outcome.error == "Worker result channel failed before a result was received"
        )
        assert outcome.diagnostics is not None
        assert outcome.diagnostics["handoff_event"] == "drain_expired"
    finally:
        if descendant_pid is None and pidfile.exists():
            descendant_pid = int(pidfile.read_text(encoding="utf-8"))
        kill_pid(descendant_pid)


def test_host_one_shot_terminal_handoff_recomputes_wait_against_fresh_clock_after_monitor_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HostTaskRunner._run_one_shot_terminal_handoff uses a fresh post-sample clock reading when computing wait_for [CC-3.5]."""
    clock_values = iter([100.0, 101.0, 102.5, 102.5, 103.0])
    monkeypatch.setattr(
        host_runner_mod,
        "time",
        SimpleNamespace(
            monotonic=lambda: next(clock_values, 103.0),
            sleep=time.sleep,
        ),
    )
    waited_timeouts: list[float | None] = []

    def _record_wait(
        response_receiver: Any,
        *,
        producer_sentinel: int | None,
        timeout: float | None,
    ) -> bool:
        del response_receiver, producer_sentinel
        waited_timeouts.append(timeout)
        return False

    monkeypatch.setattr(host_runner_mod, "wait_terminal_payload", _record_wait)

    class _FakeMonitor:
        def check_limits(self) -> tuple[bool, str | None]:
            return (True, None)

        def last_metrics(self) -> None:
            return None

        def stop(self) -> None:
            return None

    fake_monitor = _FakeMonitor()
    monkeypatch.setattr(
        host_runner_mod,
        "load_resource_monitor",
        lambda *args, **kwargs: fake_monitor,
    )
    monkeypatch.setattr(
        host_runner_mod,
        "_start_optional_monitor",
        lambda monitor, pid: monitor,
    )

    class _FakeProcess:
        pid = 43210
        sentinel = 123
        exitcode: int | None = None

        def is_alive(self) -> bool:
            return self.exitcode is None

        def join(self, timeout: float | None = None) -> None:
            del timeout

    class _FakeReceiver:
        def poll(self, timeout: float = 0.0) -> bool:
            del timeout
            return False

        def close(self) -> None:
            return None

    runner = HostTaskRunner(
        target_type="function",
        tid="1780000000000000504",
        function_target="builtins:len",
        process_target=None,
        agent=None,
        args=(),
        kwargs={},
        env={},
        working_dir=None,
        timeout=2.0,
        limits=None,
        monitor_class="fake.monitor",
        monitor_interval=1.0,
    )
    fake_process = _FakeProcess()
    monkeypatch.setattr(
        runner,
        "_stop_process",
        lambda proc: setattr(proc, "exitcode", -15),
    )

    outcome = runner._run_one_shot_terminal_handoff(
        cast(Any, fake_process),
        cast(Any, _FakeReceiver()),
        worker_pid=fake_process.pid,
        runtime_handle=None,
        cancel_requested=None,
    )

    assert outcome.status == "timeout"
    assert waited_timeouts[0] == pytest.approx(0.0)
