"""Interactive exit accepts terminal proof before delayed ack (Spec: [MF-3])."""

from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest

from tests.helpers.reactor_driver import drive_until
from tests.tasks.test_task_interactive import make_interactive_spec
from weft._constants import INTERACTIVE_STOP_COMPLETION_TIMEOUT
from weft.commands import run as run_commands
from weft.commands.run import _InteractiveRunLifecycle
from weft.core.control_messages import parse_control_request
from weft.core.tasks import Consumer

pytestmark = pytest.mark.shared


def test_interactive_exit_uses_terminal_proof_without_waiting_for_ack(
    weft_harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real client observes STOP completion even when no ack has arrived."""
    context = weft_harness.context
    tid = str(time.time_ns())
    spec = make_interactive_spec(tid)
    lifecycle = _InteractiveRunLifecycle(context, spec, use_prompt=False)
    ctrl_out = context.queue(spec.io.control["ctrl_out"], persistent=True)
    ctrl_in = context.queue(spec.io.control["ctrl_in"], persistent=True)
    send_control = lifecycle._send_control

    def send_then_publish_terminal(command: str) -> None:
        # The peer boundary produces terminal proof through the real broker.
        # Its delayed acknowledgement is deliberately absent.
        send_control(command)
        if command == "STOP":
            ctrl_out.write(
                json.dumps(
                    {
                        "type": "terminal",
                        "source": "task",
                        "tid": tid,
                        "status": "cancelled",
                        "timestamp": time.time_ns(),
                    }
                )
            )

    monkeypatch.setattr(lifecycle, "_send_control", send_then_publish_terminal)
    try:
        lifecycle.start()
        assert lifecycle.request_exit()
        assert lifecycle._status == "cancelled"
        requests = [parse_control_request(raw) for raw in ctrl_in.peek_generator()]
        assert [request.command for request in requests if request is not None] == [
            "STOP"
        ]
        assert lifecycle._client.wait(timeout=0)
    finally:
        lifecycle.close()
        ctrl_out.close()
        ctrl_in.close()


@pytest.mark.parametrize("ack_after", [None, 1.5])
def test_interactive_exit_uses_full_stop_budget_before_escalation(
    weft_harness, monkeypatch: pytest.MonkeyPatch, ack_after: float | None
) -> None:
    """A controlled clock pins the STOP deadline without a wall-clock sleep."""
    context = weft_harness.context
    spec = make_interactive_spec(str(time.time_ns()))
    lifecycle = _InteractiveRunLifecycle(context, spec, use_prompt=False)
    clock = [0.0]
    sent: list[tuple[str, float]] = []
    send_control = lifecycle._send_control

    def send(command: str) -> None:
        sent.append((command, clock[0]))
        send_control(command)

    def no_completion(timeout: float | None = None) -> bool:
        clock[0] += timeout or 0
        return False

    def response(command: str, *, status: str, timeout: float) -> dict | None:
        clock[0] += timeout
        if command == "STOP" and ack_after is not None and clock[0] >= 1 + ack_after:
            return {"command": "STOP", "status": status}
        return None

    monkeypatch.setattr(
        run_commands, "time", SimpleNamespace(monotonic=lambda: clock[0])
    )
    monkeypatch.setattr(lifecycle, "_send_control", send)
    monkeypatch.setattr(lifecycle, "wait_for_completion", no_completion)
    monkeypatch.setattr(lifecycle._client, "wait_for_control_response", response)
    try:
        assert lifecycle.request_exit() is (ack_after is not None)
        assert [command for command, _at in sent] == (
            ["STOP"] if ack_after else ["STOP", "KILL"]
        )
        if ack_after is None:
            assert sent[1][1] - sent[0][1] == pytest.approx(
                INTERACTIVE_STOP_COMPLETION_TIMEOUT
            )
    finally:
        lifecycle.close()


def test_interactive_exit_waits_for_real_eof_ignoring_child(
    weft_harness, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """STOP may need its two-second grace; do not enqueue a premature KILL."""
    script = tmp_path / "ignore_eof.py"
    script.write_text(
        "import sys, time\n"
        "print('ready', flush=True)\n"
        "sys.stdin.read()\n"
        "while True: time.sleep(0.05)\n"
    )
    context = weft_harness.context
    spec = make_interactive_spec(
        str(time.time_ns()), script_path=str(script), polling_interval=0.01
    )
    task = Consumer(context.broker_target, spec, config=context.broker_config)
    lifecycle = _InteractiveRunLifecycle(context, spec, use_prompt=False)
    sent: list[tuple[str, float]] = []
    terminal_observations: list[tuple[dict, bool]] = []
    send_control = lifecycle._send_control
    on_state = lifecycle._client._state_cb
    results: list[bool] = []
    failures: list[Exception] = []

    def send(command: str) -> None:
        sent.append((command, time.monotonic()))
        send_control(command)

    def request_exit() -> None:
        try:
            results.append(lifecycle.request_exit())
        except (OSError, RuntimeError, ValueError) as exc:
            failures.append(exc)

    monkeypatch.setattr(lifecycle, "_send_control", send)
    # Simulate unavailable fallback evidence at the reader boundary, preserving
    # the real task log, Consumer shutdown, broker frames and client watchers.
    monkeypatch.setattr(lifecycle, "_poll_terminal_log", lambda: False)
    monkeypatch.setattr(lifecycle, "_poll_monitor_terminal", lambda: False)
    worker = threading.Thread(target=request_exit)
    session = None
    try:
        lifecycle.start()
        lifecycle.send_input("start\n")
        drive_until(
            lambda: task._interactive_session,
            lambda value: value is not None,
            step=task.process_once,
            wait=task.wait_for_activity,
            timeout=5,
        )
        session = task._interactive_session
        assert session is not None

        def observe(event: dict) -> None:
            terminal_observations.append((dict(event), session.is_alive()))
            on_state(event)

        monkeypatch.setattr(lifecycle._client, "_state_cb", observe)
        worker.start()
        drive_until(
            lambda: not worker.is_alive(),
            bool,
            step=task.process_once,
            wait=task.wait_for_activity,
            timeout=15,
        )
        worker.join(timeout=5)
        assert results == [True], (sent, failures)
        assert not worker.is_alive()
        assert not failures
        assert [command for command, _at in sent] == ["STOP"]
        assert time.monotonic() - sent[0][1] > 1
        assert not session.is_alive()
        ack = lifecycle._client.wait_for_control_response(
            "STOP", status="ack", timeout=2
        )
        assert ack is not None
        assert len(terminal_observations) == 1
        assert terminal_observations[0][0]["status"] == "cancelled"
        assert terminal_observations[0][1] is False
    finally:
        if worker.ident is not None:
            worker.join(timeout=5)
        task.stop(join=False)
        lifecycle.close()
