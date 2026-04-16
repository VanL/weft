"""Short-lived detached-launch wrapper for manager bootstrap.

Spec references:
- docs/specifications/03-Manager_Architecture.md [MA-3]
- docs/specifications/05-Message_Flow_and_State.md [MF-7]
"""

from __future__ import annotations

import base64
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from weft._constants import (
    MANAGER_LAUNCHER_POLL_INTERVAL,
    MANAGER_LAUNCHER_SIGNAL_SUCCESS,
)
from weft.helpers import terminate_process_tree


def _emit_event(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _launch_runtime(command: list[str], stderr_path: Path) -> subprocess.Popen[Any]:
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_file = stderr_path.open("w", encoding="utf-8")
    creationflags = 0
    popen_kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": stderr_file,
        "close_fds": True,
    }
    if os.name == "nt":
        creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        if creationflags:
            popen_kwargs["creationflags"] = creationflags
    else:
        popen_kwargs["start_new_session"] = True
    try:
        return subprocess.Popen(command, **popen_kwargs)
    finally:
        stderr_file.close()


def _read_parent_signal(
    signal_queue: queue.SimpleQueue[str | None],
) -> str | None | object:
    try:
        return signal_queue.get_nowait()
    except queue.Empty:
        return _NO_SIGNAL


def _enqueue_parent_signal(
    signal_queue: queue.SimpleQueue[str | None],
) -> None:
    line = sys.stdin.readline()
    if line == "":
        signal_queue.put(None)
        return
    signal_queue.put(line.strip() or None)


def _terminate_runtime(process: subprocess.Popen[Any]) -> None:
    pid = process.pid
    if not isinstance(pid, int) or pid <= 0:
        return
    terminated = terminate_process_tree(pid, timeout=1.0)
    if terminated:
        return
    try:
        process.terminate()
    except Exception:
        return
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except Exception:
            return
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            return


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        sys.stderr.write("manager_detached_launcher requires 1 payload argument\n")
        return 2

    try:
        payload_json = base64.b64decode(args[0]).decode("utf-8")
        payload = json.loads(payload_json)
        command = payload["command"]
        stderr_path = Path(payload["stderr_path"])
    except Exception as exc:  # pragma: no cover - defensive
        sys.stderr.write(f"Invalid detached launcher payload: {exc}\n")
        return 2

    if not isinstance(command, list) or not all(
        isinstance(item, str) for item in command
    ):
        sys.stderr.write("Invalid detached launcher command payload\n")
        return 2

    try:
        runtime = _launch_runtime(command, stderr_path)
    except Exception as exc:
        _emit_event(
            {
                "event": "spawn_failed",
                "error": str(exc),
                "stderr_path": str(stderr_path),
            }
        )
        return 2

    _emit_event(
        {
            "event": "spawned",
            "pid": runtime.pid,
            "stderr_path": str(stderr_path),
        }
    )

    signal_queue: queue.SimpleQueue[str | None] = queue.SimpleQueue()
    reader = threading.Thread(
        target=_enqueue_parent_signal,
        args=(signal_queue,),
        daemon=True,
    )
    reader.start()

    while True:
        returncode = runtime.poll()
        if returncode is not None:
            _emit_event(
                {
                    "event": "child_exit",
                    "pid": runtime.pid,
                    "returncode": returncode,
                }
            )
            return 10

        signal_name = _read_parent_signal(signal_queue)
        if signal_name is _NO_SIGNAL:
            time.sleep(MANAGER_LAUNCHER_POLL_INTERVAL)
            continue
        if signal_name == MANAGER_LAUNCHER_SIGNAL_SUCCESS:
            return 0

        _terminate_runtime(runtime)
        return 11


_NO_SIGNAL = object()


if __name__ == "__main__":  # pragma: no cover - invoked via subprocess
    sys.exit(main())
