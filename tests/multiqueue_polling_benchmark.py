"""Benchmark multi-queue polling cost across broker backends.

This harness is intentionally dev-only. It measures how Weft's
``MultiQueueWatcher`` scales as the number of watched queues grows while using
the real SQLite and Postgres broker implementations.

Typical usage:

    source .envrc
    uv run python -m tests.multiqueue_polling_benchmark --backends sqlite
    uv run python -m tests.multiqueue_polling_benchmark \
        --backends sqlite postgres \
        --pg-dsn postgresql://postgres:postgres@127.0.0.1:32834/simplebroker_test
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from pathlib import Path

if __package__ in {None, ""}:
    REPO_ROOT = Path(__file__).resolve().parents[1]
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from tests.helpers.test_backend import (  # type: ignore[no-redef]
        POSTGRES_TEST_BACKEND,
        cleanup_prepared_roots,
        prepare_project_root,
    )
else:
    from .helpers.test_backend import (
        POSTGRES_TEST_BACKEND,
        cleanup_prepared_roots,
        prepare_project_root,
    )

from weft.context import build_context
from weft.core.tasks.multiqueue_watcher import MultiQueueWatcher, QueueMode

SQLITE_BACKEND = "sqlite"
MESSAGE_BODY = "bench-payload"


@dataclass(frozen=True)
class BenchmarkSettings:
    """Settings that shape one benchmark run."""

    backends: tuple[str, ...]
    queue_counts: tuple[int, ...]
    workloads: tuple[str, ...]
    iterations: int = 5
    warmups: int = 1
    idle_cycles: int = 50
    dispatch_iterations: int = 25
    pg_dsn: str | None = None
    json_output: bool = False

    def validate(self) -> None:
        if self.iterations < 1:
            raise ValueError("iterations must be at least 1")
        if self.warmups < 0:
            raise ValueError("warmups cannot be negative")
        if self.idle_cycles < 1:
            raise ValueError("idle_cycles must be at least 1")
        if self.dispatch_iterations < 1:
            raise ValueError("dispatch_iterations must be at least 1")
        if not self.queue_counts:
            raise ValueError("at least one queue count is required")
        if any(count < 1 for count in self.queue_counts):
            raise ValueError("queue counts must be positive")
        if POSTGRES_TEST_BACKEND in self.backends and not self.pg_dsn:
            raise ValueError(
                "Postgres benchmarks require --pg-dsn or SIMPLEBROKER_PG_TEST_DSN"
            )


@dataclass(frozen=True)
class BenchmarkResult:
    """One measured benchmark iteration."""

    backend: str
    workload: str
    queue_count: int
    iteration: int
    operations: int
    elapsed_seconds: float


@dataclass(frozen=True)
class BenchmarkSummary:
    """Median summary for one backend/workload/queue-count combination."""

    backend: str
    workload: str
    queue_count: int
    description: str
    runs: int
    operations: int
    median_elapsed_seconds: float
    median_ms_per_operation: float
    median_us_per_queue: float


@dataclass(frozen=True)
class BenchmarkComparison:
    """Relative performance between SQLite and Postgres for one benchmark slice."""

    workload: str
    queue_count: int
    description: str
    sqlite_ms_per_operation: float
    postgres_ms_per_operation: float
    slower_backend: str
    slowdown_ratio: float


@dataclass(frozen=True)
class WorkloadSpec:
    """Definition of one benchmark workload."""

    name: str
    description: str
    runner: Callable[[Path, dict[str, str], BenchmarkSettings, int], tuple[int, float]]


class InstrumentedMultiQueueWatcher(MultiQueueWatcher):
    """MultiQueueWatcher that exposes pre-check timing for benchmarks."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.precheck_count = 0
        self.last_precheck_finished_ns: int | None = None
        self.precheck_event = threading.Event()
        super().__init__(*args, **kwargs)

    def _has_pending_messages(self) -> bool:
        result = super()._has_pending_messages()
        self.precheck_count += 1
        self.last_precheck_finished_ns = time.perf_counter_ns()
        self.precheck_event.set()
        return result


