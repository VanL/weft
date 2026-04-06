"""CLI tests for `weft list` and `weft task` commands."""

from __future__ import annotations

import json

from tests.conftest import run_cli


def _submit_task(workdir, harness) -> str:
    rc, out, err = run_cli(
        "run",
        "--no-wait",
        "--function",
        "tests.tasks.sample_targets:echo_payload",
        "--arg",
        "payload",
        cwd=workdir,
        harness=harness,
    )
    assert rc == 0
    assert err == ""
    tid = out.strip()
    assert tid
    harness.register_tid(tid)
    return tid


def test_list_and_task_status(workdir, weft_harness) -> None:
    tid = _submit_task(workdir, weft_harness)
    weft_harness.wait_for_completion(tid)

    rc, out, err = run_cli("list", "--all", "--json", cwd=workdir)
    assert rc == 0
    data = json.loads(out)
    assert any(item["tid"] == tid for item in data)
    assert err == ""

    rc, out, err = run_cli("task", "status", tid, "--json", cwd=workdir)
    assert rc == 0
    payload = json.loads(out)
    assert payload["tid"] == tid
    assert payload["status"] in {"completed", "running", "failed"}
    assert err == ""


def test_task_tid_reverse(workdir, weft_harness) -> None:
    tid = _submit_task(workdir, weft_harness)
    weft_harness.wait_for_completion(tid)

    rc, out, err = run_cli("task", "tid", "--reverse", tid, cwd=workdir)
    assert rc == 0
    assert out.strip() == tid[-10:]
    assert err == ""
