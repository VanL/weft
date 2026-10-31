"""Passive environment canary for the Windows CI stall investigation.

Diagnostic-branch only; not part of the test contract. The canary runs as a
separate process beside an unchanged pytest invocation and records what the
VM's storage, process-creation, CPU, memory, and antimalware paths are doing.
It opens nothing owned by a test subject and never changes a test outcome.

Each probe runs on its own thread with its own one-second schedule, so a
blocked operation on one volume cannot delay or hide the observation of
another volume, of interpreter spawn, or of the system counters. Records are
queued in memory and appended by one writer thread, so blocked output cannot
stall a probe; every record keeps its measurement timestamp, and the writer
stamps how long it waited in the queue and how long the previous append took,
which makes output blockage visible and distinct from probe blockage. A
thread that overruns its schedule records the overrun; the stop record names
threads still blocked at shutdown, shutdown succeeds only when that record's
append is confirmed, and otherwise the shutdown facts go to a side file.

Plan: docs/plans/2026-10-30-pr48-windows-root-cause-analysis.md
(section "Stall-profile finding and revised first discriminator").
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import queue
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psutil

PROBE_BYTES = 4096
ANTIMALWARE_NAMES = frozenset(
    {
        "msmpeng.exe",
        "nissrv.exe",
        "mssense.exe",
        "sensendr.exe",
        "mpdefendercoreservice.exe",
    }
)
IMDS_URL = "http://169.254.169.254/metadata/instance/compute?api-version=2021-02-01"
PROBE_JOIN_SECONDS = 5.0
"""Shared deadline for every probe thread at shutdown."""
WRITER_JOIN_SECONDS = 3.0
"""Time the writer gets to drain after the stop record."""
SIDE_FILE_SECONDS = 2.0
"""Bound on the fallback side-file write; total shutdown stays about 10 s against the workflow's 20 s force-kill."""


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 3)


def fsync_write_latency(path: Path) -> dict[str, Any]:
    """Write and fsync one small file in place; the cost SQLite commits pay."""
    started = time.perf_counter()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, b"x" * PROBE_BYTES)
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as exc:
        return {"ms": _ms(started), "error": repr(exc)}
    return {"ms": _ms(started)}


def create_close_delete_latency(directory: Path) -> dict[str, Any]:
    """Create a new file, close it, and delete it; what on-access scanners see."""
    path = directory / f"canary-{time.time_ns()}.tmp"
    started = time.perf_counter()
    try:
        with path.open("wb") as stream:
            stream.write(b"y")
        path.unlink()
    except OSError as exc:
        return {"ms": _ms(started), "error": repr(exc)}
    return {"ms": _ms(started)}


def spawn_latency() -> dict[str, Any]:
    """Wall time of the smallest interpreter start, the unit every Weft task pays."""
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-c", "pass"],
            capture_output=True,
            timeout=120.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ms": _ms(started), "error": repr(exc)}
    return {"ms": _ms(started), "returncode": completed.returncode}


def antimalware_cpu_seconds() -> dict[str, Any]:
    """Cumulative CPU seconds per antimalware process; denied is a fact, not a gap."""
    result: dict[str, Any] = {}
    for proc in psutil.process_iter(["name"]):
        name = (proc.info.get("name") or "").lower()
        if name not in ANTIMALWARE_NAMES:
            continue
        key = f"{name}:{proc.pid}"
        try:
            times = proc.cpu_times()
            result[key] = round(times.user + times.system, 3)
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess) as exc:
            result[key] = type(exc).__name__
    return result


def disk_counters() -> dict[str, dict[str, int]]:
    """Cumulative per-physical-disk counters; an unreadable table is recorded as empty."""
    try:
        counters = psutil.disk_io_counters(perdisk=True)
    except (OSError, RuntimeError):
        return {}
    return {
        name: {
            "read_count": c.read_count,
            "write_count": c.write_count,
            "read_bytes": c.read_bytes,
            "write_bytes": c.write_bytes,
            "read_time": c.read_time,
            "write_time": c.write_time,
        }
        for name, c in counters.items()
    }


