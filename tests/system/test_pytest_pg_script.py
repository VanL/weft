"""Tests for the local Postgres-backed pytest helper script."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


def _load_pytest_pg_module() -> ModuleType:
    script_path = Path(__file__).resolve().parents[2] / "bin" / "pytest-pg"
    loader = importlib.machinery.SourceFileLoader(
        "weft_pytest_pg_script",
        str(script_path),
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise AssertionError(f"Unable to load pytest-pg script: {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    loader.exec_module(module)
    return module


class _FakeProcess:
    def __init__(self, wait_events: list[object], *, pid: int = 43210) -> None:
        self._wait_events = list(wait_events)
        self.pid = pid
        self.returncode: int | None = None

    def wait(self, timeout: float | None = None) -> int:
        if not self._wait_events:
            raise AssertionError("wait() called more times than expected")
        event = self._wait_events.pop(0)
        if isinstance(event, BaseException):
            raise event
        self.returncode = int(event)
        return self.returncode

    def poll(self) -> int | None:
        return self.returncode

    def send_signal(self, _sig: int) -> None:
        return None

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None


def test_launch_pytest_process_isolates_ctrl_c_on_current_platform(
    monkeypatch,
) -> None:
    """The helper should launch pytest in its own process group/session."""

    pytest_pg = _load_pytest_pg_module()
    recorded: dict[str, Any] = {}

    def _fake_popen(command: list[str], **kwargs: Any) -> _FakeProcess:
        recorded["command"] = command
        recorded["kwargs"] = kwargs
        return _FakeProcess([0])

    monkeypatch.setattr(pytest_pg.subprocess, "Popen", _fake_popen)

    process = pytest_pg._launch_pytest_process(["pytest"], env={"A": "1"})

    assert isinstance(process, _FakeProcess)
    assert recorded["command"] == ["pytest"]
    assert recorded["kwargs"]["cwd"] == pytest_pg.ROOT
    assert recorded["kwargs"]["env"] == {"A": "1"}
    assert recorded["kwargs"]["close_fds"] is True
    if pytest_pg.os.name == "nt":
        assert "creationflags" in recorded["kwargs"]
    else:
        assert recorded["kwargs"]["start_new_session"] is True


def test_run_pytest_command_forwards_first_interrupt_and_returns_130(
    monkeypatch,
    capsys,
) -> None:
    """A first Ctrl-C should forward SIGINT and preserve the wrapper exit code."""

    pytest_pg = _load_pytest_pg_module()
    process = _FakeProcess([KeyboardInterrupt()])
    calls: list[str] = []

    monkeypatch.setattr(
        pytest_pg,
        "_launch_pytest_process",
        lambda command, *, env: process,
    )
    monkeypatch.setattr(
        pytest_pg,
        "_send_pytest_interrupt",
        lambda proc: calls.append(f"interrupt:{proc.pid}"),
    )
    monkeypatch.setattr(
        pytest_pg,
        "_wait_for_process_exit",
        lambda proc, *, timeout: "exited",
    )
    monkeypatch.setattr(
        pytest_pg,
        "_terminate_pytest_process_tree",
        lambda proc: calls.append(f"terminate:{proc.pid}"),
    )
    monkeypatch.setattr(
        pytest_pg,
        "_kill_pytest_process_tree",
        lambda proc: calls.append(f"kill:{proc.pid}"),
    )

    exit_code = pytest_pg._run_pytest_command(["pytest"], env={"A": "1"})
    captured = capsys.readouterr()

    assert exit_code == 130
    assert calls == [f"interrupt:{process.pid}"]
    assert "forwarding SIGINT to pytest" in captured.err


def test_run_pytest_command_escalates_after_interrupt_timeout(
    monkeypatch,
    capsys,
) -> None:
    """If pytest ignores the interrupt, the helper should terminate then kill."""

    pytest_pg = _load_pytest_pg_module()
    process = _FakeProcess([KeyboardInterrupt()])
    calls: list[str] = []
    outcomes = iter(("timeout", "timeout", "exited"))

    monkeypatch.setattr(
        pytest_pg,
        "_launch_pytest_process",
        lambda command, *, env: process,
    )
    monkeypatch.setattr(
        pytest_pg,
        "_send_pytest_interrupt",
        lambda proc: calls.append(f"interrupt:{proc.pid}"),
    )
    monkeypatch.setattr(
        pytest_pg,
        "_wait_for_process_exit",
        lambda proc, *, timeout: next(outcomes),
    )
    monkeypatch.setattr(
        pytest_pg,
        "_terminate_pytest_process_tree",
        lambda proc: calls.append(f"terminate:{proc.pid}"),
    )
    monkeypatch.setattr(
        pytest_pg,
        "_kill_pytest_process_tree",
        lambda proc: calls.append(f"kill:{proc.pid}"),
    )

    exit_code = pytest_pg._run_pytest_command(["pytest"], env={"A": "1"})
    captured = capsys.readouterr()

    assert exit_code == 130
    assert calls == [
        f"interrupt:{process.pid}",
        f"terminate:{process.pid}",
        f"kill:{process.pid}",
    ]
    assert "terminating process tree" in captured.err
    assert "killing process tree" in captured.err
