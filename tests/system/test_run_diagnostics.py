"""Real subprocess coverage for opt-in pytest crash diagnostics [TS-0]."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.shared]


def _run_test(
    tmp_path: Path, source: str, diagnostics: Path | None, *, distributed: bool = False
) -> subprocess.CompletedProcess[str]:
    test_file = tmp_path / "test_probe.py"
    test_file.write_text(source, encoding="utf-8")
    env = os.environ.copy()
    env.pop("WEFT_TEST_DIAGNOSTICS_DIR", None)
    env.pop("PYTEST_ADDOPTS", None)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    if diagnostics is not None:
        env["WEFT_TEST_DIAGNOSTICS_DIR"] = str(diagnostics)
    args = [sys.executable, "-m", "pytest", "-p", "tests.helpers.run_diagnostics"]
    if distributed:
        args.extend(["-p", "xdist.plugin", "-n", "1", "--max-worker-restart=0"])
    return subprocess.run(
        [*args, "-q", str(test_file)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _events(directory: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for path in directory.glob("*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


def test_run_diagnostics_records_node_and_normal_session_exit(tmp_path: Path) -> None:
    directory = tmp_path / "diagnostics"
    result = _run_test(tmp_path, "def test_ok():\n    assert True\n", directory)
    assert result.returncode == 0, result.stdout + result.stderr
    events = _events(directory)
    assert any(event.get("nodeid") == "test_probe.py::test_ok" for event in events)
    assert any(event.get("event") == "session_finish" for event in events)
    assert len(list(directory.glob("*.stacks.log"))) == 1


def test_run_diagnostics_preserves_fatal_worker_stack_and_controller_error(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "diagnostics"
    result = _run_test(
        tmp_path,
        "import os\ndef test_crash():\n    os.abort()\n",
        directory,
        distributed=True,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    stacks = list(directory.glob("gw0-*.stacks.log"))
    assert len(stacks) == 1
    assert "test_crash" in stacks[0].read_text(encoding="utf-8")
    events = _events(directory)
    assert any(
        event.get("event") == "test_start"
        and event.get("nodeid") == "test_probe.py::test_crash"
        and event.get("worker") == "gw0"
        for event in events
    )
    assert any(
        event.get("event") == "worker_down"
        and event.get("error")
        and event.get("worker_id") == "gw0"
        for event in events
    )


@pytest.mark.parametrize("enabled", [False, True])
def test_run_diagnostics_never_replaces_test_failure(
    tmp_path: Path,
    enabled: bool,
) -> None:
    directory = tmp_path / "not_a_directory"
    directory.write_text("occupied", encoding="utf-8")
    result = _run_test(
        tmp_path,
        "def test_bad():\n    assert False, 'original failure'\n",
        directory if enabled else None,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "original failure" in result.stdout
    assert "INTERNALERROR" not in result.stdout + result.stderr
