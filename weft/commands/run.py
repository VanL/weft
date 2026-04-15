"""CLI helper to execute TaskSpecs or inline targets.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-1.1.1]
- docs/specifications/10B-Builtin_TaskSpecs.md
- docs/specifications/01-Core_Components.md [CC-2.5]
- docs/specifications/02-TaskSpec.md [TS-1], [TS-1.3]
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import typer

from simplebroker import Queue
from weft._constants import (
    CONTROL_KILL,
    CONTROL_STOP,
    DEFAULT_STREAM_OUTPUT,
    INTERACTIVE_STOP_COMPLETION_TIMEOUT,
    QUEUE_CTRL_IN_SUFFIX,
    QUEUE_CTRL_OUT_SUFFIX,
    QUEUE_INBOX_SUFFIX,
    QUEUE_OUTBOX_SUFFIX,
)
from weft.commands import specs as spec_cmd
from weft.commands._manager_bootstrap import (
    _build_manager_spec,
    _ensure_manager,
    _generate_tid,
    _select_active_manager,
    _start_manager,
    _stop_manager,
)
from weft.commands._result_wait import await_one_shot_result
from weft.commands._spawn_submission import reconcile_submitted_spawn
from weft.commands._streaming import (
    collect_interactive_queue_output as _collect_interactive_queue_output,
)
from weft.commands._task_history import is_pipeline_taskspec_payload
from weft.commands.interactive import InteractiveStreamClient
from weft.context import WeftContext, build_context
from weft.core.pipelines import (
    PipelineSpec,
    compile_linear_pipeline,
    load_pipeline_spec_payload,
)
from weft.core.spawn_requests import (
    delete_spawn_request as delete_spawn_request_message,
)
from weft.core.spawn_requests import submit_spawn_request
from weft.core.spec_parameterization import (
    materialize_taskspec_template,
    parse_declared_parameterization_args,
)
from weft.core.spec_run_input import (
    SpecRunInputRequest,
    invoke_run_input_adapter,
    normalize_declared_option_name,
    parse_declared_run_input_args,
)
from weft.core.taskspec import (
    TaskSpec,
    apply_bundle_root_to_taskspec_payload,
    resolve_taskspec_payload,
)
from weft.helpers import (
    read_limited_stdin,
    resolve_broker_max_message_size,
    stdin_is_tty,
)

# -----------------------------------------------------------------------------
# Explicit spec helpers
# -----------------------------------------------------------------------------


def _load_taskspec_reference(
    spec: str | Path,
    *,
    context_dir: Path | None,
) -> TaskSpec:
    """Load and validate a TaskSpec from an explicit path or named spec reference."""
    try:
        resolved = spec_cmd.resolve_spec_reference(
            spec,
            spec_type=spec_cmd.SPEC_TYPE_TASK,
            context_path=context_dir,
        )
        taskspec = TaskSpec.model_validate(
            resolved.payload,
            context={"template": True, "auto_expand": False},
        )
        taskspec.set_bundle_root(resolved.bundle_root)
        return taskspec
    except Exception as exc:  # pragma: no cover - validation tested elsewhere
        raise typer.Exit(code=2) from exc


def _load_pipeline_spec(
    pipeline: str | Path,
    *,
    context_dir: Path | None,
) -> tuple[PipelineSpec, str | None]:
    resolved = spec_cmd.resolve_spec_reference(
        pipeline,
        spec_type=spec_cmd.SPEC_TYPE_PIPELINE,
        context_path=context_dir,
    )
    return load_pipeline_spec_payload(resolved.payload), str(resolved.path)


def _declared_option_metavar(kind: str) -> str:
    """Return the user-facing metavar for a declared spec option."""
    if kind == "path":
        return "PATH"
    return "TEXT"


def _format_declared_option_help(name: str, declaration: Any) -> str:
    """Render one declared spec option for spec-aware CLI help."""
    option = (
        f"--{normalize_declared_option_name(name)} "
        f"{_declared_option_metavar(declaration.type)}"
    )
    detail_parts: list[str] = []
    if declaration.required:
        detail_parts.append("required")
    default = getattr(declaration, "default", None)
    if default is not None:
        detail_parts.append(f"default: {default}")
    choices = tuple(getattr(declaration, "choices", ()))
    if choices:
        detail_parts.append("choices: " + ", ".join(choices))

    description = getattr(declaration, "help", None) or ""
    if detail_parts:
        suffix = "; ".join(detail_parts)
        if description:
            return f"  {option:<22} {description} [{suffix}]"
        return f"  {option:<22} [{suffix}]"
    if description:
        return f"  {option:<22} {description}"
    return f"  {option}"


def render_spec_aware_run_help(
    ctx: typer.Context,
    *,
    spec: str | Path,
    context_dir: Path | None,
) -> str:
    """Return `weft run` help augmented with selected TaskSpec help."""
    taskspec = _load_taskspec_reference(spec, context_dir=context_dir)

    lines = [ctx.get_help(), "", f"Spec Help: {taskspec.name}"]
    if taskspec.description:
        lines.append(taskspec.description)

    parameterization = taskspec.spec.parameterization
    run_input = taskspec.spec.run_input
    if parameterization is None and run_input is None:
        lines.extend(
            [
                "",
                "This TaskSpec does not declare spec-specific CLI options.",
            ]
        )
        return "\n".join(lines)

    if parameterization is not None:
        lines.extend(["", "Parameterization Options:"])
        if parameterization.arguments:
            for name, parameter_declaration in parameterization.arguments.items():
                lines.append(_format_declared_option_help(name, parameter_declaration))
        else:
            lines.append("  None")

    if run_input is not None:
        lines.extend(["", "Run Input Options:"])
        if run_input.arguments:
            for name, run_input_declaration in run_input.arguments.items():
                lines.append(_format_declared_option_help(name, run_input_declaration))
        else:
            lines.append("  None")
        if run_input.stdin is not None:
            stdin_mode = "required" if run_input.stdin.required else "optional"
            stdin_help = run_input.stdin.help or "Piped stdin text"
            lines.append("")
            lines.append(f"Stdin: {stdin_help} [{stdin_mode}]")

    return "\n".join(lines)


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


def _read_piped_stdin(context: WeftContext) -> str | None:
    """Read non-interactive stdin using the active broker size limit."""
    if not stdin_is_tty():
        try:
            max_bytes = resolve_broker_max_message_size(context.config)
            data = read_limited_stdin(max_bytes)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        return data if data else None
    return None


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


def _enqueue_taskspec(
    context: WeftContext,
    taskspec: TaskSpec,
    work_payload: Any,
    *,
    seed_start_envelope: bool = True,
) -> int:
    # Spec: docs/specifications/03-Manager_Architecture.md#tid-correlation-wa-2, [MF-1]
    task_tid = taskspec.tid or _generate_tid(context)
    return submit_spawn_request(
        context.broker_target,
        taskspec=taskspec,
        work_payload=work_payload,
        config=context.broker_config,
        tid=task_tid,
        inherited_weft_context=taskspec.spec.weft_context,
        seed_start_envelope=seed_start_envelope,
    )


def _delete_spawn_request(context: WeftContext, message_timestamp: int) -> bool:
    """Best-effort removal of a queued spawn request after submission failure."""
    return delete_spawn_request_message(
        context.broker_target,
        message_timestamp=message_timestamp,
        config=context.broker_config,
    )


def _recover_submitted_spawn(
    context: WeftContext,
    *,
    submitted_tid: str,
    startup_error: Exception,
) -> tuple[dict[str, Any] | None, bool, subprocess.Popen[Any] | None]:
    reconciliation = reconcile_submitted_spawn(context, submitted_tid)
    if reconciliation.outcome == "spawned":
        return None, False, None
    if reconciliation.outcome == "rejected":
        reason = (
            reconciliation.error or f"Manager rejected submitted task {submitted_tid}"
        )
        raise RuntimeError(reason) from startup_error
    if reconciliation.outcome == "queued":
        deleted = _delete_spawn_request(context, int(submitted_tid))
        if deleted:
            raise startup_error
        reconciliation = reconcile_submitted_spawn(
            context,
            submitted_tid,
            timeout=0.2,
        )
        if reconciliation.outcome == "spawned":
            return None, False, None
        if reconciliation.outcome == "rejected":
            reason = (
                reconciliation.error
                or f"Manager rejected submitted task {submitted_tid}"
            )
            raise RuntimeError(reason) from startup_error
    if reconciliation.outcome == "reserved":
        raise RuntimeError(
            f"Submitted task {submitted_tid} was already claimed into "
            f"{reconciliation.reserved_queue}; manual recovery is required."
        ) from startup_error
    if reconciliation.outcome == "queued":
        raise RuntimeError(
            f"Submitted task {submitted_tid} is still queued, but rollback could "
            "not be confirmed."
        ) from startup_error
    raise RuntimeError(
        f"Submitted task {submitted_tid} could not be reconciled; rollback "
        "could not be proven."
    ) from startup_error


def _ensure_manager_after_submission(
    context: WeftContext,
    *,
    submitted_tid: int,
    verbose: bool,
) -> tuple[dict[str, Any] | None, bool, subprocess.Popen[Any] | None]:
    try:
        return _ensure_manager(
            context,
            verbose=verbose,
        )
    except Exception as exc:
        return _recover_submitted_spawn(
            context,
            submitted_tid=str(submitted_tid),
            startup_error=exc,
        )


def _wait_for_task_completion(
    context: WeftContext,
    taskspec: TaskSpec,
    *,
    json_output: bool,
    verbose: bool,
) -> tuple[str, Any | None, str | None]:
    _ = json_output, verbose
    assert taskspec.tid is not None
    outbox_name = taskspec.io.outputs.get("outbox")
    ctrl_out_name = taskspec.io.control.get("ctrl_out")

    if outbox_name is None:
        outbox_name = f"T{taskspec.tid}.{QUEUE_OUTBOX_SUFFIX}"
    if ctrl_out_name is None:
        ctrl_out_name = f"T{taskspec.tid}.{QUEUE_CTRL_OUT_SUFFIX}"
    ctrl_out_for_wait = (
        None
        if is_pipeline_taskspec_payload(taskspec.model_dump(mode="json"))
        else ctrl_out_name
    )

    return await_one_shot_result(
        context,
        taskspec.tid,
        outbox_name=outbox_name,
        ctrl_out_name=ctrl_out_for_wait,
        timeout=None,
        show_stderr=False,
    )


def _run_interactive_session(
    context: WeftContext,
    taskspec: TaskSpec,
    *,
    stdin_data: str | None,
    auto_close: bool = True,
    use_prompt: bool = False,
) -> tuple[str, Any | None, str | None]:
    assert taskspec.tid is not None
    db_path = context.broker_target
    config = context.broker_config
    outbox_name = (
        taskspec.io.outputs.get("outbox") or f"T{taskspec.tid}.{QUEUE_OUTBOX_SUFFIX}"
    )
    ctrl_out_name = (
        taskspec.io.control.get("ctrl_out")
        or f"T{taskspec.tid}.{QUEUE_CTRL_OUT_SUFFIX}"
    )
    ctrl_in_name = (
        taskspec.io.control.get("ctrl_in") or f"T{taskspec.tid}.{QUEUE_CTRL_IN_SUFFIX}"
    )
    inbox_name = (
        taskspec.io.inputs.get("inbox") or f"T{taskspec.tid}.{QUEUE_INBOX_SUFFIX}"
    )

    status_holder: dict[str, str | None] = {"status": None, "error": None}
    stdout_chunks: list[str] = []
    quit_requested = False

    def _stdout_callback(chunk: str, final: bool) -> None:
        if use_prompt:
            if chunk:
                typer.echo(chunk, nl=False)
            if final and (not chunk or not chunk.endswith("\n")):
                typer.echo()
        else:
            if chunk:
                stdout_chunks.append(chunk)

    def _stderr_callback(chunk: str, final: bool) -> None:
        if chunk:
            typer.echo(chunk, err=True, nl=False)
        if final and (not chunk or not chunk.endswith("\n")):
            typer.echo(err=True)

    def _state_callback(event: dict[str, Any]) -> None:
        status = event.get("status")
        if isinstance(status, str) and status in {
            "completed",
            "failed",
            "timeout",
            "cancelled",
            "killed",
        }:
            status_holder["status"] = status
            error = event.get("error")
            status_holder["error"] = str(error) if isinstance(error, str) else None
            return

        evt = event.get("event")
        if evt in {"work_failed", "work_timeout", "work_limit_violation"}:
            status_holder["status"] = "failed"
            status_holder["error"] = event.get("error") or evt.replace("_", " ")
        elif evt == "work_completed":
            status_holder["status"] = "completed"
        elif evt in {"control_stop", "task_signal_stop"}:
            status_holder["status"] = "cancelled"
            status_holder["error"] = event.get("error") or "Task cancelled"
        elif evt in {"control_kill", "task_signal_kill"}:
            status_holder["status"] = "killed"
            status_holder["error"] = event.get("error") or "Task killed"

    def _send_interactive_control(command: str) -> None:
        ctrl_queue = Queue(
            ctrl_in_name,
            db_path=db_path,
            persistent=False,
            config=config,
        )
        try:
            ctrl_queue.write(command)
        finally:
            ctrl_queue.close()

    def _request_interactive_exit() -> bool:
        client.close_input()
        if client.wait(timeout=1.0):
            return True
        _send_interactive_control(CONTROL_STOP)
        if (
            client.wait_for_control_response("STOP", status="ack", timeout=1.0)
            is not None
        ):
            return True
        if client.wait(timeout=0.1):
            return True
        _send_interactive_control(CONTROL_KILL)
        if (
            client.wait_for_control_response("KILL", status="ack", timeout=1.0)
            is not None
        ):
            return True
        return client.wait(timeout=0.1)

    client = InteractiveStreamClient(
        db_path=db_path,
        config=config,
        tid=taskspec.tid,
        inbox=inbox_name,
        outbox=outbox_name,
        ctrl_out=ctrl_out_name,
        on_stdout=_stdout_callback,
        on_stderr=_stderr_callback,
        on_state=_state_callback,
    )

    client.start()
    try:
        if use_prompt:
            try:
                from prompt_toolkit import PromptSession
                from prompt_toolkit.patch_stdout import patch_stdout
            except ImportError as exc:  # pragma: no cover - optional dependency guard
                raise typer.BadParameter(
                    "prompt_toolkit is required for interactive mode when stdin is a TTY"
                ) from exc

            import threading

            session: PromptSession[str] = PromptSession("weft> ")
            completion_event = threading.Event()

            def _await_completion() -> None:
                client.wait()
                completion_event.set()
                try:
                    session.app.exit()
                except Exception:  # pragma: no cover - defensive
                    pass

            waiter = threading.Thread(target=_await_completion, daemon=True)
            waiter.start()

            # Trigger the downstream prompt to render once before entering the loop.
            client.send_input("\n")

            with patch_stdout():
                while not completion_event.is_set():
                    try:
                        line = session.prompt("weft> ")
                    except EOFError:
                        client.close_input()
                        break
                    except KeyboardInterrupt:
                        typer.echo()
                        continue

                    if line is None:  # Completion triggered while waiting for input
                        break

                    stripped = line.strip()
                    if stripped in {":quit", ":exit"}:
                        quit_requested = True
                        if not _request_interactive_exit():
                            raise RuntimeError(
                                "Interactive session did not stop after :quit"
                            )
                        break

                    payload = line if line.endswith("\n") else f"{line}\n"
                    client.send_input(payload)

            waiter.join(timeout=0.5)
            if not client.wait(timeout=INTERACTIVE_STOP_COMPLETION_TIMEOUT):
                raise RuntimeError("Interactive session did not stop after :quit")
        else:
            if stdin_data:
                client.send_input(stdin_data)
                if auto_close and not client.wait(timeout=0.2):
                    client.close_input()
                    client.wait()
                else:
                    client.wait()
            else:
                if auto_close:
                    client.close_input()
                client.wait()
        status = status_holder["status"] or client.status or "completed"
        error = status_holder["error"] or client.error
        if quit_requested and status in {"cancelled", "killed"}:
            status = "completed"
            error = None
        result: Any | None = None
    finally:
        client.stop()
        if not use_prompt:
            if stdout_chunks:
                result = "".join(stdout_chunks)
            else:
                history = client.stdout_history
                if history:
                    result = "".join(history)
                    stdout_chunks.extend(history)

    if not use_prompt:
        outbox_queue = Queue(
            outbox_name,
            db_path=db_path,
            persistent=True,
            config=config,
        )
        try:
            collected = _collect_interactive_queue_output(outbox_queue)
        finally:
            outbox_queue.close()

        if collected and not result:
            result = "".join(collected)

    return status, result, error


def _build_taskspec_dict(
    *,
    tid: str | None,
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
    command_target = list(command_target or [])
    command_args = [str(part) for part in command_target[1:]]
    spec_args: list[Any] = list(base_args)

    spec_section: dict[str, Any] = {
        "type": target_type,
        "args": spec_args,
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
        if command_target:
            spec_section["process_target"] = str(command_target[0])
            if command_args:
                spec_section["args"] = [*command_args, *spec_args]

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

    io_section: dict[str, Any] = {}
    if tid is not None:
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
    interactive: bool,
) -> Any:
    if target_type == "command":
        if interactive:
            if stdin_data:
                return {"stdin": stdin_data, "close": True}
            return {}
        if stdin_data:
            return {"stdin": stdin_data}
        return {}
    if stdin_data:
        return stdin_data
    return None


def _build_spec_work_payload(
    *,
    taskspec: TaskSpec,
    context: WeftContext,
    stdin_data: str | None,
    run_input_tokens: Sequence[str],
) -> Any:
    run_input = taskspec.spec.run_input
    if run_input is None:
        if run_input_tokens:
            raise typer.BadParameter(
                "This TaskSpec does not declare spec.run_input; extra "
                "arguments are not supported with --spec."
            )
        return _initial_work_payload(
            target_type=taskspec.spec.type,
            stdin_data=stdin_data,
            interactive=bool(taskspec.spec.interactive),
        )

    if stdin_data is not None and run_input.stdin is None:
        raise typer.BadParameter(
            "This TaskSpec does not declare stdin input for spec.run_input."
        )
    if run_input.stdin is not None and run_input.stdin.required and stdin_data is None:
        raise typer.BadParameter(
            "This TaskSpec requires piped stdin for spec.run_input."
        )

    try:
        arguments = parse_declared_run_input_args(
            list(run_input_tokens),
            run_input.arguments,
        )
        return invoke_run_input_adapter(
            run_input.adapter_ref,
            request=SpecRunInputRequest(
                arguments=arguments,
                stdin_text=stdin_data,
                context_root=str(context.root),
                spec_name=taskspec.name,
            ),
            bundle_root=taskspec.get_bundle_root(),
        )
    except (TypeError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc


def _materialize_parameterized_spec(
    *,
    taskspec: TaskSpec,
    context_root: str | None,
    run_input_tokens: Sequence[str],
) -> tuple[TaskSpec, list[str]]:
    parameterization = taskspec.spec.parameterization
    if parameterization is None:
        return taskspec, list(run_input_tokens)
    try:
        arguments, remaining_tokens = parse_declared_parameterization_args(
            list(run_input_tokens),
            parameterization.arguments,
        )
        materialized = materialize_taskspec_template(
            taskspec,
            arguments=arguments,
            context_root=context_root,
        )
    except (TypeError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    return materialized, remaining_tokens


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
    autostart_enabled: bool,
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
        spec_context=str(context_dir) if context_dir is not None else None,
        autostart=autostart_enabled,
    )

    parsed_args = [_parse_cli_value(item) for item in args]
    parsed_kwargs = _parse_cli_kwargs(kwargs)
    env_map = _parse_env(env)

    task_name = _derive_name(name, command, function_target)
    metadata: dict[str, Any] = {}
    if tags:
        metadata["tags"] = list(tags)
    metadata["source"] = "weft.cli"

    stdin_data = _read_piped_stdin(context)
    stdin_is_terminal = stdin_is_tty()
    work_payload = _initial_work_payload(
        target_type=target_type,
        stdin_data=stdin_data,
        interactive=interactive,
    )
    effective_stream_output = (
        stream_output
        if stream_output is not None
        else (True if interactive else DEFAULT_STREAM_OUTPUT)
    )

    template_dict = _build_taskspec_dict(
        tid=None,
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

    taskspec = TaskSpec.model_validate(
        template_dict, context={"auto_expand": False, "template": True}
    )
    manager_record: dict[str, Any] | None = None
    started_here = False
    process_handle: subprocess.Popen[bytes] | None = None
    reuse_enabled = bool(context.config.get("WEFT_MANAGER_REUSE_ENABLED", True))

    try:
        if interactive:
            if target_type != "command":
                raise typer.BadParameter(
                    "--interactive is only supported for command targets"
                )
            if json_output:
                raise typer.BadParameter(
                    "--json is not supported together with --interactive"
                )
        tid_int = _enqueue_taskspec(
            context,
            taskspec,
            work_payload,
        )
        manager_record, started_here, process_handle = _ensure_manager_after_submission(
            context,
            submitted_tid=tid_int,
            verbose=verbose,
        )
        tid = str(tid_int)
        if verbose:
            typer.echo(
                json.dumps(
                    {
                        "tid": tid,
                        "task": task_name,
                        "db": context.broker_display_target,
                    },
                    indent=2,
                )
            )

        resolved_payload = resolve_taskspec_payload(
            taskspec.model_dump(mode="json"),
            tid=tid,
            inherited_weft_context=taskspec.spec.weft_context,
        )
        resolved_spec = TaskSpec.model_validate(
            resolved_payload, context={"auto_expand": False}
        )
        resolved_spec.set_bundle_root(taskspec.get_bundle_root())

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

        if interactive:
            use_prompt = stdin_data is None and stdin_is_terminal
            session_stdin = stdin_data
            session_auto_close = not use_prompt
            if isinstance(work_payload, dict) and "stdin" in work_payload:
                session_stdin = None
                session_auto_close = False
            status, result_value, error_message = _run_interactive_session(
                context,
                resolved_spec,
                stdin_data=session_stdin,
                auto_close=session_auto_close,
                use_prompt=use_prompt,
            )
            if not use_prompt and result_value:
                typer.echo(result_value, nl=False)
                if not str(result_value).endswith("\n"):
                    typer.echo()
                result_value = ""
        else:
            status, result_value, error_message = _wait_for_task_completion(
                context,
                resolved_spec,
                json_output=json_output,
                verbose=verbose,
            )
    except Exception as exc:
        if started_here and manager_record is not None:
            _stop_manager(context, manager_record, process_handle)
        typer.echo(f"Error submitting task: {exc}", err=True)
        return 1
    else:
        if started_here and wait and not reuse_enabled and manager_record is not None:
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

    display_error = error_message
    if status == "cancelled":
        display_error = "Task cancelled"
    elif status == "killed":
        display_error = "Task killed"

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "tid": tid,
                    "status": status,
                    "error": display_error,
                },
                ensure_ascii=False,
            )
        )
    else:
        typer.echo(f"Error executing task: {display_error}", err=True)
    return 124 if status == "timeout" else 1


def _run_spec_via_manager(
    spec_ref: str | Path,
    *,
    context_dir: Path | None = None,
    run_input_tokens: Sequence[str] = (),
    verbose: bool,
    wait: bool,
    json_output: bool,
    autostart_enabled: bool,
    persistent_override: bool | None,
) -> int:
    spec = _load_taskspec_reference(spec_ref, context_dir=context_dir)
    bundle_root = spec.get_bundle_root()
    spec_payload = spec.model_dump(mode="json")
    if persistent_override is not None:
        spec_payload.setdefault("spec", {})
        spec_payload["spec"]["persistent"] = persistent_override
    spec = TaskSpec.model_validate(
        spec_payload,
        context={"template": True, "auto_expand": False},
    )
    spec.set_bundle_root(bundle_root)
    spec, remaining_tokens = _materialize_parameterized_spec(
        taskspec=spec,
        context_root=str(context_dir)
        if context_dir is not None
        else spec.spec.weft_context,
        run_input_tokens=run_input_tokens,
    )
    if spec.spec.persistent and wait:
        raise typer.BadParameter(
            "--wait is not supported for persistent TaskSpecs; use --no-wait."
        )
    context = build_context(spec.spec.weft_context, autostart=autostart_enabled)
    stdin_data = _read_piped_stdin(context)
    work_payload = _build_spec_work_payload(
        taskspec=spec,
        context=context,
        stdin_data=stdin_data,
        run_input_tokens=remaining_tokens,
    )
    manager_record: dict[str, Any] | None = None
    started_here = False
    process_handle: subprocess.Popen[bytes] | None = None
    reuse_enabled = bool(context.config.get("WEFT_MANAGER_REUSE_ENABLED", True))

    try:
        tid_int = _enqueue_taskspec(
            context,
            spec,
            work_payload,
        )
        manager_record, started_here, process_handle = _ensure_manager_after_submission(
            context,
            submitted_tid=tid_int,
            verbose=verbose,
        )
        tid = str(tid_int)
        resolved_payload = resolve_taskspec_payload(
            spec.model_dump(mode="json"),
            tid=tid,
            inherited_weft_context=spec.spec.weft_context,
        )
        resolved_spec = TaskSpec.model_validate(
            resolved_payload, context={"auto_expand": False}
        )
        resolved_spec.set_bundle_root(spec.get_bundle_root())
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
            resolved_spec,
            json_output=json_output,
            verbose=verbose,
        )
    except Exception as exc:
        if started_here and manager_record is not None:
            _stop_manager(context, manager_record, process_handle)
        typer.echo(f"Error submitting TaskSpec: {exc}", err=True)
        return 1
    else:
        if started_here and wait and not reuse_enabled and manager_record is not None:
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


def _run_pipeline(
    pipeline: str | Path,
    *,
    pipeline_input: str | None,
    context_dir: Path | None,
    wait: bool,
    json_output: bool,
    verbose: bool,
    autostart_enabled: bool,
) -> int:
    context = build_context(spec_context=context_dir, autostart=autostart_enabled)
    pipeline_spec, source_ref = _load_pipeline_spec(pipeline, context_dir=context_dir)
    stdin_data = _read_piped_stdin(context)
    if pipeline_input is not None and stdin_data is not None:
        raise typer.BadParameter("--input cannot be used together with piped stdin")

    requested_input: Any = None
    if pipeline_input is not None:
        requested_input = _parse_cli_value(pipeline_input)
    elif stdin_data is not None:
        requested_input = stdin_data

    def _load_pipeline_stage(task_name: str) -> dict[str, Any]:
        resolved = spec_cmd.resolve_named_spec(
            task_name,
            spec_type=spec_cmd.SPEC_TYPE_TASK,
            context_path=context_dir,
        )
        return apply_bundle_root_to_taskspec_payload(
            dict(resolved.payload),
            resolved.bundle_root,
        )

    compiled = compile_linear_pipeline(
        pipeline_spec,
        context=context,
        task_loader=_load_pipeline_stage,
        source_ref=source_ref,
    )
    work_payload = (
        requested_input
        if requested_input is not None
        else compiled.bootstrap_input_fallback
    )

    manager_record: dict[str, Any] | None = None
    process_handle: subprocess.Popen[bytes] | None = None
    manager_started_here = False
    reuse_enabled = bool(context.config.get("WEFT_MANAGER_REUSE_ENABLED", True))

    try:
        tid_int = _enqueue_taskspec(
            context,
            compiled.pipeline_taskspec,
            work_payload,
            seed_start_envelope=False,
        )
        manager_record, started_here, process_handle = _ensure_manager_after_submission(
            context,
            submitted_tid=tid_int,
            verbose=verbose,
        )
        tid = str(tid_int)
        if started_here:
            manager_started_here = True

        if verbose:
            typer.echo(
                json.dumps(
                    {
                        "tid": tid,
                        "pipeline": compiled.runtime.pipeline_name,
                        "db": context.broker_display_target,
                    },
                    indent=2,
                )
            )

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
            compiled.pipeline_taskspec,
            json_output=json_output,
            verbose=verbose,
        )

    finally:
        if (
            manager_started_here
            and wait
            and not reuse_enabled
            and manager_record is not None
        ):
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
        typer.echo(f"Pipeline failed: {error_message}", err=True)
    return 124 if status == "timeout" else 1


# -----------------------------------------------------------------------------
# Public entry point
# -----------------------------------------------------------------------------


def cmd_run(
    command: Sequence[str],
    *,
    spec_run_args: Sequence[str],
    spec: str | Path | None,
    pipeline: str | Path | None,
    pipeline_input: str | None,
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
    persistent_override: bool | None,
    autostart_enabled: bool,
) -> int:
    """Execute a command, function, task spec, or pipeline.

    Four execution modes (mutually exclusive):

    \b
      weft run COMMAND [ARGS...]        Run a shell command
      weft run --function mod:fn        Call a Python function
      weft run --spec NAME|PATH         Run a task spec by stored name or path
      weft run --pipeline NAME|PATH     Run a pipeline by stored name or path

    \b
    Common patterns:
      weft run echo "hello"                     Simple command
      weft run --timeout 60 --memory 512 heavy  With resource limits
      weft run --no-wait long-task.sh            Fire and forget
      printf "data" | weft run -- processor     Pipe stdin
      weft run --function mymod:fn --arg x      Function with args
      weft run --spec probe-agents              Builtin helper TaskSpec

    By default, waits for the task to complete and prints output.
    Use --no-wait to submit and return immediately (prints TID).
    """
    if pipeline is not None:
        if spec is not None or command or function:
            raise typer.BadParameter(
                "--pipeline cannot be combined with --spec, --function, or commands"
            )
        if args or kwargs or env or tags:
            raise typer.BadParameter(
                "--arg/--kw/--env/--tag are not compatible with --pipeline."
            )
        if monitor:
            raise typer.BadParameter("--monitor is not supported with pipelines.")
        if persistent_override is not None:
            raise typer.BadParameter(
                "--continuous/--once is not supported with pipelines."
            )
        return _run_pipeline(
            pipeline,
            pipeline_input=pipeline_input,
            context_dir=context_dir,
            wait=wait,
            json_output=json_output,
            verbose=verbose,
            autostart_enabled=autostart_enabled,
        )
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
        return _run_spec_via_manager(
            spec,
            context_dir=context_dir,
            run_input_tokens=spec_run_args,
            verbose=verbose,
            wait=wait,
            json_output=json_output,
            autostart_enabled=autostart_enabled,
            persistent_override=persistent_override,
        )

    if monitor:
        raise typer.BadParameter("--monitor is only supported together with --spec.")
    if persistent_override is not None:
        raise typer.BadParameter(
            "--continuous/--once is only supported together with --spec."
        )

    if not command and not function:
        raise typer.BadParameter(
            "Provide a command to execute or specify --function module:callable."
        )
    if command and function:
        raise typer.BadParameter(
            "Cannot execute a shell command and --function simultaneously."
        )
    if command and command[0].startswith("--"):
        raise typer.BadParameter(
            f"Unknown option '{command[0]}'. If this is intentional command "
            "input, use a command that does not begin with '--'."
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
        autostart_enabled=autostart_enabled,
    )


__all__ = [
    "_build_manager_spec",
    "_ensure_manager",
    "_generate_tid",
    "_select_active_manager",
    "_start_manager",
    "cmd_run",
    "render_spec_aware_run_help",
]
