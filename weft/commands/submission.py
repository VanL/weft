"""Shared task submission helpers for CLI and Python clients.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-1.1.1]
- docs/specifications/05-Message_Flow_and_State.md [MF-1], [MF-6]
- docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]
- docs/specifications/12-Pipeline_Composition_and_UX.md [PL-1], [PL-4.1]
- docs/specifications/14-Python_API_Surfaces.md [PY-3]
"""

from __future__ import annotations

import json
import logging
import shlex
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from simplebroker import BrokerSession
from simplebroker.ext import BrokerError
from weft._constants import (
    DEFAULT_STREAM_OUTPUT,
    INTERNAL_ENDPOINT_NAMESPACE_PREFIX,
    INTERNAL_RUNTIME_ENDPOINT_NAME_KEY,
    MANAGER_NAMESPACE_AMBIGUOUS_BACKLOG_GRACE_SECONDS,
    SPEC_TYPE_PIPELINE,
    SPEC_TYPE_TASK,
    SUBMIT_OVERRIDE_NAMES,
)
from weft._exceptions import (
    CommandUsageError,
    InvalidTID,
    ManagerStartFailed,
    SpecNotFound,
    SubmissionManagerError,
    SubmissionValidationError,
    WeftError,
)
from weft.commands.types import PreparedSubmissionRequest, SubmittedTaskReceipt
from weft.context import WeftContext, build_context
from weft.core import manager_runtime
from weft.core.endpoints import validate_endpoint_claim_name
from weft.core.pipelines import compile_linear_pipeline, load_pipeline_spec_payload
from weft.core.spawn_requests import submit_spawn_request
from weft.core.taskspec import (
    TaskSpec,
    decode_taskspec_transport_payload,
    encode_taskspec_transport_payload,
    invoke_run_input_adapter,
    materialize_taskspec_template,
    parse_declared_parameterization_args,
    parse_declared_run_input_args,
    validate_taskspec_payload,
)
from weft.ext import SpecRunInputRequest
from weft.helpers.message_ids import normalize_exact_message_id

from ._spawn_submission import reconcile_submitted_spawn
from .specs import resolve_named_spec, resolve_spec_reference

logger = logging.getLogger(__name__)


def _annotate_accepted_submission_error(exc: Exception, tid: str) -> None:
    """Attach the committed TID to an unexpected post-acceptance failure."""

    marker = f"accepted_tid={tid}"
    if marker in str(exc):
        return
    message = f"{exc} [{marker}; request remains accepted]"
    exc.args = (message, *exc.args[1:]) if exc.args else (message,)
    exc.add_note(
        f"Spawn request {tid} was accepted before this failure; do not resubmit blindly."
    )


def normalize_tid(raw_tid: str) -> str:
    """Return the canonical numeric TID or raise `InvalidTID`."""

    candidate = raw_tid.strip()
    candidate = candidate.removeprefix("T")
    try:
        normalize_exact_message_id(candidate)
    except ValueError as exc:
        raise InvalidTID(f"invalid task id '{raw_tid}'") from exc
    return candidate


def normalize_taskspec(taskspec: TaskSpec | Mapping[str, Any]) -> TaskSpec:
    """Normalize a TaskSpec template or payload to a validated `TaskSpec`."""

    if isinstance(taskspec, TaskSpec):
        return taskspec
    return decode_taskspec_transport_payload(
        taskspec,
        template=not bool(taskspec.get("tid")),
    )


