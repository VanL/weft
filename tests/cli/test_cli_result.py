"""Integration tests for the ``weft result`` command."""

from __future__ import annotations

import json

from tests.conftest import run_cli


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
    return tid


def test_result_returns_payload_for_completed_task(workdir, weft_harness) -> None:
    tid = _submit_task(
        workdir,
        weft_harness,
        "--function",
        "tests.tasks.sample_targets:echo_payload",
        "--arg",
        "cli-result",
    )

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
