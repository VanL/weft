"""Opt-in subprocess reactor measurements and Windows execution proof [TS-0]."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import psutil

REQUIRED_WINDOWS = {
    "test_windows_cancel_tears_down_overlapped_reads[pending-read]",
    "test_windows_cancel_tears_down_overlapped_reads[no-pending-read]",
    "test_windows_supervised_process_construction_rolls_back[pipe]",
    "test_windows_supervised_process_construction_rolls_back[process]",
    "test_windows_supervised_process_construction_rolls_back[attach]",
    "test_closed_input_write_errors_partition_closed_pipe_from_faults[windows-broken-pipe]",
    "test_closed_input_write_errors_partition_closed_pipe_from_faults[windows-no-data]",
    "test_closed_input_write_errors_partition_closed_pipe_from_faults[windows-access-denied]",
    "test_idle_subprocess_waits_on_readiness_without_polling",
}


def require(condition: bool, detail: str) -> None:
    if not condition:
        raise RuntimeError(detail)


def verify_windows(report: Path) -> None:
    cases = [
        c
        for c in ET.parse(report).iter("testcase")
        if c.get("classname") == "tests.core.test_subprocess_runner"
    ]
    for name in sorted(REQUIRED_WINDOWS):
        found = [c for c in cases if c.get("name") == name]
        require(len(found) == 1, f"Expected exactly one execution: {name}")
        require(
            not any(
                found[0].find(t) is not None for t in ("skipped", "error", "failure")
            ),
            f"Required Windows test did not pass: {name}",
        )
    print(f"Verified all {len(REQUIRED_WINDOWS)} required Windows executions")


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def dump(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def module_provenance(root: Path, repository: Path, revision: str) -> dict[str, Any]:
    result = {}
    for name, module in tuple(sys.modules.items()):
        if name != "weft" and not name.startswith("weft."):
            continue
        filename = getattr(module, "__file__", None)
        if filename is None:
            raise RuntimeError(f"Imported Weft module has no source: {name}")
        path = Path(filename).resolve()
        relative = path.relative_to(root).as_posix()
        blob = git(repository, "hash-object", f"--path={relative}", str(path))
        expected = git(repository, "rev-parse", f"{revision}:{relative}")
        result[name] = {
            "file": str(path),
            "relative": relative,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "git_blob": blob,
            "expected_blob": expected,
            "dirty": blob != expected,
        }
    return result


class Counters:
    """Keep raw psutil counters; Linux's primary counters include exited threads."""

    def __init__(self) -> None:
        self.process = psutil.Process()
        self.resource: ModuleType | None = (
            importlib.import_module("resource")
            if sys.platform.startswith("linux")
            else None
        )

    def _resource_snapshot(self) -> dict[str, float]:
        assert self.resource is not None
        usage = self.resource.getrusage(self.resource.RUSAGE_SELF)
        return {
            "cpu_ms": 1000 * (usage.ru_utime + usage.ru_stime),
            "context_switches": float(usage.ru_nvcsw + usage.ru_nivcsw),
        }

    def snapshot(self, *, closing: bool = False) -> dict[str, dict[str, float]]:
        result: dict[str, dict[str, float]] = {}
        if closing and self.resource is not None:
            result["resource_self"] = self._resource_snapshot()
        if closing:
            cpu, ctx = self.process.cpu_times(), self.process.num_ctx_switches()
        else:
            ctx, cpu = self.process.num_ctx_switches(), self.process.cpu_times()
        result["psutil"] = {
            "cpu_ms": 1000 * (cpu.user + cpu.system),
            "context_switches": float(ctx.voluntary + ctx.involuntary),
        }
        if not closing and self.resource is not None:
            result["resource_self"] = self._resource_snapshot()
        return result

    @staticmethod
    def delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
        return {
            source: {
                metric: value - before[source][metric]
                for metric, value in values.items()
            }
            for source, values in after.items()
        }

    def calibrate(self) -> dict[str, Any]:
        before = self.snapshot()
        sleeper = threading.Thread(target=self._sleep_sequence)
        sleeper.start()
        sleeper.join()
        delta = self.delta(before, self.snapshot(closing=True))
        return {
            "sleeps": 100,
            "sleep_seconds": 0.002,
            "joined_before_final_sample": True,
            "delta": delta,
            "includes_exited_thread_evidence": {
                source: values["context_switches"] >= 80
                for source, values in delta.items()
            },
        }

    @staticmethod
    def _sleep_sequence() -> None:
        for _ in range(100):
            time.sleep(0.002)


