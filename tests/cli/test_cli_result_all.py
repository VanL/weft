"""Integration tests for the `weft result --all` command."""

from __future__ import annotations

import json
import time

from tests.conftest import run_cli
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import WEFT_STREAMING_SESSIONS_QUEUE


def _submit_task(harness: WeftTestHarness, *run_args: str) -> str:
    harness.ensure_foreground_manager()
    rc, out, err = run_cli(
        "run",
        "--no-wait",
        *run_args,
        cwd=harness.root,
        harness=harness,
    )
    assert rc == 0, err
    tid = out.strip()
    assert tid.startswith("1"), tid
    harness.register_tid(tid)
    return tid


def _wait_for_result_all_text(
    harness: WeftTestHarness,
    *,
    contains: list[str] | None = None,
    excludes: list[str] | None = None,
    peek: bool = True,
    timeout: float = 5.0,
) -> str:
    deadline = time.monotonic() + timeout
    last_out = ""
    last_err = ""
    while time.monotonic() < deadline:
        rc, out, err = run_cli(
            "result",
            "--all",
            *(["--peek"] if peek else []),
            cwd=harness.root,
            harness=harness,
        )
        if rc == 0:
            if all(item in out for item in (contains or [])) and all(
                item not in out for item in (excludes or [])
            ):
                return out
        last_out = out
        last_err = err
        time.sleep(0.05)
    raise AssertionError(
        "Timed out waiting for `weft result --all` text output; "
        f"last_out={last_out!r} last_err={last_err!r}"
    )


