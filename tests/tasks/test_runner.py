"""Tests for the TaskRunner orchestration layer."""

from __future__ import annotations

import inspect
import logging
import multiprocessing
import os
import subprocess
import sys
import threading
import time
from collections import Counter
from contextlib import contextmanager
from itertools import combinations
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures.llm_test_models import TEST_MODEL_ID
from weft.core.resource_monitor import ResourceMetrics
from weft.core.runner_diagnostics import runner_diagnostics
from weft.core.runners import RunnerOutcome
from weft.core.runners import host as host_module
from weft.core.runners.host import HostTaskRunner
from weft.core.runners.subprocess_runner import run_monitored_subprocess
from weft.core.tasks import sessions as sessions_module
from weft.core.tasks.agent_session_protocol import (
    make_booted_response,
    make_execute_request,
    make_ready_response,
    make_result_response,
    make_startup_error_response,
    make_stop_request,
)
from weft.core.tasks.runner import TaskRunner
from weft.core.tasks.sessions import AgentSession
from weft.core.taskspec import FrozenList, LimitsSection
from weft.core.terminal_handoff import (
    TerminalHandoffEvent,
    TerminalHandoffEventKind,
    TerminalHandoffProgress,
)
from weft.core.terminal_handoff_transport import (
    TerminalHandoffTransportError,
    send_terminal_payload,
)
from weft.ext import RunnerCapabilities, RunnerHandle


def test_resource_session_and_host_runner_constructors_drop_inert_context() -> None:
    command_parameters = inspect.signature(sessions_module.CommandSession).parameters
    agent_parameters = inspect.signature(AgentSession).parameters
    host_parameters = inspect.signature(HostTaskRunner).parameters

    assert "limits" not in command_parameters
    assert "limits" not in agent_parameters
    assert "db_path" not in host_parameters
    assert "config" not in host_parameters


def test_runner_outcome_drops_duplicate_worker_pid_identity() -> None:
    parameters = inspect.signature(RunnerOutcome).parameters

    assert "worker_pid" not in parameters


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


