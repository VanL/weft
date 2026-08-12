"""Shared `weft run` execution helpers.

Non-interactive run modes expose structured `execute_run()` results and leave
stdout/stderr rendering to the Typer adapter. Interactive prompt mode remains
presentation-adjacent here because it owns prompt-toolkit callbacks and live
stream display. This module is still not a public `WeftClient.run()` surface.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-1.1.1]
- docs/specifications/10B-Builtin_TaskSpecs.md
- docs/specifications/01-Core_Components.md [CC-2.5]
- docs/specifications/02-TaskSpec.md [TS-1], [TS-1.3]
- docs/specifications/05-Message_Flow_and_State.md [MF-3]
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

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
    WEFT_GLOBAL_LOG_QUEUE,
)
from weft.commands._result_wait import await_one_shot_result
from weft.commands._streaming import (
    collect_interactive_queue_output as _collect_interactive_queue_output,
)
from weft.commands._streaming import (
    poll_log_events,
)
from weft.commands._task_history import is_pipeline_taskspec_payload
from weft.commands.interactive import InteractiveStreamClient
from weft.commands.submission import (
    ensure_manager_after_submission as _shared_ensure_manager_after_submission,
)
from weft.commands.types import RunExecutionResult
from weft.context import WeftContext, build_context
from weft.core import manager_runtime
from weft.core.control_messages import encode_control_message
from weft.core.endpoints import validate_endpoint_claim_name
from weft.core.monitor.store import open_monitor_store
from weft.core.pipelines import (
    PipelineSpec,
    compile_linear_pipeline,
    load_pipeline_spec_payload,
)
from weft.core.spawn_requests import delete_spawn_request, submit_spawn_request
from weft.core.task_evidence import terminal_error_message, terminal_status_from_event
from weft.core.taskspec import (
    TaskSpec,
    encode_taskspec_transport_payload,
    invoke_run_input_adapter,
    materialize_taskspec_template,
    normalize_declared_option_name,
    parse_declared_parameterization_args,
    parse_declared_run_input_args,
    validate_taskspec_payload,
)
from weft.ext import SpecRunInputRequest
from weft.helpers import (
    read_limited_stdin,
    resolve_broker_max_message_size,
    stdin_is_tty,
)

# -----------------------------------------------------------------------------
# Explicit spec helpers
# -----------------------------------------------------------------------------


class RunUsageError(ValueError):
    """Raised when run-surface inputs do not satisfy the command contract."""

    def __init__(self, message: str, *, param_hint: str | None = None) -> None:
        super().__init__(message)
        self.param_hint = param_hint


class RunResolutionError(RuntimeError):
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
    submit: Callable[[], int],
    verbose: bool,
    wait: bool,
    reuse_enabled: bool,
    emit_verbose: bool = True,
    wait_for_completion: Callable[[str], tuple[str, Any, str | None]] | None = None,
    on_submitted: Callable[[str], None] | None = None,
) -> RunExecutionResult:
    """Share the manager-backed bootstrap -> submit -> wait -> optional stop flow."""
    manager_record: dict[str, Any] | None = None
    started_here = False
    process_handle: subprocess.Popen[bytes] | None = None
    failed = False
    manager_started_payload: dict[str, Any] | None = None

    try:
        tid_int = submit()
        manager_record, started_here, process_handle = _ensure_manager_after_submission(
            context,
            submitted_tid=tid_int,
        )
        if emit_verbose and started_here and verbose and manager_record is not None:
            _emit_manager_started(manager_record)
        if started_here and manager_record is not None:
            manager_started_payload = _manager_started_payload(manager_record)
        tid = str(tid_int)
        if on_submitted is not None:
            on_submitted(tid)

        if not wait:
            return RunExecutionResult(
                tid=tid,
                manager_started_payload=manager_started_payload,
            )

        if wait_for_completion is None:  # pragma: no cover - caller contract guard
            raise RuntimeError("wait_for_completion is required when wait=True")

        status, result_value, error_message = wait_for_completion(tid)
        return RunExecutionResult(
            tid=tid,
            status=status,
            result_value=result_value,
            error_message=error_message,
            manager_started_payload=manager_started_payload,
        )
    except Exception:  # pragma: no cover - managed execution cleanup
        failed = True
        if started_here and manager_record is not None:
            manager_runtime.stop_manager(context, manager_record, process_handle)
        raise
    finally:
        if (
            not failed
            and started_here
            and wait
            and not reuse_enabled
            and manager_record is not None
        ):
            manager_runtime.stop_manager(context, manager_record, process_handle)


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


def _read_piped_stdin(context: WeftContext) -> str | None:
    """Read non-interactive stdin using the active broker size limit."""
    if not stdin_is_tty():
        try:
            max_bytes = resolve_broker_max_message_size(context.config)
            data = read_limited_stdin(max_bytes)
        except ValueError as exc:
            raise RunUsageError(str(exc)) from exc
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
    allow_internal_runtime: bool = False,
) -> int:
    # Spec: docs/specifications/03-Manager_Architecture.md [MA-2];
    # docs/specifications/05-Message_Flow_and_State.md [MF-1]
    return submit_spawn_request(
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
) -> tuple[dict[str, Any] | None, bool, subprocess.Popen[Any] | None]:
    """Wire queue-first recovery to the manager and request cleanup owners."""

    return _shared_ensure_manager_after_submission(
        context,
        submitted_tid=submitted_tid,
        ensure_manager_fn=manager_runtime.ensure_manager,
        delete_spawn_request_fn=_delete_spawn_request,
    )


def _wait_for_task_completion(
    context: WeftContext,
    taskspec: TaskSpec,
) -> tuple[str, Any | None, str | None]:
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


class _InteractiveRunLifecycle:
    """Own one command-side interactive session and its live resources.

    Spec: [CC-2.3], [SB-0.4], [MF-3], [MF-5], [CLI-1.1.1]
    """

    def __init__(
        self,
        context: WeftContext,
        taskspec: TaskSpec,
        *,
        use_prompt: bool,
    ) -> None:
        assert taskspec.tid is not None
        self._context = context
        self._tid = taskspec.tid
        self._use_prompt = use_prompt
        self._outbox_name = taskspec.io.outputs.get("outbox") or (
            f"T{self._tid}.{QUEUE_OUTBOX_SUFFIX}"
        )
        ctrl_out_name = taskspec.io.control.get("ctrl_out") or (
            f"T{self._tid}.{QUEUE_CTRL_OUT_SUFFIX}"
        )
        self._ctrl_in_name = taskspec.io.control.get("ctrl_in") or (
            f"T{self._tid}.{QUEUE_CTRL_IN_SUFFIX}"
        )
        inbox_name = taskspec.io.inputs.get("inbox") or (
            f"T{self._tid}.{QUEUE_INBOX_SUFFIX}"
        )
        self._status: str | None = None
        self._error: str | None = None
        self._stdout_chunks: list[str] = []
        self._result: Any | None = None
        self._log_last_timestamp: int | None = None
        self._client = InteractiveStreamClient(
            db_path=context.broker_target,
            config=context.broker_config,
            tid=self._tid,
            inbox=inbox_name,
            outbox=self._outbox_name,
            ctrl_out=ctrl_out_name,
            on_stdout=self._on_stdout,
            on_stderr=self._on_stderr,
            on_state=self._on_state,
        )
        self._log_queue = context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)

    def start(self) -> None:
        """Start the queue client inside the caller-owned cleanup region."""

        self._client.start()

    def close(self) -> None:
        """Close resources in the existing client-then-log order."""

        self._client.stop()
        self._log_queue.close()
        if self._use_prompt:
            return
        if self._stdout_chunks:
            self._result = "".join(self._stdout_chunks)
            return
        history = self._client.stdout_history
        if history:
            self._result = "".join(history)
            self._stdout_chunks.extend(history)

    def send_input(self, payload: str) -> None:
        """Write one queue-mediated interactive input payload."""

        self._client.send_input(payload)

    def close_input(self) -> None:
        """Close the task's interactive input stream."""

        self._client.close_input()

    def wait_for_completion(self, timeout: float | None = None) -> bool:
        """Observe terminal evidence in the existing priority order.

        Spec: [MF-3], [MF-5]
        """

        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        while True:
            if self._client.wait(timeout=0):
                return True
            if self._poll_terminal_log() or self._poll_monitor_terminal():
                return True
            if deadline is not None and time.monotonic() >= deadline:
                return False
            wait_timeout = 0.05
            if deadline is not None:
                wait_timeout = min(wait_timeout, max(0.0, deadline - time.monotonic()))
            if self._client.wait(timeout=wait_timeout):
                return True

    def request_exit(self) -> bool:
        """Close input, then escalate STOP to KILL only as needed.

        Spec: [CC-2.4], [MF-3]
        """

        self.close_input()
        if self.wait_for_completion(timeout=1.0):
            return True
        self._send_control(CONTROL_STOP)
        if (
            self._client.wait_for_control_response(
                "STOP",
                status="ack",
                timeout=1.0,
            )
            is not None
        ):
            return True
        if self.wait_for_completion(timeout=0.1):
            return True
        self._send_control(CONTROL_KILL)
        if (
            self._client.wait_for_control_response(
                "KILL",
                status="ack",
                timeout=1.0,
            )
            is not None
        ):
            return True
        return self.wait_for_completion(timeout=0.1)

    def exit_prompt_if_completed(
        self,
        session: Any,
        completion_event: threading.Event,
    ) -> None:
        """Exit a running prompt after terminal completion is visible."""

        if (
            completion_event.is_set()
            and session.app.is_running
            and not session.app.is_done
        ):
            session.app.exit()

    def await_prompt_completion(
        self,
        session: Any,
        completion_event: threading.Event,
    ) -> None:
        """Wait for completion and safely wake the prompt event loop."""

        self.wait_for_completion()
        completion_event.set()
        loop = session.app.loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(
                lambda: self.exit_prompt_if_completed(session, completion_event)
            )
        except RuntimeError:  # pragma: no cover - closed event-loop race
            return

    def outcome(self, *, quit_requested: bool) -> tuple[str, str | None]:
        """Return the current terminal outcome with quit normalization."""

        status = self._status or self._client.status or "completed"
        error = self._error or self._client.error
        if quit_requested and status in {"cancelled", "killed"}:
            return "completed", None
        return status, error

    def collect_piped_result(self) -> Any | None:
        """Fill an empty captured result from the final outbox snapshot."""

        outbox_queue = self._context.queue(self._outbox_name, persistent=True)
        try:
            collected = _collect_interactive_queue_output(outbox_queue)
        finally:
            outbox_queue.close()
        if collected and not self._result:
            self._result = "".join(collected)
        return self._result

    def _on_stdout(self, chunk: str, final: bool) -> None:
        if self._use_prompt:
            if chunk:
                _echo(chunk, nl=False)
            if final and (not chunk or not chunk.endswith("\n")):
                _echo()
        elif chunk:
            self._stdout_chunks.append(chunk)

    @staticmethod
    def _on_stderr(chunk: str, final: bool) -> None:
        if chunk:
            _echo(chunk, err=True, nl=False)
        if final and (not chunk or not chunk.endswith("\n")):
            _echo(err=True)

    def _on_state(self, event: dict[str, Any]) -> None:
        status = event.get("status")
        if isinstance(status, str) and status in {
            "completed",
            "failed",
            "timeout",
            "cancelled",
            "killed",
        }:
            self._status = status
            error = event.get("error")
            self._error = str(error) if isinstance(error, str) else None
            return
        event_name = event.get("event")
        if event_name in {"work_failed", "work_timeout", "work_limit_violation"}:
            self._status = "failed"
            self._error = event.get("error") or str(event_name).replace("_", " ")
        elif event_name == "work_completed":
            self._status = "completed"
        elif event_name in {"control_stop", "task_signal_stop"}:
            self._status = "cancelled"
            self._error = event.get("error") or "Task cancelled"
        elif event_name in {"control_kill", "task_signal_kill"}:
            self._status = "killed"
            self._error = event.get("error") or "Task killed"

    def _send_control(self, command: str) -> None:
        ctrl_queue = self._context.queue(self._ctrl_in_name, persistent=True)
        try:
            ctrl_queue.write(encode_control_message(command))
        finally:
            ctrl_queue.close()

    def _poll_terminal_log(self) -> bool:
        events, self._log_last_timestamp = poll_log_events(
            self._log_queue,
            self._log_last_timestamp,
            self._tid,
        )
        terminal_seen = False
        for event_payload, _timestamp in events:
            event_status = terminal_status_from_event(event_payload)
            if event_status is None:
                continue
            self._status = event_status
            self._error = terminal_error_message(event_payload, event_status)
            terminal_seen = True
        return terminal_seen

    def _poll_monitor_terminal(self) -> bool:
        """Return optional Monitor terminal evidence without changing priority."""

        try:
            record = open_monitor_store(
                self._context,
                config=self._context.config,
            ).get_task(self._tid)
        except Exception:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-332] exception
            return False
        if record is None or not record.terminal_seen:
            return False
        status = record.terminal_status or record.status
        if not isinstance(status, str) or not status:
            return False
        self._status = status
        error = record.state.get("error")
        self._error = str(error) if isinstance(error, str) else None
        return True


