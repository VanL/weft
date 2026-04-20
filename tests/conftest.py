"""Shared fixtures and helpers for Weft tests."""

from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from simplebroker import Queue
from tests.helpers.test_backend import active_test_backend, prepare_cli_root
from tests.helpers.weft_harness import WeftTestHarness

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CLI_SUBPROCESS_TIMEOUT = 60.0
_BROKER_HEAVY_FIXTURES = frozenset(
    {
        "weft_harness",
        "workdir",
        "broker_env",
        "broker_target",
        "queue_factory",
        "task_factory",
    }
)
_BROKER_HEAVY_GROUP = "weft_broker_serial"
_PRIORITY_TEST_NODEIDS = (
    "tests/cli/test_cli_long_session.py::"
    "test_cli_long_session_produces_identical_transcript_across_backends",
)
_SHARED_MODULES = frozenset(
    {
        "tests/system/test_constants.py",
        "tests/system/test_test_backend.py",
        "tests/system/test_helpers.py",
        "tests/system/test_pytest_pg_script.py",
        "tests/context/test_context.py",
        "tests/commands/test_dump_load.py",
        "tests/commands/test_interactive_client.py",
        "tests/commands/test_result.py",
        "tests/commands/test_run.py",
        "tests/commands/test_task_commands.py",
        "tests/cli/test_cli_init.py",
        "tests/cli/test_cli_queue.py",
        "tests/cli/test_cli_result.py",
        "tests/cli/test_cli_system.py",
        "tests/cli/test_status.py",
        "tests/commands/test_queue.py",
        "tests/shell/test_known_interpreters.py",
        "tests/core/test_agent_resolution.py",
        "tests/core/test_agent_runtime.py",
        "tests/core/test_agent_tools.py",
        "tests/core/test_agent_validation.py",
        "tests/core/test_builtin_agent_images.py",
        "tests/core/test_builtin_dockerized_agent.py",
        "tests/core/test_builtin_platform_support.py",
        "tests/core/test_callable.py",
        "tests/core/test_client.py",
        "tests/core/test_debugger.py",
        "tests/core/test_environment_profiles.py",
        "tests/core/test_exceptions.py",
        "tests/core/test_heartbeat_helpers.py",
        "tests/core/test_llm_backend.py",
        "tests/core/test_manager.py",
        "tests/core/test_ops_shared.py",
        "tests/core/test_pipelines.py",
        "tests/core/test_provider_cli_backend.py",
        "tests/core/test_provider_cli_container_runtime.py",
        "tests/core/test_provider_cli_execution.py",
        "tests/core/test_provider_cli_session_backend.py",
        "tests/core/test_provider_cli_windows_shims.py",
        "tests/core/test_runner_plugins.py",
        "tests/core/test_spec_parameterization.py",
        "tests/core/test_spec_run_input.py",
        "tests/core/test_targets.py",
        "tests/core/test_tool_profiles.py",
        "tests/specs/manager_architecture/test_agent_spawn.py",
        "tests/specs/manager_architecture/test_manager_state_events.py",
        "tests/specs/manager_architecture/test_tid_correlation.py",
        "tests/specs/quick_reference/test_queue_names.py",
        "tests/taskspec/test_taskspec.py",
        "tests/specs/message_flow/test_agent_spawning_transition.py",
        "tests/specs/message_flow/test_spawning_transition.py",
        "tests/specs/resource_management/test_monitor_compat.py",
        "tests/specs/resource_management/test_resource_limit_killed.py",
        "tests/specs/resource_management/test_resource_metrics.py",
        "tests/specs/resource_management/test_timeout_return_code.py",
        "tests/specs/taskspec/test_agent_taskspec.py",
        "tests/specs/taskspec/test_peak_metrics.py",
        "tests/specs/taskspec/test_process_target.py",
        "tests/specs/taskspec/test_state_transitions.py",
        "tests/tasks/test_agent_execution.py",
        "tests/tasks/test_control_channel.py",
        "tests/tasks/test_heartbeat.py",
        "tests/tasks/test_multiqueue_watcher.py",
        "tests/tasks/test_pipeline_runtime.py",
        "tests/tasks/test_runner.py",
        "tests/tasks/test_task_execution.py",
        "tests/tasks/test_task_endpoints.py",
        "tests/tasks/test_task_interactive.py",
        "tests/tasks/test_task_observability.py",
        "tests/tasks/test_task_observer_behavior.py",
    }
)
_SQLITE_ONLY_MODULES = frozenset(
    {
        "tests/context/test_context_sqlite_only.py",
        "tests/cli/test_cli_init_sqlite_only.py",
        "tests/commands/test_dump_load_sqlite_only.py",
    }
)
_UNAUDITED_PATH_PREFIX_REASONS: dict[str, str] = {}
_UNAUDITED_MODULE_ALLOWLIST_REASONS = {
    "tests/cli/test_cli_list_task.py": (
        "legacy CLI list-task coverage still needs explicit backend-scope review"
    ),
    "tests/cli/test_cli_pipeline.py": (
        "legacy CLI pipeline coverage still needs explicit backend-scope review"
    ),
    "tests/cli/test_cli_result_all.py": (
        "legacy CLI result-all coverage still needs explicit backend-scope review"
    ),
    "tests/cli/test_cli_run.py": (
        "large end-to-end CLI run surface still needs explicit backend-scope review"
    ),
    "tests/cli/test_cli_spec.py": (
        "legacy CLI spec coverage still needs explicit backend-scope review"
    ),
    "tests/cli/test_cli_tidy.py": (
        "legacy CLI tidy coverage still needs explicit backend-scope review"
    ),
    "tests/cli/test_commands.py": (
        "legacy CLI command smoke coverage still needs explicit backend-scope review"
    ),
    "tests/cli/test_manager_proctitle.py": (
        "platform-sensitive process-title coverage still needs explicit backend-scope review"
    ),
    "tests/cli/test_rearrange_args.py": (
        "legacy CLI argv-shaping coverage still needs explicit backend-scope review"
    ),
    "tests/system/test_release_script.py": (
        "release-helper tooling coverage still needs explicit backend-scope review"
    ),
}
_EAGER_FAILURE_TRACEBACK_ENV = "WEFT_EAGER_FAILURE_TRACEBACK"


