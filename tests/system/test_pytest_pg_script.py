"""Tests for the local Postgres-backed pytest helper script."""

from __future__ import annotations

import builtins
import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from tests.helpers.pytest_pg_runtime import (
    load_pytest_pg_module as _load_pytest_pg_module,
)


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
    def __init__(
        self, wait_events: list[int | BaseException], *, pid: int = 43210
    ) -> None:
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


def test_send_pytest_interrupt_contains_os_failure_and_uses_terminate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest_pg = _load_pytest_pg_module()
    process = _FakeProcess([])
    calls: list[str] = []
    monkeypatch.setattr(pytest_pg, "os", SimpleNamespace(name="posix", killpg=None))
    monkeypatch.setattr(
        pytest_pg.os,
        "killpg",
        lambda *_args: (_ for _ in ()).throw(OSError("signal failed")),
        raising=False,
    )
    monkeypatch.setattr(process, "terminate", lambda: calls.append("terminate"))

    pytest_pg._send_pytest_interrupt(process)

    assert calls == ["terminate"]


def test_send_pytest_interrupt_propagates_unexpected_signal_defect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest_pg = _load_pytest_pg_module()
    process = _FakeProcess([])
    monkeypatch.setattr(pytest_pg, "os", SimpleNamespace(name="posix", killpg=None))
    monkeypatch.setattr(
        pytest_pg.os,
        "killpg",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("signal defect")),
        raising=False,
    )

    with pytest.raises(RuntimeError, match="signal defect"):
        pytest_pg._send_pytest_interrupt(process)


def test_send_pytest_interrupt_contains_terminate_os_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest_pg = _load_pytest_pg_module()
    process = _FakeProcess([])
    monkeypatch.setattr(pytest_pg, "os", SimpleNamespace(name="posix", killpg=None))
    monkeypatch.setattr(
        pytest_pg.os,
        "killpg",
        lambda *_args: (_ for _ in ()).throw(OSError("signal failed")),
        raising=False,
    )
    attempts: list[str] = []

    def fail_terminate() -> None:
        attempts.append("terminate")
        raise OSError("process already exited")

    monkeypatch.setattr(process, "terminate", fail_terminate)

    pytest_pg._send_pytest_interrupt(process)

    assert attempts == ["terminate"]


def test_send_pytest_interrupt_propagates_unexpected_terminate_defect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest_pg = _load_pytest_pg_module()
    process = _FakeProcess([])
    monkeypatch.setattr(pytest_pg, "os", SimpleNamespace(name="posix", killpg=None))
    monkeypatch.setattr(
        pytest_pg.os,
        "killpg",
        lambda *_args: (_ for _ in ()).throw(OSError("signal failed")),
        raising=False,
    )
    monkeypatch.setattr(
        process,
        "terminate",
        lambda: (_ for _ in ()).throw(RuntimeError("terminate defect")),
    )

    with pytest.raises(RuntimeError, match="terminate defect"):
        pytest_pg._send_pytest_interrupt(process)


