"""CLI helper to execute TaskSpecs or inline targets.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-1.1.1]
- docs/specifications/01-Core_Components.md [CC-2.5]
- docs/specifications/02-TaskSpec.md [TS-1]
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import typer
import psutil

from simplebroker import Queue
from simplebroker.db import BrokerDB
from weft._constants import (
    DEFAULT_STREAM_OUTPUT,
    QUEUE_CTRL_IN_SUFFIX,
    QUEUE_CTRL_OUT_SUFFIX,
    QUEUE_INBOX_SUFFIX,
    QUEUE_OUTBOX_SUFFIX,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_MANAGER_CTRL_IN_QUEUE,
    WEFT_MANAGER_CTRL_OUT_QUEUE,
    WEFT_MANAGER_LIFETIME_TIMEOUT,
    WEFT_MANAGER_OUTBOX_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    WEFT_WORKERS_REGISTRY_QUEUE,
    WORK_ENVELOPE_START,
)
from weft.context import WeftContext, build_context
from weft.core.taskspec import TaskSpec

# -----------------------------------------------------------------------------
# Legacy helpers for --spec execution
# -----------------------------------------------------------------------------


def _load_taskspec(path: Path) -> TaskSpec:
    """Load and validate a TaskSpec JSON file (Spec: [TS-1], [CLI-1.1.1])."""
    try:
        return TaskSpec.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - validation tested elsewhere
        raise typer.Exit(code=2) from exc


# -----------------------------------------------------------------------------
# Inline execution helpers
# -----------------------------------------------------------------------------


def _parse_cli_value(raw: str) -> Any:
    """Try to interpret a CLI value as JSON, falling back to plain string."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _parse_cli_kwargs(values: Sequence[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in values:
        if "=" not in item:
            raise typer.BadParameter(
                f"Keyword argument '{item}' is missing '=' (expected key=value)"
            )
        key, value = item.split("=", 1)
        result[key] = _parse_cli_value(value)
    return result


def _parse_env(values: Sequence[str]) -> dict[str, str]:
    env: dict[str, str] = {}
    for item in values:
        if "=" not in item:
            raise typer.BadParameter(
                f"Environment entry '{item}' is missing '=' (expected KEY=VALUE)"
            )
        key, value = item.split("=", 1)
        env[key] = value
    return env


def _read_stdin() -> str | None:
    """Read stdin if data is available."""
    stream = sys.stdin
    if stream is None or stream.closed:
        return None

    try:
        is_tty = stream.isatty()
    except Exception:  # pragma: no cover - StringIO during tests
        is_tty = False

    if is_tty:
        return None

    try:
        data = stream.read()
    except OSError:  # pytest capture may block reading stdin
        return None

    return data if data else None


def _derive_name(
    name: str | None, command: Sequence[str], function_target: str | None
) -> str:
    if name:
        return name
    if command:
        return Path(command[0]).name
    if function_target:
        return function_target.split(":")[-1]
    return "cli-task"


def _drain_stream_queue(queue: Queue, *, to_stderr: bool = False) -> None:
    target = sys.stderr if to_stderr else sys.stdout
    while True:
        raw_item = queue.read_one()
        if raw_item is None:
            break
        if isinstance(raw_item, tuple):
            message_obj = raw_item[0]
        else:
            message_obj = raw_item
        message = str(message_obj)
        try:
            envelope = json.loads(message)
        except json.JSONDecodeError:
            target.write(message)
            target.flush()
            continue

        data = envelope.get("data", "")
        encoding = envelope.get("encoding")
        if encoding == "text":
            target.write(data)
            target.flush()
        elif encoding == "base64":
            chunk = base64.b64decode(data)
            buffer = getattr(target, "buffer", None)
            if buffer is not None:
                buffer.write(chunk)
            else:  # pragma: no cover - fallback for text streams
                target.write(chunk.decode("utf-8", errors="replace"))
            target.flush()
        else:
            target.write(json.dumps(envelope))
            target.flush()


def _generate_tid(context: WeftContext) -> str:
    with BrokerDB(str(context.database_path)) as db:
        return str(db.generate_timestamp())


def _registry_queue(context: WeftContext) -> Queue:
    return Queue(
        WEFT_WORKERS_REGISTRY_QUEUE,
        db_path=str(context.database_path),
        persistent=False,
        config=context.broker_config,
    )


