"""Shared `weft run` execution helpers.

Submission returns structured execution receipts. Waiting and realtime output
use the common result owners through RunSession; the CLI owns prompt callbacks
and rendering. This module is not a public `WeftClient.run()` surface.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-1.1.1]
- docs/specifications/10B-Builtin_TaskSpecs.md
- docs/specifications/01-Core_Components.md [CC-2.5]
- docs/specifications/02-TaskSpec.md [TS-1], [TS-1.3]
- docs/specifications/05-Message_Flow_and_State.md [MF-3], [MF-5]
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import weft.commands.specs as spec_cmd
from simplebroker import Queue, format_message_id
from weft._constants import (
    CONTROL_KILL,
    CONTROL_STOP,
    DEFAULT_STREAM_OUTPUT,
    INTERACTIVE_STOP_COMPLETION_TIMEOUT,
    INTERNAL_RUNTIME_ENDPOINT_NAME_KEY,
    QUEUE_CTRL_IN_SUFFIX,
    QUEUE_CTRL_OUT_SUFFIX,
    QUEUE_INBOX_SUFFIX,
    QUEUE_OUTBOX_SUFFIX,
)
from weft._exceptions import CommandUsageError, SubmissionError
from weft.commands._boundary import typed_command_errors
from weft.commands._result_wait import result_observation
from weft.commands.result import await_task_result
from weft.commands.submission import (
    _annotate_accepted_submission_error,
    _bind_accepted_taskspec,
    _resolve_submission_runtime_root,
)
from weft.commands.submission import (
    _initial_work_payload as _submission_initial_work_payload,
)
from weft.commands.submission import (
    ensure_manager_after_submission as _shared_ensure_manager_after_submission,
)
from weft.commands.submission import prepare_spec as _prepare_spec_submission
from weft.commands.types import (
    CommandStream,
    RunExecutionResult,
    RunSession,
    RunSpecDescription,
    TaskControlResult,
    TaskEvent,
)
from weft.context import WeftContext, build_context
from weft.core import manager_runtime, task_evidence
from weft.core.control_messages import decode_control_object, encode_control_message
from weft.core.endpoints import validate_endpoint_claim_name
from weft.core.pipelines import (
    PipelineSpec,
    compile_linear_pipeline,
    load_pipeline_spec_payload,
)
from weft.core.spawn_requests import (
    _submit_spawn_request_with_payload,
    delete_spawn_request,
)
from weft.core.task_state import task_state_queue_name
from weft.core.taskspec import (
    TaskSpec,
    encode_taskspec_transport_payload,
    normalize_declared_option_name,
    validate_taskspec_payload,
)
from weft.helpers import _register_observation_wakeup, closing_queue_iterator

# -----------------------------------------------------------------------------
# Explicit spec helpers
# -----------------------------------------------------------------------------


class RunUsageError(CommandUsageError):
    """Raised when run-surface inputs do not satisfy the command contract."""

    def __init__(self, message: str, *, param_hint: str | None = None) -> None:
        super().__init__(message)
        self.param_hint = param_hint


class RunResolutionError(CommandUsageError):
    """Raised when a spec or pipeline reference cannot be loaded for run-mode use."""


def _echo(message: Any = "", *, err: bool = False, nl: bool = True) -> None:
    """Write a CLI-facing message without importing the Typer adapter layer."""

    stream = sys.stderr if err else sys.stdout
    text = "" if message is None else str(message)
    stream.write(text)
    if nl:
        stream.write("\n")
    stream.flush()


def _emit_manager_started(record: dict[str, Any]) -> None:
    """Emit the CLI-visible manager startup event for verbose runs."""

    _echo(json.dumps(_manager_started_payload(record), ensure_ascii=False))


def _manager_started_payload(record: dict[str, Any]) -> dict[str, Any]:
    timestamp = record.get("timestamp")
    return {
        "event": "manager_started",
        "manager_tid": record.get("tid"),
        "runtime_handle": record.get("runtime_handle"),
        "queues": {
            key: record.get(key)
            for key in ("requests", "outbox", "ctrl_in", "ctrl_out")
            if record.get(key)
        },
        "timestamp": (
            format_message_id(timestamp)
            if isinstance(timestamp, int) and not isinstance(timestamp, bool)
            else timestamp
        ),
    }


def _run_with_managed_execution(
    *,
    context: WeftContext,
    submit: Callable[[], tuple[int, dict[str, Any]]],
    verbose: bool,
    emit_verbose: bool = True,
    on_submitted: Callable[[str, WeftContext, TaskSpec], None] | None = None,
) -> RunExecutionResult:
    """Retain accepted config through the managed run lifecycle [MF-1], [MF-5]."""
    manager_record: dict[str, Any] | None = None
    started_here = False
    process_handle: subprocess.Popen[bytes] | None = None
    manager_started_payload: dict[str, Any] | None = None
    accepted_tid: str | None = None

    try:
        tid_int, shaped_payload = submit()
        accepted_tid = str(tid_int)
        availability = _ensure_manager_after_submission(
            context,
            submitted_tid=tid_int,
        )
        manager_record = availability.manager_record
        started_here = availability.started_here
        process_handle = availability.process_handle
        availability_warning = (
            f"Task {tid_int} was accepted, but manager readiness is degraded: "
            f"{availability.reason}"
            if availability.outcome == "uncertain"
            else None
        )
        if emit_verbose and started_here and verbose and manager_record is not None:
            _emit_manager_started(manager_record)
        if started_here and manager_record is not None:
            manager_started_payload = _manager_started_payload(manager_record)
        tid = str(tid_int)
        accepted_taskspec = _bind_accepted_taskspec(shaped_payload, tid)
        if on_submitted is not None:
            on_submitted(tid, context, accepted_taskspec)

        return RunExecutionResult(
            tid=tid,
            manager_started_payload=manager_started_payload,
            availability_warning=availability_warning,
        )
    except Exception as exc:
        if accepted_tid is not None:
            _annotate_accepted_submission_error(exc, accepted_tid)
        if started_here and manager_record is not None:
            manager_runtime.stop_manager(context, manager_record, process_handle)
        raise


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
        return validate_taskspec_payload(
            resolved.payload,
            bundle_root=resolved.bundle_root,
            template=True,
        )
    except Exception as exc:  # pragma: no cover - validation tested elsewhere
        raise RunResolutionError(str(exc)) from exc


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
    base_help: str,
    *,
    spec: str | Path,
    context_dir: Path | None,
) -> str:
    """Return `weft run` help augmented with selected TaskSpec help."""
    taskspec = _load_taskspec_reference(spec, context_dir=context_dir)

    lines = [base_help, "", f"Spec Help: {taskspec.name}"]
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


def _describe_taskspec(
    reference: str | Path,
    *,
    context_dir: Path | None,
) -> RunSpecDescription:
    """Resolve lossless, parser-independent help metadata for one TaskSpec."""

    taskspec = _load_taskspec_reference(reference, context_dir=context_dir)
    argument_records: list[Mapping[str, Any]] = []
    parameterization = taskspec.spec.parameterization
    if parameterization is not None:
        for name, parameter_declaration in parameterization.arguments.items():
            argument_records.append(
                {"name": name, **parameter_declaration.model_dump(mode="json")}
            )
    run_input = taskspec.spec.run_input
    if run_input is not None:
        for name, run_input_declaration in run_input.arguments.items():
            argument_records.append(
                {
                    "name": name,
                    "source": "run_input",
                    **run_input_declaration.model_dump(mode="json"),
                }
            )
    stdin_record: Mapping[str, Any] | None = None
    if run_input is not None and run_input.stdin is not None:
        stdin_record = run_input.stdin.model_dump(mode="json")
    return RunSpecDescription(
        reference=str(reference),
        usage=render_spec_aware_run_help(
            "Usage: weft run --spec NAME|PATH [SPEC OPTIONS]",
            spec=reference,
            context_dir=context_dir,
        ),
        arguments=tuple(argument_records),
        stdin=stdin_record,
    )


class _LiveRunSession:
    """Retain accepted config for direct result observation [MF-5], [PY-2]."""

    def __init__(
        self,
        execution: RunExecutionResult,
        context: WeftContext,
        *,
        _accepted_taskspec: TaskSpec | None = None,
    ) -> None:
        self.tid = execution.tid
        self._execution = execution
        self._context = context
        self._accepted_taskspec = _accepted_taskspec
        self._streams: dict[CommandStream[TaskEvent], int] = {}
        self._stream_lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._wakeups: dict[object, Callable[[], None]] = {}
        self._result: RunExecutionResult | None = None
        self._closed = False
        with ExitStack() as resources:
            resources.enter_context(context.session())
            self._input_queue = context.queue(
                (
                    _accepted_taskspec.io.inputs["inbox"]
                    if _accepted_taskspec is not None
                    else f"T{self.tid}.{QUEUE_INBOX_SUFFIX}"
                ),
                persistent=True,
            )
            resources.callback(self._input_queue.close)
            self._input_resources = resources.pop_all()

    def events(self) -> CommandStream[TaskEvent]:
        # Late import breaks the real result/events cycle.
        from .events import iter_task_realtime_events

        def events_on_owner() -> Iterator[TaskEvent]:
            stream = cast(
                CommandStream[TaskEvent],
                iter_task_realtime_events(
                    self._context,
                    self.tid,
                    follow=True,
                    _accepted_taskspec=self._accepted_taskspec,
                    cancel_event=self,
                ),
            )
            with self._stream_lock:
                self._streams[stream] = threading.get_ident()
            try:
                yield from stream
            finally:
                try:
                    stream.close()
                finally:
                    with self._stream_lock:
                        self._streams.pop(stream, None)

        return cast(CommandStream[TaskEvent], events_on_owner())

    def is_set(self) -> bool:
        """Expose output cancellation through the shared paired wake contract."""
        return self._cancel_event.is_set()

    def _register_wakeup(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Register non-blocking direct wake outside the stream-owner lock."""
        return _register_observation_wakeup(
            self._stream_lock, self._cancel_event, self._wakeups, callback
        )

    def _cancel_output(self) -> None:
        """Wake this session's streams; consuming threads retain iterator closure."""
        with self._stream_lock:
            callbacks = tuple(self._wakeups.values()) if not self.is_set() else ()
            self._cancel_event.set()
        for callback in callbacks:
            callback()

    def send_input(self, text: str) -> None:
        self._input_queue.write(json.dumps({"stdin": text}))

    def close_input(self) -> None:
        self._input_queue.write(json.dumps({"close": True}))

    @typed_command_errors
    def stop(self) -> TaskControlResult:
        """Stop this session's task on the context it was submitted with [PY-2]."""
        # Late import avoids the tasks -> system -> run dependency closure.
        from .tasks import _task_control_result

        return _task_control_result(
            "stop",
            self.tid,
            tids=None,
            all_tasks=False,
            pattern=None,
            context_path=None,
            runtime_context=self._context,
        )

    def wait(self, timeout: float | None = None) -> RunExecutionResult:
        if self._result is not None:
            return self._result
        result = await_task_result(
            self._context,
            self.tid,
            timeout=timeout,
            _accepted_taskspec=self._accepted_taskspec,
        )
        self._result = replace(
            self._execution,
            status=result.status,
            result_value=result.value,
            error_message=result.error,
        )
        return self._result

    def close(self) -> None:
        self._cancel_output()
        with self._stream_lock:
            if self._closed:
                return
            self._closed = True
            streams = [
                stream
                for stream, owner in self._streams.items()
                if owner == threading.get_ident()
            ]
        resources = ExitStack()
        resources.callback(self._input_resources.close)
        for stream in streams:
            resources.callback(stream.close)
        resources.close()


