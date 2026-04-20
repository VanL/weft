"""Low-level manager runtime launch and registry mechanics.

Spec references:
- docs/specifications/03-Manager_Architecture.md [MA-1], [MA-3]
- docs/specifications/05-Message_Flow_and_State.md [MF-7]
"""

from __future__ import annotations

import base64
import json
import logging
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from simplebroker import Queue, serialize_broker_target
from weft._constants import (
    MANAGER_COMPETING_STARTUP_GRACE_SECONDS,
    MANAGER_LAUNCHER_SIGNAL_ABORT,
    MANAGER_LAUNCHER_SIGNAL_SUCCESS,
    MANAGER_PID_LIVENESS_RECHECK_INTERVAL,
    MANAGER_POLL_INTERVAL,
    MANAGER_REGISTRY_POLL_INTERVAL,
    MANAGER_STARTUP_LOG_DIRNAME,
    MANAGER_STARTUP_TIMEOUT_SECONDS,
    MANAGER_TASK_CLASS_PATH,
    QUEUE_CTRL_IN_SUFFIX,
    QUEUE_CTRL_OUT_SUFFIX,
    TASK_PROCESS_POLL_INTERVAL,
    WEFT_MANAGER_LIFETIME_TIMEOUT,
    WEFT_MANAGER_OUTBOX_QUEUE,
    WEFT_MANAGERS_REGISTRY_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
)
from weft._exceptions import ManagerStartFailed
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

from .queue_wait import QueueChangeMonitor

logger = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class _ManagerRegistryView:
    """One polled view of the manager registry for a specific lifecycle check."""

    records: dict[str, dict[str, Any]]
    active_manager: dict[str, Any] | None
    target_record: dict[str, Any] | None


def _generate_tid(context: WeftContext) -> str:
    """Generate a unique TID via broker timestamp (Spec: [MA-2])."""
    return str(
        generate_spawn_request_timestamp(
            context.broker_target,
            config=context.broker_config,
        )
    )


def _registry_queue(context: WeftContext) -> Queue:
    return context.queue(WEFT_MANAGERS_REGISTRY_QUEUE, persistent=False)


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
    context: WeftContext,
    *,
    prune_stale: bool = True,
    queue: Queue | None = None,
) -> dict[str, dict[str, Any]]:
    registry_queue = queue or _registry_queue(context)
    owns_queue = queue is None
    snapshot: dict[str, dict[str, Any]] = {}
    stale_timestamps: list[int] = []
    try:
        for data, timestamp in iter_queue_json_entries(registry_queue):
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
                registry_queue.delete(message_id=ts)
            except Exception:
                pass
    finally:
        if owns_queue:
            registry_queue.close()

    return snapshot