def _snapshot_registry(
    context: WeftContext, *, prune_stale: bool = True
) -> dict[str, dict[str, Any]]:
    queue = _registry_queue(context)
    try:
        records_raw = cast(
            Sequence[tuple[str, int]] | None,
            queue.peek_many(limit=1000, with_timestamps=True),
        )
    except Exception:
        records_raw = None

    snapshot: dict[str, dict[str, Any]] = {}
    stale_timestamps: list[int] = []
    if not records_raw:
        return snapshot

    for entry, timestamp in records_raw:
        try:
            data = cast(dict[str, Any], json.loads(entry))
        except json.JSONDecodeError:
            continue
        tid = data.get("tid")
        if not tid:
            continue
        data["_timestamp"] = timestamp
        if (
            prune_stale
            and data.get("status") == "active"
            and data.get("role", "manager") == "manager"
        ):
            pid = data.get("pid")
            if pid and not _is_pid_alive(pid):
                stale_timestamps.append(timestamp)
                continue
        existing = snapshot.get(tid)
        existing_ts = int(existing.get("_timestamp", 0)) if existing else -1
        if existing is None or existing_ts < timestamp:
            snapshot[tid] = data

    for ts in stale_timestamps:
        try:
            queue.delete(message_id=ts)
        except Exception:
            pass

    return snapshot


def _select_active_manager(context: WeftContext) -> dict[str, Any] | None:
    snapshot = _snapshot_registry(context)
    candidates = []
    for record in snapshot.values():
        if record.get("status") != "active":
            continue
        if record.get("role", "manager") != "manager":
            continue
        pid = record.get("pid")
        if pid and _is_pid_alive(pid):
            candidates.append(record)
    if not candidates:
        return None
    return max(candidates, key=lambda rec: rec.get("_timestamp", 0))


def _is_pid_alive(pid: int) -> bool:
    try:
        process = psutil.Process(pid)
        return process.is_running()
    except psutil.NoSuchProcess:
        return False


def _build_manager_spec(context: WeftContext, tid: str) -> TaskSpec:
    idle_timeout = float(
        context.config.get(
            "WEFT_MANAGER_LIFETIME_TIMEOUT", WEFT_MANAGER_LIFETIME_TIMEOUT
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
                "ctrl_in": WEFT_MANAGER_CTRL_IN_QUEUE,
                "ctrl_out": WEFT_MANAGER_CTRL_OUT_QUEUE,
            },
        },
        "state": {},
        "metadata": {
            "role": "manager",
            "capabilities": [],
            "idle_timeout": idle_timeout,
        },
    }
    return TaskSpec.model_validate(spec_dict, context={"auto_expand": True})


def _start_manager(
    context: WeftContext, *, verbose: bool
) -> tuple[dict[str, Any], subprocess.Popen[bytes]]:
    manager_tid = _generate_tid(context)
    manager_spec = _build_manager_spec(context, manager_tid)

    spec_json = manager_spec.model_dump_json()
    config_json = json.dumps(context.config)

    spec_b64 = base64.b64encode(spec_json.encode("utf-8")).decode("ascii")
    config_b64 = base64.b64encode(config_json.encode("utf-8")).decode("ascii")

    cmd = [
        sys.executable,
        "-m",
        "weft.manager_process",
        "weft.core.manager.Manager",
        str(context.database_path),
        spec_b64,
        config_b64,
        "0.05",
    ]

    stdout = None if verbose else subprocess.DEVNULL
    stderr = None if verbose else subprocess.DEVNULL
    process = subprocess.Popen(cmd, stdout=stdout, stderr=stderr)

    if verbose:
        typer.echo(
            json.dumps(
                {
                    "manager_tid": manager_tid,
                    "pid": process.pid,
                    "db": str(context.database_path),
                },
                indent=2,
            )
        )

    deadline = time.time() + 10.0
    while time.time() < deadline:
        if process.poll() is not None:
            break
        record = _select_active_manager(context)
        if record and record.get("tid") == manager_tid:
            return record, process
        time.sleep(0.1)

    if process.poll() is None:
        process.terminate()
    typer.echo(
        "Failed to start Manager process; no registry entry appeared.",
        err=True,
    )
    raise typer.Exit(code=1)