class Measurements:
    """One source-isolated arm, using the original six-row procedure."""

    def __init__(self, arm: str) -> None:
        self.arm = arm
        self.runner = importlib.import_module("weft.core.runners.subprocess_runner")
        self.ext = importlib.import_module("weft.ext")
        self.counters = Counters()
        seam = "_PipeAdapter" if arm == "before" else "_open_process_io"
        require(
            hasattr(self.runner, seam), f"{arm} lacks expected implementation {seam}"
        )

    @contextlib.contextmanager
    def child(self, source: str) -> Iterator[Any]:
        argv = [sys.executable, "-u", "-c", source]
        if self.arm == "before":
            process = subprocess.Popen(
                argv,
                stdin=None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        else:
            process = self.runner.open_supervised_process(
                argv, pipe_stdin=False, encoding="utf-8", errors="replace"
            )
        try:
            yield process
        finally:
            try:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)
            finally:
                for name in ("stdout", "stderr", "stdin"):
                    stream = getattr(process, name)
                    if stream is not None:
                        stream.close()

    def supervise(self, process: Any, cancel: Any = None, sink: Any = None) -> Any:
        return self.runner.run_monitored_subprocess(
            process=process,
            stdin_data=None,
            timeout=None,
            limits=None,
            monitor_class=None,
            monitor_interval=1.0,
            monitor=None,
            runtime_handle=self.ext.RunnerHandle(
                runner="host",
                kind="process",
                id=str(process.pid),
                control={"authority": "host-pid"},
                observations={},
            ),
            cancel_requested=cancel,
            on_worker_started=None,
            on_runtime_handle_started=None,
            on_stdout_chunk=sink,
            stop_runtime=process.terminate,
            kill_runtime=process.kill,
        )

    def idle(self, mode: str) -> list[dict[str, Any]]:
        samples = []
        for _ in range(3):
            with self.child("import time; time.sleep(3)") as process:
                before = self.counters.snapshot()
                outcome = self.supervise(
                    process, None if mode == "none" else lambda: False
                )
                after = self.counters.snapshot(closing=True)
                require(outcome.status == "ok", repr(outcome))
                samples.append(
                    {
                        "before": before,
                        "after": after,
                        "delta": self.counters.delta(before, after),
                    }
                )
        return samples

    def output_latency(self) -> list[float]:
        samples: list[float] = []
        fragment = ""

        def sink(chunk: str, final: bool) -> None:
            nonlocal fragment
            observed = time.monotonic()
            fragment += chunk
            while "\n" in fragment:
                line, fragment = fragment.split("\n", 1)
                require(line.startswith("T"), repr(line))
                samples.append(1000 * (observed - float(line[1:])))
            if final:
                require(not fragment, repr(fragment))

        script = "import time\nfor _ in range(10):\n time.sleep(.1); print('T%r' % time.monotonic(), flush=True)"
        with self.child(script) as process:
            outcome = self.supervise(process, sink=sink)
            require(outcome.status == "ok", repr(outcome))
        require(len(samples) == 10 and not fragment, repr(samples))
        return samples

    def cancel_sample(self) -> float:
        event = threading.Event()
        fired: list[float] = []

        def fire() -> None:
            time.sleep(0.5)
            fired.append(time.monotonic())
            event.set()

        with self.child("import time; time.sleep(30)") as process:
            timer = threading.Thread(target=fire)
            timer.start()
            try:
                outcome = self.supervise(process, event.is_set)
                done = time.monotonic()
            finally:
                timer.join(timeout=5)
            require(
                not timer.is_alive() and outcome.status == "cancelled", repr(outcome)
            )
            return 1000 * (done - fired[0])

    def run(self) -> dict[str, Any]:
        calibration = self.counters.calibrate()
        idle = {mode: self.idle(mode) for mode in ("none", "pull")}
        output = self.output_latency()
        cancel = [self.cancel_sample() for _ in range(5)]
        primary = "resource_self" if self.counters.resource is not None else "psutil"
        rows: dict[str, Any] = {
            f"idle_{mode}_{metric}": statistics.median(
                s["delta"][primary][metric] for s in samples
            )
            for mode, samples in idle.items()
            for metric in ("cpu_ms", "context_switches")
        }
        coverage = (
            "complete"
            if self.arm == "after"
            or calibration["includes_exited_thread_evidence"][primary]
            else "before-lower-bound"
        )
        for mode in idle:
            key = f"idle_{mode}_context_switches"
            rows[key] = {"median": rows[key], "coverage": coverage}
        rows.update(
            {
                name: {"median": statistics.median(samples), "max": max(samples)}
                for name, samples in (
                    ("output_latency_ms", output),
                    ("cancel_latency_ms", cancel),
                )
            }
        )
        return {
            "constructor": "Popen(stdin=None)"
            if self.arm == "before"
            else "open_supervised_process(pipe_stdin=False)",
            "primary_counter": primary,
            "calibration": calibration,
            "raw": {
                "idle": idle,
                "output_latency_ms": output,
                "cancel_latency_ms": cancel,
            },
            "rows": rows,
            "limitations": [
                "Context-switch coverage is supported only where joined-thread calibration passes; otherwise before is a lower bound excluding exited threads.",
                "Windows psutil context-switch counts can omit exited threads. Windows CPU time is quantized; zero does not imply zero cost.",
                "Single runner pair; no speed threshold or cross-platform counter comparison.",
            ],
        }


