"""Tests for the TaskRunner orchestration layer."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from weft.core.tasks.runner import TaskRunner
from weft.core.taskspec import LimitsSection


def test_task_runner_executes_function_successfully():
    runner = TaskRunner(
        target_type="function",
        function_target="tests.tasks.sample_targets:echo_payload",
        process_target=None,
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


def test_task_runner_executes_command_successfully(tmp_path):
    runner = TaskRunner(
        target_type="command",
        function_target=None,
        process_target=[sys.executable, PROCESS_SCRIPT, "--result", "ok"],
        args=None,
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
        function_target=None,
        process_target=[sys.executable, "-c", "import sys; sys.exit(3)"],
        args=None,
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
        function_target=None,
        process_target=[sys.executable, PROCESS_SCRIPT, "--duration", "2"],
        args=None,
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


def test_task_runner_enforces_memory_limit(tmp_path):
    pytest.importorskip("psutil")
    limits = LimitsSection(memory_mb=1)
    runner = TaskRunner(
        target_type="command",
        function_target=None,
        process_target=[
            sys.executable,
            PROCESS_SCRIPT,
            "--memory-mb",
            "10",
            "--duration",
            "2",
        ],
        args=None,
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
        function_target=None,
        process_target=[
            sys.executable,
            PROCESS_SCRIPT,
            "--cpu-percent",
            "100",
            "--duration",
            "3",
        ],
        args=None,
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
        function_target=None,
        process_target=[
            sys.executable,
            PROCESS_SCRIPT,
            "--fds",
            "20",
            "--duration",
            "2",
        ],
        args=None,
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
    assert "Open files" in (outcome.error or "")
    assert outcome.metrics is not None


def test_task_runner_reports_multiple_violations(tmp_path):
    pytest.importorskip("psutil")
    limits = LimitsSection(memory_mb=1, max_fds=2)
    runner = TaskRunner(
        target_type="command",
        function_target=None,
        process_target=[
            sys.executable,
            PROCESS_SCRIPT,
            "--memory-mb",
            "10",
            "--fds",
            "20",
            "--duration",
            "2",
        ],
        args=None,
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
    assert "Memory" in (outcome.error or "")
    assert "Open files" in (outcome.error or "")
    assert outcome.metrics is not None
