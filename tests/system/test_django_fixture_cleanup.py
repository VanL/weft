"""Real subprocess coverage for Django integration fixture ownership.

Spec: docs/specifications/08-Testing_Strategy.md [TS-0].
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from tests.helpers.test_backend import cleanup_prepared_roots
from weft.helpers import pid_is_live, pid_matches_create_time, terminate_process_tree

pytestmark = [pytest.mark.shared]
pytest.importorskip("django")


def _reap_owned_runtime(root: Path, *, launched_after: float) -> int:
    """Recover child runtimes even if pytest dies before it writes evidence."""
    root = root.resolve()
    safe_pids = {os.getpid(), *(p.pid for p in psutil.Process().parents())}
    username = psutil.Process().username()
    owned: dict[int, psutil.Process] = {}
    for process in psutil.process_iter():
        if process.pid in safe_pids:
            continue
        try:
            if process.create_time() < launched_after or process.username() != username:
                continue
            paths = [Path(process.cwd()).resolve()]
            if not any(path.is_relative_to(root) for path in paths):
                paths.extend(
                    Path(entry.path).resolve() for entry in process.open_files()
                )
            if not any(path.is_relative_to(root) for path in paths):
                continue
        except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
            # Global discovery includes unrelated same-user processes that macOS
            # protects. No ownership evidence exists for these candidates.
            continue
        owned[process.pid] = process
        try:
            owned.update(
                (child.pid, child) for child in process.children(recursive=True)
            )
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    for process in owned.values():
        if process.is_running():
            terminate_process_tree(process.pid, timeout=1.0)
    survivors = [
        process.pid
        for process in owned.values()
        if process.is_running() and pid_is_live(process.pid)
    ]
    assert not survivors, f"Cleanup retained live owned processes: {survivors}"
    cleanup_prepared_roots(root)
    shutil.rmtree(root)
    return len(owned)


@pytest.mark.parametrize(
    "mode", ["success", "assert-failure", "inherited-settings", "controller-timeout"]
)
def test_django_module_fixture_reaps_owned_runtime(tmp_path: Path, mode: str) -> None:
    """Fixture teardown owns detached processes even when the test fails."""
    project_root = Path(__file__).resolve().parents[2]
    evidence_path = tmp_path / "runtime.json"
    runtime_root = tmp_path / "runtime-roots"
    runtime_root.mkdir()
    timeout_ready = tmp_path / "timeout-ready"
    plugin = tmp_path / "django_ownership_probe.py"
    plugin.write_text(
        "import json, time\n"
        "from pathlib import Path\n"
        "import psutil, pytest\n"
        "from django.conf import settings\n"
        "from weft.core.manager_runtime import list_manager_records\n"
        "from weft.ext import RunnerHandle\n"
        "@pytest.hookimpl(wrapper=True)\n"
        "def pytest_runtest_call(item):\n"
        "    module = item.module\n"
        f"    Path({str(evidence_path)!r}).write_text(json.dumps({{'root': str(module.TEST_ROOT), 'processes': {{}}}}))\n"
        "    try:\n"
        "        result = yield\n"
        "    finally:\n"
        "        module = item.module\n"
        "        context = module.get_core_client().context\n"
        "        processes = {}\n"
        "        for record in list_manager_records(context):\n"
        "            handle = RunnerHandle.from_dict(record['runtime_handle'])\n"
        "            for pid, _ in handle.scoped_host_processes():\n"
        "                try:\n"
        "                    owner = psutil.Process(pid)\n"
        "                    for process in [owner, *owner.children(recursive=True)]:\n"
        "                        processes[process.pid] = process.create_time()\n"
        "                except psutil.NoSuchProcess:\n"
        "                    pass\n"
        "        evidence = {'root': str(module.TEST_ROOT), 'base': str(settings.BASE_DIR),\n"
        "                    'database': str(settings.DATABASES['default']['NAME']),\n"
        "                    'context': str(settings.WEFT_DJANGO['CONTEXT']),\n"
        "                    'processes': processes}\n"
        f"        Path({str(evidence_path)!r}).write_text(json.dumps(evidence))\n"
        f"    if {mode == 'controller-timeout'!r}:\n"
        "        assert processes, 'Timeout gate requires a live manager'\n"
        f"        Path({str(evidence_path)!r}).unlink()\n"
        f"        Path({str(timeout_ready)!r}).write_text('ready')\n"
        "        while True: time.sleep(1)\n"
        f"    if {mode == 'assert-failure'!r}:\n"
        "        raise AssertionError('induced fixture teardown failure')\n"
        "    return result\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env.update(TMPDIR=str(runtime_root), TMP=str(runtime_root), TEMP=str(runtime_root))
    env["PYTHONPATH"] = os.pathsep.join(
        [str(tmp_path), str(project_root), env.get("PYTHONPATH", "")]
    )
    for key in (
        "WEFT_DJANGO_FIXTURE_BASE_DIR",
        "WEFT_DJANGO_FIXTURE_DB_PATH",
        "WEFT_DJANGO_FIXTURE_WEFT_CONTEXT",
    ):
        env.pop(key, None)
    if mode == "inherited-settings":
        inherited_root = tmp_path / "inherited-root"
        inherited_root.mkdir()
        env.update(
            WEFT_DJANGO_FIXTURE_BASE_DIR=str(inherited_root),
            WEFT_DJANGO_FIXTURE_DB_PATH=str(inherited_root / "django.sqlite3"),
            WEFT_DJANGO_FIXTURE_WEFT_CONTEXT=str(inherited_root),
        )
    # Process creation timestamps have second precision on some platforms.
    launched_after = time.time() - 1.0
    timeout_triggered = False
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "pytest",
                "integrations/weft_django/tests/test_weft_django.py::test_decorated_task_enqueue_returns_richer_submission_handle",
                "-n",
                "0",
                "-q",
                "-p",
                "django_ownership_probe",
            ],
            cwd=project_root,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            if mode == "controller-timeout":
                deadline = time.monotonic() + 60.0
                while not timeout_ready.exists():
                    assert process.poll() is None, "Child exited before timeout gate"
                    assert time.monotonic() < deadline, (
                        "Child did not reach timeout gate"
                    )
                    time.sleep(0.01)
                with pytest.raises(subprocess.TimeoutExpired):
                    process.communicate(timeout=0.1)
                assert not evidence_path.exists()
                timeout_triggered = True
                return
            stdout, stderr = process.communicate(timeout=60.0)
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10.0)
        assert process.returncode == (1 if mode == "assert-failure" else 0), (
            stdout + stderr
        )
        if mode == "assert-failure":
            assert "induced fixture teardown failure" in stdout
        evidence = json.loads(evidence_path.read_text())
        root = Path(evidence["root"])
        assert evidence["base"] == evidence["context"] == str(root)
        assert evidence["database"] == str(root / "django.sqlite3")
        assert evidence["processes"], "regression must observe a real manager"
        survivors = {
            pid: created
            for pid, created in evidence["processes"].items()
            if pid_matches_create_time(int(pid), created) and pid_is_live(int(pid))
        }
        assert not survivors, f"Fixture leaked processes: {survivors}"
        assert not root.exists()
    finally:
        # This path is known before launching pytest and does not depend on a
        # successful runtest hook, registry publication, or a surviving controller.
        reaped = _reap_owned_runtime(runtime_root, launched_after=launched_after)
        if timeout_triggered:
            assert reaped > 0, "Timeout cleanup must recover real owned processes"


@pytest.mark.parametrize("owned", [False, True])
def test_runtime_reaping_only_ignores_denied_unidentified_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owned: bool
) -> None:
    """Discovery may skip a protected stranger, but known custody stays strict."""
    username = psutil.Process().username()

    class Candidate:
        pid = 99999999

        def create_time(self) -> float:
            return time.time()

        def username(self) -> str:
            return username

        def cwd(self) -> str:
            if not owned:
                raise psutil.AccessDenied(self.pid)
            return str(tmp_path)

        def children(self, *, recursive: bool) -> list[psutil.Process]:
            raise psutil.AccessDenied(self.pid)

    monkeypatch.setattr(psutil, "process_iter", lambda: iter([Candidate()]))
    if owned:
        with pytest.raises(psutil.AccessDenied):
            _reap_owned_runtime(tmp_path, launched_after=0)
        assert tmp_path.exists()
    else:
        assert _reap_owned_runtime(tmp_path, launched_after=0) == 0
        assert not tmp_path.exists()