def _env_flag_enabled(name: str) -> bool:
    """Return whether an environment flag is explicitly enabled."""

    value = os.environ.get(name, "")
    return value.lower() in {"1", "true", "yes", "on"}


def _should_emit_eager_failure_traceback(report: pytest.TestReport) -> bool:
    """Return whether to emit a failed report immediately."""

    if not _env_flag_enabled(_EAGER_FAILURE_TRACEBACK_ENV):
        return False
    if os.environ.get("PYTEST_XDIST_WORKER"):
        return False
    if not report.failed:
        return False
    if getattr(report, "wasxfail", False):
        return False
    return hasattr(report, "longreprtext")


def _preferred_test_python() -> Path:
    if os.name == "nt":
        candidate = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = REPO_ROOT / ".venv" / "bin" / "python"
    return candidate if candidate.exists() else Path(sys.executable)


def _pg_cli_requires_active_env(args: tuple[object, ...]) -> bool:
    """Return whether a PG-backed CLI subprocess must use `uv run --active`.

    Only commands that may launch child processes which outlive the parent CLI
    need the stable interpreter path provided by `uv run --active`. One-shot
    commands can use the current interpreter directly, which keeps the PG suite
    from paying nested-uv startup overhead on every CLI assertion.
    """

    if not args:
        return False

    command = str(args[0])
    if command == "run":
        return True
    if command == "manager":
        return True
    return False


def _normalize_test_python_environment() -> None:
    python_path = _preferred_test_python()
    os.environ.setdefault("WEFT_TEST_PYTHON", str(python_path))
    os.environ.pop("__PYVENV_LAUNCHER__", None)

    python_bin = str(python_path.parent)
    current_path = os.environ.get("PATH", "")
    path_parts = [part for part in current_path.split(os.pathsep) if part]
    if python_bin not in path_parts:
        os.environ["PATH"] = os.pathsep.join([python_bin, *path_parts])

    multiprocessing.set_executable(str(python_path))


_normalize_test_python_environment()


@pytest.fixture
def weft_harness() -> Iterator[WeftTestHarness]:
    with WeftTestHarness() as harness:
        yield harness


@pytest.fixture
def workdir(weft_harness: WeftTestHarness) -> Path:
    """Per-test working directory derived from the test harness."""
    return weft_harness.root


@pytest.fixture
def broker_target(weft_harness: WeftTestHarness):
    """Resolved broker target for the active test backend."""

    return weft_harness.context.broker_target


@pytest.fixture
def queue_factory(weft_harness: WeftTestHarness):
    """Create queues bound to the active backend for the current harness root."""

    context = weft_harness.context
    created: list[Queue] = []

    def factory(name: str, *, persistent: bool = True) -> Queue:
        queue = Queue(
            name,
            db_path=context.broker_target,
            persistent=persistent,
            config=context.broker_config,
        )
        created.append(queue)
        return queue

    try:
        yield factory
    finally:
        for queue in created:
            try:
                queue.close()
            except Exception:  # pragma: no cover - defensive
                pass