def _ensure_manager(
    context: WeftContext, *, verbose: bool
) -> tuple[dict[str, Any], bool, subprocess.Popen[bytes] | None]:
    record = _select_active_manager(context)
    if record:
        pid = record.get("pid")
        if not (isinstance(pid, int) and _is_pid_alive(pid)):
            _snapshot_registry(context)  # prune stale entries
            record = _select_active_manager(context)
            if record is None:
                new_record, process = _start_manager(context, verbose=verbose)
                return new_record, True, process
        if record:
            return record, False, None
    record, process = _start_manager(context, verbose=verbose)
    return record, True, process


def _stop_manager(
    context: WeftContext,
    record: dict[str, Any],
    process: subprocess.Popen[bytes] | None,
    *,
    timeout: float = 5.0,
) -> None:
    ctrl_in = record.get("ctrl_in") or f"worker.{record['tid']}.{QUEUE_CTRL_IN_SUFFIX}"
    queue = Queue(
        ctrl_in,
        db_path=str(context.database_path),
        persistent=False,
        config=context.broker_config,
    )
    try:
        queue.write("STOP")
    except Exception:
        typer.echo("Warning: failed to send STOP to manager.", err=True)
        return

    deadline = time.time() + timeout
    while time.time() < deadline:
        snapshot = _snapshot_registry(context)
        current = snapshot.get(record["tid"])
        if current is None or current.get("status") == "stopped":
            if process is not None:
                try:
                    process.wait(timeout=timeout)
                except Exception:  # pragma: no cover - defensive
                    pass
            return
        time.sleep(0.1)

    if process is not None:
        try:
            process.terminate()
        except Exception:  # pragma: no cover - defensive
            pass


def _enqueue_taskspec(
    context: WeftContext,
    manager_record: dict[str, Any],
    taskspec: TaskSpec,
    work_payload: Any,
) -> None:
    inbox_name = manager_record.get("requests") or WEFT_SPAWN_REQUESTS_QUEUE
    queue = Queue(
        inbox_name,
        db_path=str(context.database_path),
        persistent=True,
        config=context.broker_config,
    )
    message: dict[str, Any] = {
        "taskspec": taskspec.model_dump(mode="json"),
        "inbox_message": WORK_ENVELOPE_START if work_payload is None else work_payload,
    }
    queue.write(json.dumps(message))