def apply_submit_overrides(  # noqa: C901 approved [TS-3.1] [RUFF-SUP-115] exception
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
        raise TypeError("TaskSpec spec section must be a mapping")
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
        if name.strip().startswith(INTERNAL_ENDPOINT_NAMESPACE_PREFIX) or bool(
            spec_section.get("persistent")
        ):
            metadata_section[INTERNAL_RUNTIME_ENDPOINT_NAME_KEY] = (
                validate_endpoint_claim_name(name)
            )

    return validate_taskspec_payload(
        payload,
        bundle_root=taskspec.get_bundle_root(),
        template=taskspec.tid is None,
    )


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
    return validate_taskspec_payload(
        payload,
        bundle_root=normalized.get_bundle_root(),
        template=normalized.tid is None,
    )


def _bind_declared_context(taskspec: TaskSpec) -> TaskSpec:
    """Capture declared path interpretation without initializing its broker.

    Only context-aware preparation calls this helper. Pure definition snapshots
    retain the declaration text for export and later composition.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-3]
    """

    declared_root = taskspec.spec.weft_context
    if not declared_root:
        return taskspec
    expanded_root = _expand_declared_context_root(declared_root)
    payload = taskspec.model_dump(mode="json")
    payload["spec"]["weft_context"] = str(expanded_root.resolve())
    return validate_taskspec_payload(
        payload,
        bundle_root=taskspec.get_bundle_root(),
        template=taskspec.tid is None,
    )


def _expand_declared_context_root(declared_root: str) -> Path:
    """Return the user-expanded context root or reject invalid path text."""

    if "\x00" in declared_root:
        raise ValueError("weft_context must not contain null bytes")
    try:
        return Path(declared_root).expanduser()
    except RuntimeError as exc:
        raise ValueError(str(exc)) from exc


def _initial_work_payload(
    *,
    target_type: str,
    stdin_text: str | None,
    interactive: bool,
) -> Any:
    """Build the canonical initial payload when no run-input adapter exists."""

    if target_type == "command":
        payload: dict[str, Any] = {}
        if stdin_text:
            payload["stdin"] = stdin_text
            if interactive:
                payload["close"] = True
        return payload
    return stdin_text if stdin_text else None


@dataclass(frozen=True, slots=True)
class _SubmittedPreparedOutcome:
    """Internal receipt plus the live runtime context used for submission."""

    receipt: SubmittedTaskReceipt
    runtime_context: WeftContext


def _receipt(name: str, tid: str, *, context_root: Path) -> SubmittedTaskReceipt:
    return SubmittedTaskReceipt(
        tid=tid,
        name=name,
        submitted_at_ns=int(tid) if tid.isdigit() else time.time_ns(),
        context_root=str(context_root),
    )


def ensure_manager_after_submission(  # noqa: C901 approved [TS-3.1] [RUFF-SUP-116] exception
    context: WeftContext,
    *,
    submitted_tid: str | int,
    ensure_manager_fn: Callable[..., manager_runtime.ManagerEnsureResult] | None = None,
    delete_spawn_request_fn: Callable[[WeftContext, int], bool] | None = None,
    observation: manager_runtime.ManagerAvailabilityObservation | None = None,
) -> manager_runtime.ManagerEnsureResult:
    """Recover manager availability without revoking accepted queue work.

    The successful spawn write owns acceptance. This function may prove or
    start a manager, but it never deletes or re-enqueues the accepted request.
    A supplied observation is from this submission's immediately preceding
    enqueue/observation operation in the exact runtime context, never a cached
    result. That operation must end before recovery can close same-key sessions.

    Spec: [MF-1], [MF-6], [MF-7], [SB-0.4]
    """

    submitted_tid_str = str(submitted_tid)
    del delete_spawn_request_fn

    if ensure_manager_fn is not None:
        return ensure_manager_fn(context)

    if observation is None:
        observation = manager_runtime.observe_manager_availability(context)
    if observation.outcome == "ready":
        return manager_runtime.ManagerEnsureResult(
            outcome="ready",
            manager_record=observation.manager_record,
            started_here=False,
            process_handle=None,
            reason=observation.reason,
            probe_request_id=(
                observation.probe_result.request_id
                if observation.probe_result is not None
                else None
            ),
        )

    timeout = 0.0
    if (
        observation.first_uncertain_at is not None
        and observation.backlog_pending is True
    ):
        timeout = max(
            0.0,
            observation.first_uncertain_at
            + MANAGER_NAMESPACE_AMBIGUOUS_BACKLOG_GRACE_SECONDS
            - time.monotonic(),
        )
    try:
        reconciliation = reconcile_submitted_spawn(
            context,
            submitted_tid_str,
            timeout=timeout,
            queued_is_terminal=False,
        )
    except (BrokerError, OSError) as exc:
        return manager_runtime.ManagerEnsureResult(
            outcome="uncertain",
            manager_record=None,
            started_here=False,
            process_handle=None,
            reason=f"submission_reconciliation_error:{exc}",
        )

    if reconciliation.outcome == "rejected":
        reason = reconciliation.error or "manager rejected the spawn request"
        raise SubmissionManagerError(
            f"Manager rejected accepted task {submitted_tid_str}: {reason}"
        )
    if reconciliation.outcome in {"spawned", "reserved"}:
        return manager_runtime.ManagerEnsureResult(
            outcome="not_needed",
            manager_record=None,
            started_here=False,
            process_handle=None,
            reason=f"accepted_request_{reconciliation.outcome}",
        )
    if reconciliation.outcome == "unknown":
        return manager_runtime.ManagerEnsureResult(
            outcome="uncertain",
            manager_record=None,
            started_here=False,
            process_handle=None,
            reason="accepted_request_location_unknown",
        )

    decision = manager_runtime.decide_manager_recovery(context, observation)
    if decision.outcome == "reuse":
        return manager_runtime.ManagerEnsureResult(
            outcome="ready",
            manager_record=decision.manager_record,
            started_here=False,
            process_handle=None,
            reason=decision.reason,
            probe_request_id=decision.probe_request_id,
        )
    if decision.outcome == "no_start":
        return manager_runtime.ManagerEnsureResult(
            outcome="uncertain",
            manager_record=None,
            started_here=False,
            process_handle=None,
            reason=decision.reason,
            probe_request_id=decision.probe_request_id,
        )

    try:
        final_reconciliation = reconcile_submitted_spawn(
            context,
            submitted_tid_str,
            timeout=0.0,
        )
    except (BrokerError, OSError) as exc:
        return manager_runtime.ManagerEnsureResult(
            outcome="uncertain",
            manager_record=None,
            started_here=False,
            process_handle=None,
            reason=f"final_submission_reconciliation_error:{exc}",
            probe_request_id=decision.probe_request_id,
        )
    if final_reconciliation.outcome == "rejected":
        reason = final_reconciliation.error or "manager rejected the spawn request"
        raise SubmissionManagerError(
            f"Manager rejected accepted task {submitted_tid_str}: {reason}"
        )
    if final_reconciliation.outcome != "queued":
        return manager_runtime.ManagerEnsureResult(
            outcome=(
                "not_needed"
                if final_reconciliation.outcome in {"spawned", "reserved"}
                else "uncertain"
            ),
            manager_record=None,
            started_here=False,
            process_handle=None,
            reason=f"accepted_request_{final_reconciliation.outcome}",
            probe_request_id=decision.probe_request_id,
        )

    if decision.manager_record is not None:
        logger.warning(
            "Submission recovery authorized a manager launch",
            extra={
                "incumbent_tid": decision.manager_record.get("tid"),
                "submitted_tid": submitted_tid_str,
                "probe_request_id": decision.probe_request_id,
                "decision_reason": decision.reason,
            },
        )
    try:
        record, started_here, process_handle = manager_runtime.start_manager(context)
    except (BrokerError, ManagerStartFailed, OSError) as exc:
        return manager_runtime.ManagerEnsureResult(
            outcome="uncertain",
            manager_record=None,
            started_here=False,
            process_handle=None,
            reason=f"manager_start_failed:{exc}",
            probe_request_id=decision.probe_request_id,
        )
    return manager_runtime.ManagerEnsureResult(
        outcome="ready",
        manager_record=record,
        started_here=started_here,
        process_handle=process_handle,
        reason=decision.reason,
        probe_request_id=decision.probe_request_id,
    )


def prepare_taskspec(
    taskspec: TaskSpec | Mapping[str, Any],
    *,
    payload: Any = None,
    seed_start_envelope: bool = True,
    allow_internal_runtime: bool = False,
) -> PreparedSubmissionRequest:
    """Validate and snapshot a TaskSpec submission without queue writes."""

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

    return _submit_prepared_outcome(context, prepared).receipt


def _resolve_submission_runtime_root(
    taskspec: TaskSpec,
    context: WeftContext,
) -> Path:
    """Resolve the one runtime-root rule shared by every submission surface."""

    declared_root = taskspec.spec.weft_context
    if not declared_root:
        return context.root.resolve()
    return _expand_declared_context_root(declared_root).resolve()


def _submit_prepared_outcome(
    context: WeftContext,
    prepared: PreparedSubmissionRequest,
    *,
    session: BrokerSession | None = None,
) -> _SubmittedPreparedOutcome:
    """Submit and retain the exact live runtime context for client handles.

    Enqueue and initial availability share one bounded session. Individual
    operations commit independently and end before recovery or session cleanup.

    Spec: [PY-3], [MF-1], [SB-0.4]
    """

    normalized = normalize_taskspec(prepared.taskspec)
    runtime_root = _resolve_submission_runtime_root(normalized, context)
    runtime_context = (
        context
        if runtime_root == context.root.resolve()
        else build_context(
            runtime_root,
            config=context.config,
            autostart=context.autostart_enabled,
        )
    )
    borrowed_session = session if runtime_context is context else None
    task_tid: str | None = None
    try:
        if borrowed_session is not None:
            task_tid, availability = _submit_with_session(
                runtime_context,
                prepared,
                normalized,
                borrowed_session,
            )
        else:
            with runtime_context.session() as owned_session:
                task_tid, availability = _submit_with_session(
                    runtime_context,
                    prepared,
                    normalized,
                    owned_session,
                )
    except SubmissionManagerError:
        raise
    except Exception as exc:
        if task_tid is not None:
            _annotate_accepted_submission_error(exc, task_tid)
        raise
    if availability.outcome != "ready":
        logger.warning(
            "Accepted task while manager readiness is degraded",
            extra={"tid": task_tid, "reason": availability.reason},
        )
    return _SubmittedPreparedOutcome(
        receipt=_receipt(prepared.name, task_tid, context_root=runtime_context.root),
        runtime_context=runtime_context,
    )


def _submit_with_session(
    runtime_context: WeftContext,
    prepared: PreparedSubmissionRequest,
    normalized: TaskSpec,
    session: BrokerSession,
) -> tuple[str, manager_runtime.ManagerEnsureResult]:
    """Commit one spawn request through a caller-owned session operation."""

    task_tid: str | None = None
    try:
        with session.connection() as broker:
            submitted_tid = submit_spawn_request(
                runtime_context.broker_target,
                taskspec=normalized,
                work_payload=prepared.payload,
                config=runtime_context.broker_config,
                tid=normalized.tid,
                inherited_weft_context=normalized.spec.weft_context,
                seed_start_envelope=prepared.seed_start_envelope,
                allow_internal_runtime=prepared.allow_internal_runtime,
                broker=broker,
            )
            task_tid = str(submitted_tid)
            observation = manager_runtime.observe_manager_availability(
                runtime_context, broker=broker
            )
        availability = ensure_manager_after_submission(
            runtime_context,
            submitted_tid=task_tid,
            observation=observation,
        )
    except SubmissionManagerError:
        raise
    except Exception as exc:
        if task_tid is not None:
            _annotate_accepted_submission_error(exc, task_tid)
        raise
    return task_tid, availability


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
        taskspec,
        payload=payload,
        seed_start_envelope=seed_start_envelope,
        allow_internal_runtime=allow_internal_runtime,
    )
    return submit_prepared(context, prepared)


