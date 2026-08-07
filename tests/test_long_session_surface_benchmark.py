"""Focused tests for the long-session surface benchmark helpers."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

from tests import long_session_surface_benchmark as benchmark

pytestmark = pytest.mark.shared


def test_api_surface_invoke_run_scopes_environment_and_captured_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The API benchmark call scopes environment, stdin, and captured output."""

    previous_stdin = sys.stdin
    previous_stdout = sys.stdout
    previous_stderr = sys.stderr
    monkeypatch.setenv("WEFT_SIM117_PROBE", "before")
    monkeypatch.setenv("WEFT_CONTEXT", "prior-context")
    observed: dict[str, Any] = {}

    def fake_cmd_run(command: tuple[str, ...], **kwargs: Any) -> int:
        observed.update(
            command=command,
            context=os.environ["WEFT_CONTEXT"],
            probe=os.environ["WEFT_SIM117_PROBE"],
            stdin=sys.stdin.read(),
            context_dir=kwargs["context_dir"],
        )
        print("captured-out")
        print("captured-err", file=sys.stderr)
        return 7

    monkeypatch.setattr(benchmark, "cmd_run", fake_cmd_run)

    result = benchmark.ApiSurface()._invoke_run(
        tmp_path,
        {"WEFT_SIM117_PROBE": "inside"},
        command=("probe",),
        spec=None,
        function=None,
        args=(),
        kwargs=(),
        env_vars=(),
        name=None,
        interactive=False,
        timeout=None,
        tags=(),
        wait=True,
        stdin="input-data",
    )

    assert result == (7, "captured-out", "captured-err")
    assert observed == {
        "command": ("probe",),
        "context": str(tmp_path),
        "probe": "inside",
        "stdin": "input-data",
        "context_dir": tmp_path,
    }
    assert os.environ["WEFT_SIM117_PROBE"] == "before"
    assert os.environ["WEFT_CONTEXT"] == "prior-context"
    assert sys.stdin is previous_stdin
    assert sys.stdout is previous_stdout
    assert sys.stderr is previous_stderr


def test_main_converts_benchmark_failure_to_clean_exit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class BenchmarkFailure(Exception):
        pass

    monkeypatch.setattr(
        benchmark,
        "run_benchmarks",
        lambda _settings: (_ for _ in ()).throw(
            BenchmarkFailure("surface setup failed")
        ),
    )

    assert benchmark.main([]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Benchmark failed: surface setup failed\n"


def test_main_does_not_translate_fatal_benchmark_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class BenchmarkFatal(BaseException):
        pass

    failure = BenchmarkFatal("fatal benchmark failure")
    monkeypatch.setattr(
        benchmark,
        "run_benchmarks",
        lambda _settings: (_ for _ in ()).throw(failure),
    )

    with pytest.raises(BenchmarkFatal) as exc_info:
        benchmark.main([])

    assert exc_info.value is failure
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