def _decode_result_payload(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _handle_ctrl_stream(raw: str) -> None:
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        typer.echo(raw, err=True)
        return

    if not isinstance(envelope, dict):
        typer.echo(str(envelope), err=True)
        return

    data = envelope.get("data", "")
    encoding = envelope.get("encoding", "text")
    if encoding == "base64":
        try:
            chunk = base64.b64decode(data)
            text = chunk.decode("utf-8", errors="replace")
        except Exception:
            text = ""
    else:
        text = str(data)

    is_stderr = envelope.get("stream") == "stderr"
    if text:
        typer.echo(text, err=is_stderr, nl=False)
        if envelope.get("final"):
            typer.echo(err=is_stderr)


def _process_outbox_message(raw: str, stream_buffer: list[str]) -> tuple[bool, Any]:
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        return True, _decode_result_payload(raw)

    if isinstance(envelope, dict) and envelope.get("type") == "stream":
        encoding = envelope.get("encoding", "text")
        data = envelope.get("data", "")
        if encoding == "base64":
            try:
                chunk = base64.b64decode(data)
                text = chunk.decode("utf-8", errors="replace")
            except Exception:
                text = ""
        else:
            text = str(data)
        if text:
            typer.echo(text, nl=False)
            stream_buffer.append(text)
        if envelope.get("final"):
            typer.echo()
            return True, "".join(stream_buffer)
        return False, None

    return True, envelope


def _poll_log_events(
    log_queue: Queue,
    last_timestamp: int | None,
    target_tid: str,
) -> tuple[list[tuple[dict[str, Any], int]], int | None]:
    try:
        records = cast(
            Sequence[tuple[str, int]] | None,
            log_queue.peek_many(limit=512, with_timestamps=True),
        )
    except Exception:
        return [], last_timestamp

    events: list[tuple[dict[str, Any], int]] = []
    if not records:
        return events, last_timestamp

    for entry, timestamp in records:
        if last_timestamp is not None and timestamp <= last_timestamp:
            continue
        try:
            data = cast(dict[str, Any], json.loads(entry))
        except json.JSONDecodeError:
            continue
        if data.get("tid") != target_tid:
            continue
        events.append((data, timestamp))

    if events:
        last_timestamp = events[-1][1]
    return events, last_timestamp


def _wait_for_task_completion(
    context: WeftContext,
    taskspec: TaskSpec,
    *,
    json_output: bool,
    verbose: bool,
) -> tuple[str, Any | None, str | None]:
    db_path = str(context.database_path)
    config = context.broker_config
    outbox_name = taskspec.io.outputs.get("outbox")
    ctrl_out_name = taskspec.io.control.get("ctrl_out")

    if outbox_name is None:
        outbox_name = f"T{taskspec.tid}.{QUEUE_OUTBOX_SUFFIX}"
    if ctrl_out_name is None:
        ctrl_out_name = f"T{taskspec.tid}.{QUEUE_CTRL_OUT_SUFFIX}"

    outbox_queue = Queue(
        outbox_name,
        db_path=db_path,
        persistent=True,
        config=config,
    )
    ctrl_queue = Queue(
        ctrl_out_name,
        db_path=db_path,
        persistent=False,
        config=config,
    )
    log_queue = Queue(
        WEFT_GLOBAL_LOG_QUEUE,
        db_path=db_path,
        persistent=False,
        config=config,
    )

    stream_buffer: list[str] = []
    log_last_timestamp: int | None = None
    status = "running"
    result_value: Any | None = None
    error_message: str | None = None

    while True:
        while True:
            ctrl_raw = ctrl_queue.read_one()
            if ctrl_raw is None:
                break
            ctrl_payload = ctrl_raw[0] if isinstance(ctrl_raw, tuple) else ctrl_raw
            _handle_ctrl_stream(str(ctrl_payload))

        outbox_raw = outbox_queue.read_one()
        if outbox_raw is not None:
            outbox_payload = (
                outbox_raw[0] if isinstance(outbox_raw, tuple) else outbox_raw
            )
            final, value = _process_outbox_message(str(outbox_payload), stream_buffer)
            if final:
                result_value = value
                status = "completed"
                break
            continue

        events, log_last_timestamp = _poll_log_events(
            log_queue,
            log_last_timestamp,
            taskspec.tid,
        )
        for payload, _ts in events:
            event = payload.get("event")
            if event in {"work_failed", "work_timeout", "work_limit_violation"}:
                status = "failed"
                error_message = payload.get("error") or event.replace("_", " ")
                break
            if event == "work_completed" and result_value is None:
                status = "completed"
                result_value = None
                break

        if status != "running":
            break

        time.sleep(0.05)

    return status, result_value, error_message


def _build_taskspec_dict(
    *,
    tid: str,
    context: WeftContext,
    name: str,
    target_type: str,
    function_target: str | None,
    command_target: Sequence[str] | None,
    base_args: Sequence[Any],
    base_kwargs: dict[str, Any],
    env: dict[str, str],
    timeout: float | None,
    memory: int | None,
    cpu: int | None,
    interactive: bool,
    stream_output: bool,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    spec_section: dict[str, Any] = {
        "type": target_type,
        "args": list(base_args),
        "keyword_args": base_kwargs,
        "env": env,
        "interactive": interactive,
        "stream_output": stream_output,
        "cleanup_on_exit": True,
        "weft_context": str(context.root),
    }
    if target_type == "function":
        spec_section["function_target"] = function_target
    else:
        spec_section["process_target"] = list(command_target or [])

    if timeout is not None:
        spec_section["timeout"] = timeout

    limits: dict[str, Any] = {}
    if memory is not None:
        limits["memory_mb"] = memory
    if cpu is not None:
        if not 0 < cpu <= 100:
            raise typer.BadParameter("CPU limit must be between 1 and 100 percent")
        limits["cpu_percent"] = cpu
    if limits:
        spec_section["limits"] = limits

    io_section = {
        "inputs": {"inbox": f"T{tid}.{QUEUE_INBOX_SUFFIX}"},
        "outputs": {"outbox": f"T{tid}.{QUEUE_OUTBOX_SUFFIX}"},
        "control": {
            "ctrl_in": f"T{tid}.{QUEUE_CTRL_IN_SUFFIX}",
            "ctrl_out": f"T{tid}.{QUEUE_CTRL_OUT_SUFFIX}",
        },
    }

    taskspec_dict = {
        "tid": tid,
        "name": name,
        "spec": spec_section,
        "io": io_section,
        "state": {},
        "metadata": metadata,
    }
    return taskspec_dict


def _initial_work_payload(
    *,
    target_type: str,
    stdin_data: str | None,
) -> Any:
    if target_type == "command":
        if stdin_data:
            return {"stdin": stdin_data}
        return {}
    if stdin_data:
        return stdin_data
    return None


def _run_inline(
    *,
    command: Sequence[str],
    function_target: str | None,
    args: Sequence[str],
    kwargs: Sequence[str],
    env: Sequence[str],
    name: str | None,
    interactive: bool,
    stream_output: bool | None,
    timeout: float | None,
    memory: int | None,
    cpu: int | None,
    tags: Sequence[str],
    context_dir: Path | None,
    wait: bool,
    json_output: bool,
    verbose: bool,
) -> int:
    target_type = "command" if command else "function"
    if target_type == "command" and not command:
        raise typer.BadParameter("Provide a command to execute or use --function")
    if target_type == "function":
        if not function_target or ":" not in function_target:
            raise typer.BadParameter(
                "Use --function with module:callable to execute a Python function"
            )

    context = build_context(
        spec_context=str(context_dir) if context_dir is not None else None
    )

    parsed_args = [_parse_cli_value(item) for item in args]
    parsed_kwargs = _parse_cli_kwargs(kwargs)
    env_map = _parse_env(env)

    task_name = _derive_name(name, command, function_target)
    metadata: dict[str, Any] = {}
    if tags:
        metadata["tags"] = list(tags)
    metadata["source"] = "weft.cli"

    stdin_data = _read_stdin()
    work_payload = _initial_work_payload(
        target_type=target_type,
        stdin_data=stdin_data,
    )
    tid = _generate_tid(context)

    effective_stream_output = (
        stream_output
        if stream_output is not None
        else (True if interactive else DEFAULT_STREAM_OUTPUT)
    )

    taskspec_dict = _build_taskspec_dict(
        tid=tid,
        context=context,
        name=task_name,
        target_type=target_type,
        function_target=function_target,
        command_target=command,
        base_args=parsed_args,
        base_kwargs=parsed_kwargs,
        env=env_map,
        timeout=timeout,
        memory=memory,
        cpu=cpu,
        interactive=interactive,
        stream_output=effective_stream_output,
        metadata=metadata,
    )

    taskspec = TaskSpec.model_validate(taskspec_dict, context={"auto_expand": True})
    manager_record, started_here, process_handle = _ensure_manager(
        context, verbose=verbose
    )
    reuse_enabled = bool(context.config.get("WEFT_MANAGER_REUSE_ENABLED", True))

    try:
        if verbose:
            typer.echo(
                json.dumps(
                    {"tid": tid, "task": task_name, "db": str(context.database_path)},
                    indent=2,
                )
            )

        if interactive:
            if target_type != "command":
                raise typer.BadParameter(
                    "--interactive is only supported for command targets"
                )
            if json_output:
                raise typer.BadParameter(
                    "--json is not supported together with --interactive"
                )
        _enqueue_taskspec(context, manager_record, taskspec, work_payload)

        if not wait:
            if json_output:
                typer.echo(
                    json.dumps(
                        {"tid": tid, "status": "queued"},
                        ensure_ascii=False,
                    )
                )
            else:
                typer.echo(tid)
            return 0

        status, result_value, error_message = _wait_for_task_completion(
            context,
            taskspec,
            json_output=json_output,
            verbose=verbose,
        )
    except Exception as exc:
        if started_here:
            _stop_manager(context, manager_record, process_handle)
        typer.echo(f"Error submitting task: {exc}", err=True)
        return 1
    else:
        if started_here and wait and not reuse_enabled:
            _stop_manager(context, manager_record, process_handle)

    if status == "completed":
        if json_output:
            typer.echo(
                json.dumps(
                    {
                        "tid": tid,
                        "status": status,
                        "result": result_value,
                    },
                    ensure_ascii=False,
                )
            )
        else:
            if isinstance(result_value, (dict, list)):
                typer.echo(json.dumps(result_value, ensure_ascii=False))
            elif result_value not in (None, ""):
                typer.echo(str(result_value))
        return 0

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "tid": tid,
                    "status": status,
                    "error": error_message,
                },
                ensure_ascii=False,
            )
        )
    else:
        typer.echo(f"Error executing task: {error_message}", err=True)
    return 1