def _ensure_postgres_support() -> None:
    try:
        import simplebroker_pg  # noqa: F401
    except Exception as exc:  # pragma: no cover - missing-PG envs only
        raise RuntimeError(
            "Postgres benchmark requested, but simplebroker_pg is unavailable."
        ) from exc


@contextmanager
def _backend_env(backend: str, pg_dsn: str | None) -> dict[str, str]:
    """Provide both process env and helper env for one backend."""

    keys = ("BROKER_TEST_BACKEND", "SIMPLEBROKER_PG_TEST_DSN")
    previous = {key: os.environ.get(key) for key in keys}
    env = {"BROKER_TEST_BACKEND": backend}
    if backend == POSTGRES_TEST_BACKEND:
        assert pg_dsn is not None
        env["SIMPLEBROKER_PG_TEST_DSN"] = pg_dsn

    try:
        os.environ["BROKER_TEST_BACKEND"] = backend
        if backend == POSTGRES_TEST_BACKEND:
            os.environ["SIMPLEBROKER_PG_TEST_DSN"] = pg_dsn or ""
        else:
            os.environ.pop("SIMPLEBROKER_PG_TEST_DSN", None)
        yield env
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _queue_name(index: int) -> str:
    return f"bench.poll.{index:04d}"


def _build_watcher(
    root: Path,
    env: dict[str, str],
    *,
    queue_count: int,
    processed: list[str],
) -> tuple[MultiQueueWatcher, object]:
    return _build_watcher_cls(
        MultiQueueWatcher,
        root,
        env,
        queue_count=queue_count,
        processed=processed,
    )


def _build_watcher_cls(
    watcher_cls: type[MultiQueueWatcher],
    root: Path,
    env: dict[str, str],
    *,
    queue_count: int,
    processed: list[str],
) -> tuple[MultiQueueWatcher, object]:
    prepared_root = prepare_project_root(root, env=env)
    context = build_context(spec_context=prepared_root)

    def handler(message: str, _timestamp: int, _context: object) -> None:
        processed.append(message)

    queue_configs = {
        _queue_name(index): {
            "handler": handler,
            "mode": QueueMode.READ,
        }
        for index in range(queue_count)
    }
    watcher = watcher_cls(
        queue_configs=queue_configs,
        db=context.broker_target,
        check_interval=1,
        config=context.config,
    )
    return watcher, context


def _close_watcher(watcher: MultiQueueWatcher) -> None:
    with suppress(Exception):
        watcher.stop(join=False)
    for queue_name in watcher.list_queues():
        queue = watcher.get_queue(queue_name)
        if queue is None:
            continue
        with suppress(Exception):
            queue.close()


def _workload_idle_sweep(
    root: Path,
    env: dict[str, str],
    settings: BenchmarkSettings,
    queue_count: int,
) -> tuple[int, float]:
    processed: list[str] = []
    watcher, _context = _build_watcher(
        root,
        env,
        queue_count=queue_count,
        processed=processed,
    )
    try:
        start = time.perf_counter()
        for _ in range(settings.idle_cycles):
            if watcher._has_pending_messages():
                raise RuntimeError("idle_precheck unexpectedly found pending messages")
        elapsed = time.perf_counter() - start
    finally:
        _close_watcher(watcher)

    if processed:
        raise RuntimeError("idle_precheck should not process any messages")
    return settings.idle_cycles, elapsed


def _workload_last_queue_ready(
    root: Path,
    env: dict[str, str],
    settings: BenchmarkSettings,
    queue_count: int,
) -> tuple[int, float]:
    processed: list[str] = []
    watcher, context = _build_watcher(
        root,
        env,
        queue_count=queue_count,
        processed=processed,
    )
    target_queue_name = _queue_name(queue_count - 1)
    target_queue = context.queue(target_queue_name, persistent=True)
    try:
        for index in range(settings.dispatch_iterations):
            target_queue.write(f"{MESSAGE_BODY}-{index}")

        start = time.perf_counter()
        for _ in range(settings.dispatch_iterations):
            if not watcher._has_pending_messages():
                raise RuntimeError(
                    "last_queue_ready expected a pending message during pre-check"
                )
            watcher._drain_queue()
        elapsed = time.perf_counter() - start
    finally:
        target_queue.close()
        _close_watcher(watcher)

    if len(processed) != settings.dispatch_iterations:
        raise RuntimeError(
            "last_queue_ready processed "
            f"{len(processed)} messages, expected {settings.dispatch_iterations}"
        )
    return settings.dispatch_iterations, elapsed


