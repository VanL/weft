"""Shared fixtures and helpers for Weft tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from simplebroker import Queue
from tests.helpers.weft_harness import WeftTestHarness


@pytest.fixture
def weft_harness() -> Iterator[WeftTestHarness]:
    with WeftTestHarness() as harness:
        yield harness


@pytest.fixture
def workdir(weft_harness: WeftTestHarness) -> Path:
    """Per-test working directory derived from the test harness."""
    return weft_harness.root


@pytest.fixture
def broker_env(
    weft_harness: WeftTestHarness,
) -> Iterator[tuple[str, Callable[[str], Queue]]]:
    """Provide a shared broker database path and a queue factory."""
    context = weft_harness.context
    db_path = context.database_path
    created: list[Queue] = []

    def factory(name: str) -> Queue:
        queue = Queue(
            name,
            db_path=str(db_path),
            persistent=True,
            config=context.broker_config,
        )
        created.append(queue)
        return queue

    try:
        yield str(db_path), factory
    finally:
        for queue in created:
            try:
                queue.close()
            except Exception:  # pragma: no cover - defensive
                pass


@pytest.fixture
def task_factory(broker_env: tuple[str, Callable[[str], Queue]]):
    """Create Task objects bound to the shared broker database."""
    from weft.core.tasks import Consumer

    db_path, _ = broker_env
    tasks: list[Consumer] = []

    def factory(taskspec):
        task = Consumer(db_path, taskspec)
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
    timeout: float = 20.0,
    env: dict[str, str] | None = None,
    strip_path_entry: bool = False,
    harness: WeftTestHarness | None = None,
) -> tuple[int, str, str]:
    """Execute the Weft CLI (`python -m weft.cli …`) inside *cwd*."""
    repo_root = Path(__file__).resolve().parents[1]
    python_override = os.environ.get("WEFT_TEST_PYTHON")
    if python_override:
        python_path = Path(python_override)
    else:
        if os.name == "nt":
            candidate = repo_root / ".venv" / "Scripts" / "python.exe"
        else:
            candidate = repo_root / ".venv" / "bin" / "python"
        python_path = candidate if candidate.exists() else Path(sys.executable)

    cmd = [str(python_path), "-m", "weft.cli", *map(str, args)]

    env_vars = os.environ.copy() if env is None else env.copy()
    existing_path = env_vars.get("PYTHONPATH", "")
    path_parts = [str(repo_root)]
    if existing_path:
        path_parts.append(existing_path)
    env_vars["PYTHONPATH"] = os.pathsep.join(path_parts)
    env_vars["PYTHONIOENCODING"] = "utf-8"
    if strip_path_entry and env_vars.get("PYTHONPATH"):
        env_vars["PYTHONPATH"] = ":".join(
            part for part in env_vars["PYTHONPATH"].split(":") if part
        )

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

    for blob in (stdout, stderr):
        for line in blob.splitlines():
            try:
                payload = json.loads(line)
            except Exception:
                continue
            _register_from_json(harness, payload)

    if args and args[0] == "worker" and len(args) > 1:
        subcommand = args[1]
        if subcommand in {"start", "stop", "status", "list"}:
            for arg in args:
                if isinstance(arg, str) and arg.isdigit() and len(arg) == 19:
                    harness.register_tid(arg)


def _extract_ids(harness: WeftTestHarness, text: str) -> None:
    import re

    tid_pattern = re.compile(r"\b\d{19}\b")
    pid_pattern = re.compile(r"pid\s+(\d+)", re.IGNORECASE)

    for match in tid_pattern.findall(text):
        harness.register_tid(match)
    for match in pid_pattern.findall(text):
        try:
            harness.register_pid(int(match))
        except ValueError:
            continue


def _register_from_json(harness: WeftTestHarness, payload: Any) -> None:
    if isinstance(payload, dict):
        tid = payload.get("tid")
        if isinstance(tid, str):
            harness.register_tid(tid)
        pid = payload.get("pid")
        if isinstance(pid, int):
            harness.register_pid(pid)
        managed = payload.get("managed_pids")
        if isinstance(managed, list):
            for value in managed:
                if isinstance(value, int):
                    harness.register_pid(value)
        caller = payload.get("caller_pid")
        if isinstance(caller, int):
            harness._mark_safe_pid(caller)
        for value in payload.values():
            _register_from_json(harness, value)
    elif isinstance(payload, list):
        for item in payload:
            _register_from_json(harness, item)


__all__ = ["weft_harness", "workdir", "broker_env", "task_factory", "run_cli"]