def _run_spec_via_manager(
    spec_path: Path,
    *,
    verbose: bool,
    wait: bool,
    json_output: bool,
) -> int:
    spec = _load_taskspec(spec_path)
    context = build_context(spec.spec.weft_context)
    manager_record, started_here, process_handle = _ensure_manager(
        context, verbose=verbose
    )
    reuse_enabled = bool(context.config.get("WEFT_MANAGER_REUSE_ENABLED", True))

    try:
        _enqueue_taskspec(context, manager_record, spec, None)
        if not wait:
            if json_output:
                typer.echo(
                    json.dumps(
                        {"tid": spec.tid, "status": "queued"},
                        ensure_ascii=False,
                    )
                )
            else:
                typer.echo(spec.tid)
            return 0

        status, result_value, error_message = _wait_for_task_completion(
            context,
            spec,
            json_output=json_output,
            verbose=verbose,
        )
    except Exception as exc:
        if started_here:
            _stop_manager(context, manager_record, process_handle)
        typer.echo(f"Error submitting TaskSpec: {exc}", err=True)
        return 1
    else:
        if started_here and wait and not reuse_enabled:
            _stop_manager(context, manager_record, process_handle)

    if status == "completed":
        if json_output:
            typer.echo(
                json.dumps(
                    {
                        "tid": spec.tid,
                        "status": status,
                        "result": result_value,
                    },
                    ensure_ascii=False,
                )
            )
        else:
            if isinstance(result_value, (dict, list)):
                typer.echo(json.dumps(result_value, ensure_ascii=False))
            elif result_value not in (None, ""):
                typer.echo(str(result_value))
        return 0

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "tid": spec.tid,
                    "status": status,
                    "error": error_message,
                },
                ensure_ascii=False,
            )
        )
    else:
        typer.echo(f"Error executing task: {error_message}", err=True)
    return 1


