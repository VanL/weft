"""Tests for the detached manager-launch wrapper."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Never

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft import manager_detached_launcher
from weft.context import build_context
from weft.core import manager_runtime
from weft.helpers import pid_is_live, terminate_process_tree

pytestmark = [pytest.mark.shared]


@pytest.mark.timeout(10)
@pytest.mark.parametrize(
    "event",
    [
        "withheld",
        "partial",
        "malformed",
        "eof",
        "invalid-pid",
        "valid",
        "cancelled",
        "ignores-abort",
    ],
)
def test_first_launcher_event_retains_child_ownership(
    event: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real child exists before protocol failure, and must be reaped."""
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    invocation = manager_runtime._build_manager_runtime_invocation(context)
    ready = tmp_path / "child.json"
    script = tmp_path / "protocol.py"
    script.write_text(
        "import json, os, subprocess, sys, threading\n"
        "from pathlib import Path\n"
        "detach = {'start_new_session': True} if os.name != 'nt' else {}\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import threading; threading.Event().wait()'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **detach)\n"
        f"event = {event!r}\n"
        "if event == 'valid': print(json.dumps({'event': 'spawned', 'pid': child.pid}), flush=True)\n"
        "elif event == 'malformed': print('invalid-json', flush=True)\n"
        "elif event == 'invalid-pid': print(json.dumps({'event': 'spawned', 'pid': 0}), flush=True)\n"
        "elif event == 'partial': sys.stdout.write('{'); sys.stdout.flush()\n"
        "elif event == 'eof': os.close(sys.stdout.fileno())\n"
        "if event == 'valid': print(json.dumps({'event': 'subsequent'}), flush=True)\n"
        f"ready = Path({str(ready)!r})\n"
        "pending = ready.with_suffix('.pending')\n"
        "pending.write_text(json.dumps({'child': child.pid}))\n"
        "pending.replace(ready)\n"
        "sys.stdin.readline()\n"
        "if event == 'ignores-abort': threading.Event().wait()\n"
        "child.terminate()\n"
        "child.wait()\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        manager_runtime,
        "_build_manager_detached_launcher_command",
        lambda *_: [sys.executable, str(script)],
    )
    original_popen = subprocess.Popen
    processes: list[subprocess.Popen[str]] = []

    def launch_ready(*args: Any, **kwargs: Any) -> subprocess.Popen[str]:
        process = original_popen(*args, **kwargs)
        processes.append(process)
        deadline = time.monotonic() + 5.0
        while not ready.exists():
            assert process.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.01)
        return process

    monkeypatch.setattr(manager_runtime.subprocess, "Popen", launch_ready)
    monkeypatch.setattr(manager_runtime, "MANAGER_STARTUP_TIMEOUT_SECONDS", 0.1)
    cancellation = KeyboardInterrupt("cancel bootstrap")
    if event == "cancelled":
        monkeypatch.setattr(
            manager_runtime, "_read_launcher_first_line", lambda _: _raise(cancellation)
        )
    try:
        if event == "cancelled":
            with pytest.raises(KeyboardInterrupt) as cancelled:
                manager_runtime._launch_detached_manager(context, invocation)
            assert cancelled.value is cancellation
        elif event == "valid":
            launch = manager_runtime._launch_detached_manager(context, invocation)
            assert launch.pid == json.loads(ready.read_text())["child"]
            manager_runtime._send_launcher_signal(launch.launcher_process, "ABORT")
            stdout, _stderr = launch.launcher_process.communicate(timeout=5.0)
            assert json.loads(stdout) == {"event": "subsequent"}
        else:
            with pytest.raises(
                RuntimeError, match="Failed to start Manager process"
            ) as caught:
                manager_runtime._launch_detached_manager(context, invocation)
            if event in {"withheld", "partial", "ignores-abort"}:
                assert "startup_phase=first_event" in str(caught.value)
            if event == "eof":
                assert "did not report a spawned manager PID" in str(caught.value)
                assert "startup_phase=first_event" not in str(caught.value)
        assert processes[0].poll() is not None
        assert not pid_is_live(json.loads(ready.read_text())["child"])
    finally:
        _cleanup_protocol_processes(processes, ready)