def _workload_unrelated_write_only(
    root: Path,
    env: dict[str, str],
    settings: BenchmarkSettings,
    queue_count: int,
) -> tuple[int, float]:
    processed: list[str] = []
    watcher, context = _build_watcher(
        root,
        env,
        queue_count=queue_count,
        processed=processed,
    )
    unrelated_queue = context.queue("bench.unrelated", persistent=True)

    try:
        start = time.perf_counter()
        for index in range(settings.dispatch_iterations):
            unrelated_queue.write(f"{MESSAGE_BODY}-unrelated-{index}")
        elapsed = time.perf_counter() - start
    finally:
        unrelated_queue.close()
        _close_watcher(watcher)

    if processed:
        raise RuntimeError(
            "unrelated_write_only should not process monitored queue messages"
        )
    return settings.dispatch_iterations, elapsed


def _wait_for_precheck(
    watcher: InstrumentedMultiQueueWatcher,
    *,
    previous_count: int,
    timeout: float = 5.0,
) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if watcher.precheck_count > previous_count:
            finished_ns = watcher.last_precheck_finished_ns
            if finished_ns is None:
                raise RuntimeError("pre-check count advanced without finish timestamp")
            return finished_ns
        remaining = max(0.0, deadline - time.monotonic())
        watcher.precheck_event.wait(timeout=min(0.05, remaining))
    raise TimeoutError("Timed out waiting for watcher pre-check after unrelated change")


def _workload_unrelated_queue_change(
    root: Path,
    env: dict[str, str],
    settings: BenchmarkSettings,
    queue_count: int,
) -> tuple[int, float]:
    processed: list[str] = []
    watcher, context = _build_watcher_cls(
        InstrumentedMultiQueueWatcher,
        root,
        env,
        queue_count=queue_count,
        processed=processed,
    )
    assert isinstance(watcher, InstrumentedMultiQueueWatcher)
    unrelated_queue = context.queue("bench.unrelated", persistent=True)
    thread = watcher.run_in_thread()
    total_elapsed = 0.0

    try:
        time.sleep(0.1)
        for index in range(settings.dispatch_iterations):
            watcher.precheck_event.clear()
            previous_count = watcher.precheck_count
            start_ns = time.perf_counter_ns()
            unrelated_queue.write(f"{MESSAGE_BODY}-unrelated-{index}")
            finished_ns = _wait_for_precheck(
                watcher,
                previous_count=previous_count,
            )
            total_elapsed += (finished_ns - start_ns) / 1_000_000_000
    finally:
        unrelated_queue.close()
        watcher.stop(join=True, timeout=2.0)
        thread.join(timeout=2.0)
        _close_watcher(watcher)

    if processed:
        raise RuntimeError(
            "unrelated_queue_change should not process monitored queue messages"
        )
    return settings.dispatch_iterations, total_elapsed


WORKLOADS: dict[str, WorkloadSpec] = {
    "idle_precheck": WorkloadSpec(
        name="idle_precheck",
        description="One empty pre-check across N monitored queues",
        runner=_workload_idle_sweep,
    ),
    "last_queue_ready": WorkloadSpec(
        name="last_queue_ready",
        description=(
            "One relevant wake path where only the last monitored queue has a "
            "ready message (pre-check plus drain, write cost excluded)"
        ),
        runner=_workload_last_queue_ready,
    ),
    "unrelated_write_only": WorkloadSpec(
        name="unrelated_write_only",
        description=(
            "Write to an unmonitored queue with no watcher-side false-positive "
            "polling cost included"
        ),
        runner=_workload_unrelated_write_only,
    ),
    "unrelated_queue_change": WorkloadSpec(
        name="unrelated_queue_change",
        description=(
            "Write to an unmonitored queue, then wait for one false-positive "
            "pre-check sweep across the monitored queues"
        ),
        runner=_workload_unrelated_queue_change,
    ),
}


