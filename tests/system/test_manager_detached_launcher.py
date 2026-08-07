"""Tests for the detached manager-launch wrapper."""

from __future__ import annotations

import base64
import json
import subprocess
from typing import Never

import pytest

from weft import manager_detached_launcher

pytestmark = [pytest.mark.shared]


def _launcher_payload() -> str:
    payload = {
        "command": ["python", "-m", "weft.manager_process"],
        "stderr_path": "/tmp/weft-manager.stderr.log",
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
) -> None:
    monkeypatch.setattr(
        manager_detached_launcher,
        "_launch_runtime",
        lambda _command, _stderr_path: _raise(_OrdinaryBoundaryError("spawn detail")),
    )

    result = manager_detached_launcher.main([_launcher_payload()])

    assert result == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "event": "spawn_failed",
        "error": "spawn detail",
        "stderr_path": "/tmp/weft-manager.stderr.log",
    }


def test_main_does_not_contain_fatal_launch_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fatal = BaseException("fatal spawn")
    monkeypatch.setattr(
        manager_detached_launcher,
        "_launch_runtime",
        lambda _command, _stderr_path: _raise(fatal),
    )

    with pytest.raises(BaseException) as caught:
        manager_detached_launcher.main([_launcher_payload()])

    assert caught.value is fatal
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