def _cleanup_protocol_processes(
    processes: list[subprocess.Popen[str]], ready: Path
) -> None:
    for process in processes:
        if process.poll() is None:
            terminate_process_tree(process.pid, timeout=1.0)
        process.communicate(timeout=2.0)
    if ready.exists():
        child_pid = json.loads(ready.read_text())["child"]
        if pid_is_live(child_pid):
            terminate_process_tree(child_pid, timeout=1.0)


def _launcher_payload(stderr_path: Path) -> str:
    payload = {
        "command": ["python", "-m", "weft.manager_process"],
        "stderr_path": str(stderr_path),
    }
    return base64.b64encode(json.dumps(payload).encode()).decode()


class _OrdinaryBoundaryError(Exception):
    pass


def _raise(error: BaseException) -> Never:
    raise error


class _FakeProcess:
    pid = 123

    def __init__(
        self,
        *,
        terminate_error: BaseException | None = None,
        kill_error: BaseException | None = None,
    ) -> None:
        self._terminate_error = terminate_error
        self._kill_error = kill_error
        self.wait_calls = 0

    def terminate(self) -> None:
        if self._terminate_error is not None:
            raise self._terminate_error

    def kill(self) -> None:
        if self._kill_error is not None:
            raise self._kill_error

    def wait(self, *, timeout: float) -> int:
        assert timeout == 1.0
        self.wait_calls += 1
        if self.wait_calls == 1:
            raise subprocess.TimeoutExpired("manager", timeout)
        return 0


@pytest.mark.parametrize("operation", ["terminate", "kill"])
def test_terminate_runtime_propagates_unexpected_process_defect(
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    errors = {operation: RuntimeError(f"{operation} defect")}
    process = _FakeProcess(
        terminate_error=errors.get("terminate"),
        kill_error=errors.get("kill"),
    )
    monkeypatch.setattr(
        manager_detached_launcher,
        "terminate_process_tree",
        lambda _pid, *, timeout: False,
    )

    with pytest.raises(RuntimeError, match=f"{operation} defect"):
        manager_detached_launcher._terminate_runtime(process)  # type: ignore[arg-type]


@pytest.mark.parametrize("operation", ["terminate", "kill"])
def test_terminate_runtime_contains_os_process_exit_race(
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    errors = {operation: ProcessLookupError(f"{operation} race")}
    process = _FakeProcess(
        terminate_error=errors.get("terminate"),
        kill_error=errors.get("kill"),
    )
    monkeypatch.setattr(
        manager_detached_launcher,
        "terminate_process_tree",
        lambda _pid, *, timeout: False,
    )

    manager_detached_launcher._terminate_runtime(process)  # type: ignore[arg-type]


def test_main_reports_arbitrary_payload_failure_on_stderr(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        manager_detached_launcher.base64,
        "b64decode",
        lambda _payload: _raise(_OrdinaryBoundaryError("payload detail")),
    )

    result = manager_detached_launcher.main(["payload"])

    assert result == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Invalid detached launcher payload: payload detail\n"


def test_main_does_not_contain_fatal_payload_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fatal = BaseException("fatal payload")
    monkeypatch.setattr(
        manager_detached_launcher.base64,
        "b64decode",
        lambda _payload: _raise(fatal),
    )

    with pytest.raises(BaseException) as caught:
        manager_detached_launcher.main(["payload"])

    assert caught.value is fatal
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_main_reports_arbitrary_launch_failure_as_structured_event(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    stderr_path = tmp_path / "weft-manager.stderr.log"
    monkeypatch.setattr(
        manager_detached_launcher,
        "_launch_runtime",
        lambda _command, _stderr_path: _raise(_OrdinaryBoundaryError("spawn detail")),
    )

    result = manager_detached_launcher.main([_launcher_payload(stderr_path)])

    assert result == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "event": "spawn_failed",
        "error": "spawn detail",
        "stderr_path": str(stderr_path),
    }


def test_main_does_not_contain_fatal_launch_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    fatal = BaseException("fatal spawn")
    monkeypatch.setattr(
        manager_detached_launcher,
        "_launch_runtime",
        lambda _command, _stderr_path: _raise(fatal),
    )

    with pytest.raises(BaseException) as caught:
        manager_detached_launcher.main(
            [_launcher_payload(tmp_path / "weft-manager.stderr.log")]
        )

    assert caught.value is fatal
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