def run_benchmarks(settings: BenchmarkSettings) -> list[BenchmarkResult]:
    """Run the configured benchmark matrix and return raw iteration results."""

    settings.validate()
    if POSTGRES_TEST_BACKEND in settings.backends:
        _ensure_postgres_support()

    results: list[BenchmarkResult] = []
    for backend in settings.backends:
        with _backend_env(backend, settings.pg_dsn) as env:
            for queue_count in settings.queue_counts:
                for workload_name in settings.workloads:
                    workload = WORKLOADS[workload_name]
                    total_runs = settings.warmups + settings.iterations
                    for run_index in range(total_runs):
                        with tempfile.TemporaryDirectory(
                            prefix=(
                                f"weft-poll-bench-{backend}-{workload_name}-"
                                f"q{queue_count}-"
                            )
                        ) as tempdir:
                            root = Path(tempdir)
                            operations, elapsed = workload.runner(
                                root,
                                env,
                                settings,
                                queue_count,
                            )
                            cleanup_prepared_roots(root, env=env)

                        if run_index < settings.warmups:
                            continue

                        results.append(
                            BenchmarkResult(
                                backend=backend,
                                workload=workload_name,
                                queue_count=queue_count,
                                iteration=run_index - settings.warmups + 1,
                                operations=operations,
                                elapsed_seconds=elapsed,
                            )
                        )
    return results


def summarize_results(results: list[BenchmarkResult]) -> list[BenchmarkSummary]:
    grouped: dict[tuple[str, str, int], list[BenchmarkResult]] = {}
    for result in results:
        grouped.setdefault(
            (result.backend, result.workload, result.queue_count), []
        ).append(result)

    summaries: list[BenchmarkSummary] = []
    for (backend, workload, queue_count), runs in sorted(grouped.items()):
        operations = runs[0].operations
        median_elapsed = statistics.median(run.elapsed_seconds for run in runs)
        median_ms_per_operation = (median_elapsed / operations) * 1000
        median_us_per_queue = (median_elapsed / (operations * queue_count)) * 1_000_000
        summaries.append(
            BenchmarkSummary(
                backend=backend,
                workload=workload,
                queue_count=queue_count,
                description=WORKLOADS[workload].description,
                runs=len(runs),
                operations=operations,
                median_elapsed_seconds=median_elapsed,
                median_ms_per_operation=median_ms_per_operation,
                median_us_per_queue=median_us_per_queue,
            )
        )
    return summaries


def compare_backends(summaries: list[BenchmarkSummary]) -> list[BenchmarkComparison]:
    by_key: dict[tuple[str, int], dict[str, BenchmarkSummary]] = {}
    for summary in summaries:
        by_key.setdefault((summary.workload, summary.queue_count), {})[
            summary.backend
        ] = summary

    comparisons: list[BenchmarkComparison] = []
    for (workload, queue_count), summary_map in sorted(by_key.items()):
        sqlite_summary = summary_map.get(SQLITE_BACKEND)
        postgres_summary = summary_map.get(POSTGRES_TEST_BACKEND)
        if sqlite_summary is None or postgres_summary is None:
            continue

        sqlite_ms = sqlite_summary.median_ms_per_operation
        postgres_ms = postgres_summary.median_ms_per_operation
        if sqlite_ms >= postgres_ms:
            slower_backend = SQLITE_BACKEND
            slowdown_ratio = (
                sqlite_ms / postgres_ms if postgres_ms > 0 else float("inf")
            )
        else:
            slower_backend = POSTGRES_TEST_BACKEND
            slowdown_ratio = postgres_ms / sqlite_ms if sqlite_ms > 0 else float("inf")
        comparisons.append(
            BenchmarkComparison(
                workload=workload,
                queue_count=queue_count,
                description=WORKLOADS[workload].description,
                sqlite_ms_per_operation=sqlite_ms,
                postgres_ms_per_operation=postgres_ms,
                slower_backend=slower_backend,
                slowdown_ratio=slowdown_ratio,
            )
        )
    return comparisons


def _render_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def format_row(row: list[str]) -> str:
        return "  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row))

    separator = "  ".join("-" * width for width in widths)
    rendered = [format_row(headers), separator]
    rendered.extend(format_row(row) for row in rows)
    return "\n".join(rendered)