@pytest.mark.parametrize(
    ("helper_name", "process_method"),
    [
        ("_terminate_pytest_process_tree", "terminate"),
        ("_kill_pytest_process_tree", "kill"),
    ],
)
def test_pytest_process_tree_helpers_fallback_only_for_import_error(
    helper_name: str,
    process_method: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest_pg = _load_pytest_pg_module()
    process = _FakeProcess([])
    calls: list[str] = []
    real_import = builtins.__import__

    def fail_helpers_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "weft.helpers":
            raise ImportError("helper unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_helpers_import)
    monkeypatch.setattr(
        process,
        process_method,
        lambda: calls.append(process_method),
    )

    getattr(pytest_pg, helper_name)(process)

    assert calls == [process_method]


@pytest.mark.parametrize(
    ("helper_name", "process_method"),
    [
        ("_terminate_pytest_process_tree", "terminate"),
        ("_kill_pytest_process_tree", "kill"),
    ],
)
def test_pytest_process_tree_helpers_contain_fallback_os_failure(
    helper_name: str,
    process_method: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest_pg = _load_pytest_pg_module()
    process = _FakeProcess([])
    real_import = builtins.__import__

    def fail_helpers_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "weft.helpers":
            raise ImportError("helper unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_helpers_import)
    attempts: list[str] = []

    def fail_process_method() -> None:
        attempts.append(process_method)
        raise OSError("process already exited")

    monkeypatch.setattr(process, process_method, fail_process_method)

    getattr(pytest_pg, helper_name)(process)

    assert attempts == [process_method]


@pytest.mark.parametrize(
    "helper_name",
    ["_terminate_pytest_process_tree", "_kill_pytest_process_tree"],
)
def test_pytest_process_tree_helpers_propagate_unexpected_import_defect(
    helper_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest_pg = _load_pytest_pg_module()
    process = _FakeProcess([])
    real_import = builtins.__import__

    def fail_helpers_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "weft.helpers":
            raise RuntimeError("helper import defect")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_helpers_import)

    with pytest.raises(RuntimeError, match="helper import defect"):
        getattr(pytest_pg, helper_name)(process)


@pytest.mark.parametrize(
    ("helper_name", "process_method"),
    [
        ("_terminate_pytest_process_tree", "terminate"),
        ("_kill_pytest_process_tree", "kill"),
    ],
)
def test_pytest_process_tree_helpers_propagate_unexpected_fallback_defect(
    helper_name: str,
    process_method: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest_pg = _load_pytest_pg_module()
    process = _FakeProcess([])
    real_import = builtins.__import__

    def fail_helpers_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "weft.helpers":
            raise ImportError("helper unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_helpers_import)
    monkeypatch.setattr(
        process,
        process_method,
        lambda: (_ for _ in ()).throw(RuntimeError("fallback defect")),
    )

    with pytest.raises(RuntimeError, match="fallback defect"):
        getattr(pytest_pg, helper_name)(process)


def test_main_reports_unexpected_cli_boundary_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pytest_pg = _load_pytest_pg_module()

    class CliBoundaryFailure(Exception):
        pass

    def start_container() -> tuple[str, str]:
        return "test-container", "postgresql://test"

    def fail_schema(*, env: dict[str, str]) -> None:
        assert env == {"TEST_DSN": "postgresql://test"}
        raise CliBoundaryFailure("cli boundary detail")

    cleanup_events: list[str] = []
    monkeypatch.setattr(pytest_pg.shutil, "which", lambda name: f"/test/bin/{name}")
    monkeypatch.setattr(pytest_pg, "_start_postgres_container", start_container)
    monkeypatch.setattr(
        pytest_pg,
        "_build_test_env",
        lambda *, dsn: {"TEST_DSN": dsn},
    )
    monkeypatch.setattr(pytest_pg, "_initialize_broker_schema", fail_schema)
    monkeypatch.setattr(
        pytest_pg,
        "_print_container_logs",
        lambda name: cleanup_events.append(f"logs:{name}"),
    )
    monkeypatch.setattr(
        pytest_pg,
        "_cleanup_container",
        lambda name: cleanup_events.append(f"cleanup:{name}"),
    )
    monkeypatch.setattr(pytest_pg.sys, "argv", ["pytest-pg"])

    assert pytest_pg.main() == 1
    captured = capsys.readouterr()
    assert captured.err == "cli boundary detail\n"
    assert cleanup_events == ["logs:test-container", "cleanup:test-container"]


def test_launch_pytest_process_isolates_ctrl_c_on_current_platform(
    monkeypatch: pytest.MonkeyPatch,
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
        flags = recorded["kwargs"]["creationflags"]
        group_flag = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", None)
        assert isinstance(group_flag, int)
        assert flags & group_flag
    else:
        assert recorded["kwargs"]["start_new_session"] is True


def test_worker_count_helper_caps_logical_cpu_count_plus_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The helper should oversubscribe small hosts without exceeding 12 workers."""

    worker_count = _load_worker_count_module()

    monkeypatch.setattr(worker_count.os, "cpu_count", lambda: 8)
    assert worker_count.worker_count(extra=1) == 9
    assert worker_count.worker_count(extra=2) == 10

    monkeypatch.setattr(worker_count.os, "cpu_count", lambda: 16)
    assert worker_count.worker_count(extra=1) == 12
    assert worker_count.worker_count(extra=2) == 12


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
    assert env["WEFT_PG_TEST_DSN"] == "postgresql://example"
    assert "SIMPLEBROKER_PG_TEST_DSN" not in env


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
        "postgresql://postgres@127.0.0.1:33017/weft_test"
    )
    assert env["WEFT_BACKEND"] == "postgres"
    assert env["WEFT_BACKEND_TARGET"] == (
        "postgresql://postgres@127.0.0.1:33017/weft_test"
    )
    assert env["BROKER_BACKEND_PASSWORD"] == "postgres"
    assert env["WEFT_BACKEND_PASSWORD"] == "postgres"
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


@pytest.mark.parametrize("readiness_failure", [False, True])
def test_start_postgres_container_keeps_container_until_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    readiness_failure: bool,
) -> None:
    """Retain usable containers; clean owned resources if readiness fails."""

    pytest_pg = _load_pytest_pg_module()
    commands: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(pytest_pg, "_run", fake_run)
    disposition: list[tuple[str, str]] = []

    def readiness(_name: str) -> str:
        if readiness_failure:
            raise RuntimeError("readiness failed")
        return "54321"

    monkeypatch.setattr(pytest_pg, "_wait_for_postgres", readiness)
    monkeypatch.setattr(
        pytest_pg,
        "_print_container_logs",
        lambda name: disposition.append(("logs", name)),
    )
    monkeypatch.setattr(
        pytest_pg,
        "_cleanup_container",
        lambda name: disposition.append(("cleanup", name)),
    )
    if readiness_failure:
        with pytest.raises(RuntimeError, match="readiness failed"):
            pytest_pg._start_postgres_container()
        container_name = commands[0][commands[0].index("--name") + 1]
        assert disposition == [("logs", container_name), ("cleanup", container_name)]
    else:
        container_name, dsn = pytest_pg._start_postgres_container()
        assert dsn == "postgresql://postgres:postgres@127.0.0.1:54321/weft_test"
        assert disposition == []
    assert container_name.startswith("weft-pg-test-")
    docker_run = commands[0]
    assert docker_run[:3] == ["docker", "run", "--detach"]
    assert "--rm" not in docker_run


def test_wait_for_postgres_requires_published_port_and_successful_tcp_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Port discovery alone and a rejected SQL connection are not readiness."""
    runtime = _load_pytest_pg_module()
    ports = iter([None, "54321", "54321"])
    results = iter(
        [
            subprocess.CompletedProcess([], 1, stdout="", stderr="starting up"),
            subprocess.CompletedProcess([], 0, stdout="1\n", stderr=""),
        ]
    )
    commands: list[list[str]] = []

    def query(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        assert command[command.index("-h") + 1] == "127.0.0.1"
        assert command[command.index("-d") + 1] == runtime.POSTGRES_DB
        return next(results)

    monkeypatch.setattr(runtime, "_docker_port", lambda _name: next(ports))
    monkeypatch.setattr(runtime, "subprocess", SimpleNamespace(run=query))
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)
    assert runtime._wait_for_postgres("owned-server") == "54321"
    assert len(commands) == 2


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
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
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
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
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


@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize("fault", ["none", "timeout", "unwritable"])
def test_isolated_pg_retains_evidence_before_cleanup(
    tmp_path: Path, fails: bool, fault: str
) -> None:
    """Real pytest failure resumes the yield fixture without throwing into it."""
    directory = tmp_path / "diagnostics"
    if fault == "unwritable":
        directory.write_text("occupied")
    else:
        directory.mkdir()
    (tmp_path / "server-token").write_bytes(b"isolated-server-evidence\xff\n")
    (tmp_path / "conftest.py").write_text(
        """import os
import subprocess
import re
from pathlib import Path
from types import SimpleNamespace
import pytest
from tests.helpers import pytest_pg_runtime

pytest_plugins = ['tests.helpers.pytest_pg_runtime']

@pytest.fixture(autouse=True)
def docker_boundary(monkeypatch):
    real_loader = pytest_pg_runtime.load_pytest_pg_module
    root = Path(__file__).parent
    def load():
        runtime = real_loader()
        runtime._start_postgres_container = lambda: ('owned-probe', 'postgresql://localhost/probe')
        runtime._initialize_broker_schema = lambda **kwargs: None
        ticks = iter([0.0, 0.0, 10.0 if os.environ['PROBE_FAULT'] == 'timeout' else 3.0])
        runtime.time = SimpleNamespace(monotonic=lambda: next(ticks), time_ns=lambda: 1)
        def run(argv, **kwargs):
            if argv[:3] == ['docker', 'rm', '-f']:
                (root / 'server-token').unlink()
                (root / 'removed').write_text('owned-probe')
                return subprocess.CompletedProcess(argv, 0)
            if argv[:2] == ['docker', 'inspect']:
                assert argv[2] == '--format' and argv[-1] == 'owned-probe'
                assert set(re.findall(r'\\.State\\.(\\w+)', argv[3])) == {
                    'Status', 'ExitCode', 'OOMKilled', 'Error', 'StartedAt', 'FinishedAt',
                }
                assert '.Config' not in argv[3] and '.Env' not in argv[3]
                assert kwargs['timeout'] == 10
                if os.environ['PROBE_FAULT'] == 'timeout':
                    raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
                os.write(kwargs['stdout'].fileno(), b'STATE running exit=0 oom=false\\n')
                return subprocess.CompletedProcess(argv, 0)
            if argv[:2] == ['docker', 'logs']:
                assert argv == ['docker', 'logs', '--timestamps', '--tail', '2000', 'owned-probe']
                assert kwargs['timeout'] == 7
                token = root / 'server-token'
                payload = token.read_bytes() if token.exists() else b'container gone\\n'
                os.write(kwargs['stdout'].fileno(), payload)
                return subprocess.CompletedProcess(argv, 0 if token.exists() else 1)
            (root / 'unexpected').write_text(repr(argv))
            raise AssertionError('unexpected command')
        runtime.subprocess = SimpleNamespace(
            run=run, DEVNULL=subprocess.DEVNULL, STDOUT=subprocess.STDOUT,
            TimeoutExpired=subprocess.TimeoutExpired, SubprocessError=subprocess.SubprocessError,
        )
        return runtime
    monkeypatch.setattr(pytest_pg_runtime, 'load_pytest_pg_module', load)
""",
        encoding="utf-8",
    )
    (tmp_path / "test_probe.py").write_text(
        "def test_probe(isolated_postgres_server):\n"
        + (
            "    raise AssertionError('original isolated failure')\n"
            if fails
            else "    pass\n"
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.pop("PYTEST_ADDOPTS", None)
    env["PROBE_FAULT"] = fault
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTEST_XDIST_AUTO_NUM_WORKERS"] = "1"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    env["WEFT_TEST_DIAGNOSTICS_DIR"] = str(directory)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "test_probe.py"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == int(fails), result.stdout + result.stderr
    if fails:
        assert "original isolated failure" in result.stdout
    assert ("1 failed" if fails else "1 passed") in result.stdout
    assert "ERROR" not in result.stdout + result.stderr
    assert (tmp_path / "removed").read_text() == "owned-probe"
    assert not (tmp_path / "server-token").exists()
    assert not (tmp_path / "unexpected").exists()
    artifact = directory / "postgres-owned-probe.log"
    if fault == "unwritable":
        return
    assert artifact.exists(), "isolated server evidence lost before cleanup"
    retained = artifact.read_bytes()
    assert b"owned-probe" in retained
    if fault == "timeout":
        assert b"collection error=TimeoutExpired" in retained
        assert b"skipped budget exhausted" in retained
        assert b"isolated-server-evidence" not in retained
    else:
        assert b"isolated-server-evidence\xff" in retained
        assert retained.index(b"command=") < retained.index(b"STATE running")
        assert retained.index(b"STATE running") < retained.index(b"returncode=0")
        assert retained.index(b"returncode=0") < retained.index(
            b"isolated-server-evidence"
        )


@pytest.mark.skipif(
    os.environ.get("BROKER_TEST_BACKEND") != "postgres", reason="requires bin/pytest-pg"
)
def test_postgres_readiness_rejects_bootstrap_then_initializes_schema(
    tmp_path: Path,
) -> None:
    """The temporary Unix server cannot authorize host-side fixture startup."""
    runtime = _load_pytest_pg_module()
    name = f"weft-readiness-test-{uuid.uuid4().hex[:8]}"
    probe = tmp_path / "probe"
    probe.mkdir()
    # The official image runs init scripts as postgres, not the host runner UID.
    probe.chmod(0o777)
    script = tmp_path / "hold.sh"
    script.write_text(
        "#!/bin/sh\ntouch /probe/bootstrap-ready\n"
        "while [ ! -f /probe/release ]; do sleep 0.05; done\n",
        encoding="utf-8",
    )
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                name,
                "--env",
                f"POSTGRES_PASSWORD={runtime.POSTGRES_PASSWORD}",
                "--env",
                f"POSTGRES_USER={runtime.POSTGRES_USER}",
                "--env",
                f"POSTGRES_DB={runtime.POSTGRES_DB}",
                "--publish-all",
                "--volume",
                f"{probe}:/probe",
                "--volume",
                f"{script}:/docker-entrypoint-initdb.d/hold.sh:ro",
                runtime.POSTGRES_IMAGE,
            ],
            check=True,
            capture_output=True,
        )
        deadline = time.monotonic() + 30.0
        while not (probe / "bootstrap-ready").exists():
            if time.monotonic() >= deadline:
                raise AssertionError("actual bootstrap barrier was not reached")
            time.sleep(0.05)
        port = runtime._docker_port(name)
        assert port is not None
        # Unix SQL proves the temporary server is ready while final TCP is held.
        unix = subprocess.run(
            [
                "docker",
                "exec",
                name,
                "psql",
                "-U",
                runtime.POSTGRES_USER,
                "-d",
                runtime.POSTGRES_DB,
                "-tAc",
                "SELECT 1",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        assert unix.stdout.strip() == "1"
        with pytest.raises(RuntimeError, match="Connection refused"):
            runtime._wait_for_postgres(name, timeout_seconds=3.0)
        (probe / "release").touch()
        assert runtime._wait_for_postgres(name) == port
        dsn = (
            f"postgresql://{runtime.POSTGRES_USER}:{runtime.POSTGRES_PASSWORD}"
            f"@127.0.0.1:{port}/{runtime.POSTGRES_DB}"
        )
        runtime._initialize_broker_schema(env=runtime._build_test_env(dsn=dsn))
        meta = subprocess.run(
            [
                "docker",
                "exec",
                name,
                "psql",
                "-h",
                "127.0.0.1",
                "-U",
                runtime.POSTGRES_USER,
                "-d",
                runtime.POSTGRES_DB,
                "-tAc",
                "SELECT count(*) FROM simplebroker_pg_v1.meta",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        assert meta.stdout.strip() == "1"
    finally:
        (probe / "release").touch()
        runtime._cleanup_container(name)