def disk_deltas(
    previous: dict[str, dict[str, int]], current: dict[str, dict[str, int]]
) -> dict[str, dict[str, float]]:
    """Per-interval operation counts and mean latency per operation, by disk."""
    deltas: dict[str, dict[str, float]] = {}
    for name, now in current.items():
        before = previous.get(name)
        if before is None:
            continue
        reads = now["read_count"] - before["read_count"]
        writes = now["write_count"] - before["write_count"]
        read_time = now["read_time"] - before["read_time"]
        write_time = now["write_time"] - before["write_time"]
        deltas[name] = {
            "reads": reads,
            "writes": writes,
            "read_bytes": now["read_bytes"] - before["read_bytes"],
            "write_bytes": now["write_bytes"] - before["write_bytes"],
            "read_ms_per_op": round(read_time / reads, 3) if reads else 0.0,
            "write_ms_per_op": round(write_time / writes, 3) if writes else 0.0,
        }
    return deltas


def _powershell_json(script: str, timeout: float = 60.0) -> Any:
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": repr(exc)}
    if completed.returncode != 0:
        return {
            "error": f"exit {completed.returncode}",
            "stderr": completed.stderr[-2000:],
        }
    try:
        return json.loads(completed.stdout or "null")
    except ValueError:
        return {"error": "non-json output", "stdout": completed.stdout[-2000:]}


def _imds() -> Any:
    request = urllib.request.Request(IMDS_URL, headers={"Metadata": "true"})
    try:
        with urllib.request.urlopen(request, timeout=2.0) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        return {"error": repr(exc)}


def start_record(probe_dirs: dict[str, Path]) -> dict[str, Any]:
    """Cheap facts, emitted before any probe starts so sampling begins at once."""
    return {
        "event": "start",
        "time_ns": time.time_ns(),
        "monotonic": time.monotonic(),
        "pid": os.getpid(),
        "python": sys.version,
        "platform": platform.platform(),
        "psutil": psutil.__version__,
        "cpu_count": psutil.cpu_count(),
        "memory_total": psutil.virtual_memory().total,
        "probe_dirs": {label: str(path) for label, path in probe_dirs.items()},
        "environment": {
            key: os.environ.get(key)
            for key in (
                "TEMP",
                "TMP",
                "RUNNER_TEMP",
                "GITHUB_WORKSPACE",
                "GITHUB_RUN_ID",
                "GITHUB_JOB",
            )
        },
    }


def metadata_record() -> dict[str, Any]:
    """Slow environment facts (PowerShell queries, instance metadata); gathered on a thread."""
    record: dict[str, Any] = {
        "event": "metadata",
        "time_ns": time.time_ns(),
        "monotonic": time.monotonic(),
    }
    try:
        record["partitions"] = [
            {
                "device": p.device,
                "mountpoint": p.mountpoint,
                "fstype": p.fstype,
                "opts": p.opts,
            }
            for p in psutil.disk_partitions(all=False)
        ]
    except OSError as exc:
        record["partitions"] = {"error": repr(exc)}
    if os.name == "nt":
        record["defender_status"] = _powershell_json(
            "Get-MpComputerStatus | Select-Object RealTimeProtectionEnabled,AntivirusEnabled,"
            "BehaviorMonitorEnabled,OnAccessProtectionEnabled,IoavProtectionEnabled,"
            "AntivirusSignatureVersion,QuickScanStartTime,FullScanStartTime | ConvertTo-Json"
        )
        record["defender_preference"] = _powershell_json(
            "Get-MpPreference | Select-Object DisableRealtimeMonitoring,ExclusionPath,"
            "ExclusionProcess,ScanAvgCPULoadFactor | ConvertTo-Json"
        )
        record["volumes"] = _powershell_json(
            "Get-Volume | Where-Object DriveLetter | Select-Object DriveLetter,FileSystemType,"
            "Size,SizeRemaining | ConvertTo-Json"
        )
        record["physical_disks"] = _powershell_json(
            "Get-PhysicalDisk | Select-Object DeviceId,FriendlyName,MediaType,BusType,Size"
            " | ConvertTo-Json"
        )
    record["imds"] = _imds()
    record["collection_seconds"] = round(time.monotonic() - record["monotonic"], 3)
    return record