def test_host_runner_plugin_skips_pid_identity_mismatch(monkeypatch) -> None:
    handle = RunnerHandle(
        runner="host",
        kind="process",
        id="123",
        control={"authority": "host-pid"},
        observations={
            "host_pids": [123],
            "host_processes": [{"pid": 123, "create_time": 456.5}],
        },
    )
    terminated: list[int] = []

    monkeypatch.setattr(
        host_module,
        "pid_matches_create_time",
        lambda pid, create_time: False,
    )
    monkeypatch.setattr(
        host_module,
        "terminate_process_tree",
        lambda pid, *, timeout: terminated.append(pid),
    )

    assert host_module.HostRunnerPlugin().stop(handle)
    assert terminated == []


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
def test_task_runner_executes_function_successfully():
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
    assert outcome.value == "hello!"
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
    caplog.set_level(logging.WARNING, logger="weft.core.tasks.sessions")

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
    caplog.set_level(logging.WARNING, logger="weft.core.tasks.sessions")

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
    caplog.set_level(logging.WARNING, logger="weft.core.tasks.sessions")

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
            object(),
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
            object(),
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
            object(),
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
    caplog.set_level(logging.WARNING, logger="weft.core.tasks.sessions")

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
    caplog.set_level(logging.WARNING, logger="weft.core.tasks.sessions")

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
        send_terminal_payload(response_sender, make_booted_response())
        send_terminal_payload(
            response_sender,
            make_startup_error_response(
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
        send_terminal_payload(response_sender, make_booted_response())
        time.sleep(30.0)
    finally:
        response_sender.close()


def _spawn_agent_session_for_target(
    target: Any,
    *,
    timeout: float | None = None,
    monitor: Any | None = None,
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
    )


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
        send_terminal_payload(response_sender, make_booted_response())
        send_terminal_payload(response_sender, make_ready_response())
        request_queue.get()
        time.sleep(30.0)
    finally:
        response_sender.close()


def _agent_session_ready_then_exit_worker(
    request_queue: Any,
    response_sender: Connection,
) -> None:
    try:
        send_terminal_payload(response_sender, make_booted_response())
        send_terminal_payload(response_sender, make_ready_response())
        request_queue.get()
    finally:
        response_sender.close()


def _agent_session_ready_then_seal_and_hang_worker(
    request_queue: Any,
    response_sender: Connection,
) -> None:
    send_terminal_payload(response_sender, make_booted_response())
    send_terminal_payload(response_sender, make_ready_response())
    request_queue.get()
    response_sender.close()
    time.sleep(30.0)


def _agent_session_wrong_payload_worker(
    request_queue: Any,
    response_sender: Connection,
) -> None:
    try:
        send_terminal_payload(response_sender, make_booted_response())
        send_terminal_payload(response_sender, make_ready_response())
        request_queue.get()
        send_terminal_payload(response_sender, 42)
    finally:
        response_sender.close()


def _agent_session_malformed_nested_result_worker(
    request_queue: Any,
    response_sender: Connection,
) -> None:
    try:
        send_terminal_payload(response_sender, make_booted_response())
        send_terminal_payload(response_sender, make_ready_response())
        request_queue.get()
        payload = make_result_response(status="ok")
        payload["result"] = {"outputs": "not-a-list", "metadata": {}}
        send_terminal_payload(response_sender, payload)
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
        session.wait_ready(timeout=5.0)
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
    process = subprocess.Popen(
        [sys.executable, "-c", "print('ok')"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
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
    assert outcome.value == "ok"
    assert monitor.started_with == process.pid
    assert monitor.stopped is True
    assert outcome.metrics == monitor.metrics
    assert load_calls == 0


def test_run_monitored_subprocess_emits_live_chunks_before_exit() -> None:
    process = subprocess.Popen(
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
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
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
    assert outcome.value == "first\nsecond"
    assert outcome.stderr == "warn\n"
    assert stdout_chunks[0] == ("first\n", False)
    assert stdout_chunks[-1] == ("", True)
    assert stderr_chunks[0] == ("warn\n", False)
    assert stderr_chunks[-1] == ("", True)


def test_run_monitored_subprocess_ignores_late_limit_after_process_exit() -> None:
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import subprocess, sys; "
                "print('ok', flush=True); "
                "subprocess.Popen("
                "[sys.executable, '-c', 'import time; time.sleep(0.3)'], "
                "stdout=sys.stdout, stderr=sys.stderr)"
            ),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
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
            if process.poll() is None:
                return True, None
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
    assert outcome.value == "ok"
    assert monitor.late_checks == 0
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


def test_task_runner_executes_command_successfully(tmp_path):
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
                "sys.stdout.write('interactive-out'); sys.stdout.flush(); "
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
    session = runner.start_session()
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

    assert "".join(stdout) == "interactive-out"
    assert "".join(stderr) == "interactive-err"
    assert session._stdout_closed is True
    assert session._stderr_closed is True


def test_task_runner_applies_environment_profile_defaults(tmp_path):
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


def test_task_runner_reports_command_failure(tmp_path):
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


def test_task_runner_times_out(tmp_path):
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


def test_task_runner_enforces_memory_limit(tmp_path):
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


def test_task_runner_enforces_cpu_limit(tmp_path):
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
            "--cpu-seconds",
            "2",
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


def test_task_runner_enforces_fd_limit(tmp_path):
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


def test_task_runner_reports_multiple_violations(tmp_path):
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


def test_task_runner_can_be_cancelled(tmp_path):
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
    assert first.value is not None
    assert first.value.aggregate_public_output() == "text:hello"
    assert second.status == "ok"
    assert second.value is not None
    assert second.value.aggregate_public_output() == "history:hello"


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
        taskspec_payload,
        *,
        bundle_root: str | None = None,
        load_runtime: bool = False,
        preflight: bool = False,
    ) -> None:
        assert "_weft_bundle_root" not in taskspec_payload
        del load_runtime
        validation_calls.append(("runtime", preflight, bundle_root))

    def fake_validate_tool_profile(
        taskspec_payload,
        *,
        bundle_root: str | None = None,
        load_runtime: bool = False,
        preflight: bool = False,
    ) -> None:
        assert "_weft_bundle_root" not in taskspec_payload
        del load_runtime
        validation_calls.append(("tool_profile", preflight, bundle_root))

    class FakeBackend:
        def run_with_hooks(self, work_item, **kwargs):
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
            taskspec_payload,
            *,
            bundle_root: str | None = None,
            preflight: bool = False,
        ) -> None:
            assert "_weft_bundle_root" not in taskspec_payload
            plugin_calls.append((preflight, bundle_root))

        def create_runner(self, **kwargs):
            del kwargs
            return FakeBackend()

        def stop(self, handle, *, timeout: float = 2.0) -> bool:
            del handle, timeout
            return True

        def kill(self, handle, *, timeout: float = 2.0) -> bool:
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
        taskspec_payload,
        *,
        bundle_root: str | None = None,
        load_runtime: bool = False,
        preflight: bool = False,
    ) -> None:
        del taskspec_payload, bundle_root, load_runtime
        validation_calls.append(("runtime", preflight))

    def fake_validate_tool_profile(
        taskspec_payload,
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
            taskspec_payload,
            *,
            bundle_root: str | None = None,
            preflight: bool = False,
        ) -> None:
            del taskspec_payload, bundle_root
            plugin_calls.append(preflight)

        def create_runner(self, **kwargs):
            del kwargs
            return FakeBackend()

        def stop(self, handle, *, timeout: float = 2.0) -> bool:
            del handle, timeout
            return True

        def kill(self, handle, *, timeout: float = 2.0) -> bool:
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

    session = runner.start_session()
    child_pid = _wait_for_pidfile(pidfile)
    try:
        session.terminate()
        assert _wait_for_pid_exit(child_pid)
    finally:
        session.stop_monitor()
        if not _wait_for_pid_exit(child_pid, timeout=0.1):
            psutil = pytest.importorskip("psutil")
            try:
                psutil.Process(child_pid).kill()
            except psutil.Error:
                pass


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
    def fake_docker_client(*, timeout: int = 10):
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
    timeout: float,
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


def test_function_worker_maps_arbitrary_execution_failure_to_terminal_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExtensionFailure(Exception):
        pass

    class Sender:
        closed = False

        def close(self) -> None:
            self.closed = True

    def fail_execution(*_args: object, **_kwargs: object) -> None:
        raise ExtensionFailure("extension detail")

    sent: list[RunnerOutcome] = []
    sender = Sender()
    monkeypatch.setattr(host_module, "execute_function_target", fail_execution)
    monkeypatch.setattr(
        host_module,
        "send_terminal_payload",
        lambda _sender, payload, **_kwargs: sent.append(payload) or True,
    )

    host_module._worker_entry(
        {"type": "function", "function_target": "fixture:target"},
        "payload",
        sender,  # type: ignore[arg-type]
    )

    assert sender.closed is True
    assert len(sent) == 1
    outcome = sent[0]
    assert outcome.status == "error"
    assert outcome.value is None
    assert outcome.returncode is None
    assert outcome.error is not None
    assert "ExtensionFailure: extension detail" in outcome.error
    assert outcome.diagnostics is not None
    assert outcome.diagnostics["phase"] == "execute"
    assert outcome.diagnostics["exception_type"] == "ExtensionFailure"


def test_function_worker_propagates_non_exception_failure_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FatalSignal(BaseException):
        pass

    fatal = FatalSignal("stop worker")

    def fail_execution(*_args: object, **_kwargs: object) -> None:
        raise fatal

    monkeypatch.setattr(host_module, "execute_function_target", fail_execution)

    with pytest.raises(FatalSignal) as caught:
        host_module._worker_entry(
            {"type": "function", "function_target": "fixture:target"},
            "payload",
            object(),  # type: ignore[arg-type]
        )

    assert caught.value is fatal


def test_function_host_start_callback_failures_are_logged_without_replacing_outcome(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runner = _build_function_host_runner(timeout=5.0)

    def fail_callback(_value: object) -> None:
        raise RuntimeError("contains secret")

    with caplog.at_level(logging.WARNING, logger="weft.core.runners.host"):
        outcome = runner.run_with_hooks(
            "payload",
            on_worker_started=fail_callback,
            on_runtime_handle_started=fail_callback,
        )

    assert outcome.status == "ok"
    assert outcome.value == "payload"
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
        runner.run_with_hooks("payload", **{callback_name: fail_callback})

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
    assert outcome.value == "payload"
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
        session = runner.start_session()
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
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    metrics = ResourceMetrics(memory_mb=12.5)

    class Monitor:
        def __init__(self) -> None:
            self.metrics_reads = 0

        def start(self, _pid: int) -> None:
            calls.append("start")

        def check_limits(self) -> tuple[bool, str | None]:
            calls.append("check_limits")
            raise RuntimeError("sensitive resource check failure")

        def last_metrics(self) -> ResourceMetrics | None:
            calls.append("last_metrics")
            self.metrics_reads += 1
            if self.metrics_reads == 1:
                return metrics
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
        function_target="tests.tasks.sample_targets:simulate_work",
        kwargs={"duration": 0.15},
        monitor_class="tests.fake:Monitor",
        monitor_interval=0.01,
    )

    with caplog.at_level(logging.WARNING, logger="weft.core.runners.host"):
        outcome = runner.run(None)

    assert outcome.status == "ok"
    assert outcome.value == "done"
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


def test_host_monitor_final_metrics_failure_uses_snapshot_before_stop(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    metrics = ResourceMetrics(memory_mb=19.5)

    class Monitor:
        def start(self, _pid: int) -> None:
            calls.append("start")

        def last_metrics(self) -> None:
            calls.append("last_metrics")
            raise RuntimeError("sensitive final metrics failure")

        def snapshot(self) -> ResourceMetrics:
            calls.append("snapshot")
            return metrics

        def stop(self) -> None:
            calls.append("stop")

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
    assert outcome.value == "payload"
    assert outcome.metrics is metrics
    assert calls == ["start", "last_metrics", "snapshot", "stop"]
    monitor_messages = [
        record.getMessage()
        for record in caplog.records
        if "resource" in record.getMessage()
    ]
    assert monitor_messages == ["Failed to collect final host resource metrics"]
    assert all(record.exc_info is None for record in caplog.records)
    assert "sensitive" not in caplog.text


def test_host_monitor_snapshot_and_stop_failures_do_not_replace_outcome(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class Monitor:
        def start(self, _pid: int) -> None:
            calls.append("start")

        def check_limits(self) -> tuple[bool, str | None]:
            return True, None

        def last_metrics(self) -> None:
            calls.append("last_metrics")

        def snapshot(self) -> ResourceMetrics:
            calls.append("snapshot")
            raise RuntimeError("sensitive snapshot failure")

        def stop(self) -> None:
            calls.append("stop")
            raise RuntimeError("sensitive stop failure")

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
    assert outcome.value == "payload"
    assert outcome.metrics is None
    assert calls[0] == "start"
    assert "last_metrics" in calls
    assert calls[-2:] == ["snapshot", "stop"]
    monitor_messages = [
        record.getMessage()
        for record in caplog.records
        if "resource" in record.getMessage()
    ]
    assert monitor_messages == [
        "Failed to snapshot final host resource metrics",
        "Failed to stop host resource monitor",
    ]
    assert all(record.exc_info is None for record in caplog.records)
    assert "sensitive" not in caplog.text


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
        send_terminal_payload(sender, outcome)
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


class _FailingFirstBlockingPollConnection:
    """Fail one blocking poll while delegating every other pipe operation."""

    def __init__(self, receiver: Connection) -> None:
        self._receiver = receiver
        self.blocking_poll_failed = False

    def poll(self, timeout: float = 0.0) -> bool:
        if timeout > 0.0 and not self.blocking_poll_failed:
            self.blocking_poll_failed = True
            raise OSError("blocking terminal poll failed")
        return self._receiver.poll(timeout)

    def recv_bytes(self) -> bytes:
        return self._receiver.recv_bytes()

    def close(self) -> None:
        self._receiver.close()


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


def test_host_terminal_wrong_decoded_payload_type_is_transport_failure() -> None:
    """A decoded non-RunnerOutcome payload cannot become terminal success."""

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
        send_terminal_payload(sender, 42)
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
        assert (
            outcome.diagnostics["message"]
            == "decoded terminal payload has invalid type int"
        )
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
    wrapped_receiver = _FailingFirstBlockingPollConnection(receiver)
    process = LiveProcess()
    runner = _build_function_host_runner(timeout=5.0)
    monkeypatch.setattr(
        runner,
        "_stop_process",
        lambda _process: setattr(process, "alive", False),
    )
    try:
        outcome = runner._run_one_shot_terminal_handoff(
            process,  # type: ignore[arg-type]
            wrapped_receiver,  # type: ignore[arg-type]
            worker_pid=None,
            runtime_handle=None,
            cancel_requested=None,
        )

        assert wrapped_receiver.blocking_poll_failed is True
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


def test_agent_session_blocking_poll_failure_is_consumed_as_next_turn_transport_failure() -> (
    None
):
    """A session blocking-poll failure is reduced on the following turn."""

    session = _spawn_agent_session_for_target(_agent_session_ready_then_hang_worker)
    try:
        session.wait_ready(timeout=5.0)
        wrapped_receiver = _FailingFirstBlockingPollConnection(
            session._response_receiver
        )
        session._response_receiver = wrapped_receiver  # type: ignore[assignment]

        result = session.execute("hello")

        assert wrapped_receiver.blocking_poll_failed is True
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
        assert outcome.value == "hello"
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
        send_terminal_payload(sender, {"result": "unreachable"})
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
    started = time.monotonic()
    try:
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
    assert outcome.value == "[1, 2]"


def test_host_runner_reports_prewrite_result_serialization_failure() -> None:
    """An unpicklable return value produces one bounded public error outcome."""

    runner = _build_function_host_runner(
        timeout=5.0,
        function_target="tests.tasks.sample_targets:return_unpicklable",
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
    assert isinstance(outcome.value, str)
    assert len(outcome.value) == size


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
    pid = None

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
        "terminate_process_tree",
        lambda pid, *, timeout: calls.append(f"terminate_tree:{pid}:{timeout}"),
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
        host_module, "terminate_process_tree", lambda *_args, **_kwargs: None
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
        lambda _sender, payload, **_kwargs: responses.append(payload) or True,
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
        lambda _sender, payload, **_kwargs: responses.append(payload) or True,
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
        lambda _sender, payload, **_kwargs: responses.append(payload) or True,
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
        lambda _sender, payload, **_kwargs: responses.append(payload) or True,
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
