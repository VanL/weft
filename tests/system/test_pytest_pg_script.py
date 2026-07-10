"""Tests for the local Postgres-backed pytest helper script."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


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


def _load_worker_count_module() -> ModuleType:
    script_path = Path(__file__).resolve().parents[2] / "bin" / "pytest-worker-count"
    loader = importlib.machinery.SourceFileLoader(
        "weft_pytest_worker_count_script",
        str(script_path),
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise AssertionError(
            f"Unable to load pytest worker count script: {script_path}"
        )
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


class _SyntheticKeyboardInterrupt(BaseException):
    """Synthetic interrupt used so pytest itself never observes Ctrl-C."""


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


def test_worker_count_helper_returns_logical_cpu_count_plus_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The helper should intentionally oversubscribe visible logical CPUs."""

    worker_count = _load_worker_count_module()
    monkeypatch.setattr(worker_count.os, "cpu_count", lambda: 16)

    assert worker_count.worker_count(extra=1) == 17
    assert worker_count.worker_count(extra=2) == 18


def test_worker_count_helper_writes_github_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CI should be able to export the computed xdist worker count."""

    worker_count = _load_worker_count_module()
    github_env = tmp_path / "github-env"
    monkeypatch.setenv("GITHUB_ENV", str(github_env))

    worker_count.write_github_env("PYTEST_XDIST_AUTO_NUM_WORKERS", "17")

    assert github_env.read_text(encoding="utf-8") == (
        "PYTEST_XDIST_AUTO_NUM_WORKERS=17\n"
    )


def test_pytest_pg_default_database_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """The PG helper should default to the Weft-owned test database name."""

    monkeypatch.delenv("SIMPLEBROKER_PG_TEST_DB", raising=False)
    pytest_pg = _load_pytest_pg_module()

    assert pytest_pg.POSTGRES_DB == "weft_test"


def test_build_test_env_sets_worker_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The PG helper should run with the same oversubscribed xdist count."""

    pytest_pg = _load_pytest_pg_module()
    monkeypatch.delenv("PYTEST_XDIST_AUTO_NUM_WORKERS", raising=False)
    monkeypatch.setattr(pytest_pg, "_pytest_worker_count", lambda: "17")

    env = pytest_pg._build_test_env(dsn="postgresql://example")

    assert env["PYTEST_XDIST_AUTO_NUM_WORKERS"] == "17"
    assert env["BROKER_TEST_BACKEND"] == "postgres"


def test_build_test_env_owns_backend_connection_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ambient backend config should not override the temporary test container."""

    pytest_pg = _load_pytest_pg_module()
    monkeypatch.setattr(pytest_pg, "_pytest_worker_count", lambda: "17")
    for prefix in ("BROKER", "WEFT"):
        monkeypatch.setenv(f"{prefix}_BACKEND", "postgres")
        monkeypatch.setenv(f"{prefix}_BACKEND_TARGET", "postgresql://stale")
        monkeypatch.setenv(f"{prefix}_BACKEND_PASSWORD", "wrong-password")
        monkeypatch.setenv(f"{prefix}_BACKEND_SCHEMA", "stale_schema")

    env = pytest_pg._build_test_env(
        dsn="postgresql://postgres:postgres@127.0.0.1:33017/weft_test",
    )

    assert env["BROKER_BACKEND"] == "postgres"
    assert env["BROKER_BACKEND_TARGET"] == (
        "postgresql://postgres:postgres@127.0.0.1:33017/weft_test"
    )
    assert env["WEFT_BACKEND"] == "postgres"
    assert env["WEFT_BACKEND_TARGET"] == (
        "postgresql://postgres:postgres@127.0.0.1:33017/weft_test"
    )
    assert "BROKER_BACKEND_PASSWORD" not in env
    assert "WEFT_BACKEND_PASSWORD" not in env
    assert "BROKER_BACKEND_SCHEMA" not in env
    assert "WEFT_BACKEND_SCHEMA" not in env


def test_build_test_env_preserves_explicit_worker_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Manual PG runs should be able to override the worker count."""

    pytest_pg = _load_pytest_pg_module()
    monkeypatch.setenv("PYTEST_XDIST_AUTO_NUM_WORKERS", "5")
    monkeypatch.setattr(
        pytest_pg,
        "_pytest_worker_count",
        lambda: (_ for _ in ()).throw(AssertionError("should not be called")),
    )

    env = pytest_pg._build_test_env(dsn="postgresql://example")

    assert env["PYTEST_XDIST_AUTO_NUM_WORKERS"] == "5"