def _matching_stop_ack(queue: Queue, tid: str, request_id: str) -> bool:
    """Acknowledge only this keyed STOP, retaining all other control proof [MF-3]."""
    with closing_queue_iterator(queue.peek_generator(with_timestamps=True)) as rows:
        for raw, message_id in rows:
            payload = decode_control_object(raw)
            if payload is not None and (
                payload.get("tid") == tid
                and payload.get("command") == CONTROL_STOP
                and payload.get("status") == "ack"
                and payload.get("request_id") == request_id
            ):
                queue.delete(message_id=message_id)
                return True
    return False


@typed_command_errors
def _stop_interactive_run_session(session: _LiveRunSession) -> None:
    """Close stdin and observe keyed STOP through its actual deadline [CLI-1.1.1].

    The stop observer is independent of the output stream's cancellation scope.
    ACK is progress, not a synthetic terminal task state. KILL follows only
    expiry of the named STOP completion budget.
    """
    # Late import avoids the tasks -> system -> run dependency closure.
    from .tasks import _terminal_observation_snapshot

    ctx = session._context
    tid = session.tid
    session.close_input()
    payload = (
        session._accepted_taskspec.model_dump(mode="json")
        if session._accepted_taskspec
        else None
    )
    config = task_evidence.observation_config_from_taskspec_payload(payload)
    if config is None:
        # Late import avoids the tasks -> system -> run dependency closure.
        from .tasks import _require_observation_config

        config = _require_observation_config(ctx, tid)
    names = [task_state_queue_name(tid), config.ctrl_in, config.ctrl_out, config.outbox]
    with result_observation(ctx, names) as (queues, monitor):
        request_id = uuid4().hex
        queues[config.ctrl_in].write(
            encode_control_message(CONTROL_STOP, request_id=request_id)
        )
        deadline = time.monotonic() + INTERACTIVE_STOP_COMPLETION_TIMEOUT
        while True:
            with queues[config.ctrl_out].get_connection() as broker:
                snapshot = _terminal_observation_snapshot(
                    ctx,
                    tid,
                    routing_payload=payload,
                    observation_config=config,
                    broker=broker,
                )
            if snapshot.terminal or _matching_stop_ack(
                queues[config.ctrl_out], tid, request_id
            ):
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                queues[config.ctrl_in].write(
                    encode_control_message(CONTROL_KILL, request_id=uuid4().hex)
                )
                return
            monitor.wait(remaining)


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
            raise RunUsageError(
                f"Keyword argument '{item}' is missing '=' (expected key=value)"
            )
        key, value = item.split("=", 1)
        result[key] = _parse_cli_value(value)
    return result