# -----------------------------------------------------------------------------
# Public entry point
# -----------------------------------------------------------------------------


def cmd_run(
    command: Sequence[str],
    *,
    spec: Path | None,
    function: str | None,
    args: Sequence[str],
    kwargs: Sequence[str],
    env: Sequence[str],
    name: str | None,
    interactive: bool,
    stream_output: bool | None,
    timeout: float | None,
    memory: int | None,
    cpu: int | None,
    tags: Sequence[str],
    context_dir: Path | None,
    wait: bool,
    json_output: bool,
    verbose: bool,
    monitor: bool,
    once: bool,
) -> int:
    """Execute an inline target or a TaskSpec JSON file."""
    if spec is not None:
        if command:
            raise typer.BadParameter("Provide either a TaskSpec file or a command.")
        if function:
            raise typer.BadParameter("--function cannot be used together with --spec.")
        if args or kwargs or env or tags:
            raise typer.BadParameter(
                "--arg/--kw/--env/--tag are not compatible with --spec."
            )
        if monitor:
            raise typer.BadParameter("--monitor is not yet supported with the Manager.")
        if once is False:
            raise typer.BadParameter(
                "--continuous/--no-once is not yet supported with the Manager."
            )
        return _run_spec_via_manager(
            spec,
            verbose=verbose,
            wait=wait,
            json_output=json_output,
        )

    if monitor:
        raise typer.BadParameter("--monitor is only supported together with --spec.")
    if once is False:
        raise typer.BadParameter(
            "--continuous/--no-once is only supported together with --spec."
        )

    if not command and not function:
        raise typer.BadParameter(
            "Provide a command to execute or specify --function module:callable."
        )
    if command and function:
        raise typer.BadParameter(
            "Cannot execute a shell command and --function simultaneously."
        )

    return _run_inline(
        command=command,
        function_target=function,
        args=args,
        kwargs=kwargs,
        env=env,
        name=name,
        interactive=interactive,
        stream_output=stream_output,
        timeout=timeout,
        memory=memory,
        cpu=cpu,
        tags=tags,
        context_dir=context_dir,
        wait=wait,
        json_output=json_output,
        verbose=verbose,
    )


__all__ = ["cmd_run"]