@pytest.fixture
def broker_env(
    weft_harness: WeftTestHarness,
) -> Iterator[tuple[object, Callable[[str], Queue]]]:
    """Provide a shared broker target and a queue factory."""
    context = weft_harness.context
    broker_target = context.broker_target
    created: list[Queue] = []

    def factory(name: str) -> Queue:
        queue = Queue(
            name,
            db_path=broker_target,
            persistent=True,
            config=context.broker_config,
        )
        created.append(queue)
        return queue

    try:
        yield broker_target, factory
    finally:
        for queue in created:
            try:
                queue.close()
            except Exception:  # pragma: no cover - defensive
                pass


@pytest.fixture
def task_factory(broker_env: tuple[object, Callable[[str], Queue]]):
    """Create Task objects bound to the shared broker database."""
    from weft.core.tasks import Consumer

    broker_target, _ = broker_env
    tasks: list[Consumer] = []

    def factory(taskspec):
        task = Consumer(broker_target, taskspec)
        tasks.append(task)
        return task

    try:
        yield factory
    finally:
        for task in tasks:
            try:
                task.stop()
            except Exception:
                pass


def run_cli(
    *args: object,
    cwd: Path,
    stdin: str | None = None,
    timeout: float = DEFAULT_CLI_SUBPROCESS_TIMEOUT,
    env: dict[str, str] | None = None,
    strip_path_entry: bool = False,
    harness: WeftTestHarness | None = None,
    prepare_root: bool = True,
) -> tuple[int, str, str]:
    """Execute the Weft CLI (`python -m weft.cli …`) inside *cwd*."""
    env_vars = os.environ.copy() if env is None else env.copy()
    env_vars.pop("__PYVENV_LAUNCHER__", None)
    existing_path = env_vars.get("PYTHONPATH", "")
    path_parts = [str(REPO_ROOT)]
    if existing_path:
        path_parts.append(existing_path)
    env_vars["PYTHONPATH"] = os.pathsep.join(path_parts)
    env_vars["PYTHONIOENCODING"] = "utf-8"
    if strip_path_entry and env_vars.get("PYTHONPATH"):
        env_vars["PYTHONPATH"] = ":".join(
            part for part in env_vars["PYTHONPATH"].split(":") if part
        )

    backend_name = active_test_backend(env_vars)
    if backend_name == "postgres" and _pg_cli_requires_active_env(args):
        cmd = [
            "uv",
            "run",
            "--active",
            "python",
            "-m",
            "weft.cli",
            *map(str, args),
        ]
    else:
        if backend_name == "postgres":
            python_path = Path(sys.executable)
        else:
            python_override = os.environ.get("WEFT_TEST_PYTHON")
            if python_override:
                python_path = Path(python_override)
            else:
                python_path = _preferred_test_python()
        cmd = [str(python_path), "-m", "weft.cli", *map(str, args)]

    if prepare_root:
        prepare_cli_root(args, cwd=cwd, env=env_vars)

    try:
        completed = subprocess.run(
            cmd,
            cwd=cwd,
            input=stdin,
            text=True,
            capture_output=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            env=env_vars,
        )
    except subprocess.TimeoutExpired as exc:
        debug_lines = [
            "run_cli timeout diagnostics:",
            f"  command={' '.join(map(str, cmd))}",
            f"  cwd={cwd}",
            f"  timeout={timeout!r}",
        ]
        if harness is not None:
            try:
                debug_lines.append(harness.dump_debug_state())
            except Exception as dump_exc:  # pragma: no cover - defensive
                debug_lines.append(f"WeftTestHarness dump failed: {dump_exc!r}")
        else:
            debug_lines.append("No WeftTestHarness provided.")
        debug_text = "\n".join(debug_lines)
        existing_stderr = exc.stderr or ""
        combined_stderr = (
            f"{existing_stderr}\n{debug_text}" if existing_stderr else debug_text
        )
        raise subprocess.TimeoutExpired(
            exc.cmd,
            exc.timeout,
            output=exc.output,
            stderr=combined_stderr,
        ) from exc

    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()

    if harness is not None:
        _register_cli_outputs(harness, args, stdout, stderr)

    return completed.returncode, stdout, stderr