def test_build_pytest_command_accepts_target_slices() -> None:
    """The PG helper should support release-gate sharding by pytest target."""

    pytest_pg = _load_pytest_pg_module()

    command = pytest_pg._build_pytest_command(
        marker="not sqlite_only",
        pytest_targets=["tests/cli", "tests/core"],
    )

    pytest_index = command.index("pytest")
    assert command[pytest_index + 1 : pytest_index + 3] == ["tests/cli", "tests/core"]
    assert "-m" in command
    assert command[command.index("-m") + 1] == "not sqlite_only"
    assert command[command.index("-n") + 1] == "logical"
    assert "--durations=100" in command
    assert "faulthandler_timeout=720" in command
    assert "--timeout=900" in command
    assert "--full-trace" in command


def test_build_pytest_command_defaults_to_full_tests_tree() -> None:
    """The default PG helper behavior should stay unchanged."""

    pytest_pg = _load_pytest_pg_module()

    command = pytest_pg._build_pytest_command(marker="shared")

    pytest_index = command.index("pytest")
    assert command[pytest_index + 1] == "tests"
    assert command[command.index("-m") + 1] == "shared"


def test_start_postgres_container_keeps_container_until_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wrapper should not erase failed container logs via docker --rm."""

    pytest_pg = _load_pytest_pg_module()
    commands: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(pytest_pg, "_run", fake_run)
    monkeypatch.setattr(pytest_pg, "_wait_for_postgres", lambda _name: "54321")

    container_name, dsn = pytest_pg._start_postgres_container()

    assert container_name.startswith("weft-pg-test-")
    assert dsn == "postgresql://postgres:postgres@127.0.0.1:54321/weft_test"
    docker_run = commands[0]
    assert docker_run[:3] == ["docker", "run", "--detach"]
    assert "--rm" not in docker_run


def test_print_container_logs_includes_stdout_and_stderr(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Failure diagnostics should preserve Postgres container output."""

    pytest_pg = _load_pytest_pg_module()

    def fake_run(
        cmd: list[str],
        **_kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="database system is ready\n",
            stderr="server log line\n",
        )

    monkeypatch.setattr(pytest_pg.subprocess, "run", fake_run)

    pytest_pg._print_container_logs("weft-pg-test-example")

    captured = capsys.readouterr()
    assert "Postgres container logs (weft-pg-test-example):" in captured.err
    assert "database system is ready" in captured.err
    assert "server log line" in captured.err


def test_run_pytest_command_forwards_first_interrupt_and_returns_130(
    monkeypatch,
    capsys,
) -> None:
    """A first Ctrl-C should forward SIGINT and preserve the wrapper exit code."""

    pytest_pg = _load_pytest_pg_module()
    monkeypatch.setattr(
        pytest_pg,
        "KeyboardInterrupt",
        _SyntheticKeyboardInterrupt,
        raising=False,
    )
    process = _FakeProcess([_SyntheticKeyboardInterrupt()])
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
    monkeypatch.setattr(
        pytest_pg,
        "KeyboardInterrupt",
        _SyntheticKeyboardInterrupt,
        raising=False,
    )
    process = _FakeProcess([_SyntheticKeyboardInterrupt()])
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