def prepare_definition(
    taskspec: TaskSpec | Mapping[str, Any],
    overrides: Mapping[str, Any],
    *,
    payload: Any = None,
) -> PreparedSubmissionRequest:
    """Validate overrides, normalize, and snapshot a submission without a context.

    Overrides travel as one mapping so every public-seam keyword, including
    ``payload``, is checked against the override vocabulary.

    Args:
        taskspec: TaskSpec or JSON-compatible template mapping to normalize.
        overrides: Public submit overrides to apply.
        payload: Optional initial work payload to snapshot alongside.

    Returns:
        The validated, snapshotted submission request.

    Raises:
        TypeError: If an override name is outside the public vocabulary.
        ValueError: If an override value or the resulting TaskSpec is invalid.

    Note:
        Constructs no `WeftContext`, reads no configuration, opens no broker,
        and writes nothing; `prepare` delegates here.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-3]
    """

    _validate_submit_overrides(dict(overrides))
    updated = apply_submit_overrides(normalize_taskspec(taskspec), **overrides)
    return prepare_taskspec(updated, payload=payload)


def prepare(
    context: WeftContext,
    taskspec: TaskSpec | Mapping[str, Any],
    *,
    payload: Any = None,
    **overrides: Any,
) -> PreparedSubmissionRequest:
    """Snapshot a submission and bind its declared runtime path.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-3]
    """

    prepared = prepare_definition(taskspec, overrides, payload=payload)
    return replace(prepared, taskspec=_bind_declared_context(prepared.taskspec))


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