def _register_cli_outputs(
    harness: WeftTestHarness,
    args: tuple[object, ...],
    stdout: str,
    stderr: str,
) -> None:
    _extract_ids(harness, stdout)
    _extract_ids(harness, stderr)

    manager_command = bool(args and args[0] == "manager" and len(args) > 1)
    for blob in (stdout, stderr):
        for line in blob.splitlines():
            try:
                payload = json.loads(line)
            except Exception:
                continue
            _register_from_json(harness, payload, manager_command=manager_command)

    if manager_command:
        subcommand = args[1]
        if subcommand in {"start", "stop", "status", "list"}:
            for arg in args:
                if isinstance(arg, str) and arg.isdigit() and len(arg) == 19:
                    harness.register_manager_tid(arg)


def _extract_ids(harness: WeftTestHarness, text: str) -> None:
    import re

    pid_pattern = re.compile(r"pid\s+(\d+)", re.IGNORECASE)

    for line in text.splitlines():
        candidate = line.strip()
        if candidate.isdigit() and len(candidate) == 19:
            harness.register_tid(candidate)
    for match in pid_pattern.findall(text):
        try:
            harness.register_pid(int(match))
        except ValueError:
            continue


def _register_from_json(
    harness: WeftTestHarness,
    payload: Any,
    *,
    manager_command: bool = False,
) -> None:
    if isinstance(payload, dict):
        metadata = payload.get("metadata")
        metadata_role = metadata.get("role") if isinstance(metadata, dict) else None
        tid = payload.get("tid")
        if isinstance(tid, str):
            if (
                manager_command
                or payload.get("role") == "manager"
                or metadata_role == "manager"
            ):
                harness.register_manager_tid(tid)
            else:
                harness.register_tid(tid)
        pid = payload.get("pid")
        if isinstance(pid, int):
            harness.register_pid(pid, kind="owner")
        managed = payload.get("managed_pids")
        if isinstance(managed, list):
            for value in managed:
                if isinstance(value, int):
                    harness.register_pid(value, kind="managed")
        caller = payload.get("caller_pid")
        if isinstance(caller, int):
            harness._mark_safe_pid(caller)
        for value in payload.values():
            _register_from_json(
                harness,
                value,
                manager_command=manager_command,
            )
    elif isinstance(payload, list):
        for item in payload:
            _register_from_json(
                harness,
                item,
                manager_command=manager_command,
            )


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Group broker-heavy tests onto one xdist worker and classify backend scope.

    These tests exercise the real SQLite-backed broker, spawn child processes,
    and rely on teardown cleanup of live workers. Keeping them on one xdist
    worker matches the containment pattern used in SimpleBroker for
    concurrency-sensitive suites and avoids cross-worker teardown races.

    The marker must be attached before pytest-xdist's own
    ``pytest_collection_modifyitems`` hook runs, because xdist reads the
    ``xdist_group`` marker there to rewrite node IDs for ``--dist loadgroup``.
    """
    for item in items:
        relative_path = item.path.relative_to(REPO_ROOT).as_posix()
        fixture_names = set(getattr(item, "fixturenames", ()))
        if fixture_names & _BROKER_HEAVY_FIXTURES:
            item.add_marker(pytest.mark.xdist_group(name=_BROKER_HEAVY_GROUP))

        if item.get_closest_marker("shared") or item.get_closest_marker("sqlite_only"):
            continue
        if relative_path in _SHARED_MODULES:
            item.add_marker(pytest.mark.shared)
            continue
        if relative_path in _SQLITE_ONLY_MODULES:
            item.add_marker(pytest.mark.sqlite_only)
            continue
        if relative_path in _UNAUDITED_MODULE_ALLOWLIST_REASONS:
            continue
        if any(
            relative_path.startswith(prefix)
            for prefix in _UNAUDITED_PATH_PREFIX_REASONS
        ):
            continue
        raise pytest.UsageError(
            "Unaudited test module must be marked 'shared' or 'sqlite_only', or "
            f"listed in the temporary allowlist: {relative_path}"
        )

    items.sort(
        key=lambda item: (
            0 if any(nodeid in item.nodeid for nodeid in _PRIORITY_TEST_NODEIDS) else 1
        )
    )


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Emit failed tracebacks immediately when debugging env flags opt in."""

    if not _should_emit_eager_failure_traceback(report):
        return

    header = f"Immediate traceback: {report.nodeid} [{report.when}]"
    longrepr = getattr(report, "longreprtext", "")
    print(f"\n{'=' * len(header)}", file=sys.stderr, flush=True)
    print(header, file=sys.stderr, flush=True)
    print(f"{'=' * len(header)}", file=sys.stderr, flush=True)
    print(longrepr, file=sys.stderr, flush=True)


__all__ = ["weft_harness", "workdir", "broker_env", "task_factory", "run_cli"]
