"""Installed-console acceptance coverage for terminal handoff execution."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sysconfig
from pathlib import Path

import psutil
import pytest

from tests.helpers.test_backend import cleanup_prepared_roots, prepare_project_root
from tests.helpers.weft_harness import WeftTestHarness
from weft.ext import RunnerHandle
from weft.helpers import pid_is_live

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

    scripts_dir = Path(sysconfig.get_path("scripts"))
    console_path = shutil.which("weft", path=str(scripts_dir))
    assert console_path is not None, (
        f"installed console script is missing from {scripts_dir}"
    )
    console = Path(console_path)
    assert console.is_file(), f"installed console script is missing: {console}"
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["WEFT_TASK_MONITOR_MODE"] = "report_only"
    harness = WeftTestHarness()
    managers: list[psutil.Process] = []
    try:
        root = prepare_project_root(harness.root, env=env)
        env.pop("BROKER_TEST_BACKEND", None)

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
    except BaseException as primary:
        # No runtime was launched. Avoid materializing a harness context before
        # its environment is patched: inherited defaults may name another DB.
        try:
            cleanup_prepared_roots(harness.root)
        except (OSError, RuntimeError) as cleanup_error:
            primary.add_note(f"Fresh-project schema cleanup failed: {cleanup_error}")
        try:
            harness._tempdir.cleanup()
        except OSError as cleanup_error:
            primary.add_note(f"Fresh-project directory cleanup failed: {cleanup_error}")
        harness._closed = True
        raise

    with harness:
        # Keep installed commands on the exact broker target owned by cleanup.
        # The init above ran first, while the external project was still fresh.
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        env.pop("BROKER_TEST_BACKEND", None)
        env["WEFT_TASK_MONITOR_MODE"] = "report_only"
        env["WEFT_MANAGER_REUSE_ENABLED"] = "1"
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

        assert managers_before, "reuse coverage requires an existing live manager"

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
        for record in harness._list_active_manager_records():
            runtime_handle = record["runtime_handle"]
            assert isinstance(runtime_handle, dict)
            handle = RunnerHandle.from_dict(runtime_handle)
            managers.extend(psutil.Process(pid) for pid in handle.scoped_host_pids())
        assert managers, "acceptance test must observe its real manager before cleanup"

    assert all(
        not manager.is_running() or not pid_is_live(manager.pid) for manager in managers
    ), "installed-console acceptance leaked its manager"
    assert not harness.root.exists()


def test_installed_console_init_failure_does_not_materialize_harness_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before enter, cleanup owns files only, never inherited broker defaults."""
    harness = WeftTestHarness()
    foreign_root = tmp_path / "foreign-default"
    foreign_root.mkdir()
    sentinel = foreign_root / "sentinel"
    sentinel.write_text("untouched", encoding="utf-8")
    monkeypatch.setenv("WEFT_DEFAULT_DB_LOCATION", str(foreign_root))
    monkeypatch.setitem(globals(), "WeftTestHarness", lambda: harness)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args, returncode=1, stdout="", stderr="injected init failure"
        ),
    )
    with pytest.raises(AssertionError, match="injected init failure"):
        test_installed_console_function_handoffs_from_fresh_project(tmp_path)
    assert harness._context is None
    assert harness._closed
    assert not harness.root.exists()
    assert list(foreign_root.iterdir()) == [sentinel]
    assert sentinel.read_text(encoding="utf-8") == "untouched"
