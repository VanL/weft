"""Tests for the TaskRunner orchestration layer."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from tests.fixtures.llm_test_models import TEST_MODEL_ID
from weft.core.resource_monitor import ResourceMetrics
from weft.core.runners import RunnerOutcome
from weft.core.runners.subprocess_runner import run_monitored_subprocess
from weft.core.tasks.runner import TaskRunner
from weft.core.taskspec import LimitsSection
from weft.ext import RunnerCapabilities, RunnerHandle


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
        timeout=5.0,
        limits=None,
        monitor_class=None,
        monitor_interval=0.1,
    )

    outcome = runner.run({"payload": "hello"})

    assert outcome.ok
    assert outcome.value == "hello!"
    assert outcome.error is None


PROCESS_SCRIPT = str(Path(__file__).resolve().parent / "process_target.py")


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
    runtime_handle = RunnerHandle(runner_name="docker", runtime_id="container-123")
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
        db_path=None,
        config=None,
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
            db_path=None,
            config=None,
            runtime_handle=RunnerHandle(runner_name="host", runtime_id="live-stream"),
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
        db_path=None,
        config=None,
        runtime_handle=RunnerHandle(runner_name="host", runtime_id="late-limit"),
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
        except psutil.Error:
            return True
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
            return True
        time.sleep(0.05)
    return False


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
        args=[PROCESS_SCRIPT, "--cpu-percent", "100", "--duration", "3"],
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


def test_task_runner_run_does_not_preflight_agent_runtime_per_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validation_calls: list[tuple[str, bool]] = []
    plugin_calls: list[bool] = []

    def fake_validate_runtime(
        taskspec_payload,
        *,
        load_runtime: bool = False,
        preflight: bool = False,
    ) -> None:
        del taskspec_payload, load_runtime
        validation_calls.append(("runtime", preflight))

    def fake_validate_tool_profile(
        taskspec_payload,
        *,
        load_runtime: bool = False,
        preflight: bool = False,
    ) -> None:
        del taskspec_payload, load_runtime
        validation_calls.append(("tool_profile", preflight))

    class FakeBackend:
        def run_with_hooks(self, work_item, **kwargs):  # noqa: ANN001, ANN003
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
            self, taskspec_payload, *, preflight: bool = False
        ) -> None:
            del taskspec_payload
            plugin_calls.append(preflight)
            return None

        def create_runner(self, **kwargs):  # noqa: ANN003
            del kwargs
            return FakeBackend()

        def stop(self, handle, *, timeout: float = 2.0) -> bool:  # noqa: ANN001
            del handle, timeout
            return True

        def kill(self, handle, *, timeout: float = 2.0) -> bool:  # noqa: ANN001
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
    )

    outcome = runner.run({"content": "hello"})

    assert outcome.status == "ok"
    assert plugin_calls == [False]
    assert validation_calls == [("runtime", False), ("tool_profile", False)]


def test_task_runner_start_agent_session_does_not_preflight_agent_runtime_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validation_calls: list[tuple[str, bool]] = []
    plugin_calls: list[bool] = []

    def fake_validate_runtime(
        taskspec_payload,
        *,
        load_runtime: bool = False,
        preflight: bool = False,
    ) -> None:
        del taskspec_payload, load_runtime
        validation_calls.append(("runtime", preflight))

    def fake_validate_tool_profile(
        taskspec_payload,
        *,
        load_runtime: bool = False,
        preflight: bool = False,
    ) -> None:
        del taskspec_payload, load_runtime
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
            self, taskspec_payload, *, preflight: bool = False
        ) -> None:
            del taskspec_payload
            plugin_calls.append(preflight)
            return None

        def create_runner(self, **kwargs):  # noqa: ANN003
            del kwargs
            return FakeBackend()

        def stop(self, handle, *, timeout: float = 2.0) -> bool:  # noqa: ANN001
            del handle, timeout
            return True

        def kill(self, handle, *, timeout: float = 2.0) -> bool:  # noqa: ANN001
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
