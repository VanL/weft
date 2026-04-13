"""Shared manager lifecycle helpers for CLI commands.

Spec references:
- docs/specifications/03-Manager_Architecture.md [MA-1], [MA-3]
- docs/specifications/05-Message_Flow_and_State.md [MF-7]
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

import typer

from simplebroker import Queue, serialize_broker_target
from weft._constants import (
    QUEUE_CTRL_IN_SUFFIX,
    QUEUE_CTRL_OUT_SUFFIX,
    WEFT_MANAGER_LIFETIME_TIMEOUT,
    WEFT_MANAGER_OUTBOX_QUEUE,
    WEFT_MANAGERS_REGISTRY_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
)
from weft.context import WeftContext
from weft.core.spawn_requests import generate_spawn_request_timestamp
from weft.core.taskspec import TaskSpec, resolve_taskspec_payload
from weft.helpers import (
    is_canonical_manager_record,
    iter_queue_json_entries,
    pid_is_live,
    terminate_process_tree,
)
from weft.manager_process import run_manager_process

_MANAGER_POLL_INTERVAL = 0.05
_MANAGER_TASK_CLASS_PATH = "weft.core.manager.Manager"
_MANAGER_STARTUP_TIMEOUT = 10.0
_MANAGER_STARTUP_LOG_DIRNAME = "manager-startup"
_LAUNCHER_SIGNAL_SUCCESS = "SUCCESS"
_LAUNCHER_SIGNAL_ABORT = "ABORT"


@dataclass(frozen=True)
class _ManagerRuntimeInvocation:
    """Canonical manager runtime inputs shared by detached and foreground launchers.

    Spec: docs/specifications/03-Manager_Architecture.md [MA-3]
    """

    task_cls_path: str
    tid: str
    spec: TaskSpec


@dataclass(frozen=True)
class _DetachedManagerLaunch:
    """Bootstrap metadata for a detached manager runtime."""

    pid: int
    stderr_path: Path
    launcher_process: subprocess.Popen[str]


def _generate_tid(context: WeftContext) -> str:
    """Generate a unique TID via broker timestamp (Spec: [MA-2])."""
    return str(
        generate_spawn_request_timestamp(
            context.broker_target,
            config=context.broker_config,
        )
    )


def _registry_queue(context: WeftContext) -> Queue:
    return Queue(
        WEFT_MANAGERS_REGISTRY_QUEUE,
        db_path=context.broker_target,
        persistent=False,
        config=context.broker_config,
    )


def _normalize_manager_record(
    payload: dict[str, Any],
    *,
    timestamp: int,
) -> dict[str, Any]:
    record = dict(payload)
    record.pop("_timestamp", None)
    record["timestamp"] = int(timestamp)
    record.setdefault("requests", WEFT_SPAWN_REQUESTS_QUEUE)
    record.setdefault("role", "manager")
    return record


def _snapshot_registry(
    context: WeftContext, *, prune_stale: bool = True
) -> dict[str, dict[str, Any]]:
    queue = _registry_queue(context)
    snapshot: dict[str, dict[str, Any]] = {}
    stale_timestamps: list[int] = []
    for data, timestamp in iter_queue_json_entries(queue):
        tid = data.get("tid")
        if not tid:
            continue
        record = _normalize_manager_record(data, timestamp=timestamp)
        if prune_stale and record.get("status") == "active":
            pid = record.get("pid")
            if isinstance(pid, int) and not _is_pid_alive(pid):
                stale_timestamps.append(timestamp)
                continue
        existing = snapshot.get(tid)
        existing_ts = int(existing.get("timestamp", -1)) if existing else -1
        if existing is None or existing_ts < timestamp:
            snapshot[tid] = record

    for ts in stale_timestamps:
        try:
            queue.delete(message_id=ts)
        except Exception:
            pass

    return snapshot


def _manager_record(
    context: WeftContext,
    tid: str,
    *,
    prune_stale: bool = True,
) -> dict[str, Any] | None:
    return _snapshot_registry(context, prune_stale=prune_stale).get(tid)


def _list_manager_records(
    context: WeftContext,
    *,
    include_stopped: bool = False,
    canonical_only: bool = False,
    prune_stale: bool = True,
) -> list[dict[str, Any]]:
    records = list(_snapshot_registry(context, prune_stale=prune_stale).values())
    if canonical_only:
        records = [record for record in records if is_canonical_manager_record(record)]
    if not include_stopped:
        records = [record for record in records if record.get("status") != "stopped"]
    records.sort(key=lambda rec: int(rec.get("timestamp", 0)), reverse=True)
    return records


def _select_active_manager(context: WeftContext) -> dict[str, Any] | None:
    candidates = []
    for record in _list_manager_records(
        context,
        include_stopped=False,
        canonical_only=True,
        prune_stale=True,
    ):
        if record.get("status") != "active":
            continue
        pid = record.get("pid")
        if isinstance(pid, int) and _is_pid_alive(pid):
            candidates.append(record)
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda rec: (int(rec.get("tid", 0)), rec.get("timestamp", 0)),
    )


def _is_pid_alive(pid: int | None) -> bool:
    return pid_is_live(pid)


def _lookup_manager_pid(context: WeftContext, tid: str) -> int | None:
    queue = Queue(
        WEFT_TID_MAPPINGS_QUEUE,
        db_path=context.broker_target,
        persistent=False,
        config=context.broker_config,
    )
    latest_timestamp = -1
    resolved_pid: int | None = None
    for data, timestamp in iter_queue_json_entries(queue):
        if data.get("full") != tid or timestamp < latest_timestamp:
            continue
        pid = data.get("pid")
        if isinstance(pid, int):
            latest_timestamp = timestamp
            resolved_pid = pid
    return resolved_pid


def _manager_ctrl_queue_name(tid: str, record: dict[str, Any] | None = None) -> str:
    if isinstance(record, dict):
        ctrl_in = record.get("ctrl_in")
        if isinstance(ctrl_in, str) and ctrl_in:
            return ctrl_in
    return f"T{tid}.{QUEUE_CTRL_IN_SUFFIX}"


def _send_stop(
    context: WeftContext, tid: str, *, record: dict[str, Any] | None
) -> None:
    queue = Queue(
        _manager_ctrl_queue_name(tid, record),
        db_path=context.broker_target,
        persistent=False,
        config=context.broker_config,
    )
    queue.write("STOP")


def _build_manager_spec(
    context: WeftContext,
    tid: str,
    *,
    idle_timeout_override: float | None = None,
) -> TaskSpec:
    idle_timeout = (
        float(idle_timeout_override)
        if idle_timeout_override is not None
        else float(
            context.config.get(
                "WEFT_MANAGER_LIFETIME_TIMEOUT", WEFT_MANAGER_LIFETIME_TIMEOUT
            )
        )
    )

    spec_dict = {
        "tid": tid,
        "name": "manager",
        "spec": {
            "type": "function",
            "function_target": "weft.core.manager:Manager",
            "timeout": None,
            "weft_context": str(context.root),
        },
        "io": {
            "inputs": {"inbox": WEFT_SPAWN_REQUESTS_QUEUE},
            "outputs": {"outbox": WEFT_MANAGER_OUTBOX_QUEUE},
            "control": {
                "ctrl_in": f"T{tid}.{QUEUE_CTRL_IN_SUFFIX}",
                "ctrl_out": f"T{tid}.{QUEUE_CTRL_OUT_SUFFIX}",
            },
        },
        "state": {},
        "metadata": {
            "role": "manager",
            "capabilities": [],
            "idle_timeout": idle_timeout,
        },
    }
    resolved_payload = resolve_taskspec_payload(spec_dict)
    return TaskSpec.model_validate(resolved_payload, context={"auto_expand": False})


def _build_manager_runtime_invocation(
    context: WeftContext,
    *,
    idle_timeout_override: float | None = None,
) -> _ManagerRuntimeInvocation:
    """Build canonical manager runtime inputs for all launcher modes.

    Spec: docs/specifications/03-Manager_Architecture.md [MA-3]
    """

    manager_tid = _generate_tid(context)
    manager_spec = _build_manager_spec(
        context,
        manager_tid,
        idle_timeout_override=idle_timeout_override,
    )
    return _ManagerRuntimeInvocation(
        task_cls_path=_MANAGER_TASK_CLASS_PATH,
        tid=manager_tid,
        spec=manager_spec,
    )


def _build_manager_process_command(
    context: WeftContext,
    invocation: _ManagerRuntimeInvocation,
) -> list[str]:
    """Encode the shared manager runtime invocation for detached startup.

    Spec: docs/specifications/03-Manager_Architecture.md [MA-3]
    """

    spec_json = invocation.spec.model_dump_json()
    broker_target_json = serialize_broker_target(context.broker_target)
    config_json = json.dumps(context.config)

    broker_target_b64 = base64.b64encode(broker_target_json.encode("utf-8")).decode(
        "ascii"
    )
    spec_b64 = base64.b64encode(spec_json.encode("utf-8")).decode("ascii")
    config_b64 = base64.b64encode(config_json.encode("utf-8")).decode("ascii")

    return [
        sys.executable,
        "-m",
        "weft.manager_process",
        invocation.task_cls_path,
        broker_target_b64,
        spec_b64,
        config_b64,
        str(_MANAGER_POLL_INTERVAL),
    ]


def _build_manager_detached_launcher_command(
    context: WeftContext,
    invocation: _ManagerRuntimeInvocation,
    stderr_path: Path,
) -> list[str]:
    """Build the detached-launch wrapper command for manager bootstrap.

    Spec: docs/specifications/03-Manager_Architecture.md [MA-3]
    """

    payload = {
        "command": _build_manager_process_command(context, invocation),
        "stderr_path": str(stderr_path),
    }
    payload_b64 = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    return [
        sys.executable,
        "-m",
        "weft.manager_detached_launcher",
        payload_b64,
    ]


def _manager_startup_stderr_path(context: WeftContext, tid: str) -> Path:
    startup_dir = context.logs_dir / _MANAGER_STARTUP_LOG_DIRNAME
    startup_dir.mkdir(parents=True, exist_ok=True)
    return startup_dir / f"manager-{tid}.stderr.log"


def _parse_launcher_event(line: str) -> dict[str, Any] | None:
    text = line.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _collect_launcher_events(stdout_text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in stdout_text.splitlines():
        payload = _parse_launcher_event(line)
        if payload is not None:
            events.append(payload)
    return events


def _tail_startup_stderr(path: Path, *, limit: int = 4000) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return None
    if not text:
        return None
    return text[-limit:].strip() or None


def _cleanup_startup_stderr(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        return


def _launch_detached_manager(
    context: WeftContext,
    invocation: _ManagerRuntimeInvocation,
) -> _DetachedManagerLaunch:
    stderr_path = _manager_startup_stderr_path(context, invocation.tid)
    launcher_process = subprocess.Popen(
        _build_manager_detached_launcher_command(context, invocation, stderr_path),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    first_line = ""
    if launcher_process.stdout is not None:
        first_line = launcher_process.stdout.readline()
    event = _parse_launcher_event(first_line)
    if event is None or event.get("event") != "spawned":
        _terminate_manager_process(launcher_process, timeout=1.0)
        stdout_text = first_line
        stderr_text = ""
        try:
            stdout_tail, stderr_text = launcher_process.communicate(timeout=1.0)
            stdout_text += stdout_tail
        except subprocess.TimeoutExpired:
            stdout_text = stdout_text.strip()
            stderr_text = (
                stderr_text.strip()
                or "Detached manager launcher produced no startup event."
            )
        error = "Detached manager launcher did not report a spawned manager PID."
        for payload in _collect_launcher_events(stdout_text):
            if payload.get("event") == "spawn_failed":
                reported_error = payload.get("error")
                if isinstance(reported_error, str) and reported_error:
                    error = reported_error
                    break
        details = [f"Failed to start Manager process: {error}"]
        if stderr_text.strip():
            details.append(stderr_text.strip())
        raise RuntimeError("\n".join(details))

    pid = event.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        _terminate_manager_process(launcher_process, timeout=1.0)
        try:
            launcher_process.communicate(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass
        raise RuntimeError(
            "Failed to start Manager process: detached launcher reported an invalid PID."
        )

    reported_path = event.get("stderr_path")
    launch_stderr_path = (
        Path(reported_path)
        if isinstance(reported_path, str) and reported_path
        else stderr_path
    )
    return _DetachedManagerLaunch(
        pid=pid,
        stderr_path=launch_stderr_path,
        launcher_process=launcher_process,
    )


def _send_launcher_signal(
    process: subprocess.Popen[str], signal_name: str
) -> tuple[bool, str | None]:
    if process.poll() is not None:
        return False, None
    if process.stdin is None:
        return False, "Detached manager launcher stdin is unavailable."
    try:
        process.stdin.write(f"{signal_name}\n")
        process.stdin.flush()
    except BrokenPipeError:
        return False, None
    except OSError as exc:
        return False, str(exc)
    return True, None


def _communicate_launcher(
    process: subprocess.Popen[str], *, timeout: float
) -> tuple[list[dict[str, Any]], str]:
    try:
        stdout_text, stderr_text = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate_manager_process(process, timeout=1.0)
        stdout_text, stderr_text = process.communicate()
    return _collect_launcher_events(stdout_text), stderr_text.strip()


def _format_manager_start_failure(
    *,
    message: str,
    launch: _DetachedManagerLaunch,
    launcher_events: list[dict[str, Any]],
    launcher_stderr: str,
) -> str:
    parts = [message]
    child_exit = next(
        (
            payload
            for payload in launcher_events
            if payload.get("event") == "child_exit"
        ),
        None,
    )
    if child_exit is not None:
        returncode = child_exit.get("returncode")
        if isinstance(returncode, int):
            parts.append(
                f"Detached manager exited early with return code {returncode}."
            )
    elif launch.launcher_process.returncode is not None:
        parts.append(
            f"Detached manager launcher exited with return code "
            f"{launch.launcher_process.returncode}."
        )

    stderr_tail = _tail_startup_stderr(launch.stderr_path)
    if stderr_tail:
        parts.append("Startup stderr tail:")
        parts.append(stderr_tail)
    elif launcher_stderr:
        parts.append("Detached launcher stderr:")
        parts.append(launcher_stderr)
    return "\n".join(parts)


def _fail_manager_start(
    *,
    launch: _DetachedManagerLaunch,
    message: str,
    abort_launcher: bool,
) -> NoReturn:
    launcher_events: list[dict[str, Any]] = []
    launcher_stderr = ""
    if abort_launcher:
        _send_launcher_signal(launch.launcher_process, _LAUNCHER_SIGNAL_ABORT)
    if launch.launcher_process.poll() is not None or abort_launcher:
        launcher_events, launcher_stderr = _communicate_launcher(
            launch.launcher_process,
            timeout=1.0,
        )
    typer.echo(
        _format_manager_start_failure(
            message=message,
            launch=launch,
            launcher_events=launcher_events,
            launcher_stderr=launcher_stderr,
        ),
        err=True,
    )
    raise typer.Exit(code=1)


def _acknowledge_manager_launch_success(launch: _DetachedManagerLaunch) -> None:
    sent, error = _send_launcher_signal(
        launch.launcher_process,
        _LAUNCHER_SIGNAL_SUCCESS,
    )
    launcher_events, launcher_stderr = _communicate_launcher(
        launch.launcher_process,
        timeout=2.0,
    )
    if sent and launch.launcher_process.returncode == 0:
        return
    message = "Detached manager launcher did not exit cleanly after success."
    if error:
        message = f"{message} {error}"
    raise RuntimeError(
        _format_manager_start_failure(
            message=message,
            launch=launch,
            launcher_events=launcher_events,
            launcher_stderr=launcher_stderr,
        )
    )


def _start_manager(
    context: WeftContext, *, verbose: bool
) -> tuple[dict[str, Any], bool, subprocess.Popen[Any] | None]:
    """Launch a new Manager process and wait for its registry entry (Spec: [MA-3])."""
    invocation = _build_manager_runtime_invocation(context)
    manager_tid = invocation.tid
    launch = _launch_detached_manager(context, invocation)

    if verbose:
        typer.echo(
            json.dumps(
                {
                    "manager_tid": manager_tid,
                    "pid": launch.pid,
                    "db": context.broker_display_target,
                },
                ensure_ascii=False,
            )
        )

    deadline = time.time() + _MANAGER_STARTUP_TIMEOUT
    competing_record: dict[str, Any] | None = None
    while time.time() < deadline:
        selected_record = _select_active_manager(context)
        if selected_record is not None:
            if selected_record.get("tid") != manager_tid:
                competing_record = selected_record
            else:
                if (
                    selected_record.get("status") == "active"
                    and is_canonical_manager_record(selected_record)
                    and selected_record.get("pid") == launch.pid
                    and _is_pid_alive(launch.pid)
                ):
                    try:
                        _acknowledge_manager_launch_success(launch)
                    except RuntimeError as exc:
                        typer.echo(str(exc), err=True)
                        raise typer.Exit(code=1) from exc
                    _cleanup_startup_stderr(launch.stderr_path)
                    if verbose:
                        _emit_manager_registry_snapshot(selected_record)
                    return selected_record, True, None

        if launch.launcher_process.poll() is not None:
            if competing_record is not None:
                _cleanup_startup_stderr(launch.stderr_path)
                return competing_record, False, None
            refreshed_record = _select_active_manager(context)
            if refreshed_record is not None:
                return refreshed_record, False, None
            _fail_manager_start(
                launch=launch,
                message="Failed to start Manager process; detached launcher exited before startup stabilized.",
                abort_launcher=False,
            )
        if not _is_pid_alive(launch.pid):
            if competing_record is not None:
                _send_launcher_signal(launch.launcher_process, _LAUNCHER_SIGNAL_ABORT)
                _communicate_launcher(launch.launcher_process, timeout=1.0)
                _cleanup_startup_stderr(launch.stderr_path)
                return competing_record, False, None
            _fail_manager_start(
                launch=launch,
                message="Failed to start Manager process; detached manager PID exited before startup stabilized.",
                abort_launcher=True,
            )
        time.sleep(0.1)

    if competing_record is not None:
        _send_launcher_signal(launch.launcher_process, _LAUNCHER_SIGNAL_ABORT)
        _communicate_launcher(launch.launcher_process, timeout=1.0)
        _cleanup_startup_stderr(launch.stderr_path)
        return competing_record, False, None

    _fail_manager_start(
        launch=launch,
        message="Failed to start Manager process; no stable canonical registry entry appeared.",
        abort_launcher=True,
    )


def _terminate_manager_process(
    process: subprocess.Popen[Any], *, timeout: float = 1.0
) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
    except Exception:  # pragma: no cover - defensive
        return
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:  # pragma: no cover - defensive
        try:
            process.kill()
        except Exception:
            return
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return


def _emit_manager_registry_snapshot(record: dict[str, Any]) -> None:
    """Emit a manager_started event mirroring legacy verbose output."""

    payload = {
        "event": "manager_started",
        "manager_tid": record.get("tid"),
        "pid": record.get("pid"),
        "queues": {
            key: record.get(key)
            for key in ("requests", "outbox", "ctrl_in", "ctrl_out")
            if record.get(key)
        },
        "timestamp": record.get("timestamp"),
    }
    typer.echo(json.dumps(payload, ensure_ascii=False))


def _ensure_manager(
    context: WeftContext, *, verbose: bool
) -> tuple[dict[str, Any], bool, subprocess.Popen[Any] | None]:
    """Guarantee a canonical active manager exists, starting one if necessary."""
    record = _select_active_manager(context)
    if record:
        pid = record.get("pid")
        if not (isinstance(pid, int) and _is_pid_alive(pid)):
            _snapshot_registry(context)
            record = _select_active_manager(context)
            if record is None:
                return _start_manager(context, verbose=verbose)
        if record:
            return record, False, None
    return _start_manager(context, verbose=verbose)


def _serve_manager_foreground(context: WeftContext) -> tuple[int, str | None]:
    """Run the canonical manager in the current process for supervisor use."""

    existing = _select_active_manager(context)
    if existing is not None:
        return (
            1,
            f"Manager {existing.get('tid')} already running (pid {existing.get('pid')})",
        )

    invocation = _build_manager_runtime_invocation(
        context,
        idle_timeout_override=0.0,
    )
    run_manager_process(
        invocation.task_cls_path,
        context.broker_target,
        invocation.spec,
        context.config,
        _MANAGER_POLL_INTERVAL,
    )
    return 0, None


def _stop_manager(
    context: WeftContext,
    record: dict[str, Any] | None,
    process: subprocess.Popen[Any] | None = None,
    *,
    tid: str | None = None,
    timeout: float = 5.0,
    force: bool = False,
    stop_if_absent: bool = False,
) -> tuple[bool, str | None]:
    target_tid = tid or (record.get("tid") if isinstance(record, dict) else None)
    if not isinstance(target_tid, str) or not target_tid:
        raise ValueError("manager tid is required")

    current = record or _manager_record(context, target_tid)
    if isinstance(current, dict):
        pid = current.get("pid")
        if current.get("status") == "stopped" and not _is_pid_alive(
            pid if isinstance(pid, int) else None
        ):
            return True, None

    try:
        _send_stop(context, target_tid, record=current)
    except Exception:
        typer.echo("Warning: failed to send STOP to manager.", err=True)
        return False, "failed to send STOP to manager"

    deadline = time.time() + timeout
    entry_observed = current is not None
    last_record = current
    pid_checked_at = 0.0

    while time.time() < deadline:
        current = _manager_record(context, target_tid)
        if current is None:
            if stop_if_absent or entry_observed:
                last_pid = None
                if isinstance(last_record, dict):
                    pid = last_record.get("pid")
                    if isinstance(pid, int):
                        last_pid = pid
                if not _is_pid_alive(last_pid):
                    if process is not None and process.poll() is None:
                        remaining = max(0.0, deadline - time.time())
                        if remaining > 0:
                            try:
                                process.wait(timeout=remaining)
                            except subprocess.TimeoutExpired:
                                pass
                    if process is None or process.poll() is not None:
                        return True, None
        else:
            entry_observed = True
            last_record = current
            status = current.get("status")
            pid = current.get("pid")
            current_pid = pid if isinstance(pid, int) else None
            if status == "stopped" and not _is_pid_alive(current_pid):
                if process is not None and process.poll() is None:
                    remaining = max(0.0, deadline - time.time())
                    if remaining > 0:
                        try:
                            process.wait(timeout=remaining)
                        except subprocess.TimeoutExpired:
                            pass
                if process is None or process.poll() is not None:
                    return True, None
            if stop_if_absent:
                now = time.time()
                if now - pid_checked_at >= 0.5:
                    pid_checked_at = now
                    if not _is_pid_alive(current_pid):
                        return True, None
        time.sleep(0.1)

    if force:
        kill_pid = _lookup_manager_pid(context, target_tid)

        if isinstance(kill_pid, int) and _is_pid_alive(kill_pid):
            try:
                terminate_process_tree(kill_pid, timeout=timeout)
            except (ProcessLookupError, OSError):
                return True, None
            except PermissionError:
                return False, f"Permission denied sending SIGTERM to PID {kill_pid}"
            return True, None

        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except Exception:  # pragma: no cover - defensive
                return True, None
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                return False, f"Manager {target_tid} did not stop within {timeout:.1f}s"
            return True, None
        return True, None

    return False, f"Manager {target_tid} did not stop within {timeout:.1f}s"


__all__ = [
    "_build_manager_spec",
    "_ensure_manager",
    "_list_manager_records",
    "_manager_record",
    "_select_active_manager",
    "_serve_manager_foreground",
    "_snapshot_registry",
    "_lookup_manager_pid",
    "_start_manager",
    "_stop_manager",
]