def _run_interactive_prompt(lifecycle: _InteractiveRunLifecycle) -> bool:
    """Run the PromptToolkit mode for one queue-mediated session.

    Spec: [CC-2.3], [CLI-1.1.1]
    """

    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.patch_stdout import patch_stdout
    except ImportError as exc:  # pragma: no cover - optional dependency guard
        raise RunUsageError(
            "prompt_toolkit is required for interactive mode when stdin is a TTY"
        ) from exc

    session: PromptSession[str] = PromptSession("weft> ")
    completion_event = threading.Event()

    def exit_if_completed() -> None:
        lifecycle.exit_prompt_if_completed(session, completion_event)

    waiter = threading.Thread(
        target=lambda: lifecycle.await_prompt_completion(session, completion_event),
        daemon=True,
    )
    waiter.start()
    lifecycle.send_input("\n")
    quit_requested = False

    with patch_stdout():
        while not completion_event.is_set():
            try:
                line = session.prompt("weft> ", pre_run=exit_if_completed)
            except EOFError:
                lifecycle.close_input()
                break
            except KeyboardInterrupt:
                _echo()
                continue
            if line is None:
                break
            stripped = line.strip()
            if stripped in {":quit", ":exit"}:
                quit_requested = True
                if not lifecycle.request_exit():
                    raise RuntimeError("Interactive session did not stop after :quit")
                break
            lifecycle.send_input(line if line.endswith("\n") else f"{line}\n")

    waiter.join(timeout=0.5)
    if not lifecycle.wait_for_completion(timeout=INTERACTIVE_STOP_COMPLETION_TIMEOUT):
        raise RuntimeError("Interactive session did not stop after :quit")
    return quit_requested


