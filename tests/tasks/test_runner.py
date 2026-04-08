"""Tests for the TaskRunner orchestration layer."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from tests.fixtures.llm_test_models import TEST_MODEL_ID
from weft.core.tasks.runner import TaskRunner
from weft.core.taskspec import LimitsSection


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


def _write_descendant_scripts(tmp_path: Path) -> tuple[Path, Path]:
    child_script = tmp_path / "child_sleep.py"
    child_script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")

    parent_script = tmp_path / "spawn_child.py"
    parent_script.write_text(
        """
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path


def main() -> None:
    child = subprocess.Popen([sys.executable, sys.argv[1]])
    Path(sys.argv[2]).write_text(str(child.pid), encoding="utf-8")
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
            return int(pidfile.read_text(encoding="utf-8").strip())
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
        timeout=1.0,
        limits=None,
        monitor_class=None,
        monitor_interval=0.05,
    )

    outcome = runner.run({})
    child_pid = _wait_for_pidfile(pidfile)

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
