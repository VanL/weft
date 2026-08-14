"""Focused tests for the multi-queue polling benchmark entry point."""

from __future__ import annotations

import pytest

from tests import multiqueue_polling_benchmark as benchmark

pytestmark = pytest.mark.shared


def test_parse_args_uses_weft_postgres_test_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The benchmark does not inherit SimpleBroker's standalone test DSN."""

    monkeypatch.setenv("SIMPLEBROKER_PG_TEST_DSN", "postgresql://ignored.invalid/db")
    monkeypatch.setenv("WEFT_PG_TEST_DSN", "postgresql://weft.invalid/db")

    settings = benchmark._parse_args(["--backends", "postgres"])

    assert settings.pg_dsn == "postgresql://weft.invalid/db"


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
            BenchmarkFailure("polling setup failed")
        ),
    )

    assert benchmark.main([]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Benchmark failed: polling setup failed\n"


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
