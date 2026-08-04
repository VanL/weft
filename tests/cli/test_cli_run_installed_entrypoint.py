"""Installed-console acceptance coverage for terminal handoff execution."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.shared, pytest.mark.timeout(90)]


def _run_console(
    console: Path,
    root: Path,
    *args: str,
    env: dict[str, str],
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    """Run the environment's installed console script from an external root."""

    return subprocess.run(
        [str(console), *args],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _assert_ok(completed: subprocess.CompletedProcess[str]) -> str:
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert completed.stderr.strip() == ""
    return completed.stdout.strip()


def test_installed_console_function_handoffs_from_fresh_project(
    tmp_path: Path,
) -> None:
    """Fresh-project stdlib, local, stored, reuse, and no-wait paths complete."""

    console = Path(sys.executable).with_name("weft")
    assert console.is_file(), f"installed console script is missing: {console}"
    root = tmp_path / "external-project"
    root.mkdir()
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("BROKER_TEST_BACKEND", None)
    env["WEFT_TASK_MONITOR_MODE"] = "report_only"

    initialized = subprocess.run(
        [str(console), "init", str(root)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=30.0,
        check=False,
    )
    _assert_ok(initialized)

    (root / "registry_probe.py").write_text(
        """from __future__ import annotations


def ping() -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )
    task_dir = root / ".weft" / "tasks"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "fire-check.json").write_text(
        json.dumps(
            {
                "name": "fire-check",
                "spec": {
                    "type": "function",
                    "function_target": "registry_probe:ping",
                },
                "metadata": {},
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    try:
        stdlib = _run_console(
            console,
            root,
            "run",
            "--function",
            "json:dumps",
            "--arg",
            "[1,2]",
            env=env,
        )
        assert _assert_ok(stdlib) == "[1, 2]"

        first_local = _run_console(
            console,
            root,
            "run",
            "--function",
            "registry_probe:ping",
            env=env,
        )
        assert json.loads(_assert_ok(first_local)) == {"ok": True}
        managers_before = json.loads(
            _assert_ok(
                _run_console(console, root, "manager", "list", "--json", env=env)
            )
        )

        second_local = _run_console(
            console,
            root,
            "run",
            "--function",
            "registry_probe:ping",
            env=env,
        )
        assert json.loads(_assert_ok(second_local)) == {"ok": True}
        managers_after = json.loads(
            _assert_ok(
                _run_console(console, root, "manager", "list", "--json", env=env)
            )
        )
        assert [item["tid"] for item in managers_after] == [
            item["tid"] for item in managers_before
        ]

        stored = _run_console(
            console,
            root,
            "run",
            "--spec",
            ".weft/tasks/fire-check.json",
            env=env,
        )
        assert json.loads(_assert_ok(stored)) == {"ok": True}

        submitted = _run_console(
            console,
            root,
            "run",
            "--no-wait",
            "--function",
            "registry_probe:ping",
            env=env,
        )
        tid = _assert_ok(submitted)
        assert len(tid) == 19 and tid.isdigit()
        collected = _run_console(
            console,
            root,
            "result",
            tid,
            "--timeout",
            "15",
            "--json",
            env=env,
        )
        payload = json.loads(_assert_ok(collected))
        assert payload["status"] == "completed"
        assert payload["result"] == {"ok": True}
    finally:
        _run_console(
            console,
            root,
            "manager",
            "stop",
            "--force",
            "--timeout",
            "5",
            env=env,
            timeout=15.0,
        )
