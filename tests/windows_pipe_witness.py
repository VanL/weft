"""Temporary native diagnostic only; not an acceptance test or product change."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import psutil

if sys.platform == "win32":
    import _winapi

from tests.core.test_subprocess_runner import (
    _reap_test_process,
    _supervise_real_process,
    _wait_for_child_marker,
)
from weft.core.runners import subprocess_runner

# Win32 process access rights absent from _winapi exports.
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001


CHILD = """import errno,json,os,sys,time
from pathlib import Path
ready,error,complete,release=map(Path,sys.argv[1:5])
writes={1:0,2:0}; errors={}; fatal=None
tmp=ready.with_suffix('.tmp'); tmp.write_text(str(os.getpid())); tmp.replace(ready)
while not release.exists():
 for fd in (1,2):
  try:
   count=os.write(fd,(b'x' if fd==1 else b'y')*4096)
   writes[fd]+=count
  except OSError as exc:
   record={'fd':fd,'type':type(exc).__name__,'errno':exc.errno,'winerror':getattr(exc,'winerror',None),'message':str(exc),'successful_bytes':dict(writes),'monotonic':time.monotonic()}
   unexpected=not isinstance(exc,BrokenPipeError)
   if fd not in errors or unexpected:
    errors.setdefault(fd,record)
    if unexpected: fatal=record
    evidence={'errors':errors,'fatal':fatal,'successful_bytes':writes}
    tmp=error.with_suffix('.tmp'); tmp.write_text(json.dumps(evidence)); tmp.replace(error)
   if len(errors)==2 or fatal is not None: complete.touch()
   if unexpected:
    raise
   time.sleep(.01)
"""
ROOT = """import subprocess,sys
print('ROOT_OUT',flush=True); print('ROOT_ERR',file=sys.stderr,flush=True)
subprocess.Popen([sys.executable,'-u','-c',sys.argv[1],*sys.argv[2:6]])
"""


def probe(output: Path, mode: str, index: int) -> dict[str, object]:
    case = output / f"{mode}-{index}"
    case.mkdir()
    ready, error, complete, release = (
        case / name for name in ("ready", "error.json", "complete", "release")
    )
    process = subprocess_runner.open_supervised_process(
        [
            sys.executable,
            "-u",
            "-c",
            ROOT,
            CHILD,
            str(ready),
            str(error),
            str(complete),
            str(release),
        ],
        encoding="utf-8",
        errors="replace",
    )
    descendant_handle = None
    record: dict[str, object] = {"mode": mode, "index": index}
    try:
        _wait_for_child_marker(ready)
        descendant_pid = int(ready.read_text())
        descendant_handle = _winapi.OpenProcess(
            _winapi.SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE,
            False,
            descendant_pid,
        )
        record["descendant_pid"] = descendant_pid
        record["exit_at_open"] = _winapi.GetExitCodeProcess(descendant_handle)
        assert record["exit_at_open"] == _winapi.STILL_ACTIVE, (
            "descendant exited before handle acquisition"
        )
        process.wait(timeout=5)
        if mode == "supervisor":
            outcome = _supervise_real_process(process, timeout=0.2)
            record.update(
                status=outcome.status,
                returncode=outcome.returncode,
                stdout_chars=len(outcome.stdout or ""),
                stderr_chars=len(outcome.stderr or ""),
            )
        else:
            subprocess_runner.close_supervised_pipes(
                process.stdout, process.stderr, process.stdin
            )
        _wait_for_child_marker(complete)
        # These are sequential observations, not an atomic process snapshot.
        record["observation_wait_ms"] = 2000
        record["wait_before_release"] = _winapi.WaitForSingleObject(
            descendant_handle, 2000
        )
        record["exit_before_release"] = _winapi.GetExitCodeProcess(descendant_handle)
    except (
        AssertionError,
        OSError,
        RuntimeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        record["probe_error"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        try:
            try:
                release.touch()
                subprocess_runner.close_supervised_pipes(
                    process.stdout, process.stderr, process.stdin
                )
                _reap_test_process(process)
            finally:
                try:
                    if descendant_handle is not None:
                        if (
                            _winapi.WaitForSingleObject(descendant_handle, 5000)
                            == _winapi.WAIT_TIMEOUT
                        ):
                            _winapi.TerminateProcess(descendant_handle, 1)
                            record["forced_cleanup"] = True
                            _winapi.WaitForSingleObject(descendant_handle, 5000)
                        record["final_exit"] = _winapi.GetExitCodeProcess(
                            descendant_handle
                        )
                finally:
                    if descendant_handle is not None:
                        _winapi.CloseHandle(descendant_handle)
        finally:
            if error.exists():
                record["final_write_error"] = json.loads(error.read_text())
            (case / "observation.json").write_text(json.dumps(record, indent=2))
    return record


def main() -> None:
    assert sys.platform == "win32", "This probe requires native Windows"
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[1]
    runtime = Path(subprocess_runner.__file__).resolve()
    assert runtime == root / "weft/core/runners/subprocess_runner.py"
    records = [
        probe(output, mode, index)
        for mode in ("supervisor", "reader-close")
        for index in range(5)
    ]
    report = {
        "head": os.environ.get("GITHUB_SHA"),
        "python": sys.version,
        "platform": platform.platform(),
        "psutil": psutil.__version__,
        "runtime": str(runtime),
        "runtime_sha256": hashlib.sha256(runtime.read_bytes()).hexdigest(),
        "runtime_blob": subprocess.check_output(
            ["git", "rev-parse", "HEAD:weft/core/runners/subprocess_runner.py"],
            text=True,
        ).strip(),
        "observations": records,
    }
    (output / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2), flush=True)
    if any("probe_error" in record or "forced_cleanup" in record for record in records):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
