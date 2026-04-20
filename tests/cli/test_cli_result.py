"""Integration tests for the ``weft result`` command."""

from __future__ import annotations

import json
import sys
import time

import pytest

from tests.conftest import run_cli

pytestmark = [pytest.mark.shared]


def _submit_task(workdir, harness, *run_args: str) -> str:
    rc, out, err = run_cli(
        "run",
        "--no-wait",
        *run_args,
        cwd=workdir,
        harness=harness,
    )
    assert rc == 0, err
    tid = out.strip()
    assert tid.startswith("1"), tid
    harness.register_tid(tid)
    _wait_for_outbox(harness, tid)
    return tid


def _wait_for_outbox(harness, tid: str, timeout: float = 5.0) -> None:
    queue_name = f"T{tid}.outbox"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        queue = harness.context.queue(queue_name, persistent=True)
        try:
            if queue.peek_one() is not None:
                return
        except Exception:
            pass
        finally:
            queue.close()
        time.sleep(0.05)


def test_result_returns_payload_for_completed_task(workdir, weft_harness) -> None:
    tid = _submit_task(
        workdir,
        weft_harness,
        "--function",
        "tests.tasks.sample_targets:echo_payload",
        "--arg",
        "cli-result",
    )

    weft_harness.wait_for_completion(tid)

    rc, out, err = run_cli(
        "result",
        tid,
        "--timeout",
        "5",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    assert out == "cli-result"
    assert err == ""


def test_result_json_output(workdir, weft_harness) -> None:
    tid = _submit_task(
        workdir,
        weft_harness,
        "--function",
        "tests.tasks.sample_targets:provide_payload",
    )

    weft_harness.wait_for_completion(tid)

    rc, out, err = run_cli(
        "result",
        tid,
        "--timeout",
        "5",
        "--json",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["tid"] == tid
    assert payload["status"] == "completed"
    assert payload["result"]["data"] == "payload"


def test_result_stream_outputs_streamed_task_once(workdir, weft_harness) -> None:
    tid = _submit_task(
        workdir,
        weft_harness,
        "--function",
        "tests.tasks.sample_targets:echo_payload",
        "--arg",
        "stream-once",
        "--stream-output",
    )

    weft_harness.wait_for_completion(tid)

    rc, out, err = run_cli(
        "result",
        tid,
        "--timeout",
        "5",
        "--stream",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    assert out == "stream-once"
    assert err == ""


def test_result_stream_attaches_to_running_command_and_emits_live_output(
    workdir,
    weft_harness,
) -> None:
    ready_path = workdir / "result-stream-ready.txt"
    rc, out, err = run_cli(
        "run",
        "--no-wait",
        "--stream-output",
        "--",
        sys.executable,
        "-c",
        (
            "from pathlib import Path; import time; "
            f"ready = Path({str(ready_path)!r}); "
            "print('first', flush=True); "
            "ready.write_text('ready', encoding='utf-8'); "
            "time.sleep(5); "
            "print('second', flush=True)"
        ),
        cwd=workdir,
        harness=weft_harness,
    )
    assert rc == 0, err
    tid = out.strip()
    weft_harness.register_tid(tid)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not ready_path.exists():
        time.sleep(0.05)
    assert ready_path.exists(), "streaming command did not publish readiness marker"

    rc, out, err = run_cli(
        "result",
        tid,
        "--stream",
        "--timeout",
        "1",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 124
    assert out == "first"
    assert "Timed out after" in err
    assert f"waiting for task {tid}" in err
    weft_harness.wait_for_completion(tid)


def test_result_streamed_command_preserves_trimmed_result_shape(
    workdir,
    weft_harness,
) -> None:
    rc, out, err = run_cli(
        "run",
        "--no-wait",
        "--stream-output",
        "--",
        sys.executable,
        "-c",
        ("print('first'); print('second')"),
        cwd=workdir,
        harness=weft_harness,
    )
    assert rc == 0, err
    tid = out.strip()
    weft_harness.register_tid(tid)
    weft_harness.wait_for_completion(tid)

    rc, out, err = run_cli(
        "result",
        tid,
        "--timeout",
        "5",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    assert out == "first\nsecond"
    assert err == ""


def test_result_stream_with_error_preserves_stderr_payload_selection(
    workdir,
    weft_harness,
) -> None:
    tid = _submit_task(
        workdir,
        weft_harness,
        "--function",
        "tests.tasks.sample_targets:provide_stdio",
    )

    weft_harness.wait_for_completion(tid)

    rc, out, err = run_cli(
        "result",
        tid,
        "--timeout",
        "5",
        "--stream",
        "--error",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    assert out == "stderr"
    assert err == ""


def test_result_stream_rejects_json_output(workdir, weft_harness) -> None:
    rc, out, err = run_cli(
        "result",
        "123",
        "--stream",
        "--json",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 2
    assert out == ""
    assert "cannot be used with --json" in err


def test_result_missing_task_reports_error(workdir, weft_harness) -> None:
    rc, out, err = run_cli(
        "result",
        "999",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 2
    assert out == ""
    assert "no outbox queue" in err
