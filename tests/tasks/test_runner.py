"""Tests for the TaskRunner orchestration layer."""

from __future__ import annotations

import functools
import json
import logging
import multiprocessing
import os
import queue
import subprocess
import sys
import threading
import time
from collections import Counter
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from io import TextIOWrapper
from itertools import combinations
from multiprocessing.connection import Connection
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar, cast
from unittest.mock import Mock

import psutil
import pytest

from tests.fixtures.llm_test_models import TEST_MODEL_ID
from tests.helpers.leaked_sender import (
    kill_pid,
    leaking_session_producer,
    wait_for_pidfile,
)
from tests.helpers.process_tree import kill_pids, tree_command
from tests.helpers.typing import record_and_return
from tests.tasks.sample_targets import wait_for_file
from weft import helpers as helpers_module
from weft._constants import (
    AGENT_SESSION_READY_TIMEOUT_SECONDS,
    DEFAULT_POLLING_INTERVAL,
)
from weft.core import terminal_handoff_transport as transport_module
from weft.core.agents.runtime import AgentExecutionResult
from weft.core.resource_monitor import (
    BaseResourceMonitor,
    ResourceMetrics,
    ResourceMonitor,
)
from weft.core.runner_diagnostics import runner_diagnostics
from weft.core.runners import RunnerOutcome
from weft.core.runners import host as host_module
from weft.core.runners import sessions as sessions_module
from weft.core.runners.agent_session_protocol import (
    make_booted_response,
    make_execute_request,
    make_ready_response,
    make_result_response,
    make_startup_error_response,
    make_stop_request,
    parse_request_type,
)
from weft.core.runners.host import HostTaskRunner
from weft.core.runners.outcome import make_outcome_payload, parse_outcome_payload
from weft.core.runners.sessions import AgentSession
from weft.core.runners.subprocess_runner import (
    open_supervised_process,
    run_monitored_subprocess,
)
from weft.core.targets import PreparedTaskResult, project_task_result
from weft.core.tasks.runner import TaskRunner
from weft.core.taskspec import FrozenList, LimitsSection
from weft.core.terminal_handoff import (
    TerminalHandoffEvent,
    TerminalHandoffEventKind,
    TerminalHandoffProgress,
)
from weft.core.terminal_handoff_transport import (
    TerminalHandoffTransportError,
    poll_terminal_payload,
    receive_terminal_payload,
    send_terminal_payload,
)
from weft.ext import RunnerCapabilities, RunnerHandle


def _process_gone_or_zombie(process: psutil.Process) -> bool:
    try:
        return not process.is_running() or process.status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


def test_runner_outcome_rejects_duplicate_worker_pid_identity() -> None:
    """Completed outcomes have one runtime identity authority [CC-3.2]."""
    with pytest.raises(TypeError, match="worker_pid"):
        # Deliberately invalid legacy argument exercises the runtime contract.
        RunnerOutcome(  # type: ignore[call-arg]
            status="ok",
            value="done",
            error=None,
            stdout=None,
            stderr=None,
            returncode=0,
            duration=0.0,
            worker_pid=123,
        )


def test_runner_handle_round_trips_new_shape() -> None:
    handle = RunnerHandle(
        runner="host",
        kind="process",
        id="123",
        control={"authority": "host-pid"},
        observations={"host_pids": [123, 123, -1, "bad"]},
        metadata={"label": "demo"},
    )

    payload = handle.to_dict()

    assert payload == {
        "runner": "host",
        "kind": "process",
        "id": "123",
        "control": {"authority": "host-pid"},
        "observations": {"host_pids": [123]},
        "metadata": {"label": "demo"},
    }
    assert RunnerHandle.from_dict(payload) == handle


def test_runner_handle_exposes_host_process_identities() -> None:
    handle = RunnerHandle(
        runner="host",
        kind="process",
        id="123",
        control={"authority": "host-pid"},
        observations={
            "host_pids": [999],
            "host_processes": [
                {"pid": 123, "create_time": 456.5},
                {"pid": 123, "create_time": 999.0},
                {"pid": -1, "create_time": 1.0},
            ],
        },
    )

    assert handle.scoped_host_pids() == (999,)
    assert handle.scoped_host_processes() == ((123, 456.5),)