def _select_active_manager_from_snapshot(
    snapshot: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    candidates = []
    for record in snapshot.values():
        if not is_canonical_manager_record(record):
            continue
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


def _registry_view(
    context: WeftContext,
    *,
    target_tid: str | None = None,
    prune_stale: bool = True,
    queue: Queue | None = None,
) -> _ManagerRegistryView:
    snapshot = _snapshot_registry(context, prune_stale=prune_stale, queue=queue)
    return _ManagerRegistryView(
        records=snapshot,
        active_manager=_select_active_manager_from_snapshot(snapshot),
        target_record=snapshot.get(target_tid) if target_tid is not None else None,
    )


def _record_pid(record: dict[str, Any] | None) -> int | None:
    if not isinstance(record, dict):
        return None
    pid = record.get("pid")
    return pid if isinstance(pid, int) else None


def _manager_record(
    context: WeftContext,
    tid: str,
    *,
    prune_stale: bool = True,
) -> dict[str, Any] | None:
    return _registry_view(
        context,
        target_tid=tid,
        prune_stale=prune_stale,
    ).target_record


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
    return _registry_view(context).active_manager


def _is_pid_alive(pid: int | None) -> bool:
    return pid_is_live(pid)


def _lookup_manager_pid(context: WeftContext, tid: str) -> int | None:
    queue = context.queue(WEFT_TID_MAPPINGS_QUEUE, persistent=False)
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
    queue = context.queue(_manager_ctrl_queue_name(tid, record), persistent=False)
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
        task_cls_path=MANAGER_TASK_CLASS_PATH,
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
        str(TASK_PROCESS_POLL_INTERVAL),
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
    startup_dir = context.logs_dir / MANAGER_STARTUP_LOG_DIRNAME
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


def _await_manager_start_settlement(
    context: WeftContext,
    *,
    manager_tid: str,
    deadline: float,
) -> dict[str, Any] | None:
    grace_deadline = min(
        deadline,
        time.monotonic() + MANAGER_COMPETING_STARTUP_GRACE_SECONDS,
    )
    last_active: dict[str, Any] | None = None
    registry_queue = _registry_queue(context)
    monitor = QueueChangeMonitor([registry_queue], config=context.config)
    try:
        while True:
            view = _registry_view(
                context,
                target_tid=manager_tid,
                queue=registry_queue,
            )
            last_active = view.active_manager
            if last_active is not None and last_active.get("tid") != manager_tid:
                return last_active
            remaining = grace_deadline - time.monotonic()
            if remaining <= 0:
                return last_active
            monitor.wait(min(remaining, MANAGER_REGISTRY_POLL_INTERVAL))
    finally:
        monitor.close()
        registry_queue.close()


def _wait_for_process_exit(
    process: subprocess.Popen[Any] | None,
    *,
    deadline: float,
) -> bool:
    if process is None or process.poll() is not None:
        return True
    remaining = max(0.0, deadline - time.monotonic())
    if remaining <= 0:
        return process.poll() is not None
    try:
        process.wait(timeout=remaining)
    except subprocess.TimeoutExpired:
        return process.poll() is not None
    return True


def _await_manager_stop_confirmation(
    context: WeftContext,
    *,
    target_tid: str,
    deadline: float,
    initial_record: dict[str, Any] | None,
    process: subprocess.Popen[Any] | None,
    stop_if_absent: bool,
) -> tuple[bool, dict[str, Any] | None]:
    entry_observed = initial_record is not None
    last_record = initial_record
    pid_checked_at = 0.0
    registry_queue = _registry_queue(context)
    monitor = QueueChangeMonitor([registry_queue], config=context.config)
    try:
        while time.monotonic() < deadline:
            view = _registry_view(
                context,
                target_tid=target_tid,
                queue=registry_queue,
            )
            current = view.target_record
            if current is None:
                if stop_if_absent or entry_observed:
                    if not _is_pid_alive(_record_pid(last_record)):
                        if _wait_for_process_exit(process, deadline=deadline):
                            return True, last_record
            else:
                entry_observed = True
                last_record = current
                current_pid = _record_pid(current)
                if current.get("status") == "stopped" and not _is_pid_alive(
                    current_pid
                ):
                    if _wait_for_process_exit(process, deadline=deadline):
                        return True, current
                if stop_if_absent:
                    now = time.monotonic()
                    if now - pid_checked_at >= MANAGER_PID_LIVENESS_RECHECK_INTERVAL:
                        pid_checked_at = now
                        if not _is_pid_alive(current_pid):
                            if _wait_for_process_exit(process, deadline=deadline):
                                return True, current

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            monitor.wait(min(remaining, MANAGER_REGISTRY_POLL_INTERVAL))
        return False, last_record
    finally:
        monitor.close()
        registry_queue.close()


def _fail_manager_start(
    *,
    launch: _DetachedManagerLaunch,
    message: str,
    abort_launcher: bool,
) -> NoReturn:
    launcher_events: list[dict[str, Any]] = []
    launcher_stderr = ""
    if abort_launcher:
        _send_launcher_signal(launch.launcher_process, MANAGER_LAUNCHER_SIGNAL_ABORT)
    if launch.launcher_process.poll() is not None or abort_launcher:
        launcher_events, launcher_stderr = _communicate_launcher(
            launch.launcher_process,
            timeout=1.0,
        )
    raise ManagerStartFailed(
        _format_manager_start_failure(
            message=message,
            launch=launch,
            launcher_events=launcher_events,
            launcher_stderr=launcher_stderr,
        )
    )


def _acknowledge_manager_launch_success(launch: _DetachedManagerLaunch) -> None:
    sent, error = _send_launcher_signal(
        launch.launcher_process,
        MANAGER_LAUNCHER_SIGNAL_SUCCESS,
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
    del verbose

    deadline = time.monotonic() + MANAGER_STARTUP_TIMEOUT_SECONDS
    competing_record: dict[str, Any] | None = None
    registry_queue = _registry_queue(context)
    monitor = QueueChangeMonitor([registry_queue], config=context.config)
    try:
        while time.monotonic() < deadline:
            view = _registry_view(
                context,
                target_tid=manager_tid,
                queue=registry_queue,
            )
            selected_record = view.active_manager
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
                            logger.warning(
                                "Detached manager launch for %s succeeded before "
                                "post-proof acknowledgement failed: %s",
                                manager_tid,
                                exc,
                                exc_info=True,
                            )
                        _cleanup_startup_stderr(launch.stderr_path)
                        return selected_record, True, None

            if launch.launcher_process.poll() is not None:
                if competing_record is None:
                    competing_record = _await_manager_start_settlement(
                        context,
                        manager_tid=manager_tid,
                        deadline=deadline,
                    )
                if competing_record is not None:
                    _cleanup_startup_stderr(launch.stderr_path)
                    return competing_record, False, None
                _fail_manager_start(
                    launch=launch,
                    message="Failed to start Manager process; detached launcher exited before startup stabilized.",
                    abort_launcher=False,
                )
            if not _is_pid_alive(launch.pid):
                if competing_record is None:
                    competing_record = _await_manager_start_settlement(
                        context,
                        manager_tid=manager_tid,
                        deadline=deadline,
                    )
                if competing_record is not None:
                    _send_launcher_signal(
                        launch.launcher_process,
                        MANAGER_LAUNCHER_SIGNAL_ABORT,
                    )
                    _communicate_launcher(launch.launcher_process, timeout=1.0)
                    _cleanup_startup_stderr(launch.stderr_path)
                    return competing_record, False, None
                _fail_manager_start(
                    launch=launch,
                    message="Failed to start Manager process; detached manager PID exited before startup stabilized.",
                    abort_launcher=True,
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            monitor.wait(min(remaining, MANAGER_REGISTRY_POLL_INTERVAL))
    finally:
        monitor.close()
        registry_queue.close()

    if competing_record is not None:
        _send_launcher_signal(launch.launcher_process, MANAGER_LAUNCHER_SIGNAL_ABORT)
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
        MANAGER_POLL_INTERVAL,
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
        return False, "failed to send STOP to manager"

    if current is None and stop_if_absent and process is None:
        return True, None

    deadline = time.monotonic() + timeout
    stopped, _last_record = _await_manager_stop_confirmation(
        context,
        target_tid=target_tid,
        deadline=deadline,
        initial_record=current,
        process=process,
        stop_if_absent=stop_if_absent,
    )
    if stopped:
        return True, None

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


ManagerRuntimeInvocation = _ManagerRuntimeInvocation
DetachedManagerLaunch = _DetachedManagerLaunch
ManagerRegistryView = _ManagerRegistryView


def generate_tid(context: WeftContext) -> str:
    """Return one durable manager-related TID from the active broker target."""

    return _generate_tid(context)


def manager_record(
    context: WeftContext,
    tid: str,
    *,
    prune_stale: bool = True,
) -> dict[str, Any] | None:
    """Return one manager registry record."""

    return _manager_record(context, tid, prune_stale=prune_stale)


def list_manager_records(
    context: WeftContext,
    *,
    include_stopped: bool = False,
    canonical_only: bool = False,
    prune_stale: bool = True,
) -> list[dict[str, Any]]:
    """Return manager registry records for command-layer consumers."""

    return _list_manager_records(
        context,
        include_stopped=include_stopped,
        canonical_only=canonical_only,
        prune_stale=prune_stale,
    )


def select_active_manager(context: WeftContext) -> dict[str, Any] | None:
    """Return the current canonical active manager record, if any."""

    return _select_active_manager(context)


def build_manager_spec(
    context: WeftContext,
    tid: str,
    *,
    idle_timeout_override: float | None = None,
) -> TaskSpec:
    """Build the canonical manager TaskSpec."""

    return _build_manager_spec(
        context,
        tid,
        idle_timeout_override=idle_timeout_override,
    )


def start_manager(
    context: WeftContext,
    *,
    verbose: bool,
) -> tuple[dict[str, Any], bool, subprocess.Popen[Any] | None]:
    """Start a canonical manager if one does not already exist."""

    return _start_manager(context, verbose=verbose)


def ensure_manager(
    context: WeftContext,
    *,
    verbose: bool,
) -> tuple[dict[str, Any], bool, subprocess.Popen[Any] | None]:
    """Ensure a canonical manager exists."""

    return _ensure_manager(context, verbose=verbose)


def serve_manager_foreground(context: WeftContext) -> tuple[int, str | None]:
    """Run the canonical manager in the current process."""

    return _serve_manager_foreground(context)


def stop_manager(
    context: WeftContext,
    record: dict[str, Any] | None,
    process: subprocess.Popen[Any] | None = None,
    *,
    tid: str | None = None,
    timeout: float = 5.0,
    force: bool = False,
    stop_if_absent: bool = False,
) -> tuple[bool, str | None]:
    """Stop one manager via the shared runtime mechanism."""

    return _stop_manager(
        context,
        record,
        process,
        tid=tid,
        timeout=timeout,
        force=force,
        stop_if_absent=stop_if_absent,
    )


__all__ = [
    "DetachedManagerLaunch",
    "ManagerRegistryView",
    "ManagerRuntimeInvocation",
    "build_manager_spec",
    "ensure_manager",
    "generate_tid",
    "list_manager_records",
    "manager_record",
    "select_active_manager",
    "serve_manager_foreground",
    "start_manager",
    "stop_manager",
]