def _run_interactive_piped(
    lifecycle: _InteractiveRunLifecycle,
    stdin_data: str | None,
    *,
    auto_close: bool,
) -> None:
    """Run the existing non-prompt input and auto-close branches.

    Spec: [CLI-1.1.1]
    """

    if stdin_data:
        lifecycle.send_input(stdin_data)
        if auto_close and not lifecycle.wait_for_completion(timeout=0.2):
            lifecycle.close_input()
            lifecycle.wait_for_completion()
        else:
            lifecycle.wait_for_completion()
        return
    if auto_close:
        lifecycle.close_input()
    lifecycle.wait_for_completion()


def _run_interactive_session(
    context: WeftContext,
    taskspec: TaskSpec,
    *,
    stdin_data: str | None,
    auto_close: bool = True,
    use_prompt: bool = False,
) -> tuple[str, Any | None, str | None]:
    """Run one command-side interactive session through its lifecycle owner.

    Spec: [CC-2.3], [SB-0.4], [MF-3], [MF-5], [CLI-1.1.1]
    """

    lifecycle = _InteractiveRunLifecycle(context, taskspec, use_prompt=use_prompt)
    quit_requested = False
    try:
        lifecycle.start()
        if use_prompt:
            quit_requested = _run_interactive_prompt(lifecycle)
        else:
            _run_interactive_piped(lifecycle, stdin_data, auto_close=auto_close)
        status, error = lifecycle.outcome(quit_requested=quit_requested)
    finally:
        lifecycle.close()

    result = None if use_prompt else lifecycle.collect_piped_result()
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


