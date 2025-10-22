"""Shared fixtures and helpers for Weft tests."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from simplebroker import Queue


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Per-test working directory with cwd switched for isolation."""
    monkeypatch.setenv("WEFT_TEST_MODE", "1")
    monkeypatch.setenv("WEFT_MANAGER_REUSE_ENABLED", "0")
    monkeypatch.setenv("WEFT_MANAGER_LIFETIME_TIMEOUT", "0.5")
    monkeypatch.chdir(tmp_path)
    yield tmp_path


@pytest.fixture
def broker_env(workdir: Path) -> Iterator[tuple[str, Callable[[str], Queue]]]:
    """Provide a shared broker database path and a queue factory."""
    db_path = workdir / "weft-tests.db"
    created: list[Queue] = []

    def factory(name: str) -> Queue:
        queue = Queue(name, db_path=str(db_path), persistent=True)
        created.append(queue)
        return queue

    try:
        yield str(db_path), factory
    finally:
        for queue in created:
            try:
                queue.close()
            except Exception:
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
    timeout: float = 6.0,
    env: dict[str, str] | None = None,
    strip_path_entry: bool = False,
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

    return (
        completed.returncode,
        completed.stdout.strip(),
        completed.stderr.strip(),
    )


__all__ = ["workdir", "broker_env", "task_factory", "run_cli"]
