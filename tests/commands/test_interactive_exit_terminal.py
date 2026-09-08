"""Interactive exit accepts terminal proof before delayed ack (Spec: [MF-3])."""

from __future__ import annotations

import json
import time

import pytest

from tests.tasks.test_task_interactive import make_interactive_spec
from weft.commands.run import _InteractiveRunLifecycle
from weft.core.control_messages import parse_control_request

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