def render_run_execution_result(  # noqa: C901 approved [TS-3.1] [RUFF-SUP-112] exception
    execution: RunExecutionResult,
    *,
    wait: bool,
    json_output: bool,
    verbose: bool,
    emit: Callable[..., None] = _echo,
) -> int:
    """Render one structured run result for CLI output."""

    if verbose and execution.manager_started_payload is not None:
        emit(json.dumps(execution.manager_started_payload, ensure_ascii=False))
    if verbose and execution.submitted_payload is not None:
        emit(json.dumps(execution.submitted_payload, indent=2))

    if execution.submission_error is not None:
        emit(execution.submission_error, err=True)
        return 1

    tid = execution.tid
    if not wait:
        if json_output:
            emit(json.dumps({"tid": tid, "status": "queued"}, ensure_ascii=False))
        else:
            emit(tid)
        return 0

    status = execution.status
    result_value = execution.result_value
    error_message = execution.error_message

    if status == "completed":
        if json_output:
            emit(
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
                emit(json.dumps(result_value, ensure_ascii=False))
            elif result_value not in (None, ""):
                emit(str(result_value))
        return 0

    display_error = error_message
    if status == "cancelled":
        display_error = "Task cancelled"
    elif status == "killed":
        display_error = "Task killed"

    if json_output:
        emit(
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
        emit(f"{execution.error_prefix}: {display_error}", err=True)
    return 124 if status == "timeout" else 1


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
            raise RunUsageError(
                "This TaskSpec does not declare spec.run_input; extra "
                "arguments are not supported with --spec."
            )
        return _initial_work_payload(
            target_type=taskspec.spec.type,
            stdin_data=stdin_data,
            interactive=bool(taskspec.spec.interactive),
        )

    if stdin_data is not None and run_input.stdin is None:
        raise RunUsageError(
            "This TaskSpec does not declare stdin input for spec.run_input."
        )
    if run_input.stdin is not None and run_input.stdin.required and stdin_data is None:
        raise RunUsageError("This TaskSpec requires piped stdin for spec.run_input.")

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
        raise RunUsageError(str(exc)) from exc


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
        raise RunUsageError(str(exc)) from exc
    return materialized, remaining_tokens


def _execute_inline(  # noqa: C901 approved [TS-3.1] [RUFF-SUP-113] exception
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

    taskspec = validate_taskspec_payload(template_dict, template=True)
    reuse_enabled = bool(context.config.get("WEFT_MANAGER_REUSE_ENABLED", True))

    def _wait_for_inline_completion(tid: str) -> tuple[str, Any, str | None]:
        resolved_spec = validate_taskspec_payload(
            taskspec.model_dump(mode="json"),
            bundle_root=taskspec.get_bundle_root(),
            resolved_tid=tid,
            inherited_weft_context=taskspec.spec.weft_context,
        )

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
                _echo(result_value, nl=False)
                if not str(result_value).endswith("\n"):
                    _echo()
                result_value = ""
            return status, result_value, error_message

        return _wait_for_task_completion(
            context,
            resolved_spec,
        )

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
            wait=wait,
            reuse_enabled=reuse_enabled,
            emit_verbose=False,
            wait_for_completion=_wait_for_inline_completion if wait else None,
        )
    except Exception as exc:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-333] exception
        return RunExecutionResult(
            tid="",
            submission_error=f"Error submitting task: {exc}",
        )

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
    wait: bool,
    json_output: bool,
    autostart_enabled: bool,
    persistent_override: bool | None,
) -> RunExecutionResult:
    spec = _load_taskspec_reference(spec_ref, context_dir=context_dir)
    bundle_root = spec.get_bundle_root()
    spec_payload = spec.model_dump(mode="json")
    if persistent_override is not None:
        spec_payload.setdefault("spec", {})
        spec_payload["spec"]["persistent"] = persistent_override
    spec = validate_taskspec_payload(
        spec_payload,
        bundle_root=bundle_root,
        template=True,
    )
    spec, remaining_tokens = _materialize_parameterized_spec(
        taskspec=spec,
        context_root=str(context_dir)
        if context_dir is not None
        else spec.spec.weft_context,
        run_input_tokens=run_input_tokens,
    )
    spec = _apply_explicit_run_name(spec, name)
    if spec.spec.persistent and wait:
        raise RunUsageError(
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
    reuse_enabled = bool(context.config.get("WEFT_MANAGER_REUSE_ENABLED", True))

    def _wait_for_spec_completion(tid: str) -> tuple[str, Any, str | None]:
        resolved_spec = validate_taskspec_payload(
            spec.model_dump(mode="json"),
            bundle_root=spec.get_bundle_root(),
            resolved_tid=tid,
            inherited_weft_context=spec.spec.weft_context,
        )
        return _wait_for_task_completion(
            context,
            resolved_spec,
        )

    try:
        execution = _run_with_managed_execution(
            context=context,
            submit=lambda: _enqueue_taskspec(
                context,
                spec,
                work_payload,
            ),
            verbose=verbose,
            wait=wait,
            reuse_enabled=reuse_enabled,
            emit_verbose=False,
            wait_for_completion=_wait_for_spec_completion if wait else None,
        )
    except Exception as exc:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-333] exception
        return RunExecutionResult(
            tid="",
            submission_error=f"Error submitting TaskSpec: {exc}",
        )

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
    wait: bool,
    json_output: bool,
    verbose: bool,
    autostart_enabled: bool,
) -> RunExecutionResult:
    context = build_context(spec_context=context_dir, autostart=autostart_enabled)
    pipeline_spec, source_ref = _load_pipeline_spec(pipeline, context_dir=context_dir)
    stdin_data = _read_piped_stdin(context)
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

    reuse_enabled = bool(context.config.get("WEFT_MANAGER_REUSE_ENABLED", True))

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
        wait=wait,
        reuse_enabled=reuse_enabled,
        emit_verbose=False,
        wait_for_completion=(
            (
                lambda _tid: _wait_for_task_completion(
                    context,
                    compiled.pipeline_taskspec,
                )
            )
            if wait
            else None
        ),
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
    wait: bool,
    json_output: bool,
    verbose: bool,
    persistent_override: bool | None,
    autostart_enabled: bool,
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

    By default, waits for the task to complete and prints output.
    Use --no-wait to submit and return immediately (prints TID).
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
            wait=wait,
            json_output=json_output,
            verbose=verbose,
            autostart_enabled=autostart_enabled,
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
            wait=wait,
            json_output=json_output,
            autostart_enabled=autostart_enabled,
            persistent_override=persistent_override,
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
        wait=wait,
        json_output=json_output,
        verbose=verbose,
        autostart_enabled=autostart_enabled,
    )


__all__ = [
    "RunResolutionError",
    "RunUsageError",
    "_collect_interactive_queue_output",
    "_delete_spawn_request",
    "_enqueue_taskspec",
    "_execute_inline",
    "_execute_pipeline",
    "_execute_spec_via_manager",
    "_wait_for_task_completion",
    "execute_run",
    "render_run_execution_result",
    "render_spec_aware_run_help",
]
