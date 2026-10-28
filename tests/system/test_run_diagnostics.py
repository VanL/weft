"""Real subprocess coverage for opt-in pytest crash diagnostics [TS-0]."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, TextIO

import pytest

from tests.helpers.run_diagnostics import _RunDiagnostics

pytestmark = [pytest.mark.shared]


def _run_test(
    tmp_path: Path,
    source: str,
    diagnostics: Path | None,
    *,
    distributed: bool = False,
    timeout_plugin: bool = False,
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
    if timeout_plugin:
        args.extend(["-p", "pytest_timeout"])
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


@pytest.mark.parametrize("distributed", [False, True])
def test_run_diagnostics_records_node_and_normal_session_exit(
    tmp_path: Path, distributed: bool
) -> None:
    directory = tmp_path / "diagnostics"
    result = _run_test(
        tmp_path,
        "def test_ok():\n    assert True\n",
        directory,
        distributed=distributed,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    events = _events(directory)
    assert any(event.get("nodeid") == "test_probe.py::test_ok" for event in events)
    assert any(event.get("event") == "session_finish" for event in events)
    assert len(list(directory.glob("*.stacks.log"))) == (2 if distributed else 1)
    assert len(list(directory.glob("*.terminal.log"))) == int(distributed)
    if distributed:
        assert any(
            event.get("event") == "terminal_sink_installed"
            and event.get("worker") == "gw0"
            for event in events
        )


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


def test_run_diagnostics_retains_worker_timeout_output_and_stack(
    tmp_path: Path,
) -> None:
    """Retain the real timeout dump without changing xdist's crash outcome."""
    directory = tmp_path / "diagnostics"
    token = "weft-timeout-captured-output-proof"
    result = _run_test(
        tmp_path,
        "import time\n"
        "import pytest\n"
        "@pytest.mark.timeout(1, method='thread')\n"
        "def test_timeout_probe(request):\n"
        "    request.config.get_terminal_writer().write(chr(0xD800))\n"
        f"    print({token!r}, flush=True)\n"
        "    time.sleep(60)\n",
        directory,
        distributed=True,
        timeout_plugin=True,
    )
    controller_output = result.stdout + result.stderr
    assert result.returncode == 1, controller_output
    assert "crashed while running 'test_probe.py::test_timeout_probe'" in (
        controller_output
    )
    # Worker exit closes the producer interval. The timer's dump goes through
    # its own terminal writer, not an xdist report delivered to the controller.
    assert " Timeout " not in controller_output
    assert token not in controller_output
    events = _events(directory)
    assert any(
        event.get("event") == "test_start"
        and event.get("nodeid") == "test_probe.py::test_timeout_probe"
        and event.get("worker") == "gw0"
        for event in events
    )
    assert any(
        event.get("event") == "worker_down"
        and event.get("error")
        and event.get("worker_id") == "gw0"
        for event in events
    )
    terminal_logs = list(directory.glob("gw0-*.terminal.log"))
    assert len(terminal_logs) == 1, "worker timeout output was not retained"
    output = terminal_logs[0].read_text(encoding="utf-8")
    assert "\\ud800" in output
    assert " Timeout " in output
    assert token in output
    assert "in test_timeout_probe" in output


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


def test_run_diagnostics_closed_sink_preserves_worker_timeout(tmp_path: Path) -> None:
    """An acquired sink can fail without preventing timeout's final os._exit."""
    result = _run_test(
        tmp_path,
        "import time\n"
        "import pytest\n"
        "@pytest.mark.timeout(1, method='thread')\n"
        "def test_closed_sink(request):\n"
        "    plugin = request.config.pluginmanager.get_plugin('weft-run-diagnostics')\n"
        "    plugin.terminal_file.close()\n"
        "    time.sleep(60)\n",
        tmp_path / "diagnostics",
        distributed=True,
        timeout_plugin=True,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert "crashed while running 'test_probe.py::test_closed_sink'" in output
    assert "ValueError" not in result.stderr
    assert "Traceback" not in result.stderr


def test_run_diagnostics_closes_stack_when_terminal_open_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pytestconfig: pytest.Config
) -> None:
    """Partial acquisition must not leak the already opened fatal-stack file."""
    opened: list[TextIO] = []
    original_open = Path.open

    def open_artifact(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path.name.endswith(".terminal.log"):
            raise OSError("terminal sink unavailable")
        stream = original_open(path, *args, **kwargs)
        if path.name.endswith(".stacks.log"):
            opened.append(stream)
        return stream

    monkeypatch.setattr(
        pytestconfig, "workerinput", {"workerid": "probe"}, raising=False
    )
    monkeypatch.setattr(Path, "open", open_artifact)
    with pytest.raises(OSError, match="terminal sink unavailable"):
        _RunDiagnostics(tmp_path, "probe", pytestconfig)
    assert len(opened) == 1
    assert opened[0].closed


def test_run_diagnostics_records_phases_without_replacing_failure(
    tmp_path: Path,
) -> None:
    """The real phase consumer retains progress and rethrows the same failure."""
    directory = tmp_path / "diagnostics"
    result = _run_test(
        tmp_path,
        "from tests.helpers.run_diagnostics import record_phase\n"
        "def test_phase_failure(request):\n"
        "    record_phase(request.config, 'operation_begin', argv=['probe'])\n"
        "    try:\n"
        "        raise AssertionError('original phase failure')\n"
        "    except AssertionError as error:\n"
        "        record_phase(request.config, 'operation_end', error=type(error).__name__)\n"
        "        raise\n",
        directory,
        distributed=True,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "original phase failure" in result.stdout
    phases = [event for event in _events(directory) if event["event"] == "phase"]
    assert [event["phase"] for event in phases] == ["operation_begin", "operation_end"]
    assert phases[0]["argv"] == ["probe"]
    assert phases[1]["error"] == "AssertionError"
    assert all(
        event["nodeid"] == "test_probe.py::test_phase_failure (call)"
        for event in phases
    )