class Sink:
    """In-memory queue drained to one JSONL file by a dedicated writer thread.

    Every emitted record is copied, so no caller ever shares a dictionary with
    the writer. Each appended record carries ``queue_wait_ms`` (time from
    emit to dequeue) and ``previous_append_ms`` (how long the writer spent in
    write and flush for the record before it), so a blocked append is visible
    in the next record and in the shutdown facts rather than hidden.
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._queue: queue.SimpleQueue[
            tuple[dict[str, Any], float, threading.Event] | None
        ] = queue.SimpleQueue()
        self._lock = threading.Lock()
        self.queued = 0
        self.written = 0
        self.write_errors = 0
        self.last_append_ms = 0.0
        self._thread = threading.Thread(
            target=self._drain, name="canary-writer", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def emit(self, record: dict[str, Any]) -> threading.Event:
        """Queue a copy of *record*; the returned event is set only on a confirmed append."""
        done = threading.Event()
        with self._lock:
            self.queued += 1
        self._queue.put((dict(record), time.monotonic(), done))
        return done

    def _drain(self) -> None:
        with self._path.open("a", encoding="utf-8") as stream:
            while True:
                item = self._queue.get()
                if item is None:
                    return
                record, queued_at, done = item
                record["queue_wait_ms"] = round(
                    (time.monotonic() - queued_at) * 1000.0, 3
                )
                record["previous_append_ms"] = self.last_append_ms
                append_started = time.monotonic()
                try:
                    stream.write(json.dumps(record, default=str) + "\n")
                    stream.flush()
                except (OSError, ValueError):
                    with self._lock:
                        self.write_errors += 1
                else:
                    with self._lock:
                        self.written += 1
                    done.set()
                self.last_append_ms = round(
                    (time.monotonic() - append_started) * 1000.0, 3
                )

    def close(self, timeout: float) -> dict[str, Any]:
        """Stop the writer; report what it could not append within *timeout*."""
        self._queue.put(None)
        self._thread.join(timeout)
        with self._lock:
            return {
                "writer_alive": self._thread.is_alive(),
                "records_queued": self.queued,
                "records_written": self.written,
                "write_errors": self.write_errors,
                "records_unwritten": self.queued - self.written - self.write_errors,
                "last_append_ms": self.last_append_ms,
            }


def run_scheduled(
    name: str,
    interval: float,
    stop: threading.Event,
    sink: Sink,
    measure: Callable[[], dict[str, Any]],
) -> None:
    """Run one probe on a fixed schedule; record its own overruns."""
    sample = 0
    next_due = time.monotonic()
    while not stop.is_set():
        sample += 1
        started = time.monotonic()
        record: dict[str, Any] = {
            "event": name,
            "n": sample,
            "time_ns": time.time_ns(),
            "monotonic": started,
            "schedule_overrun_ms": round((started - next_due) * 1000.0, 3),
        }
        record.update(measure())
        record["sample_ms"] = round((time.monotonic() - started) * 1000.0, 3)
        sink.emit(record)
        next_due = max(next_due + interval, time.monotonic())
        stop.wait(max(0.0, next_due - time.monotonic()))


def volume_measure(label: str, directory: Path) -> Callable[[], dict[str, Any]]:
    def measure() -> dict[str, Any]:
        return {
            "label": label,
            "fsync": fsync_write_latency(directory / "fsync-probe.bin"),
            "create_close_delete": create_close_delete_latency(directory),
        }

    return measure


def spawn_measure() -> dict[str, Any]:
    return {"spawn": spawn_latency()}


class SystemSampler:
    """CPU, memory, process count, disk counter deltas, antimalware CPU deltas."""

    def __init__(self) -> None:
        psutil.cpu_percent(interval=None)
        self._disks = disk_counters()
        self._antimalware = antimalware_cpu_seconds()

    def measure(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memory_available": psutil.virtual_memory().available,
            "process_count": len(psutil.pids()),
        }
        disks = disk_counters()
        record["disks"] = disk_deltas(self._disks, disks)
        self._disks = disks
        antimalware = antimalware_cpu_seconds()
        record["antimalware_cpu_delta"] = {
            key: round(value - self._antimalware[key], 3)
            if isinstance(value, float)
            and isinstance(self._antimalware.get(key), float)
            else value
            for key, value in antimalware.items()
        }
        self._antimalware = antimalware
        return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--output", required=True, help="JSONL file to append samples to"
    )
    parser.add_argument(
        "--stop-file", required=True, help="exit promptly once this path exists"
    )
    parser.add_argument(
        "--probe-dir",
        action="append",
        default=[],
        metavar="LABEL=PATH",
        help="directory to probe for storage latency; repeatable",
    )
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument(
        "--spawn-interval", type=float, default=2.0, help="seconds between spawn probes"
    )
    parser.add_argument("--max-seconds", type=float, default=3600.0)
    args = parser.parse_args(argv)

    probe_dirs: dict[str, Path] = {}
    for item in args.probe_dir:
        label, _, raw = item.partition("=")
        if not label or not raw:
            parser.error(f"--probe-dir expects LABEL=PATH, got {item!r}")
        path = Path(raw)
        path.mkdir(parents=True, exist_ok=True)
        probe_dirs[label] = path

    sink = Sink(Path(args.output))
    sink.start()
    stop_file = Path(args.stop_file)
    stop = threading.Event()
    sink.emit(start_record(probe_dirs))

    threads = [
        threading.Thread(
            target=run_scheduled,
            args=(
                "volume",
                args.interval,
                stop,
                sink,
                volume_measure(label, directory),
            ),
            name=f"canary-volume-{label}",
            daemon=True,
        )
        for label, directory in probe_dirs.items()
    ]
    if args.spawn_interval > 0:
        threads.append(
            threading.Thread(
                target=run_scheduled,
                args=("spawn", args.spawn_interval, stop, sink, spawn_measure),
                name="canary-spawn",
                daemon=True,
            )
        )
    threads.append(
        threading.Thread(
            target=run_scheduled,
            args=("system", args.interval, stop, sink, SystemSampler().measure),
            name="canary-system",
            daemon=True,
        )
    )
    for thread in threads:
        thread.start()
    threading.Thread(
        target=lambda: sink.emit(metadata_record()),
        name="canary-metadata",
        daemon=True,
    ).start()

    started = time.monotonic()
    reason = "max_seconds"
    while time.monotonic() - started < args.max_seconds:
        if stop_file.exists():
            reason = "stop_file"
            break
        time.sleep(0.25)
    stop.set()
    return shutdown(sink, threads, reason, started, Path(args.output))


def shutdown(
    sink: Sink,
    threads: list[threading.Thread],
    reason: str,
    started: float,
    output: Path,
) -> int:
    """Join probes and writer under a fixed budget; report what could not be written.

    Returns 0 only when the stop record's append is confirmed and nothing is
    left unwritten; 2 when the shutdown facts had to go to the side file; 3
    when even the side file could not be written within its bound.
    """
    shutdown_started = time.monotonic()
    join_deadline = shutdown_started + PROBE_JOIN_SECONDS
    for thread in threads:
        thread.join(max(0.0, join_deadline - time.monotonic()))
    stop_record: dict[str, Any] = {
        "event": "stop",
        "time_ns": time.time_ns(),
        "monotonic": time.monotonic(),
        "reason": reason,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "threads_still_blocked": [t.name for t in threads if t.is_alive()],
    }
    stop_written = sink.emit(stop_record)
    writer = sink.close(WRITER_JOIN_SECONDS)
    if stop_written.is_set() and not writer["records_unwritten"]:
        return 0
    # The stop record was not confirmed appended: leave the shutdown facts in
    # a side file so a blocked or failing writer is reported, not inferred.
    side_record = dict(stop_record)
    side_record.update(writer)
    side_record["stop_record_written"] = stop_written.is_set()
    side_record["shutdown_seconds"] = round(time.monotonic() - shutdown_started, 3)
    side = output.with_suffix(".stop.json")
    side_done = threading.Event()

    def write_side() -> None:
        try:
            side.write_text(json.dumps(side_record, default=str), encoding="utf-8")
        except OSError:
            return
        side_done.set()

    threading.Thread(target=write_side, name="canary-side", daemon=True).start()
    side_done.wait(SIDE_FILE_SECONDS)
    return 2 if side_done.is_set() else 3


if __name__ == "__main__":
    raise SystemExit(main())
