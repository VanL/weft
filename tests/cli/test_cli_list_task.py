"""CLI tests for `weft task list` and `weft task` commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import run_cli
from tests.helpers.weft_harness import WeftTestHarness
from tests.tasks.sample_targets import wait_for_file
from weft.helpers import encode_tid


def _submit_task(workdir: Path, harness: WeftTestHarness) -> str:
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


def test_task_list_and_task_status(
    workdir: Path, weft_harness: WeftTestHarness
) -> None:
    tid = _submit_task(workdir, weft_harness)
    weft_harness.wait_for_completion(tid)

    rc, out, err = run_cli("task", "list", "--all", "--json", cwd=workdir)
    assert rc == 0
    data = json.loads(out)
    entry = next(item for item in data if item["tid"] == tid)
    assert entry["runner"] == "host"
    assert entry["runtime_handle"]["runner"] == "host"
    assert isinstance(entry["last_timestamp"], str)
    assert len(entry["last_timestamp"]) == 19
    assert entry["last_timestamp"].isascii()
    assert entry["last_timestamp"].isdigit()
    assert err == ""

    rc, out, err = run_cli("task", "status", tid, "--json", cwd=workdir)
    assert rc == 0
    payload = json.loads(out)
    assert payload["tid"] == tid
    assert payload["status"] in {"completed", "running", "failed"}
    assert payload["runner"] == "host"
    assert payload["runtime_handle"]["runner"] == "host"
    assert isinstance(payload["last_timestamp"], str)
    assert len(payload["last_timestamp"]) == 19
    assert payload["last_timestamp"].isascii()
    assert payload["last_timestamp"].isdigit()
    assert err == ""


def test_task_tid_reverse(workdir: Path, weft_harness: WeftTestHarness) -> None:
    tid = _submit_task(workdir, weft_harness)
    weft_harness.wait_for_completion(tid)

    rc, out, err = run_cli("task", "tid", "--reverse", tid, cwd=workdir)
    assert rc == 0
    assert out.strip() == encode_tid(tid)
    assert err == ""


def _spell(tid: str, spelling: str) -> str:
    encoded = encode_tid(tid)
    if spelling == "full":
        return tid
    if spelling == "encoded":
        return encoded
    if spelling == "encoded-lower":
        return encoded.lower()
    if spelling == "encoded-mixed":
        return "".join(
            char.lower() if index % 2 else char for index, char in enumerate(encoded)
        )
    assert spelling == "T-encoded"
    return "T" + encoded


@pytest.mark.parametrize(
    "spelling", ["full", "encoded", "encoded-lower", "encoded-mixed", "T-encoded"]
)
def test_encoded_and_full_tid_arguments_name_the_same_task(
    workdir: Path, weft_harness: WeftTestHarness, spelling: str
) -> None:
    """Every accepted spelling names the same task [CLI-1.2.3], [OBS.5]."""

    sentinel = f"sentinel-{spelling}"
    rc, out, err = run_cli(
        "run",
        "--no-wait",
        "--function",
        "tests.tasks.sample_targets:echo_payload",
        "--arg",
        sentinel,
        cwd=workdir,
        harness=weft_harness,
    )
    assert rc == 0, err
    tid = out.strip()
    weft_harness.register_tid(tid)
    weft_harness.wait_for_completion(tid)
    argument = _spell(tid, spelling)
    if spelling in {"encoded-lower", "encoded-mixed"}:
        assert argument != encode_tid(tid)

    rc, out, err = run_cli("task", "status", argument, "--json", cwd=workdir)
    assert rc == 0, err
    assert json.loads(out)["tid"] == tid

    rc, out, err = run_cli("task", "tid", argument, cwd=workdir)
    assert rc == 0, err
    assert out.strip() == tid

    # `weft result` consumes the row, so each task is read exactly once.
    rc, out, err = run_cli("result", argument, cwd=workdir)
    assert rc == 0, err
    assert sentinel in out


def test_tid_argument_probes(workdir: Path, weft_harness: WeftTestHarness) -> None:
    """Rejected TID spellings exit 2 with a one-line diagnostic naming the input.

    Each probe's whole output is exactly one line that carries the input as
    given, so a generic message (one that drops or rewrites the input) fails.
    The empty argument has no visible spelling: its diagnostic is pinned
    exactly, the input appearing as the empty text between ``Task `` and
    `` not found``.
    """

    probes = [
        (("task", "status", "0123456789"), "Task 0123456789 not found"),
        (("task", "status", "1JZKD3PKK000I"), "Task 1JZKD3PKK000I not found"),
        (("task", "status", "8000000000000"), "Task 8000000000000 not found"),
        (("task", "status", ""), "Task  not found"),
        (
            ("task", "tid", "--reverse", "9999999999999999999"),
            "Invalid full task ID: '9999999999999999999'",
        ),
        (("task", "stop", "0123456789"), "Invalid task ID: '0123456789'"),
        (("result", "1JZKD-PKK0000"), "invalid task id '1JZKD-PKK0000'"),
    ]
    for args, diagnostic in probes:
        rc, out, err = run_cli(*args, cwd=workdir, harness=weft_harness)
        assert rc == 2, (args, rc, out, err)
        assert "Traceback" not in err, (args, err)
        lines = [line for line in (out + err).splitlines() if line.strip()]
        assert lines == [diagnostic], (args, lines)
        assert args[-1] in lines[0], (args, lines)
    queue = weft_harness.context.queue("T0123456789.ctrl_in", persistent=False)
    try:
        assert queue.peek_many() == []
    finally:
        queue.close()


def test_task_ping_outputs_extended_pong_json(
    workdir: Path, weft_harness: WeftTestHarness
) -> None:
    weft_harness.ensure_foreground_manager()
    ready_path = workdir / "ping-ready"
    release_path = workdir / "ping-release"
    rc, out, err = run_cli(
        "run",
        "--no-wait",
        "--function",
        "tests.tasks.sample_targets:signal_ready_and_wait_for_release",
        "--kw",
        f"ready_path={ready_path}",
        "--kw",
        f"release_path={release_path}",
        cwd=workdir,
        harness=weft_harness,
    )
    assert rc == 0, err
    assert err == ""
    tid = out.strip()
    assert tid
    weft_harness.register_tid(tid)

    try:
        # --no-wait proves acceptance, not child readiness [CLI-1.1.1].
        # Function entry follows observation publication and running [MF-5].
        wait_for_file(str(ready_path), timeout=30.0)

        rc, out, err = run_cli("task", "ping", tid, "--timeout", "10", cwd=workdir)

        assert rc == 0, err
        assert err == ""
        payload = json.loads(out)
        assert payload["timed_out"] is False
        assert payload["error"] is None
        assert isinstance(payload["observed_at"], str)
        assert len(payload["observed_at"]) == 19
        assert payload["observed_at"].isascii()
        assert payload["observed_at"].isdigit()
        assert payload["pong"]["command"] == "PING"
        assert payload["pong"]["message"] == "PONG"
        assert payload["pong"]["tid"] == tid
        assert payload["pong"]["task_status"] == "running"
        assert payload["pong"]["request_id"]
    finally:
        release_path.touch()
