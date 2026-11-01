"""Behavioral acceptance for optional reactor measurement evidence [TS-0]."""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import psutil
import pytest

from tests import subprocess_reactor_benchmark as benchmark

pytestmark = pytest.mark.shared


@pytest.mark.parametrize(
    "fault",
    [None, "missing", "duplicate", "wrong-class", "skipped", "failure", "error"],
)
def test_windows_execution_report_rejects_incomplete_or_nonpassing_cases(
    tmp_path: Path, fault: str | None
) -> None:
    """CI proof requires the named kernel cases, not just a successful pytest exit."""
    names = [
        "test_windows_cancel_tears_down_overlapped_reads[pending-read]",
        "test_windows_cancel_tears_down_overlapped_reads[no-pending-read]",
        "test_windows_supervised_process_construction_rolls_back[pipe]",
        "test_windows_supervised_process_construction_rolls_back[process]",
        "test_windows_supervised_process_construction_rolls_back[attach]",
        "test_closed_input_write_errors_partition_closed_pipe_from_faults[windows-broken-pipe]",
        "test_closed_input_write_errors_partition_closed_pipe_from_faults[windows-no-data]",
        "test_closed_input_write_errors_partition_closed_pipe_from_faults[windows-access-denied]",
        "test_idle_subprocess_waits_on_readiness_without_polling",
    ]
    suite = ET.Element("testsuite")
    for name in names:
        ET.SubElement(
            suite, "testcase", classname="tests.core.test_subprocess_runner", name=name
        )
    first = suite[0]
    if fault == "missing":
        suite.remove(first)
    elif fault == "duplicate":
        ET.SubElement(suite, "testcase", first.attrib)
    elif fault == "wrong-class":
        first.set("classname", "unrelated.test_subprocess_runner")
    elif fault is not None:
        ET.SubElement(first, fault)
    report = tmp_path / "report.xml"
    ET.ElementTree(suite).write(report)
    result = subprocess.run(
        [
            sys.executable,
            str(Path(benchmark.__file__)),
            "--verify-windows",
            str(report),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if fault is None:
        assert result.returncode == 0, result.stderr
        assert "Verified all 9 required Windows executions" in result.stdout
    else:
        assert result.returncode != 0, "incomplete Windows execution evidence passed"
        assert "RuntimeError" in result.stderr


def test_measurement_timeout_reaps_owned_worker_and_descendant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The external guard cleans its actual process tree before temporary-tree removal."""
    marker = tmp_path / "child.json"
    script = tmp_path / "held_worker.py"
    script.write_text(
        "import json,subprocess,sys,time\nfrom pathlib import Path\n"
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])\n"
        f"Path({str(marker)!r}).write_text(json.dumps(child.pid))\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    created: list[subprocess.Popen[Any]] = []
    children: list[psutil.Process] = []
    failure = subprocess.TimeoutExpired("fixture worker", 180)
    real_popen = subprocess.Popen

    def opened(*args: Any, **kwargs: Any) -> subprocess.Popen[Any]:
        process = real_popen(*args, **kwargs)
        created.append(process)
        real_wait = process.wait

        def wait(timeout: float | None = None) -> int:
            if timeout == 180:
                deadline = time.monotonic() + 5
                while not marker.exists():
                    assert time.monotonic() < deadline, "worker never created child"
                    time.sleep(0.01)
                children.append(psutil.Process(json.loads(marker.read_text())))
                raise failure
            return real_wait(timeout=timeout)

        monkeypatch.setattr(process, "wait", wait)
        return process

    monkeypatch.setattr(benchmark, "subprocess", SimpleNamespace(Popen=opened))
    try:
        with pytest.raises(subprocess.TimeoutExpired) as raised:
            benchmark.execute_arm(
                script, tmp_path, tmp_path, "fixture", "after", tmp_path / "unused.json"
            )
        assert raised.value is failure
        assert len(created) == len(children) == 1
        assert created[0].returncode is not None
        assert not children[0].is_running()
    finally:
        for process in created:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        for child in children:
            with contextlib.suppress(psutil.NoSuchProcess):
                child.kill()
                child.wait(timeout=5)
