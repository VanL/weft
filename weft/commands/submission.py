"""Shared task submission helpers for CLI and Python clients.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-1.1.1]
- docs/specifications/05-Message_Flow_and_State.md [MF-1], [MF-6]
- docs/specifications/12-Pipeline_Composition_and_UX.md [PL-1], [PL-4.1]
"""

from __future__ import annotations

import json
import shlex
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from weft._constants import (
    DEFAULT_STREAM_OUTPUT,
    INTERNAL_RUNTIME_ENDPOINT_NAME_KEY,
    SPEC_TYPE_PIPELINE,
    SPEC_TYPE_TASK,
    SUBMIT_OVERRIDE_NAMES,
)
from weft._exceptions import InvalidTID, SpecNotFound
from weft.commands import specs
from weft.commands.manager import _ensure_manager, _generate_tid
from weft.commands.types import PreparedSubmissionRequest, SubmittedTaskReceipt
from weft.context import WeftContext
from weft.core.endpoints import validate_endpoint_claim_name
from weft.core.pipelines import compile_linear_pipeline, load_pipeline_spec_payload
from weft.core.spawn_requests import delete_spawn_request, submit_spawn_request
from weft.core.taskspec import TaskSpec, apply_bundle_root_to_taskspec_payload

from ._spawn_submission import reconcile_submitted_spawn


def normalize_tid(raw_tid: str) -> str:
    """Return the canonical numeric TID or raise `InvalidTID`."""

    candidate = raw_tid.strip()
    if candidate.startswith("T"):
        candidate = candidate[1:]
    if not candidate or not candidate.isdigit() or len(candidate) != 19:
        raise InvalidTID(f"invalid task id '{raw_tid}'")
    if int(candidate) > 9_223_372_036_854_775_807:
        raise InvalidTID(f"invalid task id '{raw_tid}'")
    return candidate


def normalize_taskspec(taskspec: TaskSpec | Mapping[str, Any]) -> TaskSpec:
    """Normalize a TaskSpec template or payload to a validated `TaskSpec`."""

    if isinstance(taskspec, TaskSpec):
        return taskspec
    payload = dict(taskspec)
    return TaskSpec.model_validate(
        payload,
        context={"template": not bool(payload.get("tid")), "auto_expand": False},
    )


def generate_tid(context: WeftContext) -> str:
    """Generate one durable task id from the active broker target."""

    return _generate_tid(context)


