"""Public extension type usability and value preservation. Spec: [PY-1], [PY-4]."""

from __future__ import annotations

import pickle
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures.public_extension_contract import plugin
from weft.client import LimitsSection
from weft.core.tasks import runner as runner_module
from weft.core.tasks.runner import TaskRunner
from weft.ext import (
    NormalizedAgentMessage,
    NormalizedAgentWorkItem,
    ResourceMetrics,
    RunnerOutcome,
    SessionExecutionResult,
)

pytestmark = [pytest.mark.shared]

ROOT = Path(__file__).resolve().parents[2]


def test_task_runner_consumes_public_only_plugin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner_module, "require_runner_plugin", lambda name: plugin)
    runner = TaskRunner(
        target_type="command",
        tid=None,
        function_target=None,
        process_target="unused",
        agent=None,
        args=[],
        kwargs=None,
        env={},
        working_dir=None,
        timeout=None,
        limits=None,
        monitor_class=None,
        monitor_interval=None,
        runner_name="public-test",
    )
    assert runner.run({"answer": 42}).value == {"answer": 42}
    chunks: list[tuple[str, bool]] = []

    def record_chunk(text: str, final: bool) -> None:
        chunks.append((text, final))

    assert (
        runner.run_with_hooks("hooked", on_stdout_chunk=record_chunk).value == "hooked"
    )
    assert chunks == [("streamed", True)]
    activity: list[str] = []
    session = runner.start_session(on_activity=lambda: activity.append("session"))
    session.send("hello")
    assert session.poll_stdout() == ["hello"]
    assert activity == ["session"]
    session.close()
    agent_session = runner.start_agent_session()
    assert agent_session.execute("answer").value == "answer"
    agent_session.close()


@pytest.mark.parametrize(
    "value",
    [
        ResourceMetrics(
            timestamp=1, memory_mb=3.456, cpu_percent=2.34, open_files=4, connections=5
        ),
        RunnerOutcome(
            "ok",
            {"answer": 42},
            None,
            "out",
            "err",
            0,
            1.0,
            ResourceMetrics(memory_mb=2),
        ),
        SessionExecutionResult(
            "error", None, "failed", diagnostics={"detail": "cause"}
        ),
        NormalizedAgentMessage("user", {"text": "hello"}),
        NormalizedAgentWorkItem(
            (NormalizedAgentMessage("user", "hello"),),
            metadata={"key": "value"},
            tool_allow=("read",),
        ),
    ],
)
def test_public_values_survive_pickle(value: Any) -> None:
    restored = pickle.loads(pickle.dumps(value))
    assert type(restored) is type(value)
    assert restored == value


def test_public_metrics_preserve_serialization_and_limits() -> None:
    metrics = ResourceMetrics(
        timestamp=1, memory_mb=3.456, cpu_percent=2.34, open_files=4, connections=5
    )
    assert metrics.to_dict() == {
        "timestamp": 1,
        "memory_mb": 3.46,
        "cpu_percent": 2.3,
        "open_files": 4,
        "connections": 5,
    }
    assert metrics.exceeds_limits(
        LimitsSection(memory_mb=2, cpu_percent=1, max_fds=3, max_connections=4)
    ) == ["memory", "cpu", "fds", "connections"]
    assert metrics.exceeds_limits(None) == []
    assert RunnerOutcome("ok", None, None, None, None, 0, 0).ok
    assert not RunnerOutcome("error", None, "failed", None, None, 1, 0).ok


def test_mypy_accepts_public_extension_and_rejects_wrong_contracts(
    tmp_path: Path,
) -> None:
    source = (ROOT / "tests/fixtures/public_extension_contract.py").read_text()
    probe = tmp_path / "public_probe.py"
    probe.write_text(source)
    command = [
        sys.executable,
        "-m",
        "mypy",
        "--config-file",
        str(ROOT / "pyproject.toml"),
        "--cache-dir",
        str(tmp_path / "cache"),
        str(probe),
    ]
    accepted = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, timeout=60, check=False
    )
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    probe.write_text(
        source
        + '\ninvalid_session: AgentSessionProtocol = object()\ninvalid_result: RunnerOutcome = SessionExecutionResult("ok", None, None)\n'
    )
    rejected = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, timeout=60, check=False
    )
    assert rejected.returncode == 1, rejected.stdout + rejected.stderr
    assert rejected.stdout.count("[assignment]") == 2, rejected.stdout