def render_text_report(
    settings: BenchmarkSettings,
    results: list[BenchmarkResult],
) -> str:
    summaries = summarize_results(results)
    comparisons = compare_backends(summaries)

    lines = [
        "Weft Multi-Queue Polling Benchmark",
        "",
        (
            "Settings: "
            f"backends={', '.join(settings.backends)}; "
            f"queue_counts={', '.join(str(count) for count in settings.queue_counts)}; "
            f"workloads={', '.join(settings.workloads)}; "
            f"iterations={settings.iterations}; "
            f"warmups={settings.warmups}"
        ),
        "",
        "Median By Backend",
    ]

    summary_rows = [
        [
            summary.workload,
            str(summary.queue_count),
            summary.backend,
            f"{summary.median_ms_per_operation:.3f}",
            f"{summary.median_us_per_queue:.3f}",
        ]
        for summary in summaries
    ]
    lines.append(
        _render_table(
            ["Workload", "Queues", "Backend", "ms/op", "us/queue-check"],
            summary_rows,
        )
    )

    if comparisons:
        lines.extend(["", "SQLite vs Postgres"])
        comparison_rows = [
            [
                comparison.workload,
                str(comparison.queue_count),
                f"{comparison.sqlite_ms_per_operation:.3f}",
                f"{comparison.postgres_ms_per_operation:.3f}",
                comparison.slower_backend,
                f"{comparison.slowdown_ratio:.2f}x",
            ]
            for comparison in comparisons
        ]
        lines.append(
            _render_table(
                [
                    "Workload",
                    "Queues",
                    "SQLite ms/op",
                    "PG ms/op",
                    "Slower",
                    "Ratio",
                ],
                comparison_rows,
            )
        )

    return "\n".join(lines)


def _parse_args(argv: list[str] | None = None) -> BenchmarkSettings:
    parser = argparse.ArgumentParser(
        description=(
            "Measure how MultiQueueWatcher polling scales as queue count grows."
        )
    )
    parser.add_argument(
        "--backends",
        nargs="+",
        default=[SQLITE_BACKEND],
        choices=[SQLITE_BACKEND, POSTGRES_TEST_BACKEND],
        help="Backends to benchmark",
    )
    parser.add_argument(
        "--queue-counts",
        nargs="+",
        type=int,
        default=[1, 8, 32, 128],
        help="Queue counts to benchmark",
    )
    parser.add_argument(
        "--workloads",
        nargs="+",
        default=list(WORKLOADS),
        choices=sorted(WORKLOADS),
        help="Polling workloads to benchmark",
    )
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument(
        "--idle-cycles",
        type=int,
        default=50,
        help="Empty sweep iterations per benchmark run",
    )
    parser.add_argument(
        "--dispatch-iterations",
        type=int,
        default=25,
        help="Ready-message drain iterations per benchmark run",
    )
    parser.add_argument(
        "--pg-dsn",
        default=os.environ.get("SIMPLEBROKER_PG_TEST_DSN"),
        help="Postgres DSN used when --backends includes postgres",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit raw results and summaries as JSON",
    )
    args = parser.parse_args(argv)
    settings = BenchmarkSettings(
        backends=tuple(args.backends),
        queue_counts=tuple(args.queue_counts),
        workloads=tuple(args.workloads),
        iterations=args.iterations,
        warmups=args.warmups,
        idle_cycles=args.idle_cycles,
        dispatch_iterations=args.dispatch_iterations,
        pg_dsn=args.pg_dsn,
        json_output=bool(args.json),
    )
    return settings


def main(argv: list[str] | None = None) -> int:
    settings = _parse_args(argv)

    try:
        results = run_benchmarks(settings)
    except Exception as exc:
        print(f"Benchmark failed: {exc}", file=sys.stderr)
        return 1

    if settings.json_output:
        summaries = summarize_results(results)
        payload = {
            "settings": asdict(settings),
            "results": [asdict(result) for result in results],
            "summaries": [asdict(item) for item in summaries],
            "comparisons": [asdict(item) for item in compare_backends(summaries)],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(render_text_report(settings, results))
    return 0


if __name__ == "__main__":  # pragma: no cover - manual benchmark entry point
    raise SystemExit(main())