def prepare_spec(  # noqa: C901 approved [TS-3.1] [RUFF-SUP-369] exception
    context: WeftContext,
    reference: str | Path,
    *,
    spec_args: Sequence[str] = (),
    payload: Any = None,
    stdin_text: str | None = None,
    context_explicit: bool = True,
    persistent_override: bool | None = None,
    **overrides: Any,
) -> PreparedSubmissionRequest:
    """Resolve a task reference and bind its runtime path before run-input.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-3]
    """

    try:
        _validate_submit_overrides(overrides)
    except TypeError as exc:
        raise SubmissionValidationError(str(exc)) from exc
    try:
        resolved = resolve_spec_reference(
            reference,
            spec_type=SPEC_TYPE_TASK,
            context_path=context.root,
        )
    except FileNotFoundError as exc:
        raise SpecNotFound(str(exc)) from exc
    try:
        resolved_payload = dict(resolved.payload)
        if persistent_override is not None:
            spec_section = resolved_payload.setdefault("spec", {})
            if not isinstance(spec_section, dict):
                raise TypeError("TaskSpec spec section must be a mapping")
            spec_section["persistent"] = persistent_override
        taskspec = validate_taskspec_payload(
            resolved_payload,
            bundle_root=resolved.bundle_root,
            template=True,
        )
    except (ValidationError, TypeError, OSError, ValueError) as exc:
        raise SubmissionValidationError(str(exc)) from exc

    remaining = list(spec_args)
    parameterization = taskspec.spec.parameterization
    if parameterization is not None:
        try:
            arguments, remaining = parse_declared_parameterization_args(
                remaining,
                parameterization.arguments,
            )
        except (TypeError, ValueError) as exc:
            raise CommandUsageError(str(exc)) from exc
        try:
            taskspec = materialize_taskspec_template(
                taskspec,
                arguments=arguments,
                context_root=(
                    str(context.root)
                    if context_explicit
                    else taskspec.spec.weft_context
                ),
            )
        except WeftError:
            raise
        except Exception as exc:
            raise SubmissionValidationError(str(exc)) from exc

    try:
        updated = apply_submit_overrides(taskspec, **overrides)
    except ValidationError as exc:
        raise SubmissionValidationError(str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise CommandUsageError(str(exc)) from exc

    try:
        updated = _bind_declared_context(updated)
    except (ValidationError, TypeError, OSError, ValueError) as exc:
        raise SubmissionValidationError(str(exc)) from exc

    run_input = updated.spec.run_input
    if run_input is None:
        if remaining:
            raise CommandUsageError(
                "This TaskSpec does not declare submission arguments"
            )
        if payload is not None and stdin_text is not None:
            raise CommandUsageError("payload cannot be combined with stdin_text")
        if payload is not None:
            work_payload = payload
        else:
            work_payload = _initial_work_payload(
                target_type=updated.spec.type,
                stdin_text=stdin_text,
                interactive=bool(updated.spec.interactive),
            )
    else:
        if payload is not None:
            raise CommandUsageError(
                "payload cannot be combined with a TaskSpec run_input contract"
            )
        if stdin_text is not None and run_input.stdin is None:
            raise CommandUsageError(
                "stdin_text requires a TaskSpec run_input stdin contract"
            )
        if (
            run_input.stdin is not None
            and run_input.stdin.required
            and stdin_text is None
        ):
            raise CommandUsageError("This TaskSpec requires stdin_text")
        try:
            arguments = parse_declared_run_input_args(
                remaining,
                run_input.arguments,
            )
        except (TypeError, ValueError) as exc:
            raise CommandUsageError(str(exc)) from exc
        runtime_root = _resolve_submission_runtime_root(updated, context)
        try:
            work_payload = invoke_run_input_adapter(
                run_input.adapter_ref,
                request=SpecRunInputRequest(
                    arguments=arguments,
                    stdin_text=stdin_text,
                    context_root=str(runtime_root),
                    spec_name=updated.name,
                ),
                bundle_root=updated.get_bundle_root(),
            )
        except WeftError:
            raise
        except Exception as exc:
            raise SubmissionValidationError(str(exc)) from exc
    try:
        return prepare_taskspec(updated, payload=work_payload)
    except (ValidationError, TypeError, OSError, ValueError) as exc:
        raise SubmissionValidationError(str(exc)) from exc


def submit_spec(
    context: WeftContext,
    reference: str | Path,
    *,
    spec_args: Sequence[str] = (),
    payload: Any = None,
    stdin_text: str | None = None,
    **overrides: Any,
) -> SubmittedTaskReceipt:
    """Resolve and submit a stored, builtin, or file-backed task spec."""

    return submit_prepared(
        context,
        prepare_spec(
            context,
            reference,
            spec_args=spec_args,
            payload=payload,
            stdin_text=stdin_text,
            **overrides,
        ),
    )


def prepare_pipeline(
    context: WeftContext,
    reference: str | Path,
    *,
    payload: Any = None,
    **overrides: Any,
) -> PreparedSubmissionRequest:
    """Compile and snapshot a pipeline with its declared runtime path bound.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-3]
    """

    _validate_submit_overrides(overrides)
    try:
        resolved = resolve_spec_reference(
            reference,
            spec_type=SPEC_TYPE_PIPELINE,
            context_path=context.root,
        )
    except FileNotFoundError as exc:
        raise SpecNotFound(str(exc)) from exc
    pipeline_spec = load_pipeline_spec_payload(resolved.payload)

    def _load_pipeline_stage(task_name: str) -> dict[str, Any]:
        stage_ref = resolve_named_spec(
            task_name,
            spec_type=SPEC_TYPE_TASK,
            context_path=context.root,
        )
        return encode_taskspec_transport_payload(
            validate_taskspec_payload(
                stage_ref.payload,
                bundle_root=stage_ref.bundle_root,
                template=True,
            )
        )

    compiled = compile_linear_pipeline(
        pipeline_spec,
        context=context,
        task_loader=_load_pipeline_stage,
        source_ref=str(resolved.path),
    )
    updated = apply_submit_overrides(compiled.pipeline_taskspec, **overrides)
    updated = _bind_declared_context(updated)
    bootstrap_payload = (
        payload if payload is not None else compiled.bootstrap_input_fallback
    )
    return prepare_taskspec(
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


def _prepare_command(
    context: WeftContext,
    command: Sequence[str] | str,
    *,
    payload: Any = None,
    shell: bool = False,
    **overrides: Any,
) -> PreparedSubmissionRequest:
    """Validate and snapshot a command target for durable submission."""

    _validate_submit_overrides(overrides)
    if shell:
        raise NotImplementedError("submit_command(..., shell=True) is not supported")
    argv = shlex.split(command) if isinstance(command, str) else list(command)
    if not argv:
        raise ValueError("Command cannot be empty")

    explicit_name = overrides.get("name")
    taskspec = validate_taskspec_payload(
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
        template=True,
    )
    updated = apply_submit_overrides(taskspec, **overrides)
    return prepare_taskspec(updated, payload=_command_payload(payload))


def submit_command(
    context: WeftContext,
    command: Sequence[str] | str,
    *,
    payload: Any = None,
    shell: bool = False,
    **overrides: Any,
) -> SubmittedTaskReceipt:
    """Submit a command target using the same durable manager path."""

    return submit_prepared(
        context,
        _prepare_command(
            context,
            command,
            payload=payload,
            shell=shell,
            **overrides,
        ),
    )