def _wait_for_result_all_json(
    harness: WeftTestHarness,
    *,
    predicate,
    peek: bool = True,
    timeout: float = 5.0,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    last_out = ""
    last_err = ""
    while time.monotonic() < deadline:
        rc, out, err = run_cli(
            "result",
            "--all",
            "--json",
            *(["--peek"] if peek else []),
            cwd=harness.root,
            harness=harness,
        )
        if rc == 0:
            payload = json.loads(out)
            if predicate(payload):
                return payload
        last_out = out
        last_err = err
        time.sleep(0.05)
    raise AssertionError(
        "Timed out waiting for `weft result --all --json`; "
        f"last_out={last_out!r} last_err={last_err!r}"
    )


def test_result_all_empty(weft_harness: WeftTestHarness) -> None:
    """Test `weft result --all` when there are no tasks."""
    rc, out, err = run_cli(
        "result", "--all", cwd=weft_harness.root, harness=weft_harness
    )
    assert rc == 0, err
    assert out == ""


def test_result_all_simple_tasks(weft_harness: WeftTestHarness) -> None:
    """Test `weft result --all` with multiple completed tasks."""
    tid1 = _submit_task(
        weft_harness,
        "--function",
        "tests.tasks.sample_targets:echo_payload",
        "--arg",
        "result1",
    )
    tid2 = _submit_task(
        weft_harness,
        "--function",
        "tests.tasks.sample_targets:echo_payload",
        "--arg",
        "result2",
    )

    weft_harness.wait_for_completion(tid1)
    weft_harness.wait_for_completion(tid2)
    out = _wait_for_result_all_text(
        weft_harness,
        contains=[f"{tid1}: result1", f"{tid2}: result2"],
    )

    assert f"{tid1}: result1" in out
    assert f"{tid2}: result2" in out


def test_result_all_json_output(weft_harness: WeftTestHarness) -> None:
    """Test `weft result --all --json`."""
    tid1 = _submit_task(
        weft_harness,
        "--function",
        "tests.tasks.sample_targets:echo_payload",
        "--arg",
        "json1",
    )
    weft_harness.wait_for_completion(tid1)
    data = _wait_for_result_all_json(
        weft_harness,
        predicate=lambda payload: (
            "results" in payload
            and len(payload["results"]) == 1
            and payload["results"][0]["tid"] == tid1
        ),
    )

    assert "results" in data
    assert len(data["results"]) == 1
    assert data["results"][0]["tid"] == tid1
    assert data["results"][0]["result"] == "json1"


def test_result_all_filters_active_streaming_tasks(
    weft_harness: WeftTestHarness,
) -> None:
    """Test that `weft result --all` does not include active streaming tasks."""
    weft_harness.ensure_foreground_manager()
    completed_tid = _submit_task(
        weft_harness,
        "--function",
        "tests.tasks.sample_targets:echo_payload",
        "--arg",
        "done",
    )
    weft_harness.wait_for_completion(completed_tid)

    streaming_tid = "1777000000000000002"
    streaming_outbox = f"T{streaming_tid}.outbox"
    outbox = weft_harness.context.queue(streaming_outbox, persistent=True)
    stream_registry = weft_harness.context.queue(
        WEFT_STREAMING_SESSIONS_QUEUE,
        persistent=False,
    )
    try:
        outbox.write(
            json.dumps(
                {
                    "type": "stream",
                    "stream": "stdout",
                    "chunk": 0,
                    "final": True,
                    "encoding": "text",
                    "data": "not-ready\n",
                }
            )
        )
        stream_registry.write(
            json.dumps(
                {
                    "session_id": f"{streaming_tid}:{streaming_outbox}:test",
                    "tid": streaming_tid,
                    "mode": "stream",
                    "queue": streaming_outbox,
                    "started_at": time.time_ns(),
                }
            )
        )
    finally:
        outbox.close()
        stream_registry.close()

    out = _wait_for_result_all_text(
        weft_harness,
        contains=[f"{completed_tid}: done"],
        excludes=[streaming_tid],
    )

    assert f"{completed_tid}: done" in out
    assert streaming_tid not in out


def test_result_all_peek_preserves_messages(weft_harness: WeftTestHarness) -> None:
    tid = _submit_task(
        weft_harness,
        "--function",
        "tests.tasks.sample_targets:echo_payload",
        "--arg",
        "keep",
    )
    weft_harness.wait_for_completion(tid)
    out = _wait_for_result_all_text(weft_harness, contains=[f"{tid}: keep"])

    rc, out, err = run_cli(
        "result",
        "--all",
        "--peek",
        cwd=weft_harness.root,
        harness=weft_harness,
    )
    assert rc == 0, err
    assert f"{tid}: keep" in out

    rc, out, err = run_cli(
        "result", "--all", cwd=weft_harness.root, harness=weft_harness
    )
    assert rc == 0, err
    assert f"{tid}: keep" in out

    rc, out, err = run_cli(
        "result", "--all", cwd=weft_harness.root, harness=weft_harness
    )
    assert rc == 0, err
    assert f"{tid}: keep" not in out


def test_result_all_json_output_does_not_prefix_interactive_stream_text(
    weft_harness: WeftTestHarness,
) -> None:
    tid = "1777000000000000001"
    outbox = weft_harness.context.queue(f"T{tid}.outbox", persistent=True)
    try:
        outbox.write(
            json.dumps(
                {
                    "type": "stream",
                    "stream": "stdout",
                    "chunk": 0,
                    "final": False,
                    "encoding": "text",
                    "data": "echo: hello\n",
                }
            )
        )
        outbox.write(
            json.dumps(
                {
                    "type": "stream",
                    "stream": "stdout",
                    "chunk": 1,
                    "final": False,
                    "encoding": "text",
                    "data": "goodbye\n",
                }
            )
        )
        outbox.write(
            json.dumps(
                {
                    "type": "stream",
                    "stream": "stdout",
                    "chunk": 2,
                    "final": True,
                    "encoding": "text",
                    "data": "",
                }
            )
        )
    finally:
        outbox.close()

    rc, out, err = run_cli(
        "result", "--all", "--json", cwd=weft_harness.root, harness=weft_harness
    )
    assert rc == 0, err

    payload = json.loads(out)
    assert "results" in payload
    assert payload["results"] == [{"tid": tid, "result": "echo: hello\ngoodbye\n"}]