def _parse_env(values: Sequence[str]) -> dict[str, str]:
    env: dict[str, str] = {}
    for item in values:
        if "=" not in item:
            raise RunUsageError(
                f"Environment entry '{item}' is missing '=' (expected KEY=VALUE)"
            )
        key, value = item.split("=", 1)
        env[key] = value
    return env


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


def _enqueue_taskspec(
    context: WeftContext,
    taskspec: TaskSpec,
    work_payload: Any,
    *,
    seed_start_envelope: bool = True,
    allow_internal_runtime: bool = False,
) -> tuple[int, dict[str, Any]]:
    """Return committed TID and shaped transport [MA-2], [MF-1], [MF-5]."""
    return _submit_spawn_request_with_payload(
        context.broker_target,
        taskspec=taskspec,
        work_payload=work_payload,
        config=context.broker_config,
        tid=taskspec.tid,
        inherited_weft_context=taskspec.spec.weft_context,
        seed_start_envelope=seed_start_envelope,
        allow_internal_runtime=allow_internal_runtime,
    )


def _delete_spawn_request(context: WeftContext, message_timestamp: int) -> bool:
    """Best-effort removal of a queued spawn request after submission failure."""

    return delete_spawn_request(
        context.broker_target,
        message_timestamp=message_timestamp,
        config=context.broker_config,
    )