def apply_submit_overrides(
    taskspec: TaskSpec,
    *,
    name: str | None = None,
    description: str | None = None,
    tags: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    working_dir: str | None = None,
    stream_output: bool | None = None,
    timeout: float | None = None,
    memory_mb: int | None = None,
    cpu_percent: int | None = None,
    runner: str | None = None,
    runner_options: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> TaskSpec:
    """Apply the public submit overrides to a TaskSpec template."""

    payload = taskspec.model_dump(mode="json")
    spec_section = payload.setdefault("spec", {})
    metadata_section = payload.setdefault("metadata", {})

    if not isinstance(spec_section, dict):
        raise ValueError("TaskSpec spec section must be a mapping")
    if not isinstance(metadata_section, dict):
        metadata_section = {}
        payload["metadata"] = metadata_section

    if metadata:
        metadata_section.update(dict(metadata))
    if description is not None:
        metadata_section["description"] = description
    if tags is not None:
        metadata_section["tags"] = list(tags)
    if env:
        current_env = spec_section.get("env")
        current_env = current_env if isinstance(current_env, dict) else {}
        current_env.update(dict(env))
        spec_section["env"] = current_env
    if working_dir is not None:
        spec_section["working_dir"] = working_dir
    if stream_output is not None:
        spec_section["stream_output"] = bool(stream_output)
    if timeout is not None:
        spec_section["timeout"] = timeout
    if memory_mb is not None or cpu_percent is not None:
        limits = spec_section.get("limits")
        limits = limits if isinstance(limits, dict) else {}
        if memory_mb is not None:
            limits["memory_mb"] = memory_mb
        if cpu_percent is not None:
            limits["cpu_percent"] = cpu_percent
        spec_section["limits"] = limits
    if runner is not None or runner_options is not None:
        runner_section = spec_section.get("runner")
        runner_section = runner_section if isinstance(runner_section, dict) else {}
        if runner is not None:
            runner_section["name"] = runner
        if runner_options is not None:
            options = runner_section.get("options")
            options = options if isinstance(options, dict) else {}
            options.update(dict(runner_options))
            runner_section["options"] = options
        spec_section["runner"] = runner_section

    if name is not None:
        payload["name"] = name
        metadata_section.pop(INTERNAL_RUNTIME_ENDPOINT_NAME_KEY, None)
        if bool(spec_section.get("persistent")):
            metadata_section[INTERNAL_RUNTIME_ENDPOINT_NAME_KEY] = (
                validate_endpoint_claim_name(name)
            )

    updated = TaskSpec.model_validate(
        payload,
        context={"template": taskspec.tid is None, "auto_expand": False},
    )
    updated.set_bundle_root(taskspec.get_bundle_root())
    return updated


def _validate_submit_overrides(overrides: Mapping[str, Any]) -> None:
    unknown = sorted(set(overrides) - SUBMIT_OVERRIDE_NAMES)
    if unknown:
        unknown_text = ", ".join(unknown)
        raise TypeError(f"Unknown submit override(s): {unknown_text}")


def _snapshot_payload(payload: Any) -> Any:
    """Return a JSON-compatible copy of a work payload."""

    if payload is None:
        return None
    try:
        return json.loads(json.dumps(payload))
    except TypeError as exc:
        raise ValueError("Submission payload must be JSON-serializable") from exc


def _snapshot_taskspec(taskspec: TaskSpec | Mapping[str, Any]) -> TaskSpec:
    normalized = normalize_taskspec(taskspec)
    payload = json.loads(json.dumps(normalized.model_dump(mode="json")))
    snapshotted = TaskSpec.model_validate(
        payload,
        context={"template": normalized.tid is None, "auto_expand": False},
    )
    snapshotted.set_bundle_root(normalized.get_bundle_root())
    return snapshotted


def _receipt(name: str, tid: str) -> SubmittedTaskReceipt:
    return SubmittedTaskReceipt(
        tid=tid,
        name=name,
        submitted_at_ns=int(tid) if tid.isdigit() else time.time_ns(),
    )


def ensure_manager_after_submission(
    context: WeftContext,
    *,
    submitted_tid: str | int,
    verbose: bool = False,
    ensure_manager_fn: Callable[
        ..., tuple[dict[str, Any] | None, bool, subprocess.Popen[Any] | None]
    ]
    | None = None,
    delete_spawn_request_fn: Callable[[WeftContext, int], bool] | None = None,
) -> tuple[dict[str, Any] | None, bool, subprocess.Popen[Any] | None]:
    """Ensure a manager or reconcile a queue-first submission failure."""

    submitted_tid_str = str(submitted_tid)
    ensure_manager_impl = ensure_manager_fn or _ensure_manager

    def _delete_spawn_request_with_context(
        context_arg: WeftContext,
        message_timestamp: int,
    ) -> bool:
        return delete_spawn_request(
            context_arg.broker_target,
            message_timestamp=message_timestamp,
            config=context_arg.broker_config,
        )

    delete_spawn_request_impl = (
        delete_spawn_request_fn or _delete_spawn_request_with_context
    )

    try:
        return ensure_manager_impl(context, verbose=verbose)
    except Exception as exc:  # pragma: no cover - manager startup reconciliation
        startup_error = exc

    reconciliation = reconcile_submitted_spawn(context, submitted_tid_str)
    if reconciliation.outcome == "spawned":
        return None, False, None
    if reconciliation.outcome == "rejected":
        reason = (
            reconciliation.error
            or f"Manager rejected submitted task {submitted_tid_str}"
        )
        raise RuntimeError(reason) from startup_error
    if reconciliation.outcome == "queued":
        deleted = delete_spawn_request_impl(
            context,
            int(submitted_tid_str),
        )
        if deleted:
            raise startup_error
        reconciliation = reconcile_submitted_spawn(
            context,
            submitted_tid_str,
            timeout=0.2,
        )
        if reconciliation.outcome == "spawned":
            return None, False, None
        if reconciliation.outcome == "rejected":
            reason = (
                reconciliation.error
                or f"Manager rejected submitted task {submitted_tid_str}"
            )
            raise RuntimeError(reason) from startup_error
    if reconciliation.outcome == "reserved":
        raise RuntimeError(
            f"Submitted task {submitted_tid_str} was already claimed into "
            f"{reconciliation.reserved_queue}; manual recovery is required."
        ) from startup_error
    if reconciliation.outcome == "queued":
        raise RuntimeError(
            f"Submitted task {submitted_tid_str} is still queued, but rollback could "
            "not be confirmed."
        ) from startup_error
    raise RuntimeError(
        f"Submitted task {submitted_tid_str} could not be reconciled; rollback "
        "could not be proven."
    ) from startup_error


def prepare_taskspec(
    context: WeftContext,
    taskspec: TaskSpec | Mapping[str, Any],
    *,
    payload: Any = None,
    seed_start_envelope: bool = True,
    allow_internal_runtime: bool = False,
) -> PreparedSubmissionRequest:
    """Validate and snapshot a TaskSpec submission without queue writes."""

    del context
    normalized = _snapshot_taskspec(taskspec)
    return PreparedSubmissionRequest(
        name=normalized.name,
        taskspec=normalized,
        payload=_snapshot_payload(payload),
        seed_start_envelope=seed_start_envelope,
        allow_internal_runtime=allow_internal_runtime,
    )


def submit_prepared(
    context: WeftContext,
    prepared: PreparedSubmissionRequest,
) -> SubmittedTaskReceipt:
    """Write a previously prepared submission through the spawn queue."""

    normalized = normalize_taskspec(prepared.taskspec)
    task_tid = normalized.tid or generate_tid(context)
    submit_spawn_request(
        context.broker_target,
        taskspec=normalized,
        work_payload=prepared.payload,
        config=context.broker_config,
        tid=task_tid,
        inherited_weft_context=normalized.spec.weft_context,
        seed_start_envelope=prepared.seed_start_envelope,
        allow_internal_runtime=prepared.allow_internal_runtime,
    )
    ensure_manager_after_submission(context, submitted_tid=task_tid)
    return _receipt(prepared.name, task_tid)


def submit_taskspec(
    context: WeftContext,
    taskspec: TaskSpec | Mapping[str, Any],
    *,
    payload: Any = None,
    seed_start_envelope: bool = True,
    allow_internal_runtime: bool = False,
) -> SubmittedTaskReceipt:
    """Submit a TaskSpec through the durable manager-backed spawn path."""

    prepared = prepare_taskspec(
        context,
        taskspec,
        payload=payload,
        seed_start_envelope=seed_start_envelope,
        allow_internal_runtime=allow_internal_runtime,
    )
    return submit_prepared(context, prepared)


def prepare(
    context: WeftContext,
    taskspec: TaskSpec | Mapping[str, Any],
    *,
    payload: Any = None,
    **overrides: Any,
) -> PreparedSubmissionRequest:
    """Validate, normalize, and snapshot a TaskSpec submission."""

    _validate_submit_overrides(overrides)
    updated = apply_submit_overrides(normalize_taskspec(taskspec), **overrides)
    return prepare_taskspec(context, updated, payload=payload)


def submit(
    context: WeftContext,
    taskspec: TaskSpec | Mapping[str, Any],
    *,
    payload: Any = None,
    **overrides: Any,
) -> SubmittedTaskReceipt:
    """Submit a TaskSpec after applying the public override contract."""

    return submit_prepared(
        context,
        prepare(context, taskspec, payload=payload, **overrides),
    )


def prepare_spec(
    context: WeftContext,
    reference: str | Path,
    *,
    payload: Any = None,
    **overrides: Any,
) -> PreparedSubmissionRequest:
    """Resolve, validate, and snapshot a stored or file-backed task spec."""

    _validate_submit_overrides(overrides)
    try:
        resolved = specs.resolve_spec_reference(
            reference,
            spec_type=SPEC_TYPE_TASK,
            context_path=context.root,
        )
    except FileNotFoundError as exc:
        raise SpecNotFound(str(exc)) from exc
    taskspec = TaskSpec.model_validate(
        resolved.payload,
        context={"template": True, "auto_expand": False},
    )
    taskspec.set_bundle_root(resolved.bundle_root)
    updated = apply_submit_overrides(taskspec, **overrides)
    return prepare_taskspec(context, updated, payload=payload)


def submit_spec(
    context: WeftContext,
    reference: str | Path,
    *,
    payload: Any = None,
    **overrides: Any,
) -> SubmittedTaskReceipt:
    """Resolve and submit a stored, builtin, or file-backed task spec."""

    return submit_prepared(
        context,
        prepare_spec(context, reference, payload=payload, **overrides),
    )


def prepare_pipeline(
    context: WeftContext,
    reference: str | Path,
    *,
    payload: Any = None,
    **overrides: Any,
) -> PreparedSubmissionRequest:
    """Resolve, compile, validate, and snapshot a pipeline submission."""

    _validate_submit_overrides(overrides)
    try:
        resolved = specs.resolve_spec_reference(
            reference,
            spec_type=SPEC_TYPE_PIPELINE,
            context_path=context.root,
        )
    except FileNotFoundError as exc:
        raise SpecNotFound(str(exc)) from exc
    pipeline_spec = load_pipeline_spec_payload(resolved.payload)

    def _load_pipeline_stage(task_name: str) -> dict[str, Any]:
        stage_ref = specs.resolve_named_spec(
            task_name,
            spec_type=SPEC_TYPE_TASK,
            context_path=context.root,
        )
        return apply_bundle_root_to_taskspec_payload(
            dict(stage_ref.payload),
            stage_ref.bundle_root,
        )

    compiled = compile_linear_pipeline(
        pipeline_spec,
        context=context,
        task_loader=_load_pipeline_stage,
        source_ref=str(resolved.path),
    )
    updated = apply_submit_overrides(compiled.pipeline_taskspec, **overrides)
    bootstrap_payload = (
        payload if payload is not None else compiled.bootstrap_input_fallback
    )
    return prepare_taskspec(
        context,
        updated,
        payload=bootstrap_payload,
        seed_start_envelope=False,
        allow_internal_runtime=True,
    )


def submit_pipeline(
    context: WeftContext,
    reference: str | Path,
    *,
    payload: Any = None,
    **overrides: Any,
) -> SubmittedTaskReceipt:
    """Resolve, compile, and submit a stored or file-backed pipeline spec."""

    return submit_prepared(
        context,
        prepare_pipeline(context, reference, payload=payload, **overrides),
    )


def _command_name(argv: Sequence[str], explicit_name: str | None) -> str:
    if explicit_name:
        return explicit_name
    return Path(argv[0]).name


def _command_payload(payload: Any) -> Any:
    if payload is None:
        return {}
    if isinstance(payload, dict) and {"stdin", "close"} & set(payload):
        return payload
    return {"stdin": payload}


def submit_command(
    context: WeftContext,
    command: Sequence[str] | str,
    *,
    payload: Any = None,
    shell: bool = False,
    **overrides: Any,
) -> SubmittedTaskReceipt:
    """Submit a command target using the same durable manager path."""

    _validate_submit_overrides(overrides)
    if shell:
        raise NotImplementedError("submit_command(..., shell=True) is not supported")
    argv = shlex.split(command) if isinstance(command, str) else list(command)
    if not argv:
        raise ValueError("Command cannot be empty")

    explicit_name = overrides.get("name")
    taskspec = TaskSpec.model_validate(
        {
            "name": _command_name(
                argv, explicit_name if isinstance(explicit_name, str) else None
            ),
            "spec": {
                "type": "command",
                "process_target": str(argv[0]),
                "args": [str(part) for part in argv[1:]],
                "keyword_args": {},
                "env": {},
                "interactive": False,
                "stream_output": DEFAULT_STREAM_OUTPUT,
                "cleanup_on_exit": True,
                "weft_context": str(context.root),
            },
            "metadata": {"source": "weft.client"},
        },
        context={"template": True, "auto_expand": False},
    )
    updated = apply_submit_overrides(taskspec, **overrides)
    return submit_taskspec(context, updated, payload=_command_payload(payload))
