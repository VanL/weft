"""Integration tests for the `weft result --all` command."""

from __future__ import annotations

import json
import time

from tests.conftest import run_cli
from tests.helpers.weft_harness import WeftTestHarness
from weft.commands import tasks as task_cmd


def _submit_task(harness: WeftTestHarness, *run_args: str) -> str:
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


def _wait_for_task_status(
    harness: WeftTestHarness,
    tid: str,
    *,
    status: str,
    timeout: float = 5.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = task_cmd.task_status(tid, context_path=harness.root)
        if snapshot is not None and snapshot.status == status:
            return
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for task {tid} to reach {status!r}")


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


def test_result_all_filters_running_tasks(weft_harness: WeftTestHarness) -> None:
    """Test that `weft result --all` does not include running/streaming tasks."""
    completed_tid = _submit_task(
        weft_harness,
        "--function",
        "tests.tasks.sample_targets:echo_payload",
        "--arg",
        "done",
    )
    weft_harness.wait_for_completion(completed_tid)

    # Start a task that will be streaming but not complete
    streaming_tid = _submit_task(
        weft_harness,
        "--function",
        "tests.tasks.sample_targets:streaming_echo",
        "--stream-output",
    )
    _wait_for_task_status(weft_harness, streaming_tid, status="running")
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