def run_worker(args: argparse.Namespace) -> None:
    root = args.root.resolve()
    os.chdir(root)
    sys.path.insert(0, str(root))
    measurement = Measurements(args.arm)
    # Fail fast on mixed source roots; check again below for lazy imports.
    module_provenance(root, args.repository, args.revision)
    result = measurement.run()
    modules = module_provenance(root, args.repository, args.revision)
    result.update(
        {
            "revision": args.revision,
            "arm": args.arm,
            "modules": modules,
            "dirty_imports": any(m["dirty"] for m in modules.values()),
            "platform": platform.platform(),
            "python": sys.version,
            "executable": sys.executable,
            "psutil": psutil.__version__,
        }
    )
    dump(args.output, result)


def reap_worker_tree(worker: subprocess.Popen[Any]) -> None:
    descendants: list[psutil.Process] = []
    with contextlib.suppress(psutil.NoSuchProcess):
        descendants = psutil.Process(worker.pid).children(recursive=True)
    worker.kill()
    for child in descendants:
        with contextlib.suppress(psutil.NoSuchProcess):
            child.kill()
    worker.wait(timeout=10)
    _, alive = psutil.wait_procs(descendants, timeout=10)
    require(
        not alive, f"Measurement descendants survived cleanup: {[p.pid for p in alive]}"
    )


def execute_arm(
    script: Path, root: Path, repository: Path, revision: str, arm: str, output: Path
) -> None:
    command = [
        sys.executable,
        "-I",
        str(script),
        "--worker",
        "--arm",
        arm,
        "--root",
        str(root),
        "--repository",
        str(repository),
        "--revision",
        revision,
        "--output",
        str(output),
    ]
    worker = subprocess.Popen(command, cwd=root)
    try:
        code = worker.wait(timeout=180)
    except BaseException:
        reap_worker_tree(worker)
        raise
    require(code == 0, f"{arm} measurement worker failed: {code}")


def resolve_baseline(root: Path, baseline: str) -> str:
    require(
        re.fullmatch(r"[0-9a-fA-F]{40}", baseline) is not None,
        "--baseline must be a complete 40-hex Git commit ID",
    )
    try:
        return git(root, "rev-parse", "--verify", f"{baseline}^{{commit}}")
    except subprocess.CalledProcessError:
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "fetch",
                "--depth=1",
                "--no-tags",
                "origin",
                baseline,
            ],
            check=True,
        )
        return git(root, "rev-parse", "--verify", f"{baseline}^{{commit}}")


def pair(args: argparse.Namespace) -> None:
    root, output = args.root.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    baseline = resolve_baseline(root, args.baseline)
    candidate = git(root, "rev-parse", "HEAD")
    script = Path(__file__).resolve()
    dump(
        output / "procedure.json",
        {
            "baseline": baseline,
            "candidate_checkout": candidate,
            "candidate_dirty_status": git(
                root, "status", "--porcelain", "--untracked-files=no"
            ),
            "github": {
                key: os.environ.get(key)
                for key in ("GITHUB_SHA", "GITHUB_REF", "GITHUB_EVENT_NAME")
            },
            "script_sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
            "order": ["before", "after"],
            "idle_samples_per_mode": 3,
            "idle_seconds": 3,
            "output_samples": 10,
            "cancel_samples": 5,
        },
    )
    with tempfile.TemporaryDirectory(prefix="weft-reactor-baseline-") as temporary:
        temp = Path(temporary)
        archive, tree = temp / "baseline.tar", temp / "tree"
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "archive",
                "--format=tar",
                f"--output={archive}",
                baseline,
            ],
            check=True,
        )
        tree.mkdir()
        with tarfile.open(archive) as tar:
            tar.extractall(tree, filter="data")
        for arm, source, revision in (
            ("before", tree, baseline),
            ("after", root, candidate),
        ):
            execute_arm(script, source, root, revision, arm, output / f"{arm}.json")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    parser.add_argument("--baseline")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--arm", choices=("before", "after"))
    parser.add_argument("--revision")
    parser.add_argument("--repository", type=Path)
    parser.add_argument("--verify-windows", type=Path)
    args = parser.parse_args()
    if args.verify_windows:
        verify_windows(args.verify_windows)
    elif args.worker:
        require(
            all((args.arm, args.revision, args.repository, args.output)),
            "Incomplete worker arguments",
        )
        run_worker(args)
    else:
        require(
            args.baseline is not None and args.output is not None,
            "Pair requires --baseline and --output",
        )
        pair(args)


if __name__ == "__main__":
    main()
