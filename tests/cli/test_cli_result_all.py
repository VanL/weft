"""Integration tests for the `weft result --all` command."""

from __future__ import annotations

import json
import time

from tests.conftest import run_cli
from tests.helpers.weft_harness import WeftTestHarness


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

    # Let the filesystem settle
    time.sleep(0.2)

    rc, out, err = run_cli(
        "result", "--all", cwd=weft_harness.root, harness=weft_harness
    )
    assert rc == 0, err

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

    # Let the filesystem settle
    time.sleep(0.2)

    rc, out, err = run_cli(
        "result", "--all", "--json", cwd=weft_harness.root, harness=weft_harness
    )
    assert rc == 0, err

    data = json.loads(out)
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
    # Give it a moment to start and produce some output
    time.sleep(1)

    rc, out, err = run_cli(
        "result", "--all", cwd=weft_harness.root, harness=weft_harness
    )
    assert rc == 0, err

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
    time.sleep(0.2)

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
