"""Integration tests for the ``weft result`` command."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from simplebroker.ext import OperationalError
from tests.conftest import run_cli
from tests.helpers.weft_harness import WeftTestHarness
from weft.cli.app import app
from weft.commands import TaskResult
from weft.commands import tasks as task_commands
from weft.core.outbox import decode_stream_text

pytestmark = [pytest.mark.shared]


def test_result_terminal_timeout_uses_exit_124(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tid = "1779600000000000001"
    monkeypatch.setattr(
        "weft.cli.app.commands.cmd_result",
        lambda *_args, **_kwargs: TaskResult(
            tid=tid,
            status="timeout",
            value=None,
            stdout=None,
            stderr=None,
            error="target timed out",
        ),
    )

    result = CliRunner().invoke(app, ["result", tid])

    assert result.exit_code == 124
    assert result.stdout == ""
    assert result.stderr == "target timed out\n"
    assert result.exception is not None
    assert "Traceback" not in result.stderr


def _submit_task(workdir: Path, harness: WeftTestHarness, *run_args: str) -> str:
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


def _wait_for_outbox(harness: WeftTestHarness, tid: str, timeout: float = 5.0) -> None:
    queue_name = f"T{tid}.outbox"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        queue = harness.context.queue(queue_name, persistent=True)
        try:
            ready = queue.peek_one() is not None
        except OperationalError:
            ready = False
        finally:
            queue.close()
        if ready:
            return
        time.sleep(0.05)


def test_wait_for_outbox_retries_transient_backend_failure() -> None:
    attempts = 0
    queues: list[FakeQueue] = []

    class FakeQueue:
        def __init__(self) -> None:
            self.close_count = 0

        def peek_one(self) -> object:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OperationalError("transient backend failure")
            return "ready"

        def close(self) -> None:
            self.close_count += 1

    def make_queue(*_args: object, **_kwargs: object) -> FakeQueue:
        queue = FakeQueue()
        queues.append(queue)
        return queue

    harness = Mock(spec=WeftTestHarness, context=Mock(queue=make_queue))

    _wait_for_outbox(harness, "123", timeout=1.0)

    assert attempts == 2
    assert [queue.close_count for queue in queues] == [1, 1]


def _wait_for_path(path: Path, *, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.05)
    return path.exists()


def _wait_for_stream_chunk(
    harness: WeftTestHarness,
    tid: str,
    chunk: str,
    timeout: float = 20.0,
    *,
    stream: str = "stdout",
    control: bool = False,
) -> bool:
    queue_name = f"T{tid}.{'ctrl_out' if control else 'outbox'}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        queue = harness.context.queue(queue_name, persistent=True)
        try:
            for raw in queue.peek_many(limit=20) or []:
                payload = raw[0] if isinstance(raw, tuple) else raw
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if (
                    data.get("type") == "stream"
                    and data.get("stream", "stdout") == stream
                    and decode_stream_text(data) == chunk
                ):
                    return True
        finally:
            queue.close()
        time.sleep(0.05)
    return False


def test_result_returns_payload_for_completed_task(
    workdir: Path, weft_harness: WeftTestHarness
) -> None:
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


def test_result_json_output(workdir: Path, weft_harness: WeftTestHarness) -> None:
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


def test_result_stream_outputs_streamed_task_once(
    workdir: Path, weft_harness: WeftTestHarness
) -> None:
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
    workdir: Path,
    weft_harness: WeftTestHarness,
) -> None:
    ready_path = workdir / "result-stream-ready.txt"
    release_path = workdir / "result-stream-release.txt"
    rc, out, err = run_cli(
        "run",
        "--no-wait",
        "--stream-output",
        "--",
        sys.executable,
        "-c",
        (
            "from pathlib import Path\n"
            "import time\n"
            f"ready = Path({str(ready_path)!r})\n"
            f"release = Path({str(release_path)!r})\n"
            "print('first', flush=True)\n"
            "ready.write_text('ready', encoding='utf-8')\n"
            "deadline = time.monotonic() + 120\n"
            "while not release.exists() and time.monotonic() < deadline:\n"
            "    time.sleep(0.05)\n"
            "print('second', flush=True)\n"
        ),
        cwd=workdir,
        harness=weft_harness,
    )
    assert rc == 0, err
    tid = out.strip()
    weft_harness.register_tid(tid)
    assert _wait_for_path(ready_path), (
        "streaming command did not publish readiness marker"
    )
    assert _wait_for_stream_chunk(weft_harness, tid, "first\n"), (
        "streaming command readiness marker appeared before the stdout chunk was "
        "broker-visible"
    )

    try:
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
    finally:
        release_path.write_text("release", encoding="utf-8")
    weft_harness.wait_for_completion(tid)


def test_result_streamed_command_preserves_trimmed_result_shape(
    workdir: Path,
    weft_harness: WeftTestHarness,
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
    workdir: Path,
    weft_harness: WeftTestHarness,
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


def test_result_stream_rejects_json_output(
    workdir: Path, weft_harness: WeftTestHarness
) -> None:
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


def test_result_missing_task_reports_error(
    workdir: Path, weft_harness: WeftTestHarness
) -> None:
    rc, out, err = run_cli(
        "result",
        "999",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 2
    assert out == ""
    assert "no outbox queue" in err
    assert "Traceback" not in err


def test_result_missing_task_with_zero_timeout_reports_timeout(
    workdir: Path,
    weft_harness: WeftTestHarness,
) -> None:
    rc, out, err = run_cli(
        "result",
        "999",
        "--timeout",
        "0",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 124
    assert out == ""
    assert "Timed out after 0.0 seconds waiting for task 999" in err
    assert "Traceback" not in err


@pytest.mark.parametrize(
    "result_args,error_fragment",
    [
        ((), "task id is required"),
        (("not-a-tid",), "invalid task id 'not-a-tid'"),
    ],
    ids=["omitted-tid", "invalid-tid"],
)
def test_result_invalid_invocation_exits_cleanly(
    workdir: Path,
    weft_harness: WeftTestHarness,
    result_args: tuple[str, ...],
    error_fragment: str,
) -> None:
    """The shipped entrypoint contains result invocation errors [CLI-1.2.2]."""
    rc, out, err = run_cli(
        "result",
        *result_args,
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 2
    assert out == ""
    assert error_fragment in err
    assert "Traceback" not in err


@pytest.mark.parametrize(
    "run_args,status,event,exit_code,error_fragment",
    [
        (
            ("--function", "tests.tasks.sample_targets:fail_payload"),
            "failed",
            "work_failed",
            1,
            "intentional failure for testing",
        ),
        (
            (
                "--function",
                "tests.tasks.sample_targets:streaming_echo",
                "--kw",
                "delay=60",
                "--timeout",
                "0.5",
            ),
            "timeout",
            "work_timeout",
            124,
            "timed out",
        ),
    ],
)
def test_result_stream_preserves_real_task_failure_exit_status(
    workdir: Path,
    weft_harness: WeftTestHarness,
    run_args: tuple[str, ...],
    status: str,
    event: str,
    exit_code: int,
    error_fragment: str,
) -> None:
    """Real Manager/Consumer terminal outcomes keep the CLI result exit policy."""
    rc, out, err = run_cli(
        "run",
        "--no-wait",
        *run_args,
        cwd=workdir,
        harness=weft_harness,
    )
    assert rc == 0, err
    tid = out.strip()
    weft_harness.register_tid(tid)
    with pytest.raises(RuntimeError, match=event):
        weft_harness.wait_for_completion(tid)
    snapshot = task_commands.task_terminal_snapshot(tid, context=weft_harness.context)
    assert snapshot.terminal
    assert snapshot.status == status

    rc, out, err = run_cli(
        "result",
        tid,
        "--stream",
        "--timeout",
        "5",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == exit_code
    assert out == ""
    assert error_fragment in err.lower()
    if run_args[0] == "--function" and status == "failed":
        # Function failures intentionally carry the captured target traceback.
        # Pin that diagnostic, rather than mistaking it for a CLI crash.
        assert snapshot.error is not None
        assert err == snapshot.error.strip()
        assert "weft/cli/" not in err
        assert "weft/commands/" not in err
    else:
        assert "Traceback" not in err


@pytest.mark.parametrize("interactive", [False, True])
def test_result_stream_keeps_live_stderr_off_stdout(
    workdir: Path,
    weft_harness: WeftTestHarness,
    interactive: bool,
) -> None:
    """Real command stderr stays diagnostic on outbox and live control routes."""
    ready_path = workdir / "diagnostic-stream-ready.txt"
    release_path = workdir / "diagnostic-stream-release.txt"
    rc, out, err = run_cli(
        "run",
        "--no-wait",
        "--interactive" if interactive else "--stream-output",
        "--",
        sys.executable,
        "-c",
        (
            "from pathlib import Path\n"
            "import sys, time\n"
            f"ready = Path({str(ready_path)!r})\n"
            f"release = Path({str(release_path)!r})\n"
            "print('live stdout', flush=True)\n"
            "print('live stderr', file=sys.stderr, flush=True)\n"
            "ready.write_text('ready', encoding='utf-8')\n"
            "deadline = time.monotonic() + 120\n"
            "while not release.exists() and time.monotonic() < deadline:\n"
            "    time.sleep(0.05)\n"
        ),
        cwd=workdir,
        harness=weft_harness,
    )
    assert rc == 0, err
    tid = out.strip()
    weft_harness.register_tid(tid)
    if interactive:
        # Real input starts the session. Keep it alive after EOF so runtime-only
        # ctrl_out frames remain observable through the actual CLI stream.
        with weft_harness.context.queue(f"T{tid}.inbox") as inbox:
            inbox.write(json.dumps({"close": True}))

    try:
        assert _wait_for_path(ready_path)
        assert _wait_for_stream_chunk(weft_harness, tid, "live stdout\n")
        assert _wait_for_stream_chunk(
            weft_harness,
            tid,
            "live stderr\n",
            stream="stderr",
            control=interactive,
        )
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
        assert out == "live stdout"
        assert "live stderr" in err
        assert "Timed out after" in err
        assert not release_path.exists(), "observe diagnostics before task exit"
    finally:
        release_path.write_text("release", encoding="utf-8")
    weft_harness.wait_for_completion(tid)