def _ensure_manager_after_submission(
    context: WeftContext,
    *,
    submitted_tid: str | int,
) -> manager_runtime.ManagerEnsureResult:
    """Wire queue-first recovery to the manager and request cleanup owners."""

    return _shared_ensure_manager_after_submission(
        context,
        submitted_tid=submitted_tid,
    )


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
            raise RunUsageError("CPU limit must be between 1 and 100 percent")
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


def _apply_explicit_run_name(taskspec: TaskSpec, explicit_name: str | None) -> TaskSpec:
    """Apply an explicit CLI name override to a runtime TaskSpec template.

    For persistent tasks only, an explicit CLI name also becomes the runtime
    endpoint claim name. This remains submission-time shaping; the task still
    claims and releases the endpoint through its ordinary lifecycle.
    """

    if explicit_name is None:
        return taskspec

    payload = taskspec.model_dump(mode="json")
    payload["name"] = explicit_name
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        payload["metadata"] = metadata
    metadata.pop(INTERNAL_RUNTIME_ENDPOINT_NAME_KEY, None)

    try:
        endpoint_name = validate_endpoint_claim_name(explicit_name)
    except ValueError as exc:
        raise RunUsageError(str(exc), param_hint="--name") from exc

    if bool(getattr(taskspec.spec, "persistent", False)):
        metadata[INTERNAL_RUNTIME_ENDPOINT_NAME_KEY] = endpoint_name

    return validate_taskspec_payload(
        payload,
        bundle_root=taskspec.get_bundle_root(),
        template=taskspec.tid is None,
    )