@pytest.mark.parametrize("kill", [False, True])
def test_host_runner_plugin_skips_pid_identity_mismatch(
    kill: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    process = psutil.Process()
    handle = RunnerHandle(
        runner="host",
        kind="process",
        id=str(process.pid),
        control={"authority": "host-pid"},
        observations={
            "host_processes": [
                {"pid": process.pid, "create_time": process.create_time() - 100}
            ]
        },
    )
    plugin = host_module.HostRunnerPlugin()
    with caplog.at_level(logging.WARNING):
        attempted = plugin.kill(handle) if kill else plugin.stop(handle)
    assert attempted is False
    assert str(process.pid) in caplog.text


@pytest.mark.parametrize("kill", [False, True])
@pytest.mark.parametrize("identity", [None, float("nan"), float("inf"), -1.0])
def test_verified_tree_refuses_missing_or_mismatched_identity(
    kill: bool,
    identity: float | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        assert not helpers_module.terminate_verified_process_tree(
            os.getpid(), identity, kill=kill, timeout=0.1
        )
    assert str(os.getpid()) in caplog.text


@pytest.mark.parametrize("kill", [False, True])
def test_verified_tree_terminates_matching_real_descendants(kill: bool) -> None:
    script = (
        "import subprocess,sys; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        "print(child.pid,flush=True); child.wait()"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script], stdout=subprocess.PIPE, text=True
    )
    assert process.stdout is not None
    child = psutil.Process(int(process.stdout.readline()))
    try:
        identity = psutil.Process(process.pid).create_time()
        assert helpers_module.terminate_verified_process_tree(
            process.pid, identity, kill=kill, timeout=0.5
        )
        assert process.poll() is not None
        assert _process_gone_or_zombie(child)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdout.close()
        if child.is_running():
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass


@pytest.mark.parametrize("kill", [False, True])
def test_verified_tree_signals_the_verified_instance(
    kill: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Mock()
    root.pid = 123
    root.create_time.return_value = 10.0
    root.status.return_value = psutil.STATUS_RUNNING
    # The verified root has no descendants; enumeration is not under test.
    monkeypatch.setattr(helpers_module, "descendant_processes", lambda _root: [])
    acquired = Mock(return_value=root)
    monkeypatch.setattr(helpers_module.psutil, "Process", acquired)
    forbidden_wait = Mock(side_effect=AssertionError("must not steal owned child reap"))
    monkeypatch.setattr(helpers_module.psutil, "wait_procs", forbidden_wait)
    assert helpers_module.terminate_verified_process_tree(
        123, 10.0, kill=kill, timeout=0.1
    )
    acquired.assert_called_once_with(123)
    root.create_time.assert_called_once_with()
    root.kill.assert_called_once_with()
    assert root.terminate.call_count == (0 if kill else 1)


@pytest.mark.parametrize("kill", [False, True])
def test_verified_tree_preserves_owned_multiprocessing_reap(kill: bool) -> None:
    process = multiprocessing.get_context("spawn").Process(
        target=time.sleep, args=(60,)
    )
    process.start()
    assert process.pid is not None
    try:
        identity = psutil.Process(process.pid).create_time()
        assert helpers_module.terminate_verified_process_tree(
            process.pid, identity, kill=kill, timeout=0.2
        )
        process.join(timeout=5)
        assert process.exitcode is not None
        assert not process.is_alive()
    finally:
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
        process.close()


@pytest.mark.parametrize("kill", [False, True])
def test_verified_tree_rejects_zombie_root(
    kill: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Mock()
    root.create_time.return_value = 10.0
    root.status.return_value = psutil.STATUS_ZOMBIE
    monkeypatch.setattr(helpers_module.psutil, "Process", lambda pid: root)
    assert not helpers_module.terminate_verified_process_tree(
        123, 10.0, kill=kill, timeout=0.1
    )
    root.children.assert_not_called()
    root.terminate.assert_not_called()
    root.kill.assert_not_called()


@pytest.mark.parametrize("caller_handling_exception", [False, True])
@pytest.mark.parametrize("outcome_status", ["ok", "error"])
def test_host_return_rejects_worker_surviving_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    caller_handling_exception: bool,
    outcome_status: str,
) -> None:
    runner = _build_function_host_runner(timeout=5.0)
    context = Mock()
    context.Pipe.return_value = (Mock(), Mock())
    context.Process.return_value.pid = 123
    context.Process.return_value.is_alive.return_value = True
    monkeypatch.setattr(runner, "_ctx", context)
    monkeypatch.setattr(host_module, "_host_handle", lambda pid: None)
    monkeypatch.setattr(runner, "_stop_process", lambda process: None)
    monkeypatch.setattr(
        runner,
        "_run_one_shot_terminal_handoff",
        lambda *args, **kwargs: RunnerOutcome(
            status=outcome_status,
            value="value",
            error=None,
            stdout=None,
            stderr=None,
            returncode=0,
            duration=0.0,
        ),
    )
    if caller_handling_exception:
        try:
            raise ValueError("caller is handling an unrelated exception")
        except ValueError:
            with pytest.raises(RuntimeError, match="reap"):
                runner.run_with_hooks(None)
    else:
        with pytest.raises(RuntimeError, match="reap"):
            runner.run_with_hooks(None)


def test_host_return_follows_real_worker_reap() -> None:
    runner = _build_function_host_runner(timeout=5.0)
    workers: list[int | None] = []
    outcome = runner.run_with_hooks("payload", on_worker_started=workers.append)
    assert outcome.status == "ok"
    assert len(workers) == 1
    assert workers[0] is not None
    assert not psutil.pid_exists(workers[0])


def test_runner_handle_rejects_legacy_shape() -> None:
    with pytest.raises(ValueError, match="legacy keys"):
        RunnerHandle.from_dict(
            {
                "runner_name": "host",
                "runtime_id": "123",
                "host_pids": [123],
                "metadata": {},
            }
        )


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("control", "runner handle control must be a mapping"),
        ("observations", "runner handle observations must be a mapping"),
    ],
)
def test_runner_handle_constructor_rejects_non_mapping_fields_as_type_error(
    field: str,
    message: str,
) -> None:
    kwargs: dict[str, Any] = {
        "runner": "host",
        "kind": "process",
        "id": "123",
        "control": {"authority": "host-pid"},
        "observations": {},
    }
    kwargs[field] = ["not-a-mapping"]

    with pytest.raises(TypeError) as exc_info:
        RunnerHandle(**kwargs)
    assert type(exc_info.value) is TypeError
    assert str(exc_info.value) == message
    assert exc_info.value.__cause__ is None


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {
                "runner": 1,
                "kind": "process",
                "id": "123",
                "control": {"authority": "host-pid"},
            },
            "runner handle requires string runner, kind, and id",
        ),
        (
            {
                "runner": "host",
                "kind": "process",
                "id": "123",
                "control": ["not-a-mapping"],
            },
            "runner handle control must be a mapping",
        ),
        (
            {
                "runner": "host",
                "kind": "process",
                "id": "123",
                "control": {"authority": "host-pid"},
                "observations": ["not-a-mapping"],
            },
            "runner handle observations must be a mapping",
        ),
        (
            {
                "runner": "host",
                "kind": "process",
                "id": "123",
                "control": {"authority": "host-pid"},
                "metadata": ["not-a-mapping"],
            },
            "runner handle metadata must be a mapping",
        ),
    ],
)
def test_runner_handle_from_dict_rejects_persisted_shape_as_value_error(
    payload: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(ValueError) as exc_info:
        RunnerHandle.from_dict(payload)
    assert type(exc_info.value) is ValueError
    assert str(exc_info.value) == message
    assert exc_info.value.__cause__ is None


@pytest.mark.timeout(30)
def test_task_runner_executes_function_successfully() -> None:
    runner = TaskRunner(
        target_type="function",
        tid=None,
        function_target="tests.tasks.sample_targets:echo_payload",
        process_target=None,
        agent=None,
        args=[],
        kwargs={"suffix": "!"},
        env={},
        working_dir=None,
        timeout=None,
        limits=None,
        monitor_class=None,
        monitor_interval=0.1,
    )

    outcome = runner.run({"payload": "hello"})

    assert outcome.ok
    assert isinstance(outcome.value, PreparedTaskResult)
    assert project_task_result(outcome.value) == "hello!"
    assert outcome.error is None


def test_task_runner_reports_abrupt_worker_exit_diagnostics() -> None:
    runner = TaskRunner(
        target_type="function",
        tid=None,
        function_target="tests.tasks.sample_targets:abrupt_exit",
        process_target=None,
        agent=None,
        args=[73],
        kwargs=None,
        env={},
        working_dir=None,
        timeout=5.0,
        limits=None,
        monitor_class=None,
        monitor_interval=0.1,
    )

    outcome = runner.run(None)

    assert outcome.status == "error"
    assert outcome.returncode == 73
    assert outcome.error == "Worker exited before returning a result (exit code 73)"
    assert outcome.diagnostics is not None
    assert outcome.diagnostics["phase"] == "result_handoff"
    assert outcome.diagnostics["exitcode"] == 73


def test_task_runner_reports_command_spawn_diagnostics() -> None:
    runner = TaskRunner(
        target_type="command",
        tid=None,
        function_target=None,
        process_target="weft-definitely-missing-command",
        agent=None,
        args=None,
        kwargs=None,
        env={},
        working_dir=None,
        timeout=5.0,
        limits=None,
        monitor_class=None,
        monitor_interval=0.1,
    )

    outcome = runner.run(None)

    assert outcome.status == "error"
    assert outcome.diagnostics is not None
    assert outcome.diagnostics["phase"] == "process_spawn"
    assert outcome.diagnostics["target_type"] == "command"
    assert outcome.diagnostics["exception_type"] == "FileNotFoundError"


def test_agent_session_close_releases_multiprocessing_handles() -> None:  # noqa: C901 approved [TS-3.1] [RUFF-SUP-221] exception
    class FakeProcess:
        def __init__(self) -> None:
            self.closed = False
            self.join_calls: list[float | None] = []

        @property
        def pid(self) -> int:
            return 123

        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            self.join_calls.append(timeout)

        def close(self) -> None:
            self.closed = True

    class FakeQueue:
        def __init__(self) -> None:
            self.cancelled = False
            self.closed = False
            self.joined = False

        def cancel_join_thread(self) -> None:
            self.cancelled = True

        def close(self) -> None:
            self.closed = True

        def join_thread(self) -> None:
            self.joined = True

        def put(self, _payload: object) -> None:
            raise AssertionError("closed sessions must not enqueue stop requests")

    request_queue = FakeQueue()
    response_receiver = FakeQueue()
    process = FakeProcess()
    session = AgentSession(
        process,  # type: ignore[arg-type]
        request_queue,  # type: ignore[arg-type]
        response_receiver,  # type: ignore[arg-type]
        monitor=None,
        timeout=None,
    )

    session.close()

    assert request_queue.closed is True
    assert request_queue.cancelled is True
    assert request_queue.joined is False
    assert response_receiver.closed is True
    assert response_receiver.joined is False
    assert process.closed is True


@pytest.mark.parametrize("error_type", [OSError, ValueError])
def test_agent_session_ipc_cleanup_reports_supported_process_close_failure(
    caplog: pytest.LogCaptureFixture,
    error_type: type[Exception],
) -> None:
    calls: list[str] = []

    class Process:
        def close(self) -> None:
            calls.append("process.close")
            raise error_type("sensitive agent process close failure")

    class RequestQueue:
        def cancel_join_thread(self) -> None:
            calls.append("request.cancel_join_thread")

        def close(self) -> None:
            calls.append("request.close")

    class ResponseReceiver:
        def close(self) -> None:
            calls.append("response.close")

    session = AgentSession(
        Process(),  # type: ignore[arg-type]
        RequestQueue(),  # type: ignore[arg-type]
        ResponseReceiver(),  # type: ignore[arg-type]
        None,
        timeout=None,
    )
    caplog.set_level(logging.WARNING, logger="weft.core.runners.sessions")

    session._close_ipc_resources()

    assert len(calls) == 4
    assert set(calls) == {
        "request.cancel_join_thread",
        "request.close",
        "response.close",
        "process.close",
    }
    assert [record.getMessage() for record in caplog.records] == [
        "Failed to close agent session process handle"
    ]
    assert all(record.exc_info is None for record in caplog.records)
    assert "sensitive" not in caplog.text


def test_agent_session_ipc_cleanup_propagates_unexpected_process_close_failure() -> (
    None
):
    class Process:
        def close(self) -> None:
            raise RuntimeError("unexpected agent process close defect")

    class Endpoint:
        def cancel_join_thread(self) -> None:
            return None

        def close(self) -> None:
            return None

    session = AgentSession(
        Process(),  # type: ignore[arg-type]
        Endpoint(),  # type: ignore[arg-type]
        Endpoint(),  # type: ignore[arg-type]
        None,
        timeout=None,
    )

    with pytest.raises(RuntimeError, match="unexpected agent process close defect"):
        session._close_ipc_resources()


def test_agent_session_reports_monitor_cleanup_failures_and_clears_owner(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[str] = []

    class Monitor:
        def last_metrics(self) -> None:
            calls.append("last_metrics")
            raise RuntimeError("sensitive agent metrics failure")

        def stop(self) -> None:
            calls.append("stop")
            raise RuntimeError("sensitive agent monitor stop failure")

    session = AgentSession(
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        Monitor(),  # type: ignore[arg-type]
        timeout=None,
    )
    caplog.set_level(logging.WARNING, logger="weft.core.runners.sessions")

    session.stop_monitor()
    session.stop_monitor()

    assert calls == ["last_metrics", "stop"]
    assert session._monitor is None
    assert [record.getMessage() for record in caplog.records] == [
        "Failed to collect final agent session metrics",
        "Failed to stop agent session resource monitor",
    ]
    assert all(record.exc_info is None for record in caplog.records)
    assert "sensitive" not in caplog.text


def test_agent_session_metrics_failure_returns_cache_and_later_recovers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cached = ResourceMetrics(memory_mb=1.0)
    refreshed = ResourceMetrics(memory_mb=2.0)

    class Monitor:
        def __init__(self) -> None:
            self.calls = 0

        def last_metrics(self) -> ResourceMetrics:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("sensitive agent metrics failure")
            return refreshed

    monitor = Monitor()
    session = AgentSession(
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        monitor,  # type: ignore[arg-type]
        timeout=None,
    )
    session._last_metrics = cached
    caplog.set_level(logging.WARNING, logger="weft.core.runners.sessions")

    assert session.last_metrics is cached
    assert session._monitor is monitor
    assert session.last_metrics is refreshed
    assert monitor.calls == 2
    assert [record.getMessage() for record in caplog.records] == [
        "Failed to collect agent session metrics"
    ]
    assert all(record.exc_info is None for record in caplog.records)
    assert "sensitive" not in caplog.text


@pytest.mark.parametrize(
    "session_factory",
    [
        lambda monitor: sessions_module.CommandSession(
            cast(
                subprocess.Popen[bytes], object()
            ),  # Process is unused by this metrics-only probe.
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            monitor,
        ),
        lambda monitor: AgentSession(
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            monitor,
            timeout=None,
        ),
    ],
)
def test_session_limit_verdict_survives_optional_metrics_read_failure(
    session_factory: Any,
) -> None:
    class Monitor:
        def check_limits(self) -> tuple[bool, str]:
            return False, "memory limit"

        def last_metrics(self) -> ResourceMetrics:
            raise RuntimeError("metrics unavailable")

        def stop(self) -> None:
            return None

    monitor = Monitor()
    session = session_factory(monitor)

    assert session.poll_limits() == (False, "memory limit")


@pytest.mark.parametrize(
    "session_factory",
    [
        lambda monitor: sessions_module.CommandSession(
            cast(
                subprocess.Popen[bytes], object()
            ),  # Process is unused by this metrics-only probe.
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            monitor,
        ),
        lambda monitor: AgentSession(
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            monitor,
            timeout=None,
        ),
    ],
)
def test_session_monitor_adapter_failure_fails_open_and_releases_monitor(
    session_factory: Any,
) -> None:
    class AdapterFailure(Exception):
        pass

    cached = ResourceMetrics(memory_mb=3.0)

    class Monitor:
        stop_calls = 0

        def check_limits(self) -> tuple[bool, str | None]:
            raise AdapterFailure("configured monitor failed")

        def last_metrics(self) -> ResourceMetrics:
            return cached

        def stop(self) -> None:
            self.stop_calls += 1

    monitor = Monitor()
    session = session_factory(monitor)

    assert session.poll_limits() == (True, None)
    assert session.last_metrics is cached
    assert session._monitor is None
    assert monitor.stop_calls == 1


@pytest.mark.parametrize(
    "session_factory",
    [
        lambda monitor: sessions_module.CommandSession(
            cast(
                subprocess.Popen[bytes], object()
            ),  # Process is unused by this metrics-only probe.
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            monitor,
        ),
        lambda monitor: AgentSession(
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            monitor,
            timeout=None,
        ),
    ],
)
def test_session_monitor_poll_propagates_non_exception_failure_identity(
    session_factory: Any,
) -> None:
    class FatalSignal(BaseException):
        pass

    fatal = FatalSignal("stop polling")

    class Monitor:
        def check_limits(self) -> tuple[bool, str | None]:
            raise fatal

    monitor = Monitor()
    session = session_factory(monitor)

    with pytest.raises(FatalSignal) as caught:
        session.poll_limits()

    assert caught.value is fatal
    assert session._monitor is monitor


def test_agent_session_startup_error_survives_immediate_child_exit() -> None:
    session = _spawn_agent_session_for_target(_agent_session_startup_error_worker)
    try:
        with pytest.raises(RuntimeError) as exc_info:
            session.wait_ready(timeout=5.0)
    finally:
        session.close()

    message = str(exc_info.value)
    assert "startup boom" in message
    assert "last_handshake=startup_error" in message
    assert "phase=runtime_startup" in message


def test_agent_session_reports_child_exit_before_handshake() -> None:
    session = _spawn_agent_session_for_target(
        _agent_session_exit_without_handshake_worker
    )
    try:
        with pytest.raises(RuntimeError) as exc_info:
            session.wait_ready(timeout=5.0)
    finally:
        session.close()

    message = str(exc_info.value)
    assert "Agent session failed to signal readiness" in message
    assert "exitcode=73" in message
    assert "alive=False" in message


def test_agent_session_reports_hang_after_boot_handshake() -> None:
    session = _spawn_agent_session_for_target(_agent_session_boot_then_hang_worker)
    try:
        with pytest.raises(RuntimeError) as exc_info:
            session.wait_ready(timeout=5.0)
    finally:
        session.close()

    message = str(exc_info.value)
    assert "Agent session failed to signal readiness" in message
    assert "alive=True" in message
    assert "last_handshake=booted" in message


def test_agent_startup_late_drain_survives_process_join_os_error(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    join_attempts = 0
    drain_timeouts: list[float] = []

    class Process:
        def join(self, timeout: float | None = None) -> None:
            nonlocal join_attempts
            assert timeout == 0.2
            join_attempts += 1
            raise OSError("sensitive startup process wait failure")

    session = AgentSession(
        Process(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        None,
        timeout=None,
    )

    def drain(*, timeout: float) -> dict[str, bool]:
        drain_timeouts.append(timeout)
        return {"late": True}

    monkeypatch.setattr(session, "_drain_ready_response", drain)
    caplog.set_level(logging.WARNING, logger="weft.core.runners.sessions")

    assert session._join_and_drain_ready_response() == {"late": True}
    assert join_attempts == 1
    assert drain_timeouts == [0.0]
    assert [record.getMessage() for record in caplog.records] == [
        "Failed to join agent startup process before late drain"
    ]
    assert all(record.exc_info is None for record in caplog.records)
    assert "sensitive" not in caplog.text


def test_agent_startup_late_drain_propagates_unexpected_join_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Process:
        def join(self, timeout: float | None = None) -> None:
            del timeout
            raise RuntimeError("unexpected startup process wait defect")

    session = AgentSession(
        Process(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        None,
        timeout=None,
    )
    monkeypatch.setattr(
        session,
        "_drain_ready_response",
        lambda *, timeout: pytest.fail(f"unexpected late drain: {timeout}"),
    )

    with pytest.raises(RuntimeError, match="unexpected startup process wait defect"):
        session._join_and_drain_ready_response()


def test_agent_wait_ready_survives_join_os_error_after_channel_seal(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    join_attempts = 0
    drain_timeouts: list[float] = []

    class Process:
        def join(self, timeout: float | None = None) -> None:
            nonlocal join_attempts
            assert timeout is not None
            join_attempts += 1
            raise OSError("sensitive sealed-channel wait failure")

    session = AgentSession(
        Process(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        None,
        timeout=None,
    )
    monkeypatch.setattr(
        session,
        "_read_response_payload",
        lambda *, timeout: (_ for _ in ()).throw(EOFError()),
    )

    def drain(*, timeout: float) -> dict[str, Any]:
        drain_timeouts.append(timeout)
        return make_ready_response()

    monkeypatch.setattr(session, "_drain_ready_response", drain)
    caplog.set_level(logging.WARNING, logger="weft.core.runners.sessions")

    session.wait_ready(timeout=0.1)

    assert join_attempts == 1
    assert drain_timeouts == [0.2]
    assert [record.getMessage() for record in caplog.records] == [
        "Failed to join agent startup process after channel seal"
    ]
    assert all(record.exc_info is None for record in caplog.records)
    assert "sensitive" not in caplog.text


def test_agent_wait_ready_propagates_unexpected_sealed_channel_join_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Process:
        def join(self, timeout: float | None = None) -> None:
            del timeout
            raise RuntimeError("unexpected sealed-channel wait defect")

    session = AgentSession(
        Process(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        None,
        timeout=None,
    )
    monkeypatch.setattr(
        session,
        "_read_response_payload",
        lambda *, timeout: (_ for _ in ()).throw(EOFError()),
    )
    monkeypatch.setattr(
        session,
        "_drain_ready_response",
        lambda *, timeout: pytest.fail(f"unexpected late drain: {timeout}"),
    )

    with pytest.raises(RuntimeError, match="unexpected sealed-channel wait defect"):
        session.wait_ready(timeout=0.1)


PROCESS_SCRIPT = str(Path(__file__).resolve().parent / "process_target.py")


def _agent_session_startup_error_worker(
    _request_queue: Any,
    response_sender: Connection,
) -> None:
    diagnostics = runner_diagnostics(
        phase="runtime_startup",
        runner="host",
        target_type="agent",
        message="startup boom",
        last_handshake="booted",
    )
    try:
        send_terminal_payload(response_sender, make_booted_response)
        send_terminal_payload(
            response_sender,
            lambda: make_startup_error_response(
                "startup boom",
                diagnostics=diagnostics,
            ),
        )
    finally:
        response_sender.close()


def _agent_session_exit_without_handshake_worker(
    _request_queue: Any,
    _response_sender: Connection,
) -> None:
    import os

    os._exit(73)


def _agent_session_boot_then_hang_worker(
    _request_queue: Any,
    response_sender: Connection,
) -> None:
    try:
        send_terminal_payload(response_sender, make_booted_response)
        time.sleep(30.0)
    finally:
        response_sender.close()


def _spawn_agent_session_for_target(
    target: Any,
    *,
    timeout: float | None = None,
    monitor: Any | None = None,
    limits: LimitsSection | None = None,
) -> AgentSession:
    ctx = multiprocessing.get_context("spawn")
    ctx.set_executable(sys.executable)
    request_queue = ctx.Queue()
    response_receiver, response_sender = ctx.Pipe(duplex=False)
    process = ctx.Process(target=target, args=(request_queue, response_sender))
    process.start()
    response_sender.close()
    return AgentSession(
        process,
        request_queue,
        response_receiver,
        monitor=monitor,
        timeout=timeout,
        limits=limits,
    )


def _agent_session_hold_then_reply_worker(
    hold_seconds: float,
    request_queue: Any,
    response_sender: Connection,
) -> None:
    """Reply ready, then answer each work item ``hold_seconds`` after taking it."""

    try:
        send_terminal_payload(response_sender, make_booted_response)
        send_terminal_payload(response_sender, make_ready_response)
        while True:
            request = request_queue.get()
            if parse_request_type(request) != "execute":
                return
            time.sleep(hold_seconds)
            send_terminal_payload(
                response_sender,
                lambda: make_result_response(
                    status="ok",
                    result=AgentExecutionResult(
                        runtime="fixture", model=None, output_mode="text", outputs=()
                    ),
                ),
            )
    finally:
        response_sender.close()


_CONFIGURED_LIMIT = LimitsSection(memory_mb=1024)
"""A limit no test process reaches. A runner limits input that configures any
limit makes sampling enforcement on the monitor's interval ([RM-5.1]);
without one, sampling serves metrics only and follows the doubling
schedule."""


class _RecordingLimitMonitor(BaseResourceMonitor):
    """Monitor double that records a `perf_counter` timestamp per limit check.

    `perf_counter` keeps sub-millisecond resolution on every platform;
    `time.monotonic` on Windows before Python 3.13 ticks at about 15.6 ms,
    which made a sample and the wait that follows it share one timestamp.
    """

    def __init__(
        self,
        *,
        polling_interval: float,
        violation_at: int | None = None,
    ) -> None:
        super().__init__(polling_interval=polling_interval)
        self.sample_times: list[float] = []
        self._violation_at = violation_at

    def start(self, pid: int) -> None:
        self._pid = pid

    def stop(self) -> None:
        return

    def snapshot(self) -> ResourceMetrics:
        return ResourceMetrics(memory_mb=1.0)

    def check_limits(self) -> tuple[bool, str | None]:
        self.sample_times.append(time.perf_counter())
        if self._violation_at is not None and len(self.sample_times) >= (
            self._violation_at
        ):
            return False, "test sustained violation"
        return True, None

    def last_metrics(self) -> ResourceMetrics | None:
        return ResourceMetrics(memory_mb=1.0)


@pytest.mark.shared
def test_agent_session_silent_producer_waits_without_cadence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no cancel predicate and no monitor, the handoff wait has no timer.

    Spec: [CC-2.2.1], [CC-3.5]
    """

    session = _spawn_agent_session_for_target(
        functools.partial(_agent_session_hold_then_reply_worker, 1.0)
    )
    budgets: list[float | None] = []
    passes: list[None] = []
    real_wait_seconds = session._terminal_handoff_wait_seconds
    real_observe = session._observe_terminal_handoff

    def recording_wait_seconds(**kwargs: Any) -> float | None:
        budget = real_wait_seconds(**kwargs)
        budgets.append(budget)
        return budget

    def counting_observe(*args: Any, **kwargs: Any) -> Any:
        passes.append(None)
        return real_observe(*args, **kwargs)

    monkeypatch.setattr(
        session, "_terminal_handoff_wait_seconds", recording_wait_seconds
    )
    monkeypatch.setattr(session, "_observe_terminal_handoff", counting_observe)
    try:
        session.wait_ready(timeout=10.0)
        result = session.execute("hello", cancel_requested=None)

        assert budgets
        assert all(budget is None for budget in budgets)
        assert 1 <= len(passes) <= 3
        assert result.status == "ok"
    finally:
        session.close()


@pytest.mark.shared
def test_agent_session_leaked_sender_still_drains_after_producer_exit(
    tmp_path: Path,
) -> None:
    """Producer exit, seen on its sentinel, starts drain while the channel stays open.

    Spec: [CC-3.5]
    """

    pidfile = tmp_path / "grandchild.pid"
    session = _spawn_agent_session_for_target(
        functools.partial(leaking_session_producer, str(pidfile))
    )
    grandchild_pid: int | None = None
    try:
        session.wait_ready(timeout=10.0)
        grandchild_pid = wait_for_pidfile(pidfile)

        started = time.monotonic()
        result = session.execute("hello", cancel_requested=None)
        elapsed = time.monotonic() - started

        assert result.status == "error"
        assert result.diagnostics is not None
        assert result.diagnostics["handoff_event"] == "drain_expired"
        assert elapsed < 2.0
        assert psutil.Process(grandchild_pid).is_running()
    finally:
        kill_pid(grandchild_pid)
        session.close()


@pytest.mark.shared
def test_agent_session_samples_limits_at_monitor_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A silent monitored session samples on its monitor's interval deadline.

    Spec: [RM-5.1]
    """

    monitor = _RecordingLimitMonitor(polling_interval=0.05, violation_at=5)
    session = _spawn_agent_session_for_target(
        functools.partial(_agent_session_hold_then_reply_worker, 5.0),
        monitor=monitor,
        limits=_CONFIGURED_LIMIT,
    )
    wait_times: list[float] = []
    real_wait_seconds = session._terminal_handoff_wait_seconds

    def recording_wait_seconds(**kwargs: Any) -> float | None:
        wait_times.append(time.perf_counter())
        return real_wait_seconds(**kwargs)

    monkeypatch.setattr(
        session, "_terminal_handoff_wait_seconds", recording_wait_seconds
    )
    try:
        session.wait_ready(timeout=10.0)
        started = time.monotonic()
        result = session.execute("hello", cancel_requested=None)
        elapsed = time.monotonic() - started

        assert result.status == "limit"
        assert len(monitor.sample_times) == 5
        assert wait_times
        assert monitor.sample_times[0] < wait_times[0]
        assert elapsed < 2.0
    finally:
        session.close()


class _SlowSampleMonitor(_RecordingLimitMonitor):
    """Recording monitor whose limit check consumes a measurable time."""

    def __init__(self, *, polling_interval: float, sample_seconds: float) -> None:
        super().__init__(polling_interval=polling_interval)
        self.sample_ends: list[float] = []
        self._sample_seconds = sample_seconds

    def check_limits(self) -> tuple[bool, str | None]:
        verdict = super().check_limits()
        time.sleep(self._sample_seconds)
        self.sample_ends.append(time.monotonic())
        return verdict


@pytest.mark.shared
def test_agent_session_wait_after_slow_sample_ends_at_the_sampling_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A time-consuming sample cannot push the wake past the armed deadline.

    The next instant is armed one interval after the sample ends, so the wait
    requested after the sample must end no later than that instant ([RM-5.1]).

    Spec: [RM-5.1], [CC-3.5]
    """

    interval = 0.5
    monitor = _SlowSampleMonitor(polling_interval=interval, sample_seconds=0.2)
    session = _spawn_agent_session_for_target(
        functools.partial(_agent_session_hold_then_reply_worker, 0.4),
        monitor=monitor,
        limits=_CONFIGURED_LIMIT,
    )
    waits: list[tuple[float, float | None]] = []
    real_wait_seconds = session._terminal_handoff_wait_seconds

    def recording_wait_seconds(**kwargs: Any) -> float | None:
        budget = real_wait_seconds(**kwargs)
        waits.append((time.monotonic(), budget))
        return budget

    monkeypatch.setattr(
        session, "_terminal_handoff_wait_seconds", recording_wait_seconds
    )
    try:
        session.wait_ready(timeout=10.0)
        result = session.execute("hello", cancel_requested=None)

        assert result.status == "ok"
        assert monitor.sample_ends
        first_sample_end = monitor.sample_ends[0]
        called_at, budget = waits[0]
        assert called_at >= first_sample_end
        assert budget is not None
        assert budget <= interval + 0.05
        assert called_at + budget <= first_sample_end + interval + 0.05
    finally:
        session.close()


@pytest.mark.shared
def test_agent_session_short_items_cannot_starve_sampling() -> None:
    """Short work items neither restart nor outpace the session's sampling instant.

    Spec: [RM-5.1]
    """

    monitor = _RecordingLimitMonitor(polling_interval=0.2)
    session = _spawn_agent_session_for_target(
        functools.partial(_agent_session_hold_then_reply_worker, 0.02),
        monitor=monitor,
        limits=_CONFIGURED_LIMIT,
    )
    statuses: list[str] = []
    try:
        session.wait_ready(timeout=10.0)
        for index in range(20):
            result = session.execute(f"item-{index}", cancel_requested=None)
            statuses.append(result.status)

        gaps = [
            later - earlier
            for earlier, later in zip(
                monitor.sample_times, monitor.sample_times[1:], strict=False
            )
        ]
        assert len(monitor.sample_times) >= 2
        assert all(gap >= 0.18 for gap in gaps), gaps
        assert statuses == ["ok"] * 20
    finally:
        session.close()


@pytest.mark.shared
def test_agent_session_without_limits_samples_every_work_item() -> None:
    """Without a limit each work item samples at its own start.

    Nothing is enforced, so a session-wide clock would only leave short items
    reporting an earlier item's metrics.

    Spec: [RM-5.1]
    """

    monitor = _RecordingLimitMonitor(polling_interval=0.2)
    session = _spawn_agent_session_for_target(
        functools.partial(_agent_session_hold_then_reply_worker, 0.02),
        monitor=monitor,
    )
    try:
        session.wait_ready(timeout=10.0)
        statuses = [
            session.execute(f"item-{index}", cancel_requested=None).status
            for index in range(5)
        ]

        assert statuses == ["ok"] * 5
        assert len(monitor.sample_times) >= 5
    finally:
        session.close()


class _PostReadyFailingRequestQueue:
    """Picklable request seam that fails inside the production worker loop."""

    def put(self, _request: Any) -> None:
        pass

    def get(self) -> dict[str, Any]:
        raise RuntimeError("session request transport failed")

    def cancel_join_thread(self) -> None:
        pass

    def close(self) -> None:
        pass


def _agent_session_ready_then_hang_worker(
    request_queue: Any,
    response_sender: Connection,
) -> None:
    try:
        send_terminal_payload(response_sender, make_booted_response)
        send_terminal_payload(response_sender, make_ready_response)
        request_queue.get()
        time.sleep(30.0)
    finally:
        response_sender.close()


def _agent_session_ready_then_exit_worker(
    request_queue: Any,
    response_sender: Connection,
) -> None:
    try:
        send_terminal_payload(response_sender, make_booted_response)
        send_terminal_payload(response_sender, make_ready_response)
        request_queue.get()
    finally:
        response_sender.close()


def _agent_session_ready_then_seal_and_hang_worker(
    request_queue: Any,
    response_sender: Connection,
) -> None:
    send_terminal_payload(response_sender, make_booted_response)
    send_terminal_payload(response_sender, make_ready_response)
    request_queue.get()
    response_sender.close()
    time.sleep(30.0)


def _agent_session_wrong_payload_worker(
    request_queue: Any,
    response_sender: Connection,
) -> None:
    try:
        send_terminal_payload(response_sender, make_booted_response)
        send_terminal_payload(response_sender, make_ready_response)
        request_queue.get()
        response_sender.send_bytes(b"42")
    finally:
        response_sender.close()


def _agent_session_malformed_nested_result_worker(
    request_queue: Any,
    response_sender: Connection,
) -> None:
    try:
        send_terminal_payload(response_sender, make_booted_response)
        send_terminal_payload(response_sender, make_ready_response)
        request_queue.get()
        payload = make_ready_response()
        payload.update(
            type="result",
            status="ok",
            error=None,
            result={"outputs": "not-a-list", "metadata": {}},
        )
        send_terminal_payload(response_sender, lambda: payload)
    finally:
        response_sender.close()


def test_production_agent_worker_post_ready_error_survives_immediate_exit() -> None:
    """The production worker's post-ready result frame survives its exit."""

    ctx = multiprocessing.get_context("spawn")
    ctx.set_executable(sys.executable)
    request_queue = _PostReadyFailingRequestQueue()
    response_receiver, response_sender = ctx.Pipe(duplex=False)
    process = ctx.Process(
        target=host_module._agent_session_worker_entry,
        args=(
            {
                "type": "agent",
                "agent": {
                    "runtime": "llm",
                    "model": TEST_MODEL_ID,
                    "conversation_scope": "per_task",
                    "runtime_config": {
                        "plugin_modules": ["tests.fixtures.llm_test_models"],
                    },
                },
            },
            request_queue,
            response_sender,
        ),
    )
    process.start()
    worker_pid = process.pid
    assert worker_pid is not None
    response_sender.close()
    session = AgentSession(
        process,
        request_queue,  # type: ignore[arg-type]
        response_receiver,
        monitor=None,
        timeout=5.0,
    )
    try:
        session.wait_ready(timeout=AGENT_SESSION_READY_TIMEOUT_SECONDS)
        result = session.execute("hello")

        assert result.status == "error"
        assert result.error is not None
        assert "Traceback (most recent call last)" in result.error
        assert "RuntimeError: session request transport failed" in result.error
        assert result.diagnostics is not None
        assert result.diagnostics["phase"] == "execute"
        assert result.diagnostics["exception_type"] == "RuntimeError"
        assert (
            "RuntimeError: session request transport failed"
            in result.diagnostics["traceback_tail"]
        )
        assert _wait_for_pid_exit(worker_pid)
        with pytest.raises(RuntimeError, match="Agent session is closed"):
            session.execute("again")
    finally:
        session.close()


@pytest.mark.parametrize("verdict", ("timeout", "cancelled", "limit"))
def test_agent_session_invalidating_verdict_closes_session(verdict: str) -> None:
    """Every accepted stop verdict cleans up before a deterministic rejection."""

    class LimitMonitor:
        polling_interval = DEFAULT_POLLING_INTERVAL

        def __init__(self) -> None:
            self.stopped = False

        def check_limits(self) -> tuple[bool, str]:
            return False, "test limit"

        def last_metrics(self) -> ResourceMetrics:
            return ResourceMetrics(memory_mb=1.0)

        def stop(self) -> None:
            self.stopped = True

    monitor = LimitMonitor() if verdict == "limit" else None
    session = _spawn_agent_session_for_target(
        _agent_session_ready_then_hang_worker,
        timeout=0.05 if verdict == "timeout" else None,
        monitor=monitor,
    )
    try:
        session.wait_ready(timeout=5.0)
        result = session.execute(
            "hello",
            cancel_requested=(lambda: True) if verdict == "cancelled" else None,
        )

        assert result.status == verdict
        with pytest.raises(RuntimeError, match="Agent session is closed"):
            session.execute("again")
        if monitor is not None:
            assert monitor.stopped is True
    finally:
        session.close()


def test_agent_session_monitor_metrics_failure_cannot_block_invalid_cleanup() -> None:
    """Best-effort metrics never outrank cancellation or endpoint cleanup."""

    class ExplodingMetricsMonitor:
        polling_interval = DEFAULT_POLLING_INTERVAL

        def __init__(self) -> None:
            self.stopped = False

        def check_limits(self) -> tuple[bool, None]:
            return True, None

        def last_metrics(self) -> None:
            raise RuntimeError("metrics unavailable after exit")

        def stop(self) -> None:
            self.stopped = True

    monitor = ExplodingMetricsMonitor()
    session = _spawn_agent_session_for_target(
        _agent_session_ready_then_hang_worker,
        monitor=monitor,
    )
    try:
        session.wait_ready(timeout=5.0)
        result = session.execute("hello", cancel_requested=lambda: True)

        assert result.status == "cancelled"
        assert monitor.stopped is True
        with pytest.raises(RuntimeError, match="Agent session is closed"):
            session.execute("again")
    finally:
        session.close()


def test_agent_session_eof_without_result_closes_session() -> None:
    """Same-turn worker exit or channel seal invalidates the session."""

    session = _spawn_agent_session_for_target(_agent_session_ready_then_exit_worker)
    try:
        session.wait_ready(timeout=5.0)
        result = session.execute("hello")

        assert result.status == "error"
        assert result.error is not None
        assert result.error in {
            "Worker exited before returning a result (exit code 0)",
            "Worker result channel failed before a result was received",
        }
        assert result.diagnostics is not None
        assert result.diagnostics["phase"] == "result_handoff"
        assert result.diagnostics["handoff_event"] == "channel_sealed"
        with pytest.raises(RuntimeError, match="Agent session is closed"):
            session.execute("again")
    finally:
        session.close()


def test_agent_session_live_eof_is_channel_failure() -> None:
    """EOF while the session process remains live cannot claim worker exit."""

    session = _spawn_agent_session_for_target(
        _agent_session_ready_then_seal_and_hang_worker
    )
    try:
        session.wait_ready(timeout=5.0)
        result = session.execute("hello")

        assert result.status == "error"
        assert (
            result.error == "Worker result channel failed before a result was received"
        )
        assert result.diagnostics is not None
        assert result.diagnostics["handoff_event"] == "channel_sealed"
        with pytest.raises(RuntimeError, match="Agent session is closed"):
            session.execute("again")
    finally:
        session.close()


def test_agent_session_wrong_payload_type_is_transport_failure() -> None:
    """A decoded non-protocol object cannot become an outcome event."""

    session = _spawn_agent_session_for_target(_agent_session_wrong_payload_worker)
    try:
        session.wait_ready(timeout=5.0)
        result = session.execute("hello")

        assert result.status == "error"
        assert (
            result.error == "Worker result channel failed before a result was received"
        )
        assert result.diagnostics is not None
        assert result.diagnostics["handoff_event"] == "transport_failed"
    finally:
        session.close()


def test_agent_session_malformed_nested_result_is_transport_failure() -> None:
    """Nested private result validation cannot escape or leave a live session."""

    session = _spawn_agent_session_for_target(
        _agent_session_malformed_nested_result_worker
    )
    try:
        session.wait_ready(timeout=5.0)
        result = session.execute("hello")

        assert result.status == "error"
        assert (
            result.error == "Worker result channel failed before a result was received"
        )
        assert result.diagnostics is not None
        assert result.diagnostics["handoff_event"] == "transport_failed"
        with pytest.raises(RuntimeError, match="Agent session is closed"):
            session.execute("again")
    finally:
        session.close()


def test_agent_session_does_not_poll_limits_after_producer_exit() -> None:  # noqa: C901 approved [TS-3.1] [RUFF-SUP-236] exception
    """A stale monitor cannot replace dead-producer/channel evidence with limit."""

    class DeadProcess:
        pid = 321
        exitcode = 0

        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            del timeout

        def close(self) -> None:
            return

    class RequestQueue:
        def put(self, _payload: object) -> None:
            return

        def cancel_join_thread(self) -> None:
            return

        def close(self) -> None:
            return

    class SealedReceiver:
        def poll(self, _timeout: float = 0.0) -> bool:
            return True

        def recv_bytes(self) -> bytes:
            raise EOFError

        def close(self) -> None:
            return

    class LateLimitMonitor:
        polling_interval = DEFAULT_POLLING_INTERVAL

        def __init__(self) -> None:
            self.check_calls = 0

        def check_limits(self) -> tuple[bool, str]:
            self.check_calls += 1
            return False, "late limit after exit"

        def last_metrics(self) -> None:
            return None

        def stop(self) -> None:
            return

    monitor = LateLimitMonitor()
    session = AgentSession(
        DeadProcess(),  # type: ignore[arg-type]
        RequestQueue(),  # type: ignore[arg-type]
        SealedReceiver(),  # type: ignore[arg-type]
        monitor,  # type: ignore[arg-type]
        timeout=None,
    )

    result = session.execute("hello")

    assert result.status == "error"
    assert result.error == "Worker exited before returning a result (exit code 0)"
    assert monitor.check_calls == 0


PERSISTENT_SESSION_EVENT_ORDER: tuple[TerminalHandoffEventKind, ...] = (
    "cancel_requested",
    "timeout_requested",
    "outcome_received",
    "limit_reached",
    "transport_failed",
    "channel_sealed",
    "producer_exited",
    "drain_expired",
)


@pytest.mark.parametrize(
    ("first_kind", "second_kind"),
    tuple(combinations(PERSISTENT_SESSION_EVENT_ORDER, 2)),
)
def test_agent_session_routes_all_event_pairs_through_persistent_policy(
    first_kind: TerminalHandoffEventKind,
    second_kind: TerminalHandoffEventKind,
) -> None:
    """The session adapter uses its declared selector for all 28 event pairs."""

    events = tuple(
        TerminalHandoffEvent(
            kind=kind,
            outcome=object() if kind == "outcome_received" else None,
        )
        for kind in (second_kind, first_kind)
    )

    step = AgentSession._reduce_terminal_observations(
        TerminalHandoffProgress(),
        events,
    )

    assert step is not None
    assert step.event.kind == first_kind


def test_run_monitored_subprocess_uses_supplied_monitor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = open_supervised_process(
        [sys.executable, "-c", "print('ok')"],
    )

    class FakeMonitor:
        def __init__(self) -> None:
            self.started_with: int | None = None
            self.stopped = False
            self.metrics = ResourceMetrics(memory_mb=12.5)

        def start(self, pid: int) -> None:
            self.started_with = pid

        def check_limits(self) -> tuple[bool, str | None]:
            return True, None

        def last_metrics(self) -> ResourceMetrics | None:
            return self.metrics

        def snapshot(self) -> ResourceMetrics:
            return self.metrics

        def stop(self) -> None:
            self.stopped = True

    monitor = FakeMonitor()
    runtime_handle = RunnerHandle(
        runner="docker",
        kind="container",
        id="container-123",
        control={"authority": "runner"},
        observations={"container_id": "container-123"},
    )
    load_calls = 0

    def _unexpected_load(*args: object, **kwargs: object) -> object:
        nonlocal load_calls
        load_calls += 1
        raise AssertionError("load_resource_monitor() should not run")

    monkeypatch.setattr(
        "weft.core.runners.subprocess_runner.load_resource_monitor",
        _unexpected_load,
    )

    outcome = run_monitored_subprocess(
        process=process,
        stdin_data=None,
        timeout=5.0,
        limits=None,
        monitor_class="weft.core.resource_monitor.ResourceMonitor",
        monitor_interval=0.05,
        monitor=monitor,
        runtime_handle=runtime_handle,
        cancel_requested=None,
        on_worker_started=None,
        on_runtime_handle_started=None,
        stop_runtime=lambda: None,
        kill_runtime=lambda: None,
    )

    assert outcome.status == "ok"
    assert outcome.value == f"ok{os.linesep}"
    assert monitor.started_with == process.pid
    assert monitor.stopped is True
    assert outcome.metrics == monitor.metrics
    assert load_calls == 0


def test_run_monitored_subprocess_emits_live_chunks_before_exit() -> None:
    process = open_supervised_process(
        [
            sys.executable,
            "-c",
            (
                "import sys, time; "
                "print('first', flush=True); "
                "print('warn', file=sys.stderr, flush=True); "
                "time.sleep(0.5); "
                "print('second', flush=True)"
            ),
        ],
        encoding="utf-8",
        errors="replace",
    )

    stdout_chunks: list[tuple[str, bool]] = []
    stderr_chunks: list[tuple[str, bool]] = []
    first_stdout_seen = threading.Event()
    outcome_holder: dict[str, RunnerOutcome] = {}

    def _on_stdout_chunk(chunk: str, final: bool) -> None:
        stdout_chunks.append((chunk, final))
        if chunk:
            first_stdout_seen.set()

    def _on_stderr_chunk(chunk: str, final: bool) -> None:
        stderr_chunks.append((chunk, final))

    def _run() -> None:
        outcome_holder["outcome"] = run_monitored_subprocess(
            process=process,
            stdin_data=None,
            timeout=5.0,
            limits=None,
            monitor_class=None,
            monitor_interval=0.05,
            monitor=None,
            runtime_handle=RunnerHandle(
                runner="host",
                kind="process",
                id="live-stream",
                control={"authority": "host-pid"},
                observations={},
            ),
            cancel_requested=None,
            on_worker_started=None,
            on_runtime_handle_started=None,
            on_stdout_chunk=_on_stdout_chunk,
            on_stderr_chunk=_on_stderr_chunk,
            stop_runtime=lambda: None,
            kill_runtime=lambda: None,
        )

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    try:
        assert first_stdout_seen.wait(timeout=1.0), "expected live stdout before exit"
        assert worker.is_alive(), "process should still be running after first chunk"
    finally:
        worker.join(timeout=5.0)

    outcome = outcome_holder["outcome"]
    assert outcome.status == "ok"
    assert outcome.value == f"first{os.linesep}second{os.linesep}"
    assert outcome.stderr == "warn\n"
    assert "".join(chunk for chunk, _final in stdout_chunks) == outcome.value
    assert stdout_chunks[-1] == ("", True)
    assert stderr_chunks[0] == ("warn\n", False)
    assert stderr_chunks[-1] == ("", True)


def test_run_monitored_subprocess_ignores_late_limit_after_process_exit(
    tmp_path: Path,
) -> None:
    # The child exits only after the monitor releases it, so the first limit
    # check is guaranteed to find it alive and the exit is guaranteed to land
    # before the violation is reported, whatever the scheduler does.
    release = tmp_path / "release"
    process = open_supervised_process(
        [
            sys.executable,
            "-c",
            (
                "import os, subprocess, sys, time; "
                "print('ok', flush=True); "
                f"release = {str(release)!r}; "
                "[time.sleep(0.01) for _ in iter(lambda: os.path.exists(release), True)]; "
                "subprocess.Popen("
                "[sys.executable, '-c', 'import time; time.sleep(0.3)'], "
                "stdout=sys.stdout, stderr=sys.stderr)"
            ),
        ],
        encoding="utf-8",
        errors="replace",
    )

    class LateViolationMonitor:
        def __init__(self) -> None:
            self.late_checks = 0
            self.stopped = False

        def start(self, pid: int) -> None:
            del pid

        def check_limits(self) -> tuple[bool, str | None]:
            # Force the race the supervisor must tolerate: the supervisor saw
            # the process alive, then the process exits before the monitor
            # reports. The grandchild keeps stdout open so the supervisor is
            # still draining when the violation arrives.
            assert process.poll() is None
            release.write_text("", encoding="utf-8")
            process.wait()
            self.late_checks += 1
            return False, "late limit after process exit"

        def last_metrics(self) -> ResourceMetrics | None:
            return ResourceMetrics(memory_mb=1.0)

        def snapshot(self) -> ResourceMetrics:
            return ResourceMetrics(memory_mb=1.0)

        def stop(self) -> None:
            self.stopped = True

    monitor = LateViolationMonitor()

    outcome = run_monitored_subprocess(
        process=process,
        stdin_data=None,
        timeout=5.0,
        limits=None,
        monitor_class=None,
        monitor_interval=0.05,
        monitor=monitor,
        runtime_handle=RunnerHandle(
            runner="host",
            kind="process",
            id="late-limit",
            control={"authority": "host-pid"},
            observations={},
        ),
        cancel_requested=None,
        on_worker_started=None,
        on_runtime_handle_started=None,
        stop_runtime=lambda: None,
        kill_runtime=lambda: None,
    )

    assert outcome.status == "ok"
    assert outcome.value == f"ok{os.linesep}"
    assert outcome.returncode == 0
    assert monitor.late_checks == 1
    assert monitor.stopped is True


def _write_descendant_scripts(tmp_path: Path) -> tuple[Path, Path]:
    child_script = tmp_path / "child_sleep.py"
    child_script.write_text(
        """
from __future__ import annotations

import os
import sys
import time
from pathlib import Path


def main() -> None:
    Path(sys.argv[1]).write_text(str(os.getpid()), encoding="utf-8")
    time.sleep(30)


if __name__ == "__main__":
    main()
""".strip()
        + "\n",
        encoding="utf-8",
    )

    parent_script = tmp_path / "spawn_child.py"
    parent_script.write_text(
        """
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path


def main() -> None:
    child = subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]])
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if Path(sys.argv[2]).exists():
            break
        time.sleep(0.01)
    time.sleep(30)


if __name__ == "__main__":
    main()
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return parent_script, child_script


def _wait_for_pidfile(pidfile: Path, *, timeout: float = 2.0) -> int:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pidfile.exists():
            raw = pidfile.read_text(encoding="utf-8").strip()
            if raw:
                try:
                    return int(raw)
                except ValueError:
                    pass
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for pid file {pidfile}")


def _wait_for_pid_exit(pid: int, *, timeout: float = 5.0) -> bool:
    psutil = pytest.importorskip("psutil")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            process = psutil.Process(pid)
            if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.Error:
            return True
        time.sleep(0.05)
    return False


def test_wait_for_pid_exit_accepts_disappearance_during_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    psutil = pytest.importorskip("psutil")

    class VanishedProcess:
        def is_running(self) -> bool:
            return True

        def status(self) -> str:
            raise psutil.NoSuchProcess(123)

    monkeypatch.setattr(psutil, "Process", lambda _pid: VanishedProcess())

    assert _wait_for_pid_exit(123, timeout=0.1)


def test_task_runner_executes_command_successfully(tmp_path: Path) -> None:
    runner = TaskRunner(
        target_type="command",
        tid=None,
        function_target=None,
        process_target=sys.executable,
        agent=None,
        args=[PROCESS_SCRIPT, "--result", "ok"],
        kwargs=None,
        env={},
        working_dir=str(tmp_path),
        timeout=5.0,
        limits=None,
        monitor_class=None,
        monitor_interval=0.1,
    )

    outcome = runner.run({})

    assert outcome.ok
    assert isinstance(outcome.value, str)
    assert outcome.value.strip() == "ok"
    assert outcome.returncode == 0


def test_task_runner_collects_immediate_command_stdout_and_stderr_tail(
    tmp_path: Path,
) -> None:
    """Command completion follows both stream EOF markers after immediate exit."""

    runner = TaskRunner(
        target_type="command",
        tid=None,
        function_target=None,
        process_target=sys.executable,
        agent=None,
        args=[
            "-c",
            (
                "import sys; "
                "sys.stdout.write('final-out'); sys.stdout.flush(); "
                "sys.stderr.write('final-err'); sys.stderr.flush()"
            ),
        ],
        kwargs=None,
        env={},
        working_dir=str(tmp_path),
        timeout=5.0,
        limits=None,
        monitor_class=None,
        monitor_interval=0.05,
    )

    outcome = runner.run({})

    assert outcome.status == "ok"
    assert outcome.stdout == "final-out"
    assert outcome.stderr == "final-err"
    assert outcome.value == "final-out"


def test_interactive_session_collects_immediate_exit_stream_tail(
    tmp_path: Path,
) -> None:
    """Interactive readers publish both tails and EOF without target sleeps."""

    runner = TaskRunner(
        target_type="command",
        tid=None,
        function_target=None,
        process_target=sys.executable,
        agent=None,
        args=[
            "-c",
            (
                "import sys; "
                "sys.stdout.buffer.write(b'interactive-out\\r\\nnext\\rtail'); sys.stdout.flush(); "
                "sys.stderr.write('interactive-err'); sys.stderr.flush()"
            ),
        ],
        kwargs=None,
        env={},
        working_dir=str(tmp_path),
        timeout=5.0,
        limits=None,
        monitor_class=None,
        monitor_interval=0.05,
    )
    session = runner.start_session(on_activity=lambda: None)
    assert isinstance(session, sessions_module.CommandSession)
    stdout: list[str] = []
    stderr: list[str] = []
    deadline = time.monotonic() + 5.0
    try:
        while time.monotonic() < deadline:
            stdout.extend(session.poll_stdout())
            stderr.extend(session.poll_stderr())
            if (
                not session.is_alive()
                and session._stdout_closed
                and session._stderr_closed
            ):
                break
            threading.Event().wait(0.01)
    finally:
        session.stop_monitor()

    assert "".join(stdout) == "interactive-out\r\nnext\rtail"
    assert "".join(stderr) == "interactive-err"
    assert session._stdout_closed is True
    assert session._stderr_closed is True


def _start_interactive_activity_session(
    tmp_path: Path,
    source: str,
) -> tuple[sessions_module.CommandSession, threading.Event]:
    activity = threading.Event()
    runner = TaskRunner(
        target_type="command",
        tid=None,
        function_target=None,
        process_target=sys.executable,
        agent=None,
        args=["-u", "-c", source],
        kwargs=None,
        env={},
        working_dir=str(tmp_path),
        timeout=5.0,
        limits=None,
        monitor_class=None,
        monitor_interval=0.05,
    )
    session = runner.start_session(on_activity=activity.set)
    assert isinstance(session, sessions_module.CommandSession)
    return session, activity


@pytest.mark.parametrize("pipe_newline", [None, "\r\n"])
def test_interactive_session_preserves_supplied_stdin_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pipe_newline: str | None
) -> None:
    """Real text pipes preserve input even with Windows newline translation defaults."""
    original_popen = subprocess.Popen

    def open_process(*args: Any, **kwargs: Any) -> subprocess.Popen[Any]:
        process = original_popen(*args, **kwargs)
        # Configure the real dependency's newline policy, not a fake process.
        assert process.stdin is not None
        cast(TextIOWrapper, process.stdin).reconfigure(newline=pipe_newline)
        return process

    monkeypatch.setattr(host_module.subprocess, "Popen", open_process)
    session, activity = _start_interactive_activity_session(
        tmp_path,
        "import sys; data=sys.stdin.buffer.read(); sys.stdout.buffer.write(data)",
    )
    text = "CRLF\r\nLF\nCR\rΩ"
    chunks: list[str] = []
    deadline = time.monotonic() + 5.0
    try:
        session.send(text)
        session.close_stdin()
        while True:
            activity.clear()
            chunks.extend(session.poll_stdout())
            if session._stdout_closed:
                break
            assert activity.wait(max(0.0, deadline - time.monotonic())), (
                "interactive echo did not finish its stdout"
            )
        assert "".join(chunks) == text
    finally:
        session.terminate()


def test_interactive_stdout_publication_notifies_activity(tmp_path: Path) -> None:
    session, activity = _start_interactive_activity_session(
        tmp_path,
        "import sys, time; sys.stdout.write('ready\\n'); sys.stdout.flush(); time.sleep(30)",
    )
    try:
        assert activity.wait(5.0)
        assert "".join(session.poll_stdout()) == f"ready{os.linesep}"
        assert session.is_alive()
    finally:
        session.terminate()


def test_interactive_stderr_publication_notifies_activity(tmp_path: Path) -> None:
    session, activity = _start_interactive_activity_session(
        tmp_path,
        "import sys, time; sys.stderr.write('ready\\n'); sys.stderr.flush(); time.sleep(30)",
    )
    try:
        assert activity.wait(5.0)
        assert "".join(session.poll_stderr()) == "ready\n"
        assert session.is_alive()
    finally:
        session.terminate()


def test_interactive_process_exit_notifies_before_inherited_pipes_close(
    tmp_path: Path,
) -> None:
    """Process exit wakes the reactor even while a descendant holds both pipes."""

    pytest.importorskip("psutil")
    pidfile = tmp_path / "inherited-pipe-child.pid"
    child_source = "import time; time.sleep(30)"
    parent_source = (
        "import subprocess, sys; from pathlib import Path; "
        f"child = subprocess.Popen([sys.executable, '-c', {child_source!r}]); "
        f"Path({str(pidfile)!r}).write_text(str(child.pid), encoding='utf-8')"
    )
    session, activity = _start_interactive_activity_session(tmp_path, parent_source)
    child_pid = _wait_for_pidfile(pidfile, timeout=5.0)
    try:
        assert activity.wait(5.0)
        assert session.is_alive() is False
        assert session.poll_stdout() == []
        assert session.poll_stderr() == []
        assert session._stdout_closed is False
        assert session._stderr_closed is False
        assert psutil.Process(child_pid).is_running()
    finally:
        session.stop_monitor()
        try:
            psutil.Process(child_pid).kill()
        except psutil.Error:
            pass
        assert _wait_for_pid_exit(child_pid)


def test_task_runner_applies_environment_profile_defaults(tmp_path: Path) -> None:
    runner = TaskRunner(
        target_type="command",
        tid=None,
        function_target=None,
        process_target=sys.executable,
        agent=None,
        args=["-c", "import os; print(os.environ['WEFT_ENV_PROFILE'], end='')"],
        kwargs=None,
        env={},
        working_dir=str(tmp_path),
        timeout=5.0,
        limits=None,
        monitor_class=None,
        monitor_interval=0.1,
        environment_profile_ref=(
            "tests.fixtures.runtime_profiles_fixture:host_environment_profile"
        ),
    )

    outcome = runner.run({})

    assert outcome.ok
    assert outcome.value == "host-default"


def test_task_runner_reports_command_failure(tmp_path: Path) -> None:
    runner = TaskRunner(
        target_type="command",
        tid=None,
        function_target=None,
        process_target=sys.executable,
        agent=None,
        args=["-c", "import sys; sys.exit(3)"],
        kwargs=None,
        env={},
        working_dir=str(tmp_path),
        timeout=5.0,
        limits=None,
        monitor_class=None,
        monitor_interval=0.1,
    )

    outcome = runner.run({})

    assert outcome.status == "error"
    assert outcome.returncode == 3
    assert outcome.error is not None


def test_task_runner_times_out(tmp_path: Path) -> None:
    runner = TaskRunner(
        target_type="command",
        tid=None,
        function_target=None,
        process_target=sys.executable,
        agent=None,
        args=[PROCESS_SCRIPT, "--duration", "2"],
        kwargs=None,
        env={},
        working_dir=str(tmp_path),
        timeout=0.2,
        limits=None,
        monitor_class=None,
        monitor_interval=0.05,
    )

    outcome = runner.run({})

    assert outcome.status == "timeout"
    assert outcome.error is not None


def test_task_runner_timeout_terminates_command_descendants(tmp_path: Path) -> None:
    pytest.importorskip("psutil")
    parent_script, child_script = _write_descendant_scripts(tmp_path)
    pidfile = tmp_path / "child.pid"
    runner = TaskRunner(
        target_type="command",
        tid=None,
        function_target=None,
        process_target=sys.executable,
        agent=None,
        args=[str(parent_script), str(child_script), str(pidfile)],
        kwargs=None,
        env={},
        working_dir=str(tmp_path),
        timeout=3.0,
        limits=None,
        monitor_class=None,
        monitor_interval=0.05,
    )

    outcome = runner.run({})
    child_pid = _wait_for_pidfile(pidfile, timeout=5.0)

    try:
        assert outcome.status == "timeout"
        assert _wait_for_pid_exit(child_pid)
    finally:
        if not _wait_for_pid_exit(child_pid, timeout=0.1):
            psutil = pytest.importorskip("psutil")
            try:
                psutil.Process(child_pid).kill()
            except psutil.Error:
                pass


def test_task_runner_enforces_memory_limit(tmp_path: Path) -> None:
    pytest.importorskip("psutil")
    limits = LimitsSection(memory_mb=1)
    runner = TaskRunner(
        target_type="command",
        tid=None,
        function_target=None,
        process_target=sys.executable,
        agent=None,
        args=[PROCESS_SCRIPT, "--memory-mb", "10", "--duration", "2"],
        kwargs=None,
        env={},
        working_dir=str(tmp_path),
        timeout=5.0,
        limits=limits,
        monitor_class="weft.core.resource_monitor.ResourceMonitor",
        monitor_interval=0.05,
    )

    outcome = runner.run({})

    assert outcome.status == "limit"
    assert outcome.error is not None
    assert outcome.metrics is not None


def test_task_runner_memory_limit_counts_grandchild_memory(tmp_path: Path) -> None:
    """A memory limit fires on memory held two levels below the command root.

    Root and child are bare interpreters well under the limit; only the
    grandchild's 160 MiB can exceed it, so a monitor that measured the root
    alone, or only its direct children, would let the work finish.

    Spec: [RM-1], [RM-5.1]
    """

    argv, pid_dir = tree_command(tmp_path, depth=2, fanout=1, leaf_memory_mb=160)
    runner = TaskRunner(
        target_type="command",
        tid=None,
        function_target=None,
        process_target=argv[0],
        agent=None,
        args=argv[1:],
        kwargs=None,
        env={},
        working_dir=str(tmp_path),
        timeout=30.0,
        limits=LimitsSection(memory_mb=120),
        monitor_class="weft.core.resource_monitor.ResourceMonitor",
        monitor_interval=0.05,
    )

    try:
        outcome = runner.run({})
    finally:
        kill_pids({int(path.name) for path in pid_dir.iterdir() if path.name.isdigit()})

    assert outcome.status == "limit"
    assert "Memory" in (outcome.error or "")


class _SampleCountingMonitor(ResourceMonitor):
    """The built-in monitor, recording when each limit check samples."""

    sample_times: ClassVar[list[float]] = []

    def check_limits(self) -> tuple[bool, str | None]:
        type(self).sample_times.append(time.monotonic())
        return super().check_limits()


@pytest.mark.parametrize("target_type", ["command", "function"])
@pytest.mark.parametrize(
    ("limits", "min_samples", "max_samples"),
    [
        # Metrics only: samples at 1, 2, 4, 8, 16 and 32 intervals (0.05 s
        # to 1.6 s); scheduling delay can only lower that count.
        pytest.param(LimitsSection(), 3, 8, id="no-limit"),
        # Enforcement: every interval, about forty samples in two seconds;
        # the bound tolerates wake latency of three intervals per sample.
        pytest.param(_CONFIGURED_LIMIT, 10, None, id="limit"),
    ],
)
def test_task_runner_sampling_clock_follows_configured_limits(
    target_type: str,
    limits: LimitsSection,
    min_samples: int,
    max_samples: int | None,
) -> None:
    """Sampling every interval is enforcement; without a limit it doubles.

    Either way the completed work item still reports its metrics.

    Spec: [RM-5.1]
    """

    _SampleCountingMonitor.sample_times = []
    command = target_type == "command"
    runner = TaskRunner(
        target_type=target_type,
        tid=None,
        function_target=None if command else "time:sleep",
        process_target=sys.executable if command else None,
        agent=None,
        args=["-c", "import time; time.sleep(2.0)"] if command else [2.0],
        kwargs=None,
        env={},
        working_dir=None,
        timeout=30.0,
        limits=limits,
        monitor_class=f"{__name__}._SampleCountingMonitor",
        monitor_interval=0.05,
    )

    outcome = runner.run({} if command else None)

    samples = len(_SampleCountingMonitor.sample_times)
    assert outcome.status == "ok"
    assert outcome.metrics is not None
    assert outcome.metrics.memory_mb > 0
    assert samples >= min_samples
    assert max_samples is None or samples <= max_samples


_ON_LINUX = sys.platform.startswith("linux")

# Announces itself, then exits 0 once the release file exists. Its own
# deadline keeps a broken test from leaving it behind.
_GATED_EXIT_CHILD = """
import os, sys, time
ready, release = sys.argv[1], sys.argv[2]
open(ready, "w").close()
deadline = time.monotonic() + 25
while not os.path.exists(release):
    if time.monotonic() > deadline:
        sys.exit(3)
    time.sleep(0.005)
"""


def _is_unreaped_exit(process: psutil.Process) -> bool:
    try:
        return bool(process.status() == psutil.STATUS_ZOMBIE)
    except psutil.ZombieProcess:
        return True


def _wait_until(predicate: Any, *, what: str) -> None:
    deadline = time.monotonic() + 15.0
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.002)


class _LinuxExitReadings:
    """One psutil process as Linux reports it once exited and unreaped.

    Live readings pass through untouched. Where this platform raises
    ``ZombieProcess``, the value Linux psutil returns is substituted:
    ``rss=0``, CPU times 0.0, ``num_fds() == 0``, no connections.
    """

    substitutions: ClassVar[list[str]] = []

    def __init__(self, process: psutil.Process) -> None:
        self._process = process
        self.pid = process.pid

    def _read(self, name: str, exited_value: Any) -> Any:
        try:
            return getattr(self._process, name)()
        except psutil.ZombieProcess:
            type(self).substitutions.append(name)
            return exited_value

    def memory_info(self) -> Any:
        return self._read("memory_info", SimpleNamespace(rss=0))

    def cpu_times(self) -> Any:
        return self._read("cpu_times", SimpleNamespace(user=0.0, system=0.0))

    def num_fds(self) -> int:
        return int(self._read("num_fds", 0))

    def net_connections(self) -> list[Any]:
        return list(self._read("net_connections", []))


class _ExitBetweenPollAndSampleMonitor(ResourceMonitor):
    """The built-in monitor; its second sample finds the root just exited.

    The supervisor calls ``check_limits`` only after ``process.poll()``
    returned ``None``, and it is the only caller of ``poll``. The second call
    releases the child and reaches the real ``check_limits`` only once the
    child has exited and is unreaped: the ordering "root exits between the
    liveness poll and the sample".
    """

    ready_path: ClassVar[Path]
    release_path: ClassVar[Path]
    calls: ClassVar[int] = 0
    live_samples: ClassVar[list[ResourceMetrics | None]] = []
    root_exited_at_second_sample: ClassVar[list[bool]] = []
    errors: ClassVar[list[BaseException]] = []

    def _process_tree(self) -> list[Any]:
        if _ON_LINUX:
            return super()._process_tree()
        assert self._process is not None
        # The child has no descendants.
        return [_LinuxExitReadings(self._process)]

    def check_limits(self) -> tuple[bool, str | None]:
        cls = type(self)
        cls.calls += 1
        try:
            assert self._process is not None
            root = self._process
            if cls.calls == 1:
                _wait_until(cls.ready_path.exists, what="child start")
                result = super().check_limits()
                cls.live_samples.append(self.last_metrics())
                return result
            if cls.calls == 2:
                cls.release_path.touch()
                _wait_until(lambda: _is_unreaped_exit(root), what="child exit")
                cls.root_exited_at_second_sample.append(_is_unreaped_exit(root))
        except BaseException as exc:  # the runner would only log it
            cls.errors.append(exc)
            raise
        return super().check_limits()


@pytest.mark.skipif(
    os.name != "posix", reason="Windows has no exited-but-unreaped process state"
)
def test_command_runner_keeps_live_metrics_when_root_exits_between_poll_and_sample(
    tmp_path: Path,
) -> None:
    """A work item sampled while alive reports that sample, not zeros.

    Spec: [RM-5.1]
    """

    monitor_cls = _ExitBetweenPollAndSampleMonitor
    monitor_cls.ready_path = tmp_path / "ready"
    monitor_cls.release_path = tmp_path / "release"
    monitor_cls.calls = 0
    monitor_cls.live_samples = []
    monitor_cls.root_exited_at_second_sample = []
    monitor_cls.errors = []
    _LinuxExitReadings.substitutions = []

    runner = TaskRunner(
        target_type="command",
        tid=None,
        function_target=None,
        process_target=sys.executable,
        agent=None,
        args=[
            "-c",
            _GATED_EXIT_CHILD,
            str(monitor_cls.ready_path),
            str(monitor_cls.release_path),
        ],
        kwargs=None,
        env={},
        working_dir=None,
        timeout=30.0,
        limits=LimitsSection(memory_mb=4096),
        monitor_class=f"{__name__}._ExitBetweenPollAndSampleMonitor",
        monitor_interval=0.05,
    )

    outcome = runner.run({})

    # The ordering was forced as described, and the exit readings were the
    # platform's own on Linux and the substituted Linux values elsewhere.
    assert monitor_cls.errors == []
    assert monitor_cls.calls == 2
    assert monitor_cls.root_exited_at_second_sample == [True]
    assert bool(_LinuxExitReadings.substitutions) == (not _ON_LINUX)
    (live,) = monitor_cls.live_samples
    assert live is not None and live.memory_mb > 0
    assert outcome.status == "ok"
    assert outcome.returncode == 0

    assert outcome.metrics is not None
    assert outcome.metrics.memory_mb > 0
    assert outcome.metrics == live


def test_task_runner_enforces_cpu_limit(tmp_path: Path) -> None:
    # Keep CPU pressure active until enforcement; a fixed CPU-time budget can
    # finish before the monitor collects its five samples under suite load.
    pytest.importorskip("psutil")
    limits = LimitsSection(cpu_percent=1)
    runner = TaskRunner(
        target_type="command",
        tid=None,
        function_target=None,
        process_target=sys.executable,
        agent=None,
        args=[
            PROCESS_SCRIPT,
            "--cpu-percent",
            "100",
            "--duration",
            "15",
        ],
        kwargs=None,
        env={},
        working_dir=str(tmp_path),
        timeout=10.0,
        limits=limits,
        monitor_class="weft.core.resource_monitor.ResourceMonitor",
        monitor_interval=0.05,
    )

    outcome = runner.run({})

    assert outcome.status == "limit"
    assert "CPU" in (outcome.error or "")
    assert outcome.metrics is not None


def test_task_runner_enforces_fd_limit(tmp_path: Path) -> None:
    pytest.importorskip("psutil")
    limits = LimitsSection(max_fds=5)
    runner = TaskRunner(
        target_type="command",
        tid=None,
        function_target=None,
        process_target=sys.executable,
        agent=None,
        args=[PROCESS_SCRIPT, "--fds", "20", "--duration", "2"],
        kwargs=None,
        env={},
        working_dir=str(tmp_path),
        timeout=5.0,
        limits=limits,
        monitor_class="weft.core.resource_monitor.ResourceMonitor",
        monitor_interval=0.05,
    )

    outcome = runner.run({})

    assert outcome.status == "limit"
    assert any(
        label in (outcome.error or "") for label in ("Open files", "Open handles")
    )
    assert outcome.metrics is not None


def test_task_runner_reports_multiple_violations(tmp_path: Path) -> None:
    pytest.importorskip("psutil")
    limits = LimitsSection(memory_mb=1, max_fds=2)
    runner = TaskRunner(
        target_type="command",
        tid=None,
        function_target=None,
        process_target=sys.executable,
        agent=None,
        args=[PROCESS_SCRIPT, "--memory-mb", "10", "--fds", "20", "--duration", "2"],
        kwargs=None,
        env={},
        working_dir=str(tmp_path),
        timeout=5.0,
        limits=limits,
        monitor_class="weft.core.resource_monitor.ResourceMonitor",
        monitor_interval=0.05,
    )

    outcome = runner.run({})

    assert outcome.status == "limit"
    assert outcome.error is not None
    assert any(
        label in outcome.error for label in ("Memory", "Open files", "Open handles")
    )
    assert outcome.metrics is not None


def test_task_runner_can_be_cancelled(tmp_path: Path) -> None:
    cancel_after_start = False

    def on_worker_started(_pid: int | None) -> None:
        nonlocal cancel_after_start
        cancel_after_start = True

    runner = TaskRunner(
        target_type="command",
        tid=None,
        function_target=None,
        process_target=sys.executable,
        agent=None,
        args=[PROCESS_SCRIPT, "--duration", "5"],
        kwargs=None,
        env={},
        working_dir=str(tmp_path),
        timeout=10.0,
        limits=None,
        monitor_class=None,
        monitor_interval=0.05,
    )

    outcome = runner.run_with_hooks(
        {},
        cancel_requested=lambda: cancel_after_start,
        on_worker_started=on_worker_started,
    )

    assert outcome.status == "cancelled"
    assert outcome.error == "Target execution cancelled"


def test_task_runner_agent_session_continues_conversation() -> None:
    runner = TaskRunner(
        target_type="agent",
        tid="123",
        function_target=None,
        process_target=None,
        agent={
            "runtime": "llm",
            "model": TEST_MODEL_ID,
            "conversation_scope": "per_task",
            "runtime_config": {
                "plugin_modules": ["tests.fixtures.llm_test_models"],
            },
        },
        args=None,
        kwargs=None,
        env={},
        working_dir=None,
        timeout=5.0,
        limits=None,
        monitor_class=None,
        monitor_interval=0.05,
    )

    session = runner.start_agent_session()
    try:
        first = session.execute("hello")
        second = session.execute("__history__")
    finally:
        session.close()

    assert first.status == "ok"
    assert isinstance(first.value, PreparedTaskResult)
    assert project_task_result(first.value).aggregate_public_output() == "text:hello"
    assert second.status == "ok"
    assert isinstance(second.value, PreparedTaskResult)
    assert (
        project_task_result(second.value).aggregate_public_output() == "history:hello"
    )


class _PrivateLimitsMonitor(BaseResourceMonitor):
    """A custom monitor that keeps its limits private.

    The documented monitor protocol ([RM-5.1]) is start, stop, snapshot,
    check_limits and last_metrics plus the keyword constructor. It requires
    neither a ``limits`` attribute nor a call to the base constructor.
    """

    samples: ClassVar[int] = 0

    def __init__(
        self, *, limits: Any | None = None, polling_interval: float = 1.0
    ) -> None:
        self._private_limits = limits
        self.polling_interval = polling_interval

    def start(self, pid: int) -> None:
        del pid

    def stop(self) -> None:
        return None

    def snapshot(self) -> ResourceMetrics:
        return ResourceMetrics(memory_mb=1.0)

    def check_limits(self) -> tuple[bool, str | None]:
        type(self).samples += 1
        return True, None

    def last_metrics(self) -> ResourceMetrics | None:
        return ResourceMetrics(memory_mb=1.0)


def test_task_runner_agent_session_accepts_a_monitor_without_limits_attribute() -> None:
    """A persistent session takes its sampling clock from the runner's limits.

    Spec: [RM-5.1]
    """

    _PrivateLimitsMonitor.samples = 0
    runner = TaskRunner(
        target_type="agent",
        tid="123",
        function_target=None,
        process_target=None,
        agent={
            "runtime": "llm",
            "model": TEST_MODEL_ID,
            "conversation_scope": "per_task",
            "runtime_config": {
                "plugin_modules": ["tests.fixtures.llm_test_models"],
            },
        },
        args=None,
        kwargs=None,
        env={},
        working_dir=None,
        timeout=5.0,
        limits=LimitsSection(),
        monitor_class=f"{__name__}._PrivateLimitsMonitor",
        monitor_interval=0.05,
    )

    session = runner.start_agent_session()
    try:
        result = session.execute("hello")
    finally:
        session.close()

    assert result.status == "ok"
    assert result.metrics is not None
    assert _PrivateLimitsMonitor.samples >= 1


def test_task_runner_agent_session_startup_uses_dedicated_ready_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin = tmp_path / "slow_llm_plugin.py"
    plugin.write_text(
        "\n".join(  # noqa: FLY002 approved [TS-3.1] [RUFF-SUP-239] exception
            [
                "import time",
                "import llm",
                "from tests.fixtures.llm_test_models import DeterministicAgentModel",
                "",
                "time.sleep(0.35)",
                "",
                "@llm.hookimpl",
                "def register_models(register):",
                "    register(DeterministicAgentModel())",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    runner = TaskRunner(
        target_type="agent",
        tid="slow-ready",
        function_target=None,
        process_target=None,
        agent={
            "runtime": "llm",
            "model": TEST_MODEL_ID,
            "conversation_scope": "per_task",
            "runtime_config": {
                "plugin_modules": ["slow_llm_plugin"],
            },
        },
        args=None,
        kwargs=None,
        env={},
        working_dir=None,
        timeout=0.1,
        limits=None,
        monitor_class=None,
        monitor_interval=0.05,
    )

    session = runner.start_agent_session()
    session.close()


def test_task_runner_run_does_not_preflight_agent_runtime_per_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validation_calls: list[tuple[str, bool, str | None]] = []
    plugin_calls: list[tuple[bool, str | None]] = []

    def fake_validate_runtime(
        taskspec_payload: Mapping[str, object],
        *,
        bundle_root: str | None = None,
        load_runtime: bool = False,
        preflight: bool = False,
    ) -> None:
        assert "_weft_bundle_root" not in taskspec_payload
        del load_runtime
        validation_calls.append(("runtime", preflight, bundle_root))

    def fake_validate_tool_profile(
        taskspec_payload: Mapping[str, object],
        *,
        bundle_root: str | None = None,
        load_runtime: bool = False,
        preflight: bool = False,
    ) -> None:
        assert "_weft_bundle_root" not in taskspec_payload
        del load_runtime
        validation_calls.append(("tool_profile", preflight, bundle_root))

    class FakeBackend:
        def run_with_hooks(self, work_item: object, **kwargs: object) -> RunnerOutcome:
            del work_item, kwargs
            return RunnerOutcome(
                status="ok",
                value="ok",
                error=None,
                stdout=None,
                stderr=None,
                returncode=0,
                duration=0.0,
            )

    class FakePlugin:
        name = "host"
        capabilities = RunnerCapabilities()

        def check_version(self) -> None:
            return None

        def validate_taskspec(
            self,
            taskspec_payload: Mapping[str, object],
            *,
            bundle_root: str | None = None,
            preflight: bool = False,
        ) -> None:
            assert "_weft_bundle_root" not in taskspec_payload
            plugin_calls.append((preflight, bundle_root))

        def create_runner(self, **kwargs: object) -> FakeBackend:
            del kwargs
            return FakeBackend()

        def stop(self, handle: RunnerHandle, *, timeout: float = 2.0) -> bool:
            del handle, timeout
            return True

        def kill(self, handle: RunnerHandle, *, timeout: float = 2.0) -> bool:
            del handle, timeout
            return True

    monkeypatch.setattr(
        "weft.core.tasks.runner.require_runner_plugin", lambda name: FakePlugin()
    )
    monkeypatch.setattr(
        "weft.core.tasks.runner.validate_taskspec_agent_runtime",
        fake_validate_runtime,
    )
    monkeypatch.setattr(
        "weft.core.tasks.runner.validate_taskspec_agent_tool_profile",
        fake_validate_tool_profile,
    )

    runner = TaskRunner(
        target_type="agent",
        tid="123",
        function_target=None,
        process_target=None,
        agent={
            "runtime": "llm",
            "model": TEST_MODEL_ID,
            "conversation_scope": "per_message",
            "runtime_config": {
                "plugin_modules": ["tests.fixtures.llm_test_models"],
            },
        },
        args=None,
        kwargs=None,
        env={},
        working_dir=None,
        timeout=5.0,
        limits=None,
        monitor_class=None,
        monitor_interval=0.05,
        bundle_root="/tmp/weft-test-bundle",
    )

    outcome = runner.run({"content": "hello"})

    assert outcome.status == "ok"
    assert plugin_calls == [(False, "/tmp/weft-test-bundle")]
    assert validation_calls == [
        ("runtime", False, "/tmp/weft-test-bundle"),
        ("tool_profile", False, "/tmp/weft-test-bundle"),
    ]


def test_task_runner_start_agent_session_does_not_preflight_agent_runtime_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validation_calls: list[tuple[str, bool]] = []
    plugin_calls: list[bool] = []

    def fake_validate_runtime(
        taskspec_payload: Mapping[str, object],
        *,
        bundle_root: str | None = None,
        load_runtime: bool = False,
        preflight: bool = False,
    ) -> None:
        del taskspec_payload, bundle_root, load_runtime
        validation_calls.append(("runtime", preflight))

    def fake_validate_tool_profile(
        taskspec_payload: Mapping[str, object],
        *,
        bundle_root: str | None = None,
        load_runtime: bool = False,
        preflight: bool = False,
    ) -> None:
        del taskspec_payload, bundle_root, load_runtime
        validation_calls.append(("tool_profile", preflight))

    class FakeSession:
        def close(self) -> None:
            return None

    class FakeBackend:
        def start_agent_session(self) -> FakeSession:
            return FakeSession()

    class FakePlugin:
        name = "host"
        capabilities = RunnerCapabilities()

        def check_version(self) -> None:
            return None

        def validate_taskspec(
            self,
            taskspec_payload: Mapping[str, object],
            *,
            bundle_root: str | None = None,
            preflight: bool = False,
        ) -> None:
            del taskspec_payload, bundle_root
            plugin_calls.append(preflight)

        def create_runner(self, **kwargs: object) -> FakeBackend:
            del kwargs
            return FakeBackend()

        def stop(self, handle: RunnerHandle, *, timeout: float = 2.0) -> bool:
            del handle, timeout
            return True

        def kill(self, handle: RunnerHandle, *, timeout: float = 2.0) -> bool:
            del handle, timeout
            return True

    monkeypatch.setattr(
        "weft.core.tasks.runner.require_runner_plugin", lambda name: FakePlugin()
    )
    monkeypatch.setattr(
        "weft.core.tasks.runner.validate_taskspec_agent_runtime",
        fake_validate_runtime,
    )
    monkeypatch.setattr(
        "weft.core.tasks.runner.validate_taskspec_agent_tool_profile",
        fake_validate_tool_profile,
    )

    runner = TaskRunner(
        target_type="agent",
        tid="123",
        function_target=None,
        process_target=None,
        agent={
            "runtime": "llm",
            "model": TEST_MODEL_ID,
            "conversation_scope": "per_task",
            "runtime_config": {
                "plugin_modules": ["tests.fixtures.llm_test_models"],
            },
        },
        args=None,
        kwargs=None,
        env={},
        working_dir=None,
        timeout=5.0,
        limits=None,
        monitor_class=None,
        monitor_interval=0.05,
    )

    session = runner.start_agent_session()
    session.close()

    assert plugin_calls == [False]
    assert validation_calls == [("runtime", False), ("tool_profile", False)]


def test_command_session_terminate_kills_descendants(tmp_path: Path) -> None:
    pytest.importorskip("psutil")
    parent_script, child_script = _write_descendant_scripts(tmp_path)
    pidfile = tmp_path / "interactive-child.pid"
    runner = TaskRunner(
        target_type="command",
        tid=None,
        function_target=None,
        process_target=sys.executable,
        agent=None,
        args=[str(parent_script), str(child_script), str(pidfile)],
        kwargs=None,
        env={},
        working_dir=str(tmp_path),
        timeout=5.0,
        limits=None,
        monitor_class=None,
        monitor_interval=0.05,
    )

    session = runner.start_session(on_activity=lambda: None)
    child_identity: tuple[int, float] | None = None
    try:
        try:
            child_pid = _wait_for_pidfile(pidfile)
        except AssertionError as error:
            error.add_note(
                f"Session returncode={session.returncode()!r}; "
                f"stderr={''.join(session.poll_stderr())!r}"
            )
            raise
        child_identity = (child_pid, psutil.Process(child_pid).create_time())
        session.terminate()
        assert _wait_for_pid_exit(child_pid)
    finally:
        handle = session.handle
        if handle is not None:
            for pid, create_time in handle.scoped_host_processes():
                helpers_module.terminate_verified_process_tree(
                    pid, create_time, kill=True
                )
        if child_identity is not None:
            helpers_module.terminate_verified_process_tree(*child_identity, kill=True)
        session.stop_monitor()


def test_task_runner_materializes_docker_container_profile_at_plugin_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_module = pytest.importorskip("weft_docker.plugin")
    if plugin_module.os.name == "nt":
        pytest.skip("Docker runner is currently unsupported on Windows")
    profile_file = tmp_path / ".weft" / "docker-profiles.toml"
    profile_file.parent.mkdir(parents=True)
    profile_file.write_text(
        """
        [profiles.ops]
        image = "busybox:latest"
        network = "project_ops"
        mount_workdir = false
        container_workdir = "/app/project"

        [profiles.ops.env]
        SERVICE_URL = "https://internal-service:8443"
        """.strip()
        + "\n",
        encoding="utf-8",
    )

    class FakeDockerClient:
        def ping(self) -> None:
            return None

    @contextmanager
    def fake_docker_client(*, timeout: int = 10) -> Iterator[FakeDockerClient]:
        del timeout
        yield FakeDockerClient()

    monkeypatch.setattr(plugin_module, "_load_docker_sdk", lambda: object())
    monkeypatch.setattr(plugin_module, "_docker_client", fake_docker_client)

    runner = TaskRunner(
        target_type="command",
        tid="1844674407370955161",
        function_target=None,
        process_target="python3",
        agent=None,
        args=["-c", "print('ok')"],
        kwargs=None,
        env={"SERVICE_URL": "https://explicit.example.test"},
        working_dir=str(tmp_path),
        timeout=5.0,
        limits=None,
        monitor_class=None,
        monitor_interval=0.05,
        runner_name="docker",
        runner_options={
            "container_profile": "ops",
            "container_profile_file": str(profile_file),
        },
    )

    backend = runner._backend
    assert isinstance(backend, plugin_module.DockerCommandRunner)
    assert backend._image == "busybox:latest"
    assert backend._network == "project_ops"
    assert backend._mount_workdir is False
    assert backend._container_workdir == "/app/project"
    assert backend._env["SERVICE_URL"] == "https://explicit.example.test"


def _build_function_host_runner(
    timeout: float | None,
    *,
    function_target: str = "tests.tasks.sample_targets:echo_payload",
    args: list[Any] | None = None,
    kwargs: dict[str, Any] | None = None,
    monitor_class: str | None = None,
    monitor_interval: float = 0.1,
) -> HostTaskRunner:
    return HostTaskRunner(
        target_type="function",
        tid=None,
        function_target=function_target,
        process_target=None,
        agent=None,
        args=args or [],
        kwargs=kwargs or {},
        env={},
        working_dir=None,
        timeout=timeout,
        limits=None,
        monitor_class=monitor_class,
        monitor_interval=monitor_interval,
    )


class _RecordingTerminalSender:
    """Observe production framing without replacing its encoder or error guard."""

    def __init__(self) -> None:
        self.frames: list[bytes] = []
        self.closed = False

    def send_bytes(self, frame: bytes) -> None:
        self.frames.append(frame)

    def close(self) -> None:
        self.closed = True


def test_spawned_worker_rejects_command_targets_without_running_them(
    tmp_path: Path,
) -> None:
    """The spawned worker never executes command targets.

    Command tasks are served by ``run_with_hooks`` delegating to
    ``_run_command_with_hooks`` (Spec: [RM-5]), so reaching the spawned worker
    with ``type == "command"`` is a programming error. It must surface as an
    explicit terminal error instead of silently starting a second subprocess.
    """

    sentinel = tmp_path / "command-ran"

    sender = _RecordingTerminalSender()

    host_module._worker_entry(
        {
            "type": "command",
            "process_target": sys.executable,
            "args": ["-c", f"open({str(sentinel)!r}, 'w').close()"],
        },
        "payload",
        sender,  # type: ignore[arg-type]
    )

    assert sentinel.exists() is False
    assert sender.closed is True
    assert len(sender.frames) == 1
    outcome = parse_outcome_payload(json.loads(sender.frames[0]))
    assert outcome.status == "error"
    assert outcome.value is None
    assert outcome.returncode is None
    assert outcome.error is not None
    assert "command" in outcome.error
    assert outcome.diagnostics is not None
    assert outcome.diagnostics["phase"] == "execute"
    assert outcome.diagnostics["target_type"] == "command"


@pytest.mark.parametrize("transport_failure", [False, True])
def test_function_worker_maps_arbitrary_execution_failure_to_terminal_outcome(
    transport_failure: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExtensionFailure(Exception):
        pass

    error_type = (
        TerminalHandoffTransportError if transport_failure else ExtensionFailure
    )

    def fail_execution(*_args: object, **_kwargs: object) -> None:
        raise error_type("extension detail")

    sender = _RecordingTerminalSender()
    monkeypatch.setattr(host_module, "execute_function_target", fail_execution)

    host_module._worker_entry(
        {"type": "function", "function_target": "fixture:target"},
        "payload",
        sender,  # type: ignore[arg-type]
    )

    assert sender.closed is True
    assert len(sender.frames) == 1
    outcome = parse_outcome_payload(json.loads(sender.frames[0]))
    assert outcome.status == "error"
    assert outcome.value is None
    assert outcome.returncode is None
    assert outcome.error is not None
    assert f"{error_type.__name__}: extension detail" in outcome.error
    assert outcome.diagnostics is not None
    assert outcome.diagnostics["phase"] == "execute"
    assert outcome.diagnostics["exception_type"] == error_type.__name__


def test_function_worker_propagates_non_exception_failure_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FatalSignal(BaseException):
        pass

    fatal = FatalSignal("stop worker")

    def fail_execution(*_args: object, **_kwargs: object) -> None:
        raise fatal

    monkeypatch.setattr(host_module, "execute_function_target", fail_execution)
    sender = _RecordingTerminalSender()

    with pytest.raises(FatalSignal) as caught:
        host_module._worker_entry(
            {"type": "function", "function_target": "fixture:target"},
            "payload",
            sender,  # type: ignore[arg-type]
        )

    assert caught.value is fatal
    assert sender.closed is True
    assert sender.frames == []


def test_function_worker_prepares_output_inside_context_and_writes_after_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Application conversion sees task env/cwd; the frame leaves after restoration."""

    env_name = "WEFT_TEST_RESULT_CONTEXT"
    monkeypatch.setenv(env_name, "parent")
    parent_cwd = Path.cwd()
    observations: list[tuple[str, str, Path]] = []

    class ContextValue:
        def __str__(self) -> str:
            observations.append(("convert", os.environ[env_name], Path.cwd()))
            return "context-result"

    class Sender(_RecordingTerminalSender):
        def send_bytes(self, frame: bytes) -> None:
            observations.append(("write", os.environ[env_name], Path.cwd()))
            super().send_bytes(frame)

    monkeypatch.setattr(
        host_module, "execute_function_target", lambda *_a, **_k: ContextValue()
    )
    sender = Sender()

    host_module._worker_entry(
        {
            "type": "function",
            "function_target": "fixture:target",
            "env": {env_name: "child"},
            "working_dir": str(tmp_path),
        },
        None,
        sender,  # type: ignore[arg-type]
    )

    assert observations == [
        ("convert", "child", tmp_path),
        ("write", "parent", parent_cwd),
    ]
    assert sender.closed is True
    assert len(sender.frames) == 1
    outcome = parse_outcome_payload(json.loads(sender.frames[0]))
    assert outcome.status == "ok"
    assert isinstance(outcome.value, PreparedTaskResult)
    assert project_task_result(outcome.value) == "context-result"


@pytest.mark.parametrize("phase", ["entry", "exit"])
def test_function_worker_context_failure_replaces_unsent_result(
    phase: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runtime context failures retain execute classification and one sole write."""

    @contextmanager
    def failing_context(_spec_data: Mapping[str, Any]) -> Iterator[None]:
        if phase == "entry":
            raise RuntimeError("context entry failed")
        yield
        raise RuntimeError("context exit failed")

    monkeypatch.setattr(host_module, "_worker_runtime_context", failing_context)
    monkeypatch.setattr(
        host_module, "execute_function_target", lambda *_a, **_k: "unsent success"
    )
    sender = _RecordingTerminalSender()

    host_module._worker_entry(
        {"type": "function", "function_target": "fixture:target"},
        None,
        sender,  # type: ignore[arg-type]
    )

    assert sender.closed is True
    assert len(sender.frames) == 1
    outcome = parse_outcome_payload(json.loads(sender.frames[0]))
    assert outcome.status == "error"
    assert outcome.value is None
    assert outcome.error is not None
    assert f"context {phase} failed" in outcome.error
    assert outcome.diagnostics is not None
    assert outcome.diagnostics["phase"] == "execute"


def test_function_worker_failed_fallback_closes_pipe_without_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed fallback encoder cannot re-enter execution handling or retry."""

    cycle: list[Any] = []
    cycle.append(cycle)
    monkeypatch.setattr(host_module, "execute_function_target", lambda *_a, **_k: cycle)
    real_dumps = json.dumps
    fallback_attempts: list[str] = []

    def fail_fallback(payload: Any, **kwargs: Any) -> str:
        if isinstance(payload, dict) and payload.get("status") == "error":
            fallback_attempts.append("encode")
            raise ValueError("fallback encoding failed")
        return real_dumps(payload, **kwargs)

    monkeypatch.setattr(transport_module.json, "dumps", fail_fallback)
    receiver, sender = multiprocessing.get_context("spawn").Pipe(duplex=False)
    try:
        with pytest.raises(TerminalHandoffTransportError, match="fallback"):
            host_module._worker_entry(
                {"type": "function", "function_target": "fixture:target"},
                None,
                sender,
            )

        assert fallback_attempts == ["encode"]
        assert sender.closed is True
        assert poll_terminal_payload(receiver, 1.0)
        with pytest.raises(EOFError):
            receive_terminal_payload(receiver)
    finally:
        sender.close()
        receiver.close()


def test_function_worker_partial_write_never_sends_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once delivery starts, a write error closes the endpoint without a second frame."""

    class BrokenSender(_RecordingTerminalSender):
        def send_bytes(self, frame: bytes) -> None:
            super().send_bytes(frame)
            raise BrokenPipeError("partial delivery")

    monkeypatch.setattr(
        host_module, "execute_function_target", lambda *_a, **_k: "result"
    )
    sender = BrokenSender()

    with pytest.raises(TerminalHandoffTransportError, match="delivery failed"):
        host_module._worker_entry(
            {"type": "function", "function_target": "fixture:target"},
            None,
            sender,  # type: ignore[arg-type]
        )

    assert sender.closed is True
    assert len(sender.frames) == 1
    outcome = parse_outcome_payload(json.loads(sender.frames[0]))
    assert outcome.status == "ok"
    assert isinstance(outcome.value, PreparedTaskResult)
    assert project_task_result(outcome.value) == "result"


@pytest.mark.parametrize(
    ("persistent", "invalid_identity", "error_fragment"),
    [
        pytest.param(
            False, True, "runtime must be a nonempty string", id="one-shot-identity"
        ),
        pytest.param(
            True, True, "runtime must be a nonempty string", id="session-identity"
        ),
        pytest.param(True, False, "agent", id="session-result-type"),
    ],
)
def test_agent_worker_invalid_result_emits_serialization_error(
    persistent: bool,
    invalid_identity: bool,
    error_fragment: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raw adapter result defects fail child preparation before parent parsing."""

    invalid_result: Any = (
        AgentExecutionResult(
            runtime="", model=None, output_mode="text", outputs=("usable output",)
        )
        if invalid_identity
        else "text"
    )

    class Session:
        closed = False

        def execute(self, _work_item: object) -> Any:
            return invalid_result

        def close(self) -> None:
            self.closed = True

    session = Session()
    monkeypatch.setattr(
        host_module, "execute_agent_target", lambda *_a, **_k: invalid_result
    )
    monkeypatch.setattr(
        host_module, "start_agent_runtime_session", lambda *_a, **_k: session
    )
    ctx = multiprocessing.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    request_queue = ctx.Queue()
    spec_data = {"type": "agent", "agent": {"runtime": "llm", "model": TEST_MODEL_ID}}
    try:
        if persistent:
            request_queue.put(make_execute_request("hello"))
            request_queue.put(make_stop_request())
            host_module._agent_session_worker_entry(spec_data, request_queue, sender)
            assert session.closed is True
        else:
            host_module._worker_entry(spec_data, "hello", sender)

        assert sender.closed is True
        frames = [
            json.loads(receiver.recv_bytes()) for _ in range(3 if persistent else 1)
        ]
        assert [frame["type"] for frame in frames] == (
            ["booted", "ready", "result"] if persistent else ["outcome"]
        )
        response = frames[-1]
        assert response["status"] == "error"
        assert response["result"] is None
        assert error_fragment in response["error"]
        assert len(response["error"]) <= 550
        assert response["diagnostics"]["phase"] == "result_serialization"
        with pytest.raises(EOFError):
            receiver.recv_bytes()
    finally:
        sender.close()
        receiver.close()
        request_queue.close()
        request_queue.join_thread()


@pytest.mark.timeout(30)
def test_function_host_start_callback_failures_are_logged_without_replacing_outcome(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runner = _build_function_host_runner(timeout=None)

    def fail_callback(_value: object) -> None:
        raise RuntimeError("contains secret")

    with caplog.at_level(logging.WARNING, logger="weft.core.runners.host"):
        outcome = runner.run_with_hooks(
            "payload",
            on_worker_started=fail_callback,
            on_runtime_handle_started=fail_callback,
        )

    assert outcome.status == "ok"
    assert isinstance(outcome.value, PreparedTaskResult)
    assert project_task_result(outcome.value) == "payload"
    callback_messages = [
        record.message for record in caplog.records if "callback" in record.message
    ]
    assert callback_messages == [
        "Host worker-start callback failed",
        "Host runtime-handle callback failed",
    ]
    assert all(record.exc_info is None for record in caplog.records)
    assert "contains secret" not in caplog.text


@pytest.mark.parametrize(
    "callback_name",
    ["on_worker_started", "on_runtime_handle_started"],
)
def test_function_host_start_callback_propagates_non_exception_failure_identity(
    callback_name: str,
) -> None:
    class FatalSignal(BaseException):
        pass

    fatal = FatalSignal("stop callback")
    runner = _build_function_host_runner(timeout=5.0)

    def fail_callback(_value: object) -> None:
        raise fatal

    with pytest.raises(FatalSignal) as caught:
        runner.run_with_hooks(
            "payload",
            on_worker_started=fail_callback
            if callback_name == "on_worker_started"
            else None,
            on_runtime_handle_started=fail_callback
            if callback_name == "on_runtime_handle_started"
            else None,
        )

    assert caught.value is fatal


def test_host_monitor_start_failure_attempts_stop_without_replacing_outcome(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class Monitor:
        def start(self, _pid: int) -> None:
            calls.append("start")
            raise RuntimeError("sensitive monitor start failure")

        def stop(self) -> None:
            calls.append("stop")
            raise RuntimeError("sensitive monitor cleanup failure")

    monkeypatch.setattr(
        host_module, "load_resource_monitor", lambda *_a, **_k: Monitor()
    )
    runner = _build_function_host_runner(
        timeout=5.0,
        monitor_class="tests.fake:Monitor",
        monitor_interval=60.0,
    )

    with caplog.at_level(logging.WARNING, logger="weft.core.runners.host"):
        outcome = runner.run("payload")

    assert outcome.status == "ok"
    assert isinstance(outcome.value, PreparedTaskResult)
    assert project_task_result(outcome.value) == "payload"
    assert calls == ["start", "stop"]
    monitor_messages = [
        record.getMessage()
        for record in caplog.records
        if "resource monitor" in record.getMessage()
    ]
    assert monitor_messages == [
        "Failed to start host resource monitor",
        "Failed to stop host resource monitor after startup failure",
    ]
    assert all(record.exc_info is None for record in caplog.records)
    assert "sensitive" not in caplog.text


def test_host_monitor_without_pid_attempts_stop_and_reports_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[str] = []

    class Monitor:
        def stop(self) -> None:
            calls.append("stop")
            raise RuntimeError("sensitive missing-pid cleanup failure")

    with caplog.at_level(logging.WARNING, logger="weft.core.runners.host"):
        monitor = host_module._start_optional_monitor(Monitor(), None)

    assert monitor is None
    assert calls == ["stop"]
    assert [record.getMessage() for record in caplog.records] == [
        "Host resource monitor disabled because worker PID is unavailable",
        "Failed to stop host resource monitor without a worker PID",
    ]
    assert all(record.exc_info is None for record in caplog.records)
    assert "sensitive" not in caplog.text


def test_host_monitor_without_pid_reports_disable_after_successful_cleanup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[str] = []

    class Monitor:
        def stop(self) -> None:
            calls.append("stop")

    with caplog.at_level(logging.WARNING, logger="weft.core.runners.host"):
        monitor = host_module._start_optional_monitor(Monitor(), None)

    assert monitor is None
    assert calls == ["stop"]
    assert [record.getMessage() for record in caplog.records] == [
        "Host resource monitor disabled because worker PID is unavailable"
    ]
    assert all(record.exc_info is None for record in caplog.records)


def test_command_session_monitor_start_failure_clears_session_owner(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class Monitor:
        def start(self, _pid: int) -> None:
            calls.append("start")
            raise RuntimeError("sensitive session monitor start failure")

        def stop(self) -> None:
            calls.append("stop")
            raise RuntimeError("sensitive session monitor cleanup failure")

    monkeypatch.setattr(
        host_module, "load_resource_monitor", lambda *_a, **_k: Monitor()
    )
    runner = HostTaskRunner(
        target_type="command",
        tid=None,
        function_target=None,
        process_target=sys.executable,
        agent=None,
        args=["-c", "import time; time.sleep(30)"],
        kwargs=None,
        env={},
        working_dir=None,
        timeout=5.0,
        limits=None,
        monitor_class="tests.fake:Monitor",
        monitor_interval=0.1,
    )

    with caplog.at_level(logging.WARNING, logger="weft.core.runners.host"):
        session = runner.start_session(on_activity=lambda: None)
    try:
        assert session._monitor is None
        assert calls == ["start", "stop"]
        assert [record.getMessage() for record in caplog.records] == [
            "Failed to start host resource monitor",
            "Failed to stop host resource monitor after startup failure",
        ]
        assert all(record.exc_info is None for record in caplog.records)
        assert "sensitive" not in caplog.text
    finally:
        session.terminate()


def test_host_monitor_poll_failures_preserve_cached_metrics_and_outcome(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    calls: list[str] = []
    metrics = ResourceMetrics(memory_mb=12.5)

    class Monitor:
        def __init__(self) -> None:
            self.metrics_reads = 0
            self.ready_observed = False

        def start(self, _pid: int) -> None:
            calls.append("start")
            wait_for_file(str(ready), timeout=5.0)
            self.ready_observed = True

        def check_limits(self) -> tuple[bool, str | None]:
            calls.append("check_limits")
            raise RuntimeError("sensitive resource check failure")

        def last_metrics(self) -> ResourceMetrics | None:
            calls.append("last_metrics")
            self.metrics_reads += 1
            if self.metrics_reads == 1:
                return metrics
            if self.metrics_reads == 2:
                release.touch()
            raise RuntimeError("sensitive metrics read failure")

        def snapshot(self) -> ResourceMetrics:
            raise AssertionError("cached metrics must prevent a snapshot")

        def stop(self) -> None:
            calls.append("stop")
            raise RuntimeError("sensitive monitor stop failure")

    monitor = Monitor()
    monkeypatch.setattr(host_module, "load_resource_monitor", lambda *_a, **_k: monitor)
    runner = _build_function_host_runner(
        timeout=5.0,
        function_target="tests.tasks.sample_targets:signal_ready_and_wait_for_release",
        args=[str(ready), str(release)],
        kwargs={"timeout": 60.0},
        monitor_class="tests.fake:Monitor",
        monitor_interval=0.01,
    )

    try:
        with caplog.at_level(logging.WARNING, logger="weft.core.runners.host"):
            outcome = runner.run(None)
    finally:
        release.touch()

    assert monitor.ready_observed, "monitored worker did not reach target entry"
    assert monitor.metrics_reads >= 3
    assert outcome.status == "ok"
    assert isinstance(outcome.value, PreparedTaskResult)
    assert project_task_result(outcome.value) == "done"
    assert outcome.metrics is metrics
    assert "check_limits" in calls
    assert calls[-1] == "stop"
    messages = [
        record.getMessage()
        for record in caplog.records
        if "resource" in record.getMessage()
    ]
    assert "Failed to check host resource limits" in messages
    assert "Failed to collect host resource metrics" in messages
    assert messages[-2:] == [
        "Failed to collect final host resource metrics",
        "Failed to stop host resource monitor",
    ]
    assert all(record.exc_info is None for record in caplog.records)
    assert "sensitive" not in caplog.text


@pytest.mark.parametrize("target_type", ["command", "function"])
def test_task_runner_work_item_ending_before_first_sample_reports_no_metrics(
    target_type: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A work item that ends before its first sample reports no metrics.

    The built-in monitor observes a live runtime ([RM-5.1]). Once the worker
    has been reaped there is nothing left to sample, so the outcome carries no
    metrics and a healthy task logs no monitor failure.

    Spec: [RM-5.1]
    """

    command = target_type == "command"
    runner = TaskRunner(
        target_type=target_type,
        tid=None,
        function_target=(
            None if command else "tests.tasks.sample_targets:echo_payload"
        ),
        process_target=sys.executable if command else None,
        agent=None,
        args=["-c", "print('done')"] if command else None,
        kwargs=None,
        env={},
        working_dir=None,
        timeout=30.0,
        limits=LimitsSection(),
        monitor_class="weft.core.resource_monitor.ResourceMonitor",
        # The first sample is due one interval after start, long after the
        # work item has ended.
        monitor_interval=60.0,
    )

    with caplog.at_level(logging.WARNING, logger="weft.core.runners"):
        outcome = runner.run({} if command else "payload")

    assert outcome.status == "ok"
    assert outcome.metrics is None
    runner_warnings = [
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("weft.core.runners")
    ]
    assert runner_warnings == []


ONE_SHOT_EVENT_ORDER: tuple[TerminalHandoffEventKind, ...] = (
    "cancel_requested",
    "outcome_received",
    "timeout_requested",
    "limit_reached",
    "transport_failed",
    "channel_sealed",
    "producer_exited",
    "drain_expired",
)


@pytest.mark.parametrize(
    ("first_kind", "second_kind"),
    tuple(combinations(ONE_SHOT_EVENT_ORDER, 2)),
)
def test_host_runner_routes_all_event_pairs_through_one_shot_policy(
    first_kind: TerminalHandoffEventKind,
    second_kind: TerminalHandoffEventKind,
) -> None:
    """The host adapter uses its declared selector for all 28 event pairs."""

    events = tuple(
        TerminalHandoffEvent(
            kind=kind,
            outcome=object() if kind == "outcome_received" else None,
        )
        for kind in (second_kind, first_kind)
    )

    step = HostTaskRunner._reduce_terminal_observations(
        TerminalHandoffProgress(),
        events,
    )

    assert step is not None
    assert step.event.kind == first_kind


@pytest.mark.parametrize(
    "adapter_reduce",
    (
        HostTaskRunner._reduce_terminal_observations,
        AgentSession._reduce_terminal_observations,
    ),
    ids=("one-shot", "persistent-session"),
)
@pytest.mark.parametrize(
    ("stop_kind", "stop_action", "return_action"),
    (
        ("cancel_requested", "stop_for_cancel", "return_cancelled"),
        ("timeout_requested", "stop_for_timeout", "return_timeout"),
        ("limit_reached", "stop_for_limit", "return_limit"),
    ),
)
def test_both_adapters_consume_stop_levels_before_later_seal(
    adapter_reduce: Any,
    stop_kind: TerminalHandoffEventKind,
    stop_action: str,
    return_action: str,
) -> None:
    """Repeated stop levels cannot starve terminal evidence in either adapter."""

    first = adapter_reduce(
        TerminalHandoffProgress(),
        (TerminalHandoffEvent(kind=stop_kind),),
    )
    assert first is not None
    assert first.decision.action == stop_action

    second = adapter_reduce(
        first.progress,
        (
            TerminalHandoffEvent(kind=stop_kind),
            TerminalHandoffEvent(kind="channel_sealed"),
        ),
    )
    assert second is not None
    assert second.event.kind == "channel_sealed"
    assert second.decision.action == return_action


@pytest.mark.parametrize(
    "adapter_reduce",
    (
        HostTaskRunner._reduce_terminal_observations,
        AgentSession._reduce_terminal_observations,
    ),
    ids=("one-shot", "persistent-session"),
)
def test_both_adapters_consume_dead_producer_before_drain_expiry(
    adapter_reduce: Any,
) -> None:
    """A repeated dead-producer level cannot starve either adapter's deadline."""

    first = adapter_reduce(
        TerminalHandoffProgress(),
        (TerminalHandoffEvent(kind="producer_exited"),),
    )
    assert first is not None
    assert first.decision.action == "begin_drain"

    second = adapter_reduce(
        first.progress,
        (
            TerminalHandoffEvent(kind="producer_exited"),
            TerminalHandoffEvent(kind="drain_expired"),
        ),
    )
    assert second is not None
    assert second.event.kind == "drain_expired"
    assert second.decision.action == "return_protocol_failure"


def _terminal_outcome_sender(
    sender: Connection,
    outcome: RunnerOutcome,
) -> None:
    try:
        send_terminal_payload(sender, lambda: make_outcome_payload(outcome))
    finally:
        sender.close()


def _terminal_exit_without_send(_sender: Connection) -> None:
    os._exit(73)


class _DeferredFirstPollConnection:
    """Expose a real pipe only after one production adapter observation turn."""

    def __init__(self, receiver: Connection) -> None:
        self._receiver = receiver
        self.poll_calls = 0

    def poll(self, timeout: float = 0.0) -> bool:
        self.poll_calls += 1
        if self.poll_calls == 1:
            return False
        return self._receiver.poll(timeout)

    def recv_bytes(self) -> bytes:
        return self._receiver.recv_bytes()


class _FailingResponseReadConnection:
    """Fail the production response-read poll at the owner boundary."""

    def __init__(self, receiver: Connection) -> None:
        self._receiver = receiver

    def poll(self, timeout: float = 0.0) -> bool:
        del timeout
        raise OSError("session read failed")

    def recv_bytes(self) -> bytes:
        return self._receiver.recv_bytes()

    def close(self) -> None:
        self._receiver.close()


class _WindowsSealedResponseConnection:
    """Model Windows' broken-pipe signal after the writer closes."""

    def __init__(self, receiver: Connection) -> None:
        self._receiver = receiver

    def poll(self, timeout: float = 0.0) -> bool:
        del timeout
        raise BrokenPipeError(109, "The pipe has been ended")

    def recv_bytes(self) -> bytes:
        raise BrokenPipeError(109, "The pipe has been ended")

    def close(self) -> None:
        self._receiver.close()


def test_host_terminal_receive_failure_is_transport_failure() -> None:
    """A ready channel whose receive fails produces bounded transport evidence."""

    class DeadProcess:
        pid = None
        exitcode = 73

        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            del timeout

    class FailingReceiver:
        def poll(self, timeout: float = 0.0) -> bool:
            del timeout
            return True

        def recv_bytes(self) -> bytes:
            raise OSError("terminal receive failed")

    outcome = _build_function_host_runner(timeout=5.0)._run_one_shot_terminal_handoff(
        DeadProcess(),  # type: ignore[arg-type]
        FailingReceiver(),  # type: ignore[arg-type]
        worker_pid=None,
        runtime_handle=None,
        cancel_requested=None,
    )

    assert outcome.status == "error"
    assert outcome.error == "Worker result channel failed before a result was received"
    assert outcome.diagnostics is not None
    assert outcome.diagnostics["handoff_event"] == "transport_failed"
    assert "terminal payload receive failed" in outcome.diagnostics["message"]


def test_host_windows_broken_pipe_is_channel_seal() -> None:
    """Windows' ended-pipe signal preserves worker-exit diagnostics."""

    class DeadProcess:
        pid = None
        exitcode = 73

        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            del timeout

    raw_receiver, sender = multiprocessing.get_context("spawn").Pipe(duplex=False)
    receiver = _WindowsSealedResponseConnection(raw_receiver)
    try:
        outcome = _build_function_host_runner(
            timeout=5.0
        )._run_one_shot_terminal_handoff(
            DeadProcess(),  # type: ignore[arg-type]
            receiver,  # type: ignore[arg-type]
            worker_pid=None,
            runtime_handle=None,
            cancel_requested=None,
        )

        assert outcome.error == "Worker exited before returning a result (exit code 73)"
        assert outcome.returncode == 73
        assert outcome.diagnostics is not None
        assert outcome.diagnostics["handoff_event"] == "channel_sealed"
    finally:
        sender.close()
        receiver.close()


@pytest.mark.parametrize("frame", [b"42", b'{"type":', b'{"type":"outcome"}'])
def test_host_malformed_terminal_frame_is_transport_failure(frame: bytes) -> None:
    """Invalid JSON, root types and incomplete envelopes fail the response channel."""

    class DeadProcess:
        pid = None
        exitcode = 0

        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            del timeout

    ctx = multiprocessing.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    try:
        sender.send_bytes(frame)
        sender.close()

        outcome = _build_function_host_runner(
            timeout=5.0
        )._run_one_shot_terminal_handoff(
            DeadProcess(),  # type: ignore[arg-type]
            receiver,
            worker_pid=None,
            runtime_handle=None,
            cancel_requested=None,
        )

        assert outcome.status == "error"
        assert (
            outcome.error == "Worker result channel failed before a result was received"
        )
        assert outcome.diagnostics is not None
        assert outcome.diagnostics["handoff_event"] == "transport_failed"
    finally:
        sender.close()
        receiver.close()


def test_host_blocking_poll_failure_is_consumed_as_next_turn_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blocking-poll failure is stored and reduced on the following turn."""

    class LiveProcess:
        pid = None
        exitcode = None

        def __init__(self) -> None:
            self.alive = True

        def is_alive(self) -> bool:
            return self.alive

        def join(self, timeout: float | None = None) -> None:
            del timeout

    ctx = multiprocessing.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    real_wait = host_module.wait_terminal_payload
    failure = {"raised": False}

    def failing_first_wait(
        wait_receiver: Connection,
        *,
        producer_sentinel: int | None,
        timeout: float | None,
    ) -> bool:
        if not failure["raised"]:
            failure["raised"] = True
            raise OSError("blocking terminal poll failed")
        return real_wait(
            wait_receiver,
            producer_sentinel=producer_sentinel,
            timeout=timeout,
        )

    process = LiveProcess()
    runner = _build_function_host_runner(timeout=5.0)
    monkeypatch.setattr(host_module, "wait_terminal_payload", failing_first_wait)
    monkeypatch.setattr(
        runner,
        "_stop_process",
        lambda _process: setattr(process, "alive", False),
    )
    try:
        outcome = runner._run_one_shot_terminal_handoff(
            process,  # type: ignore[arg-type]
            receiver,
            worker_pid=None,
            runtime_handle=None,
            cancel_requested=None,
        )

        assert failure["raised"] is True
        assert outcome.status == "error"
        assert outcome.diagnostics is not None
        assert outcome.diagnostics["handoff_event"] == "transport_failed"
        assert outcome.diagnostics["message"] == "blocking terminal poll failed"
    finally:
        sender.close()
        receiver.close()


def test_one_shot_stop_after_begin_drain_preserves_first_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later accepted stop cannot replace an ordinary drain deadline."""

    clock = {"now": 0.0}
    cancel_checks = 0

    class DeadProcess:
        pid = None
        exitcode = 0

        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            del timeout

    class EmptyReceiver:
        def poll(self, timeout: float = 0.0) -> bool:
            clock["now"] += timeout
            return False

    def cancel_after_drain() -> bool:
        nonlocal cancel_checks
        cancel_checks += 1
        return cancel_checks > 2

    runner = _build_function_host_runner(timeout=5.0)
    monkeypatch.setattr(host_module.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(host_module, "wait_terminal_payload", _fake_clock_wait(clock))
    monkeypatch.setattr(
        runner,
        "_stop_process",
        lambda _process: clock.__setitem__("now", clock["now"] + 0.01),
    )

    outcome = runner._run_one_shot_terminal_handoff(
        DeadProcess(),  # type: ignore[arg-type]
        EmptyReceiver(),  # type: ignore[arg-type]
        worker_pid=None,
        runtime_handle=None,
        cancel_requested=cancel_after_drain,
    )

    assert outcome.status == "cancelled"
    assert cancel_checks == 3
    assert clock["now"] == pytest.approx(
        host_module.TERMINAL_HANDOFF_DRAIN_TIMEOUT_SECONDS
    )


@pytest.mark.parametrize(
    ("exit_after_join", "expected_error", "expected_returncode"),
    (
        (True, "Worker exited before returning a result (exit code 73)", 73),
        (False, "Worker result channel failed before a result was received", None),
    ),
)
def test_host_channel_seal_refines_exit_after_bounded_join(
    exit_after_join: bool,
    expected_error: str,
    expected_returncode: int | None,
) -> None:
    """A seal gets one bounded join before the host reports a numeric exit."""

    class JoiningProcess:
        pid = None

        def __init__(self) -> None:
            self.alive = True
            self.exitcode: int | None = None
            self.join_timeouts: list[float | None] = []

        def is_alive(self) -> bool:
            return self.alive

        def join(self, timeout: float | None = None) -> None:
            self.join_timeouts.append(timeout)
            if exit_after_join:
                self.alive = False
                self.exitcode = 73

        def terminate(self) -> None:
            self.alive = False

        def kill(self) -> None:
            self.alive = False

    class SealedReceiver:
        def poll(self, timeout: float = 0.0) -> bool:
            del timeout
            return True

        def recv_bytes(self) -> bytes:
            raise EOFError

    process = JoiningProcess()
    outcome = _build_function_host_runner(timeout=5.0)._run_one_shot_terminal_handoff(
        process,  # type: ignore[arg-type]
        SealedReceiver(),  # type: ignore[arg-type]
        worker_pid=None,
        runtime_handle=None,
        cancel_requested=None,
    )

    assert (
        process.join_timeouts[0] == host_module.TERMINAL_HANDOFF_DRAIN_TIMEOUT_SECONDS
    )
    assert (
        process.join_timeouts.count(host_module.TERMINAL_HANDOFF_DRAIN_TIMEOUT_SECONDS)
        == 1
    )
    assert outcome.status == "error"
    assert outcome.error == expected_error
    assert outcome.returncode == expected_returncode
    assert outcome.diagnostics is not None
    assert outcome.diagnostics["handoff_event"] == "channel_sealed"


def test_agent_session_response_read_failure_is_transport_failure() -> None:
    """A session response-read failure invalidates with transport evidence."""

    session = _spawn_agent_session_for_target(_agent_session_ready_then_hang_worker)
    try:
        session.wait_ready(timeout=5.0)
        session._response_receiver = _FailingResponseReadConnection(  # type: ignore[assignment]
            session._response_receiver
        )

        result = session.execute("hello")

        assert result.status == "error"
        assert (
            result.error == "Worker result channel failed before a result was received"
        )
        assert result.diagnostics is not None
        assert result.diagnostics["handoff_event"] == "transport_failed"
        assert "session read failed" in result.diagnostics["message"]
        with pytest.raises(RuntimeError, match="Agent session is closed"):
            session.execute("again")
    finally:
        session.close()


def test_agent_session_windows_broken_pipe_is_channel_seal() -> None:
    """Windows' ended-pipe signal cannot become a transport failure."""

    session = _spawn_agent_session_for_target(_agent_session_ready_then_hang_worker)
    try:
        session.wait_ready(timeout=5.0)
        session._response_receiver = _WindowsSealedResponseConnection(  # type: ignore[assignment]
            session._response_receiver
        )

        result = session.execute("hello")

        assert result.status == "error"
        assert result.diagnostics is not None
        assert result.diagnostics["handoff_event"] == "channel_sealed"
        with pytest.raises(RuntimeError, match="Agent session is closed"):
            session.execute("again")
    finally:
        session.close()


def _fake_clock_wait(clock: dict[str, float]) -> Any:
    """Fake handoff wait that advances a fake clock by the requested budget.

    It replaces the session's real wait boundary, ``wait_terminal_payload``,
    one-for-one with the fake receiver ``poll(timeout)`` these fake-clock tests
    used before the wait joined the producer sentinel.
    """

    def wait(
        _receiver: object,
        *,
        producer_sentinel: int | None,
        timeout: float | None,
    ) -> bool:
        del producer_sentinel
        assert timeout is not None
        clock["now"] += timeout
        return False

    return wait


def test_agent_session_blocking_poll_failure_is_consumed_as_next_turn_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A session blocking-wait failure is reduced on the following turn."""

    session = _spawn_agent_session_for_target(_agent_session_ready_then_hang_worker)
    real_wait = sessions_module.wait_terminal_payload
    failure = {"raised": False}

    def failing_first_wait(
        receiver: Connection,
        *,
        producer_sentinel: int | None,
        timeout: float | None,
    ) -> bool:
        if not failure["raised"]:
            failure["raised"] = True
            raise OSError("blocking terminal poll failed")
        return real_wait(receiver, producer_sentinel=producer_sentinel, timeout=timeout)

    monkeypatch.setattr(sessions_module, "wait_terminal_payload", failing_first_wait)
    try:
        session.wait_ready(timeout=5.0)

        result = session.execute("hello")

        assert failure["raised"] is True
        assert result.status == "error"
        assert result.diagnostics is not None
        assert result.diagnostics["handoff_event"] == "transport_failed"
        assert result.diagnostics["message"] == "blocking terminal poll failed"
        with pytest.raises(RuntimeError, match="Agent session is closed"):
            session.execute("again")
    finally:
        session.close()


def test_agent_session_producer_exit_starts_bounded_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ordinary producer exit starts one drain whose deadline bounds the wait."""

    clock = {"now": 0.0}

    class DeadProcess:
        pid = None
        exitcode = 0

        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            del timeout

        def close(self) -> None:
            return

    class RequestQueue:
        def put(self, _payload: object) -> None:
            return

        def cancel_join_thread(self) -> None:
            return

        def close(self) -> None:
            return

    class EmptyReceiver:
        def poll(self, timeout: float = 0.0) -> bool:
            clock["now"] += timeout
            return False

        def close(self) -> None:
            return

    session = AgentSession(
        DeadProcess(),  # type: ignore[arg-type]
        RequestQueue(),  # type: ignore[arg-type]
        EmptyReceiver(),  # type: ignore[arg-type]
        monitor=None,
        timeout=None,
    )
    monkeypatch.setattr(sessions_module.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(
        sessions_module, "wait_terminal_payload", _fake_clock_wait(clock)
    )

    result = session.execute("hello")

    assert result.status == "error"
    assert result.diagnostics is not None
    assert result.diagnostics["handoff_event"] == "drain_expired"
    assert clock["now"] == sessions_module.TERMINAL_HANDOFF_DRAIN_TIMEOUT_SECONDS


def test_session_stop_after_begin_drain_preserves_first_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later session stop cannot replace an ordinary drain deadline."""

    clock = {"now": 0.0}
    cancel_checks = 0

    class DeadProcess:
        pid = None
        exitcode = 0

        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            del timeout

        def close(self) -> None:
            return

    class RequestQueue:
        def put(self, _payload: object) -> None:
            return

        def cancel_join_thread(self) -> None:
            return

        def close(self) -> None:
            return

    class EmptyReceiver:
        def poll(self, timeout: float = 0.0) -> bool:
            clock["now"] += timeout
            return False

        def close(self) -> None:
            return

    def cancel_after_drain() -> bool:
        nonlocal cancel_checks
        cancel_checks += 1
        return cancel_checks > 2

    session = AgentSession(
        DeadProcess(),  # type: ignore[arg-type]
        RequestQueue(),  # type: ignore[arg-type]
        EmptyReceiver(),  # type: ignore[arg-type]
        monitor=None,
        timeout=None,
    )
    monkeypatch.setattr(sessions_module.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(
        sessions_module, "wait_terminal_payload", _fake_clock_wait(clock)
    )
    monkeypatch.setattr(
        session,
        "terminate",
        lambda **_kwargs: clock.__setitem__("now", clock["now"] + 0.01),
    )

    result = session.execute("hello", cancel_requested=cancel_after_drain)

    assert result.status == "cancelled"
    assert cancel_checks == 3
    assert clock["now"] == pytest.approx(
        sessions_module.TERMINAL_HANDOFF_DRAIN_TIMEOUT_SECONDS
    )


def test_real_pipe_exit_then_outcome_uses_production_handoff_driver() -> None:
    """A withheld first read proves exit then outcome converges on the outcome."""

    ctx = multiprocessing.get_context("spawn")
    ctx.set_executable(sys.executable)
    receiver, sender = ctx.Pipe(duplex=False)
    worker_outcome = RunnerOutcome(
        status="ok",
        value="hello",
        error=None,
        stdout=None,
        stderr=None,
        returncode=0,
        duration=0.0,
    )
    process = ctx.Process(
        target=_terminal_outcome_sender,
        args=(sender, worker_outcome),
    )
    process.start()
    sender.close()
    deferred_receiver = _DeferredFirstPollConnection(receiver)
    try:
        process.join(timeout=5.0)
        assert process.exitcode == 0

        outcome = _build_function_host_runner(
            timeout=5.0
        )._run_one_shot_terminal_handoff(
            process,
            deferred_receiver,  # type: ignore[arg-type]
            worker_pid=process.pid,
            runtime_handle=None,
            cancel_requested=None,
        )

        assert deferred_receiver.poll_calls >= 2
        assert outcome.status == "ok"
        assert isinstance(outcome.value, PreparedTaskResult)
        assert project_task_result(outcome.value) == "hello"
    finally:
        receiver.close()
        if process.is_alive():
            process.kill()
            process.join(timeout=1.0)
        process.close()


def _terminal_write_failure_sender(
    sender: Connection,
    status_sender: Connection,
) -> None:
    try:
        send_terminal_payload(sender, lambda: {"result": "unreachable"})
    except TerminalHandoffTransportError as exc:
        status_sender.send(str(exc))
    finally:
        sender.close()
        status_sender.close()


def test_real_pipe_write_failure_reaches_bounded_parent_transport_verdict() -> None:
    """A real broken pipe writes once and the adapter returns a generic failure."""

    ctx = multiprocessing.get_context("spawn")
    ctx.set_executable(sys.executable)
    receiver, sender = ctx.Pipe(duplex=False)
    status_receiver, status_sender = ctx.Pipe(duplex=False)
    process = ctx.Process(
        target=_terminal_write_failure_sender,
        args=(sender, status_sender),
    )
    process.start()
    sender.close()
    status_sender.close()
    receiver.close()
    try:
        assert status_receiver.poll(5.0)
        assert "delivery failed" in status_receiver.recv()
        process.join(timeout=5.0)
        assert process.exitcode == 0

        outcome = _build_function_host_runner(
            timeout=5.0
        )._run_one_shot_terminal_handoff(
            process,
            receiver,
            worker_pid=process.pid,
            runtime_handle=None,
            cancel_requested=None,
        )

        assert outcome.status == "error"
        assert (
            outcome.error == "Worker result channel failed before a result was received"
        )
        assert outcome.diagnostics is not None
        assert outcome.diagnostics["handoff_event"] == "transport_failed"
    finally:
        status_receiver.close()
        if process.is_alive():
            process.kill()
        process.join(timeout=1.0)
        process.close()


def test_one_shot_leaked_sender_reaches_bounded_drain_expiry() -> None:
    """A retained writer cannot turn producer exit into an indefinite wait."""

    ctx = multiprocessing.get_context("spawn")
    ctx.set_executable(sys.executable)
    receiver, sender = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_terminal_exit_without_send, args=(sender,))
    runner = _build_function_host_runner(timeout=5.0)
    process.start()
    try:
        # [EXEC.7] bounds draining after producer exit, not interpreter startup.
        process.join(timeout=5.0)
        assert process.exitcode == 73
        started = time.monotonic()
        outcome = runner._run_one_shot_terminal_handoff(
            process,
            receiver,
            worker_pid=process.pid,
            runtime_handle=None,
            cancel_requested=None,
        )

        assert time.monotonic() - started < 2.0
        assert outcome.status == "error"
        assert (
            outcome.error == "Worker result channel failed before a result was received"
        )
        assert outcome.diagnostics is not None
        assert outcome.diagnostics["handoff_event"] == "drain_expired"
    finally:
        sender.close()
        receiver.close()
        if process.is_alive():
            process.kill()
        process.join(timeout=1.0)
        process.close()


def test_host_runner_normalizes_nested_frozen_args_before_spawn() -> None:
    """Nested immutable TaskSpec containers cannot break worker bootstrap."""

    runner = _build_function_host_runner(
        timeout=5.0,
        function_target="json:dumps",
        args=[FrozenList([1, 2])],
    )

    outcome = runner.run_with_hooks(None)

    assert outcome.status == "ok"
    assert isinstance(outcome.value, PreparedTaskResult)
    assert project_task_result(outcome.value) == "[1, 2]"


def test_host_runner_accepts_child_local_json_value() -> None:
    """JSON-compatible results must not require importing the child's class."""

    runner = _build_function_host_runner(
        timeout=5.0,
        function_target="tests.tasks.sample_targets:return_child_local_mapping",
    )
    outcome = runner.run_with_hooks(None)
    assert outcome.status == "ok", outcome.error
    assert isinstance(outcome.value, PreparedTaskResult)
    assert outcome.value.kind == "value"
    assert outcome.value.outputs[0].format == "json"
    assert outcome.value.outputs[0].text == '{"answer": "child-local"}'
    assert project_task_result(outcome.value) == {"answer": "child-local"}


def test_host_runner_converts_child_local_value_only_in_worker(tmp_path: Path) -> None:
    """Unpicklable application text is converted exactly once in the child."""

    evidence = tmp_path / "conversion-pids"
    worker_pids: list[int | None] = []
    runner = _build_function_host_runner(
        timeout=5.0,
        function_target="tests.tasks.sample_targets:return_child_local_text",
        args=[str(evidence)],
    )

    outcome = runner.run_with_hooks(None, on_worker_started=worker_pids.append)

    assert outcome.status == "ok", outcome.error
    assert isinstance(outcome.value, PreparedTaskResult)
    assert project_task_result(outcome.value) == "child-local-text"
    assert len(worker_pids) == 1
    assert worker_pids[0] is not None
    assert worker_pids[0] != os.getpid()
    assert evidence.read_text(encoding="utf-8") == f"str:{worker_pids[0]}\n"


@pytest.mark.parametrize(
    ("target", "cause"),
    [
        ("return_cyclic_result", "Circular reference detected"),
        ("return_unprintable_result", "UnprintableError"),
    ],
)
def test_host_runner_reports_prewrite_result_serialization_failure(
    target: str, cause: str
) -> None:
    """A failed public conversion has bounded diagnostics even when str(exc) fails."""

    runner = _build_function_host_runner(
        timeout=5.0,
        function_target=f"tests.tasks.sample_targets:{target}",
    )

    outcome = runner.run_with_hooks(None)

    assert outcome.status == "error"
    assert outcome.error is not None
    assert outcome.error.startswith(
        "Task returned a value that Weft could not serialize: "
    )
    assert len(outcome.error) <= 550
    assert outcome.diagnostics is not None
    assert outcome.diagnostics["phase"] == "result_serialization"
    assert cause in outcome.error


@pytest.mark.parametrize(
    ("outputs", "expected_text"),
    [
        ([], []),
        ([{"answer": 42}], ['{"answer": 42}']),
        (["first", {"answer": 42}, None], ["first", '{"answer": 42}', "null"]),
    ],
)
def test_function_host_preserves_agent_result_cardinality(
    outputs: list[Any], expected_text: list[str]
) -> None:
    """Result type, not task target type, determines agent output cardinality."""

    runner = _build_function_host_runner(
        timeout=5.0,
        function_target="tests.tasks.sample_targets:return_agent_execution_result",
        args=[outputs],
    )

    outcome = runner.run_with_hooks(None)

    assert outcome.status == "ok", outcome.error
    assert isinstance(outcome.value, PreparedTaskResult)
    assert outcome.value.kind == "agent"
    assert [output.text for output in outcome.value.outputs] == expected_text
    projected = project_task_result(outcome.value)
    assert isinstance(projected, AgentExecutionResult)
    assert projected.outputs == tuple(outputs)
    assert projected.metadata == {"source": "function"}


def test_host_runner_large_result_exceeds_pipe_buffer_without_deadlock() -> None:
    """The parent drains framed result bytes while a live producer writes them."""

    size = 4_194_304
    runner = _build_function_host_runner(
        timeout=10.0,
        function_target="tests.tasks.sample_targets:large_output",
        args=[size],
    )

    outcome = runner.run_with_hooks(None)

    assert outcome.status == "ok"
    assert isinstance(outcome.value, PreparedTaskResult)
    assert outcome.value.outputs[0].text == "x" * size


def test_function_timeout_reports_timeout_when_no_result_is_ready(
    tmp_path: Path,
) -> None:
    """A live function with no ready result still reaches the timeout verdict."""

    runner = _build_function_host_runner(
        timeout=0.05,
        function_target="tests.tasks.sample_targets:wait_for_file",
        args=[str(tmp_path / "absent")],
    )

    outcome = runner.run_with_hooks(None)

    assert outcome.status == "timeout"


def test_one_shot_cancel_beats_ready_success_in_same_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An accepted cancel remains authoritative after a ready success is decoded."""

    class Process:
        pid = None
        exitcode = 0

        def __init__(self) -> None:
            self.alive = True

        def is_alive(self) -> bool:
            return self.alive

        def join(self, timeout: float | None = None) -> None:
            del timeout

    process = Process()
    runner = _build_function_host_runner(timeout=5.0)
    monkeypatch.setattr(
        runner, "_stop_process", lambda _process: setattr(process, "alive", False)
    )
    receiver, sender = multiprocessing.get_context("spawn").Pipe(duplex=False)
    try:
        send_terminal_payload(
            sender,
            lambda: make_outcome_payload(
                RunnerOutcome(
                    status="ok",
                    value="ready success",
                    error=None,
                    stdout=None,
                    stderr=None,
                    returncode=0,
                    duration=0.0,
                )
            ),
        )
        sender.close()

        outcome = runner._run_one_shot_terminal_handoff(
            process,  # type: ignore[arg-type]
            receiver,
            worker_pid=None,
            runtime_handle=None,
            cancel_requested=lambda: True,
        )

        assert process.alive is False
        assert outcome.status == "cancelled"
        assert outcome.value is None
    finally:
        sender.close()
        receiver.close()


def test_one_shot_stop_effect_cannot_reset_absolute_drain_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Time spent stopping is charged against the first accepted drain bound."""

    clock = {"now": 0.0}

    class Process:
        pid = None
        exitcode = 0

        def __init__(self) -> None:
            self.alive = True

        def is_alive(self) -> bool:
            return self.alive

        def join(self, timeout: float | None = None) -> None:
            del timeout

    class EmptyReceiver:
        def poll(self, timeout: float = 0.0) -> bool:
            clock["now"] += timeout
            return False

    process = Process()
    runner = _build_function_host_runner(timeout=5.0)
    monkeypatch.setattr(host_module.time, "monotonic", lambda: clock["now"])

    def delayed_stop(_process: Process) -> None:
        clock["now"] += 1.0
        process.alive = False

    monkeypatch.setattr(runner, "_stop_process", delayed_stop)

    outcome = runner._run_one_shot_terminal_handoff(
        process,  # type: ignore[arg-type]
        EmptyReceiver(),  # type: ignore[arg-type]
        worker_pid=None,
        runtime_handle=None,
        cancel_requested=lambda: True,
    )

    assert outcome.status == "cancelled"
    assert clock["now"] == 1.0


def test_session_stop_effect_cannot_reset_absolute_drain_deadline(  # noqa: C901 approved [TS-3.1] [RUFF-SUP-237] exception
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persistent cleanup time cannot extend the accepted stop deadline."""

    clock = {"now": 0.0}

    class Process:
        pid = None
        exitcode = 0

        def __init__(self) -> None:
            self.alive = True

        def is_alive(self) -> bool:
            return self.alive

        def join(self, timeout: float | None = None) -> None:
            del timeout

        def close(self) -> None:
            return

    class RequestQueue:
        def put(self, _payload: object) -> None:
            return

        def cancel_join_thread(self) -> None:
            return

        def close(self) -> None:
            return

    class EmptyReceiver:
        def poll(self, timeout: float = 0.0) -> bool:
            clock["now"] += timeout
            return False

        def close(self) -> None:
            return

    process = Process()
    session = AgentSession(
        process,  # type: ignore[arg-type]
        RequestQueue(),  # type: ignore[arg-type]
        EmptyReceiver(),  # type: ignore[arg-type]
        monitor=None,
        timeout=None,
    )
    monkeypatch.setattr(sessions_module.time, "monotonic", lambda: clock["now"])

    def delayed_terminate(*, deadline: float | None = None) -> None:
        del deadline
        clock["now"] += 1.0
        process.alive = False

    monkeypatch.setattr(session, "terminate", delayed_terminate)

    result = session.execute("hello", cancel_requested=lambda: True)

    assert result.status == "cancelled"
    assert clock["now"] == 1.0


class _StartFailureEndpoint:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _StartFailureQueue:
    def __init__(self) -> None:
        self.closed = False
        self.joined = False

    def close(self) -> None:
        self.closed = True

    def join_thread(self) -> None:
        self.joined = True


class _StartFailureProcess:
    def __init__(self) -> None:
        self.closed = False

    def start(self) -> None:
        raise RuntimeError("spawn failed")

    def close(self) -> None:
        self.closed = True


class _StartFailureContext:
    def __init__(self) -> None:
        self.receiver = _StartFailureEndpoint()
        self.sender = _StartFailureEndpoint()
        self.queue = _StartFailureQueue()
        self.process = _StartFailureProcess()

    def Pipe(  # Mirrors the multiprocessing context API.
        self,
        *,
        duplex: bool,
    ) -> tuple[_StartFailureEndpoint, _StartFailureEndpoint]:
        assert duplex is False
        return self.receiver, self.sender

    def Queue(self) -> _StartFailureQueue:
        return self.queue

    def Process(self, **_kwargs: Any) -> _StartFailureProcess:
        return self.process


class _StartedProcess(_StartFailureProcess):
    pid: int | None = None

    def __init__(self) -> None:
        super().__init__()
        self.alive = False
        self.joined = False

    def start(self) -> None:
        self.alive = True

    def is_alive(self) -> bool:
        return self.alive

    def join(self, timeout: float | None = None) -> None:
        del timeout
        self.joined = True

    def terminate(self) -> None:
        self.alive = False

    def kill(self) -> None:
        self.alive = False


class _StartedContext(_StartFailureContext):
    process: _StartedProcess

    def __init__(self) -> None:
        super().__init__()
        self.process = _StartedProcess()


def test_host_queue_cleanup_reports_each_failure_and_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[str] = []

    class Queue:
        def close(self) -> None:
            calls.append("close")
            raise RuntimeError("sensitive queue close failure")

        def join_thread(self) -> None:
            calls.append("join")
            raise RuntimeError("sensitive queue join failure")

    caplog.set_level(logging.WARNING, logger="weft.core.runners.host")

    HostTaskRunner._close_mp_queue(Queue())  # type: ignore[arg-type]

    assert calls == ["close", "join"]
    assert [record.getMessage() for record in caplog.records] == [
        "Failed to close host runner multiprocessing queue",
        "Failed to join host runner queue thread",
    ]
    assert all(record.exc_info is None for record in caplog.records)
    assert "sensitive" not in caplog.text


@pytest.mark.parametrize("error_type", [OSError, ValueError])
def test_host_process_handle_cleanup_reports_supported_failure(
    caplog: pytest.LogCaptureFixture,
    error_type: type[Exception],
) -> None:
    class Process:
        def close(self) -> None:
            raise error_type("sensitive process-handle close failure")

    caplog.set_level(logging.WARNING, logger="weft.core.runners.host")

    HostTaskRunner._close_process_handle(Process())  # type: ignore[arg-type]

    assert [record.getMessage() for record in caplog.records] == [
        "Failed to close host runner process handle"
    ]
    assert all(record.exc_info is None for record in caplog.records)
    assert "sensitive" not in caplog.text


def test_host_process_handle_cleanup_propagates_unexpected_failure() -> None:
    class Process:
        def close(self) -> None:
            raise RuntimeError("unexpected process-handle defect")

    with pytest.raises(RuntimeError, match="unexpected process-handle defect"):
        HostTaskRunner._close_process_handle(Process())  # type: ignore[arg-type]


def test_host_process_stop_reports_join_failure_and_escalates(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class Process:
        pid = 123

        def __init__(self) -> None:
            self.alive = True
            self.join_attempts = 0

        def is_alive(self) -> bool:
            calls.append("is_alive")
            return self.alive

        def join(self, timeout: float | None = None) -> None:
            assert timeout == 0.2
            calls.append("join")
            self.join_attempts += 1
            if self.join_attempts == 1:
                raise OSError("sensitive process wait failure")

        def terminate(self) -> None:
            calls.append("terminate")
            self.alive = False

        def kill(self) -> None:
            calls.append("kill")
            self.alive = False

    monkeypatch.setattr(
        host_module,
        "terminate_verified_process_tree",
        lambda pid, create_time, *, kill, timeout: calls.append(
            f"terminate_tree:{pid}:{timeout}"
        ),
    )
    caplog.set_level(logging.WARNING, logger="weft.core.runners.host")

    HostTaskRunner._stop_process(Process())  # type: ignore[arg-type]

    assert calls == [
        "is_alive",
        "terminate_tree:123:0.2",
        "join",
        "is_alive",
        "terminate",
        "join",
        "is_alive",
    ]
    assert [record.getMessage() for record in caplog.records] == [
        "Failed to join host runner process before escalation"
    ]
    assert all(record.exc_info is None for record in caplog.records)
    assert "sensitive" not in caplog.text


def test_host_process_stop_propagates_unexpected_join_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Process:
        pid = 123

        def is_alive(self) -> bool:
            return True

        def join(self, timeout: float | None = None) -> None:
            del timeout
            raise RuntimeError("unexpected process wait defect")

    monkeypatch.setattr(
        host_module, "terminate_verified_process_tree", lambda *_args, **_kwargs: False
    )

    with pytest.raises(RuntimeError, match="unexpected process wait defect"):
        HostTaskRunner._stop_process(Process())  # type: ignore[arg-type]


def test_agent_worker_reports_session_close_failure_and_closes_ipc(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class Session:
        def close(self) -> None:
            calls.append("session.close")
            raise RuntimeError("sensitive agent runtime cleanup failure")

    class RequestQueue:
        def get(self) -> dict[str, Any]:
            return make_stop_request()

        def close(self) -> None:
            calls.append("request_queue.close")

    class ResponseSender:
        def close(self) -> None:
            calls.append("response_sender.close")

    monkeypatch.setattr(
        host_module,
        "start_agent_runtime_session",
        lambda *_args, **_kwargs: Session(),
    )
    monkeypatch.setattr(
        host_module,
        "send_terminal_payload",
        lambda *_args, **_kwargs: True,
    )
    caplog.set_level(logging.WARNING, logger="weft.core.runners.host")

    host_module._agent_session_worker_entry(
        {"agent": {"runtime": "llm"}},
        RequestQueue(),  # type: ignore[arg-type]
        ResponseSender(),  # type: ignore[arg-type]
    )

    assert calls[0] == "session.close"
    assert len(calls) == 3
    assert set(calls[1:]) == {"request_queue.close", "response_sender.close"}
    assert [record.getMessage() for record in caplog.records] == [
        "Failed to close host agent runtime session"
    ]
    assert all(record.exc_info is None for record in caplog.records)
    assert "sensitive" not in caplog.text


def test_agent_worker_maps_work_item_failure_and_closes_session_and_ipc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExtensionFailure(Exception):
        pass

    calls: list[str] = []

    class Session:
        def execute(self, _work_item: object) -> None:
            calls.append("session.execute")
            raise ExtensionFailure("work item detail")

        def close(self) -> None:
            calls.append("session.close")

    class RequestQueue:
        def __init__(self) -> None:
            self._requests = iter([make_execute_request("hello")])

        def get(self) -> dict[str, Any]:
            return next(self._requests)

        def close(self) -> None:
            calls.append("request_queue.close")

    class ResponseSender:
        def close(self) -> None:
            calls.append("response_sender.close")

    responses: list[dict[str, Any]] = []
    monkeypatch.setattr(
        host_module,
        "start_agent_runtime_session",
        lambda *_args, **_kwargs: Session(),
    )
    monkeypatch.setattr(
        host_module,
        "send_terminal_payload",
        lambda _sender, payload_factory, **_kwargs: record_and_return(
            responses, payload_factory(), True
        ),
    )

    host_module._agent_session_worker_entry(
        {"agent": {"runtime": "llm"}},
        RequestQueue(),  # type: ignore[arg-type]
        ResponseSender(),  # type: ignore[arg-type]
    )

    assert [response["type"] for response in responses] == [
        "booted",
        "ready",
        "result",
    ]
    assert responses[-1]["status"] == "error"
    assert "ExtensionFailure: work item detail" in responses[-1]["error"]
    assert responses[-1]["diagnostics"]["phase"] == "execute"
    assert responses[-1]["diagnostics"]["exception_type"] == "ExtensionFailure"
    assert calls[:2] == ["session.execute", "session.close"]
    assert len(calls[2:]) == 2
    assert Counter(calls[2:]) == Counter(
        {"request_queue.close": 1, "response_sender.close": 1}
    )


def test_agent_worker_maps_post_ready_boundary_failure_to_result_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExtensionFailure(Exception):
        pass

    calls: list[str] = []

    class Session:
        def close(self) -> None:
            calls.append("session.close")

    class RequestQueue:
        def get(self) -> dict[str, Any]:
            raise ExtensionFailure("request transport detail")

        def close(self) -> None:
            calls.append("request_queue.close")

    class ResponseSender:
        def close(self) -> None:
            calls.append("response_sender.close")

    responses: list[dict[str, Any]] = []
    monkeypatch.setattr(
        host_module,
        "start_agent_runtime_session",
        lambda *_args, **_kwargs: Session(),
    )
    monkeypatch.setattr(
        host_module,
        "send_terminal_payload",
        lambda _sender, payload_factory, **_kwargs: record_and_return(
            responses, payload_factory(), True
        ),
    )

    host_module._agent_session_worker_entry(
        {"agent": {"runtime": "llm"}},
        RequestQueue(),  # type: ignore[arg-type]
        ResponseSender(),  # type: ignore[arg-type]
    )

    assert [response["type"] for response in responses] == [
        "booted",
        "ready",
        "result",
    ]
    assert responses[-1]["status"] == "error"
    assert "ExtensionFailure: request transport detail" in responses[-1]["error"]
    assert responses[-1]["diagnostics"]["phase"] == "execute"
    assert responses[-1]["diagnostics"]["exception_type"] == "ExtensionFailure"
    assert calls[0] == "session.close"
    assert len(calls[1:]) == 2
    assert Counter(calls[1:]) == Counter(
        {"request_queue.close": 1, "response_sender.close": 1}
    )


def test_agent_worker_propagates_non_exception_work_item_failure_identity_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FatalSignal(BaseException):
        pass

    fatal = FatalSignal("stop work item")
    calls: list[str] = []

    class Session:
        def execute(self, _work_item: object) -> None:
            calls.append("session.execute")
            raise fatal

        def close(self) -> None:
            calls.append("session.close")

    class RequestQueue:
        def get(self) -> dict[str, Any]:
            return make_execute_request("hello")

        def close(self) -> None:
            calls.append("request_queue.close")

    class ResponseSender:
        def close(self) -> None:
            calls.append("response_sender.close")

    responses: list[dict[str, Any]] = []
    monkeypatch.setattr(
        host_module,
        "start_agent_runtime_session",
        lambda *_args, **_kwargs: Session(),
    )
    monkeypatch.setattr(
        host_module,
        "send_terminal_payload",
        lambda _sender, payload_factory, **_kwargs: record_and_return(
            responses, payload_factory(), True
        ),
    )

    with pytest.raises(FatalSignal) as caught:
        host_module._agent_session_worker_entry(
            {"agent": {"runtime": "llm"}},
            RequestQueue(),  # type: ignore[arg-type]
            ResponseSender(),  # type: ignore[arg-type]
        )

    assert caught.value is fatal
    assert [response["type"] for response in responses] == ["booted", "ready"]
    assert calls[:2] == ["session.execute", "session.close"]
    assert len(calls[2:]) == 2
    assert Counter(calls[2:]) == Counter(
        {"request_queue.close": 1, "response_sender.close": 1}
    )


def test_agent_worker_maps_startup_failure_and_closes_ipc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExtensionFailure(Exception):
        pass

    calls: list[str] = []

    class RequestQueue:
        def close(self) -> None:
            calls.append("request_queue.close")

    class ResponseSender:
        def close(self) -> None:
            calls.append("response_sender.close")

    responses: list[dict[str, Any]] = []

    def fail_start(*_args: object, **_kwargs: object) -> None:
        raise ExtensionFailure("startup detail")

    monkeypatch.setattr(host_module, "start_agent_runtime_session", fail_start)
    monkeypatch.setattr(
        host_module,
        "send_terminal_payload",
        lambda _sender, payload_factory, **_kwargs: record_and_return(
            responses, payload_factory(), True
        ),
    )

    host_module._agent_session_worker_entry(
        {"agent": {"runtime": "llm"}},
        RequestQueue(),  # type: ignore[arg-type]
        ResponseSender(),  # type: ignore[arg-type]
    )

    assert [response["type"] for response in responses] == [
        "booted",
        "startup_error",
    ]
    assert "ExtensionFailure: startup detail" in responses[-1]["error"]
    assert responses[-1]["diagnostics"]["phase"] == "runtime_startup"
    assert responses[-1]["diagnostics"]["exception_type"] == "ExtensionFailure"
    assert len(calls) == 2
    assert Counter(calls) == Counter(
        {"request_queue.close": 1, "response_sender.close": 1}
    )


def test_agent_worker_propagates_non_exception_startup_failure_identity_and_closes_ipc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FatalSignal(BaseException):
        pass

    fatal = FatalSignal("stop worker")
    calls: list[str] = []

    class RequestQueue:
        def close(self) -> None:
            calls.append("request_queue.close")

    class ResponseSender:
        def close(self) -> None:
            calls.append("response_sender.close")

    def fail_start(*_args: object, **_kwargs: object) -> None:
        raise fatal

    monkeypatch.setattr(host_module, "start_agent_runtime_session", fail_start)
    monkeypatch.setattr(host_module, "send_terminal_payload", lambda *_a, **_k: True)

    with pytest.raises(FatalSignal) as caught:
        host_module._agent_session_worker_entry(
            {"agent": {"runtime": "llm"}},
            RequestQueue(),  # type: ignore[arg-type]
            ResponseSender(),  # type: ignore[arg-type]
        )

    assert caught.value is fatal
    assert len(calls) == 2
    assert Counter(calls) == Counter(
        {"request_queue.close": 1, "response_sender.close": 1}
    )


def test_one_shot_spawn_failure_closes_both_response_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed worker start releases every private process and pipe handle."""

    context = _StartFailureContext()
    runner = _build_function_host_runner(timeout=5.0)
    monkeypatch.setattr(runner, "_ctx", context)

    with pytest.raises(RuntimeError, match="spawn failed"):
        runner.run_with_hooks(None)

    assert context.receiver.closed is True
    assert context.sender.closed is True
    assert context.process.closed is True


def test_agent_session_spawn_failure_closes_queue_and_response_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed session startup releases its request queue and both pipe ends."""

    context = _StartFailureContext()
    runner = HostTaskRunner(
        target_type="agent",
        tid="1780000000000000000",
        function_target=None,
        process_target=None,
        agent={"runtime": "llm"},
        args=None,
        kwargs=None,
        env=None,
        working_dir=None,
        timeout=5.0,
        limits=None,
        monitor_class=None,
        monitor_interval=0.1,
    )
    monkeypatch.setattr(runner, "_ctx", context)

    with pytest.raises(RuntimeError, match="spawn failed"):
        runner.start_agent_session()

    assert context.queue.closed is True
    assert context.queue.joined is True
    assert context.receiver.closed is True
    assert context.sender.closed is True
    assert context.process.closed is True


def test_agent_session_monitor_load_failure_cleans_started_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Post-spawn monitor construction failure cannot leak session resources."""

    context = _StartedContext()
    runner = HostTaskRunner(
        target_type="agent",
        tid="1780000000000000000",
        function_target=None,
        process_target=None,
        agent={"runtime": "llm"},
        args=None,
        kwargs=None,
        env=None,
        working_dir=None,
        timeout=5.0,
        limits=None,
        monitor_class="tests.fake:Monitor",
        monitor_interval=0.1,
    )
    monkeypatch.setattr(runner, "_ctx", context)

    def fail_monitor_load(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("monitor load failed")

    monkeypatch.setattr(
        host_module,
        "load_resource_monitor",
        fail_monitor_load,
    )

    with pytest.raises(RuntimeError, match="monitor load failed"):
        runner.start_agent_session()

    assert context.process.alive is False
    assert context.process.joined is True
    assert context.process.closed is True
    assert context.queue.closed is True
    assert context.queue.joined is True
    assert context.receiver.closed is True
    assert context.sender.closed is True


def test_agent_session_construction_failure_reports_monitor_cleanup_failure(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Monitor cleanup remains visible when session construction fails."""

    calls: list[str] = []
    context = _StartedContext()
    context.process.pid = 123
    runner = HostTaskRunner(
        target_type="agent",
        tid="1780000000000000000",
        function_target=None,
        process_target=None,
        agent={"runtime": "llm"},
        args=None,
        kwargs=None,
        env=None,
        working_dir=None,
        timeout=5.0,
        limits=None,
        monitor_class="tests.fake:Monitor",
        monitor_interval=0.1,
    )
    monkeypatch.setattr(runner, "_ctx", context)

    class Monitor:
        def start(self, pid: int) -> None:
            assert pid == 123
            calls.append("start")

        def stop(self) -> None:
            calls.append("stop")
            raise RuntimeError("sensitive agent monitor cleanup failure")

    def fail_session_construction(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("session construction failed")

    def stop_process(process: _StartedProcess) -> None:
        calls.append("stop_process")
        process.terminate()
        process.join()

    monkeypatch.setattr(
        host_module,
        "load_resource_monitor",
        lambda *_args, **_kwargs: Monitor(),
    )
    monkeypatch.setattr(host_module, "AgentSession", fail_session_construction)
    monkeypatch.setattr(runner, "_stop_process", stop_process)

    with (
        caplog.at_level(logging.WARNING, logger="weft.core.runners.host"),
        pytest.raises(RuntimeError, match="session construction failed"),
    ):
        runner.start_agent_session()

    assert calls == ["start", "stop", "stop_process"]
    assert [record.getMessage() for record in caplog.records] == [
        "Failed to stop host agent resource monitor during startup cleanup"
    ]
    assert all(record.exc_info is None for record in caplog.records)
    assert "sensitive" not in caplog.text
    assert context.process.alive is False
    assert context.process.joined is True
    assert context.process.closed is True
    assert context.queue.closed is True
    assert context.queue.joined is True
    assert context.receiver.closed is True
    assert context.sender.closed is True


def test_command_handle_acquisition_failure_reaps_process_and_closes_pipes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Host owns the spawned command before handle metadata can fail."""
    created: list[subprocess.Popen[str]] = []
    real_open = host_module.open_supervised_process

    def opened(*args: Any, **kwargs: Any) -> subprocess.Popen[str]:
        process = real_open(*args, **kwargs)
        created.append(process)
        return process

    failure = RuntimeError("handle lookup failed")

    def failed_handle(_pid: int | None) -> Any:
        raise failure

    monkeypatch.setattr(host_module, "open_supervised_process", opened)
    monkeypatch.setattr(host_module, "_host_handle", failed_handle)
    runner = TaskRunner(
        target_type="command",
        tid=None,
        function_target=None,
        process_target=sys.executable,
        agent=None,
        args=["-c", "import time; time.sleep(30)"],
        kwargs=None,
        env={},
        working_dir=str(tmp_path),
        timeout=5.0,
        limits=None,
        monitor_class=None,
        monitor_interval=0.1,
    )
    try:
        with pytest.raises(RuntimeError, match="handle lookup failed") as raised:
            runner.run({})
        assert raised.value is failure
        assert len(created) == 1
        process = created[0]
        assert process.returncode is not None
        assert all(
            pipe is None or pipe.closed
            for pipe in (process.stdin, process.stdout, process.stderr)
        )
    finally:
        for process in created:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe is not None:
                    pipe.close()


def test_command_session_terminate_preserves_owned_popen_signal_returncode() -> None:
    """CommandSession.terminate never lets psutil waitpid steal Popen's exit status [WT-4.2]."""
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        session = sessions_module.CommandSession(
            process,
            queue.Queue(),
            queue.Queue(),
            None,
        )
        session.terminate()
        rc = session.returncode()
        assert rc is not None
        assert rc != 0
        if os.name == "posix":
            assert rc < 0
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe is not None:
                pipe.close()


def test_host_command_cancellation_preserves_owned_popen_signal_returncode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """HostTaskRunner._run_command_with_hooks reaps cancelled Popen with real signal status [WT-4.2]."""
    created: list[subprocess.Popen[str]] = []
    real_open = host_module.open_supervised_process
    worker_started = threading.Event()

    def opened(*args: Any, **kwargs: Any) -> subprocess.Popen[str]:
        process = real_open(*args, **kwargs)
        created.append(process)
        return process

    monkeypatch.setattr(host_module, "open_supervised_process", opened)
    runner = host_module.HostTaskRunner(
        target_type="command",
        tid=None,
        function_target=None,
        process_target=sys.executable,
        agent=None,
        args=["-c", "import time; print('ready', flush=True); time.sleep(30)"],
        kwargs=None,
        env={},
        working_dir=str(tmp_path),
        timeout=10.0,
        limits=None,
        monitor_class=None,
        monitor_interval=0.1,
        bundle_root=None,
    )
    try:
        outcome = runner.run_with_hooks(
            {},
            cancel_requested=worker_started.is_set,
            on_stdout_chunk=lambda chunk, _final: (
                worker_started.set() if "ready" in chunk else None
            ),
        )
        assert outcome.status == "cancelled"
        assert len(created) == 1
        rc = created[0].returncode
        assert rc is not None
        assert rc != 0
        if os.name == "posix":
            assert rc < 0
    finally:
        for process in created:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe is not None:
                    pipe.close()