def _execute_inline(
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
    json_output: bool,
    verbose: bool,
    autostart_enabled: bool,
    stdin_data: str | None = None,
    on_submitted: Callable[[str, WeftContext, TaskSpec], None] | None = None,
) -> RunExecutionResult:
    target_type = "command" if command else "function"
    if target_type == "command" and not command:
        raise RunUsageError("Provide a command to execute or use --function")
    if target_type == "function" and (
        not function_target or ":" not in function_target
    ):
        raise RunUsageError(
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

    work_payload = _submission_initial_work_payload(
        target_type=target_type,
        stdin_text=stdin_data,
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

    taskspec = validate_taskspec_payload(template_dict, template=True)

    if interactive:
        if target_type != "command":
            raise RunUsageError("--interactive is only supported for command targets")
        if json_output:
            raise RunUsageError("--json is not supported together with --interactive")

    try:
        execution = _run_with_managed_execution(
            context=context,
            submit=lambda: _enqueue_taskspec(
                context,
                taskspec,
                work_payload,
            ),
            verbose=verbose,
            emit_verbose=False,
            on_submitted=on_submitted,
        )
    except Exception as exc:
        raise SubmissionError(f"Error submitting task: {exc}") from exc

    return replace(
        execution,
        submitted_payload={
            "tid": execution.tid,
            "task": task_name,
            "db": context.broker_display_target,
        },
    )


def _execute_spec_via_manager(
    spec_ref: str | Path,
    *,
    name: str | None = None,
    context_dir: Path | None = None,
    run_input_tokens: Sequence[str] = (),
    verbose: bool,
    json_output: bool,
    autostart_enabled: bool,
    persistent_override: bool | None,
    stdin_data: str | None = None,
    on_submitted: Callable[[str, WeftContext, TaskSpec], None] | None = None,
    session_wait_requested: bool = False,
) -> RunExecutionResult:
    submission_context = build_context(
        spec_context=context_dir,
        autostart=autostart_enabled,
    )
    prepared = _prepare_spec_submission(
        submission_context,
        spec_ref,
        spec_args=run_input_tokens,
        stdin_text=stdin_data,
        context_explicit=context_dir is not None,
        persistent_override=persistent_override,
        name=name,
    )
    spec = cast(TaskSpec, prepared.taskspec)
    if spec.spec.persistent and session_wait_requested:
        raise RunUsageError(
            "--wait is not supported for persistent TaskSpecs; use --no-wait."
        )
    runtime_root = _resolve_submission_runtime_root(spec, submission_context)
    context = (
        submission_context
        if runtime_root.resolve() == submission_context.root.resolve()
        else build_context(
            runtime_root,
            config=submission_context.config,
            autostart=autostart_enabled,
        )
    )
    work_payload = prepared.payload

    try:
        execution = _run_with_managed_execution(
            context=context,
            submit=lambda: _enqueue_taskspec(
                context,
                spec,
                work_payload,
            ),
            verbose=verbose,
            emit_verbose=False,
            on_submitted=on_submitted,
        )
    except Exception as exc:
        raise SubmissionError(f"Error submitting TaskSpec: {exc}") from exc

    return replace(
        execution,
        submitted_payload={
            "tid": execution.tid,
            "task": spec.name,
            "db": context.broker_display_target,
        },
    )


def _execute_pipeline(
    pipeline: str | Path,
    *,
    name: str | None,
    pipeline_input: str | None,
    context_dir: Path | None,
    json_output: bool,
    verbose: bool,
    autostart_enabled: bool,
    stdin_data: str | None = None,
    on_submitted: Callable[[str, WeftContext, TaskSpec], None] | None = None,
) -> RunExecutionResult:
    context = build_context(spec_context=context_dir, autostart=autostart_enabled)
    pipeline_spec, source_ref = _load_pipeline_spec(pipeline, context_dir=context_dir)
    if pipeline_input is not None and stdin_data is not None:
        raise RunUsageError("--input cannot be used together with piped stdin")

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
        return encode_taskspec_transport_payload(
            validate_taskspec_payload(
                resolved.payload,
                bundle_root=resolved.bundle_root,
                template=True,
            )
        )

    compiled = compile_linear_pipeline(
        pipeline_spec,
        context=context,
        task_loader=_load_pipeline_stage,
        source_ref=source_ref,
    )
    compiled = replace(
        compiled,
        pipeline_taskspec=_apply_explicit_run_name(compiled.pipeline_taskspec, name),
    )
    work_payload = (
        requested_input
        if requested_input is not None
        else compiled.bootstrap_input_fallback
    )

    execution = _run_with_managed_execution(
        context=context,
        submit=lambda: _enqueue_taskspec(
            context,
            compiled.pipeline_taskspec,
            work_payload,
            seed_start_envelope=False,
            allow_internal_runtime=True,
        ),
        verbose=verbose,
        emit_verbose=False,
        on_submitted=on_submitted,
    )

    return replace(
        execution,
        error_prefix="Pipeline failed",
        submitted_payload={
            "tid": execution.tid,
            "pipeline": compiled.runtime.pipeline_name,
            "db": context.broker_display_target,
        },
    )


# -----------------------------------------------------------------------------
# Public entry point
# -----------------------------------------------------------------------------


def execute_run(  # noqa: C901 approved [TS-3.1] [RUFF-SUP-114] exception
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
    json_output: bool,
    verbose: bool,
    persistent_override: bool | None,
    autostart_enabled: bool,
    stdin_text: str | None = None,
    on_submitted: Callable[[str, WeftContext, TaskSpec], None] | None = None,
    session_wait_requested: bool = False,
) -> RunExecutionResult:
    """Execute a command, function, task spec, or pipeline without rendering.

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

    Submission itself does not wait or render. `cmd_run` supplies the public
    waiting session and the CLI renders its structured outcome.
    """
    if pipeline is not None:
        if spec is not None or command or function:
            raise RunUsageError(
                "--pipeline cannot be combined with --spec, --function, or commands"
            )
        if args or kwargs or env or tags:
            raise RunUsageError(
                "--arg/--kw/--env/--tag are not compatible with --pipeline."
            )
        if persistent_override is not None:
            raise RunUsageError("--continuous/--once is not supported with pipelines.")
        return _execute_pipeline(
            pipeline,
            name=name,
            pipeline_input=pipeline_input,
            context_dir=context_dir,
            json_output=json_output,
            verbose=verbose,
            autostart_enabled=autostart_enabled,
            stdin_data=stdin_text,
            on_submitted=on_submitted,
        )
    if spec is not None:
        if command:
            raise RunUsageError("Provide either a TaskSpec file or a command.")
        if function:
            raise RunUsageError("--function cannot be used together with --spec.")
        if args or kwargs or env or tags:
            raise RunUsageError(
                "--arg/--kw/--env/--tag are not compatible with --spec."
            )
        return _execute_spec_via_manager(
            spec,
            name=name,
            context_dir=context_dir,
            run_input_tokens=spec_run_args,
            verbose=verbose,
            json_output=json_output,
            autostart_enabled=autostart_enabled,
            persistent_override=persistent_override,
            stdin_data=stdin_text,
            on_submitted=on_submitted,
            session_wait_requested=session_wait_requested,
        )

    if persistent_override is not None:
        raise RunUsageError(
            "--continuous/--once is only supported together with --spec."
        )

    if not command and not function:
        raise RunUsageError(
            "Provide a command to execute or specify --function module:callable."
        )
    if command and function:
        raise RunUsageError(
            "Cannot execute a shell command and --function simultaneously."
        )
    if command and command[0].startswith("--"):
        raise RunUsageError(
            f"Unknown option '{command[0]}'. If this is intentional command "
            "input, use a command that does not begin with '--'."
        )

    return _execute_inline(
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
        json_output=json_output,
        verbose=verbose,
        autostart_enabled=autostart_enabled,
        stdin_data=stdin_text,
        on_submitted=on_submitted,
    )


@typed_command_errors
def cmd_run(
    command: Sequence[str],
    *,
    spec_args: Sequence[str] = (),
    spec: str | Path | None = None,
    pipeline: str | Path | None = None,
    input: str | None = None,
    function: str | None = None,
    arg: Sequence[str] = (),
    kw: Sequence[str] = (),
    env: Sequence[str] = (),
    name: str | None = None,
    interactive: bool = False,
    stream_output: bool | None = None,
    timeout: float | None = None,
    memory: int | None = None,
    cpu: int | None = None,
    tag: Sequence[str] = (),
    context: str | Path | None = None,
    wait: bool = True,
    continuous: bool | None = None,
    autostart: bool = True,
    describe: bool = False,
    stdin_text: str | None = None,
) -> RunSpecDescription | RunSession | RunExecutionResult:
    """Execute the same semantic operation exposed by ``weft run``.

    Process input and output remain adapter concerns. The decoded stdin value
    is explicit so embedded callers never touch ambient process I/O.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2].
    """

    context_dir = Path(context) if context is not None else None
    if describe:
        if spec is None:
            raise RunUsageError("describe mode requires --spec", param_hint="--spec")
        return _describe_taskspec(spec, context_dir=context_dir)

    submitted_context: WeftContext | None = None
    submitted_taskspec: TaskSpec | None = None

    def _capture_submission(
        _tid: str, resolved_context: WeftContext, accepted_taskspec: TaskSpec
    ) -> None:
        nonlocal submitted_context, submitted_taskspec
        submitted_context = resolved_context
        submitted_taskspec = accepted_taskspec

    execution = execute_run(
        tuple(command),
        spec_run_args=tuple(spec_args),
        spec=spec,
        pipeline=pipeline,
        pipeline_input=input,
        function=function,
        args=tuple(arg),
        kwargs=tuple(kw),
        env=tuple(env),
        name=name,
        interactive=interactive,
        stream_output=stream_output,
        timeout=timeout,
        memory=memory,
        cpu=cpu,
        tags=tuple(tag),
        context_dir=context_dir,
        json_output=False,
        verbose=False,
        persistent_override=continuous,
        autostart_enabled=autostart,
        stdin_text=stdin_text,
        on_submitted=_capture_submission,
        session_wait_requested=wait,
    )
    if wait:
        if submitted_context is None:  # pragma: no cover - execution invariant
            raise SubmissionError("run submission completed without a context")
        return _LiveRunSession(
            execution, submitted_context, _accepted_taskspec=submitted_taskspec
        )
    return execution


__all__ = [
    "RunResolutionError",
    "RunUsageError",
    "cmd_run",
    "execute_run",
    "render_spec_aware_run_help",
]
