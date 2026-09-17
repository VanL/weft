"""Tests for the shared task-submission write boundary [MF-1]."""

from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Never

import pytest

import weft.commands.submission as submission_mod
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import WEFT_GLOBAL_LOG_QUEUE, WEFT_SPAWN_REQUESTS_QUEUE
from weft._exceptions import (
    CommandUsageError,
    InvalidTID,
    ManagerStartFailed,
    SubmissionManagerError,
    SubmissionValidationError,
)
from weft.client import normalize_taskspec_payload
from weft.commands._spawn_submission import SpawnSubmissionReconciliation
from weft.commands.types import PreparedSubmissionRequest
from weft.context import WeftContext
from weft.core import manager_runtime as core_manager_runtime
from weft.core.taskspec import (
    TaskSpec,
    resolve_taskspec_payload,
    validate_taskspec_payload,
)
from weft.helpers import iter_queue_json_entries

pytestmark = [pytest.mark.shared]


def _write_declared_argument_spec(root: Path) -> Path:
    spec_path = root / "declared.json"
    spec_path.write_text(
        json.dumps(
            {
                "name": "declared",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                    "weft_context": str(root),
                    "parameterization": {
                        "adapter_ref": "tests.commands.test_submission:materialize_for_test",
                        "arguments": {"provider": {"type": "string"}},
                    },
                    "run_input": {
                        "adapter_ref": "weft.builtins.run_input:arguments_payload",
                        "arguments": {"prompt": {"type": "string"}},
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return spec_path


def materialize_for_test(request: Any) -> dict[str, Any]:
    """Materialize the fixture without relying on a bundle-local import."""

    payload = json.loads(json.dumps(request.taskspec_payload))
    assert isinstance(payload, dict)
    payload["name"] = "task-" + request.arguments["provider"]
    return payload


def capture_run_input_context(request: Any) -> dict[str, Any]:
    """Expose the resolved adapter context for seam-order assertions."""

    return {"context_root": request.context_root}


def materialize_declared_context(request: Any) -> dict[str, Any]:
    """Select a runtime root through the real parameterization adapter path."""

    payload: dict[str, Any] = request.taskspec_payload
    payload["spec"]["weft_context"] = request.arguments["root"]
    return payload


def capture_run_input_context_and_name(request: Any) -> dict[str, Any]:
    """Expose both runtime binding and submit-override ordering."""

    return {"context_root": request.context_root, "name": request.spec_name}


def fail_run_input_with_missing_file(request: Any) -> dict[str, Any]:
    """Expose adapter filesystem failures to submission-boundary tests."""

    raise FileNotFoundError("adapter fixture missing")


def fail_run_input_with_value_error(request: Any) -> dict[str, Any]:
    """Expose adapter validation failures to submission-boundary tests."""

    raise ValueError("adapter rejected materialized input")


def fail_run_input_with_runtime_error(request: Any) -> dict[str, Any]:
    """Expose arbitrary adapter failures to submission-boundary tests."""

    raise RuntimeError("adapter execution failed")


ADAPTER_USAGE_ERROR = CommandUsageError("adapter usage error")


def fail_run_input_with_typed_usage(request: Any) -> dict[str, Any]:
    """Raise a deliberate public usage error from a run-input adapter."""

    raise ADAPTER_USAGE_ERROR


def materialize_with_typed_usage(request: Any) -> dict[str, Any]:
    """Raise a deliberate public usage error from a parameterization adapter."""

    raise ADAPTER_USAGE_ERROR


def materialize_with_runtime_error(request: Any) -> dict[str, Any]:
    """Raise an ordinary failure from a parameterization adapter."""

    raise RuntimeError("parameterization adapter failed")


def test_prepare_spec_processes_parameterization_then_run_input(
    weft_harness: WeftTestHarness,
) -> None:
    spec_path = _write_declared_argument_spec(weft_harness.root)

    prepared = submission_mod.prepare_spec(
        weft_harness.context,
        spec_path,
        spec_args=("--provider", "gemini", "--prompt", "hello"),
    )

    assert prepared.taskspec.name == "task-gemini"
    assert prepared.payload == {"prompt": "hello"}


def test_prepare_spec_rejects_payload_when_run_input_is_declared(
    weft_harness: WeftTestHarness,
) -> None:
    spec_path = _write_declared_argument_spec(weft_harness.root)

    with pytest.raises(CommandUsageError, match="payload cannot be combined"):
        submission_mod.prepare_spec(
            weft_harness.context,
            spec_path,
            spec_args=("--provider", "gemini", "--prompt", "hello"),
            payload={"other": True},
        )


def test_prepare_spec_routes_stdin_as_initial_payload_without_run_input(
    weft_harness: WeftTestHarness,
) -> None:
    spec_path = weft_harness.root / "plain.json"
    spec_path.write_text(
        json.dumps(
            {
                "name": "plain",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                    "weft_context": str(weft_harness.root),
                },
            }
        ),
        encoding="utf-8",
    )

    prepared = submission_mod.prepare_spec(
        weft_harness.context,
        spec_path,
        stdin_text="hello",
    )

    assert prepared.payload == "hello"


@pytest.mark.parametrize(
    ("interactive", "stdin_text", "expected"),
    [
        (False, None, {}),
        (False, "hello", {"stdin": "hello"}),
        (True, "hello", {"stdin": "hello", "close": True}),
    ],
)
def test_prepare_spec_preserves_command_initial_payload_semantics(
    weft_harness: WeftTestHarness,
    interactive: bool,
    stdin_text: str | None,
    expected: dict[str, Any],
) -> None:
    spec_path = weft_harness.root / "command.json"
    spec_path.write_text(
        json.dumps(
            {
                "name": "command",
                "spec": {
                    "type": "command",
                    "process_target": "python -c \"print('ok')\"",
                    "interactive": interactive,
                },
            }
        ),
        encoding="utf-8",
    )

    prepared = submission_mod.prepare_spec(
        weft_harness.context,
        spec_path,
        stdin_text=stdin_text,
    )

    assert prepared.payload == expected


@pytest.mark.parametrize("declared_context", [False, True])
def test_prepare_spec_passes_resolved_runtime_root_to_run_input_adapter(
    weft_harness: WeftTestHarness,
    tmp_path: Path,
    declared_context: bool,
) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    spec_section: dict[str, Any] = {
        "type": "function",
        "function_target": "tests.tasks.sample_targets:echo_payload",
        "run_input": {
            "adapter_ref": ("tests.commands.test_submission:capture_run_input_context"),
            "arguments": {},
        },
    }
    if declared_context:
        spec_section["weft_context"] = str(runtime_root)
    spec_path = weft_harness.root / "adapter-context.json"
    spec_path.write_text(
        json.dumps({"name": "adapter-context", "spec": spec_section}),
        encoding="utf-8",
    )

    prepared = submission_mod.prepare_spec(weft_harness.context, spec_path)

    expected_root = runtime_root if declared_context else weft_harness.context.root
    assert prepared.payload == {"context_root": str(expected_root.resolve())}


def test_prepare_spec_expands_home_in_runtime_context(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    spec_path = weft_harness.root / "home-context.json"
    spec_path.write_text(
        json.dumps(
            {
                "name": "home-context",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                    "weft_context": "~/runtime",
                    "run_input": {
                        "adapter_ref": (
                            "tests.commands.test_submission:capture_run_input_context"
                        ),
                        "arguments": {},
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    prepared = submission_mod.prepare_spec(weft_harness.context, spec_path)

    assert prepared.payload == {"context_root": str(runtime_root.resolve())}


@pytest.mark.parametrize("declared_root", ["runtime", "~/runtime"])
@pytest.mark.parametrize("resolved", [False, True])
def test_prepare_binds_declared_context_without_mutating_definition(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    declared_root: str,
    resolved: bool,
) -> None:
    """Runtime preparation freezes path interpretation, while exports stay pure."""

    origin = tmp_path / "origin"
    origin.mkdir()
    destination = origin / "runtime"
    monkeypatch.chdir(origin)
    monkeypatch.setenv("HOME", str(origin))
    monkeypatch.setenv("USERPROFILE", str(origin))
    payload = {
        "name": "bound-context",
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:echo_payload",
            "weft_context": declared_root,
        },
    }
    if resolved:
        payload = resolve_taskspec_payload(payload, tid="1777000000000000812")
    original = validate_taskspec_payload(
        payload, template=not resolved, bundle_root=origin
    )
    original_json = original.model_dump(mode="json")
    work_payload = {"value": ["before"]}

    exported = normalize_taskspec_payload(original, name="overridden")
    prepared = submission_mod.prepare(
        weft_harness.context, original, payload=work_payload, name="overridden"
    )

    assert not destination.exists(), "Binding must not initialize an alternate root"
    assert exported["spec"]["weft_context"] == declared_root
    assert original.model_dump(mode="json") == original_json
    assert prepared.taskspec is not original
    assert prepared.taskspec.spec.weft_context == str(destination)
    assert prepared.taskspec.tid == original.tid
    assert prepared.taskspec.io == original.io
    assert prepared.taskspec.get_bundle_root() == original.get_bundle_root()
    assert prepared.name == prepared.taskspec.name == "overridden"
    assert prepared.seed_start_envelope is True
    assert prepared.allow_internal_runtime is False
    work_payload["value"].append("after")
    assert prepared.payload == {"value": ["before"]}

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("HOME", str(elsewhere))
    monkeypatch.setenv("USERPROFILE", str(elsewhere))
    assert prepared.taskspec.spec.weft_context == str(destination)
    assert not destination.exists()


def test_prepare_leaves_absent_declared_context_unset(
    weft_harness: WeftTestHarness,
) -> None:
    prepared = submission_mod.prepare(
        weft_harness.context,
        {
            "name": "portable-context",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
        },
    )

    assert prepared.taskspec.spec.weft_context is None


@pytest.mark.parametrize("declared_root", ["runtime", "~/runtime"])
def test_prepare_spec_binds_materialized_context_before_run_input(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    declared_root: str,
) -> None:
    origin = tmp_path / "origin"
    origin.mkdir()
    monkeypatch.chdir(origin)
    monkeypatch.setenv("HOME", str(origin))
    monkeypatch.setenv("USERPROFILE", str(origin))
    destination = origin / "runtime"
    source_payload = {
        "name": "materialized-context",
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:echo_payload",
            "weft_context": "discarded-by-parameterization",
            "parameterization": {
                "adapter_ref": (
                    "tests.commands.test_submission:materialize_declared_context"
                ),
                "arguments": {"root": {"type": "string"}},
            },
            "run_input": {
                "adapter_ref": (
                    "tests.commands.test_submission:capture_run_input_context_and_name"
                ),
                "arguments": {},
            },
        },
    }
    source = weft_harness.root / "materialized-context.json"
    source.write_text(json.dumps(source_payload), encoding="utf-8")

    prepared = submission_mod.prepare_spec(
        weft_harness.context,
        source,
        spec_args=("--root", declared_root),
        name="overridden",
    )

    assert prepared.taskspec.spec.weft_context == str(destination)
    assert prepared.payload == {"context_root": str(destination), "name": "overridden"}
    assert prepared.taskspec.spec.parameterization is None
    assert json.loads(source.read_text(encoding="utf-8")) == source_payload
    assert not destination.exists()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("HOME", str(elsewhere))
    monkeypatch.setenv("USERPROFILE", str(elsewhere))
    assert prepared.taskspec.spec.weft_context == prepared.payload["context_root"]


def test_prepare_pipeline_preserves_compiled_root_and_request_flags(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stage = weft_harness.context.weft_dir / "tasks" / "declared-stage.json"
    stage.parent.mkdir(parents=True, exist_ok=True)
    stage.write_text(
        json.dumps(
            {
                "name": "declared-stage",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                    "weft_context": "stage-runtime",
                },
            }
        ),
        encoding="utf-8",
    )
    pipeline = weft_harness.root / "pipeline.json"
    pipeline.write_text(
        json.dumps(
            {
                "name": "declared-pipeline",
                "stages": [{"name": "only", "task": "declared-stage"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    prepared = submission_mod.prepare_pipeline(
        weft_harness.context, pipeline, payload={"value": "initial"}, name="overridden"
    )

    taskspec = prepared.taskspec
    runtime = taskspec.metadata["_weft_pipeline_runtime"]
    assert taskspec.spec.weft_context == str(weft_harness.context.root)
    assert taskspec.tid == runtime["pipeline_tid"]
    assert taskspec.io.inputs["inbox"] == runtime["queues"]["inbox"]
    assert taskspec.io.outputs["outbox"] == runtime["queues"]["outbox"]
    assert runtime["stages"][0]["taskspec"]["spec"]["weft_context"] == "stage-runtime"
    assert prepared.name == "overridden"
    assert prepared.payload == {"value": "initial"}
    assert prepared.seed_start_envelope is False
    assert prepared.allow_internal_runtime is True


@pytest.mark.parametrize(
    "declared_context",
    [
        "invalid\u0000context",
        pytest.param(
            "~weft_nonexistent_context_user/project",
            marks=pytest.mark.skipif(
                os.name == "nt",
                reason="Windows expands unknown user homes without lookup",
            ),
        ),
    ],
)
def test_prepare_spec_rejects_invalid_context_before_run_input(
    weft_harness: WeftTestHarness,
    declared_context: str,
) -> None:
    source = weft_harness.root / "invalid-context.json"
    source.write_text(
        json.dumps(
            {
                "name": "invalid-context",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                    "weft_context": declared_context,
                    "run_input": {
                        "adapter_ref": (
                            "tests.commands.test_submission:fail_run_input_with_runtime_error"
                        ),
                        "arguments": {},
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SubmissionValidationError) as caught:
        submission_mod.prepare_spec(weft_harness.context, source)

    assert type(caught.value.__cause__) is ValueError


def test_prepare_spec_rejects_stdin_when_run_input_declares_no_stdin(
    weft_harness: WeftTestHarness,
) -> None:
    spec_path = _write_declared_argument_spec(weft_harness.root)

    with pytest.raises(CommandUsageError, match="stdin contract"):
        submission_mod.prepare_spec(
            weft_harness.context,
            spec_path,
            spec_args=("--provider", "gemini", "--prompt", "hello"),
            stdin_text="hello",
        )


def test_prepare_spec_rejects_payload_plus_stdin_without_run_input(
    weft_harness: WeftTestHarness,
) -> None:
    spec_path = weft_harness.root / "plain.json"
    spec_path.write_text(
        json.dumps(
            {
                "name": "plain",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(CommandUsageError, match="payload cannot be combined"):
        submission_mod.prepare_spec(
            weft_harness.context,
            spec_path,
            payload={"explicit": True},
            stdin_text="hello",
        )


def test_prepare_spec_classifies_malformed_taskspec_as_submission_validation(
    weft_harness: WeftTestHarness,
) -> None:
    spec_path = weft_harness.root / "malformed.json"
    spec_path.write_text(
        json.dumps({"name": "malformed", "spec": {"type": "command"}}),
        encoding="utf-8",
    )

    with pytest.raises(SubmissionValidationError) as caught:
        submission_mod.prepare_spec(weft_harness.context, spec_path)

    assert type(caught.value.__cause__).__name__ == "ValidationError"


def test_prepare_spec_classifies_unknown_override_as_submission_validation(
    weft_harness: WeftTestHarness,
) -> None:
    spec_path = _write_declared_argument_spec(weft_harness.root)

    with pytest.raises(SubmissionValidationError, match="Unknown submit override"):
        submission_mod.prepare_spec(
            weft_harness.context,
            spec_path,
            unknown_override=True,
        )


@pytest.mark.parametrize(
    "adapter_ref",
    [
        "tests.commands.test_submission:fail_run_input_with_missing_file",
        "tests.commands.test_submission:fail_run_input_with_value_error",
        "tests.commands.test_submission:fail_run_input_with_runtime_error",
        "tests.missing_run_input_adapter:adapt",
        "tests.commands.test_submission:missing_adapter_attribute",
    ],
)
def test_prepare_spec_preserves_adapter_failures_as_submission_validation(
    weft_harness: WeftTestHarness,
    adapter_ref: str,
) -> None:
    spec_path = weft_harness.root / "adapter-failure.json"
    spec_path.write_text(
        json.dumps(
            {
                "name": "adapter-failure",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                    "run_input": {"adapter_ref": adapter_ref, "arguments": {}},
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SubmissionValidationError) as caught:
        submission_mod.prepare_spec(weft_harness.context, spec_path)

    assert isinstance(caught.value.__cause__, Exception)


@pytest.mark.parametrize("stage", ["parameterization", "run_input"])
def test_prepare_spec_preserves_adapter_raised_weft_error_identity(
    weft_harness: WeftTestHarness,
    stage: str,
) -> None:
    spec_path = weft_harness.root / f"typed-{stage}.json"
    spec: dict[str, Any] = {
        "type": "function",
        "function_target": "tests.tasks.sample_targets:echo_payload",
    }
    spec_args: tuple[str, ...] = ()
    if stage == "parameterization":
        spec["parameterization"] = {
            "adapter_ref": (
                "tests.commands.test_submission:materialize_with_typed_usage"
            ),
            "arguments": {},
        }
    else:
        spec["run_input"] = {
            "adapter_ref": (
                "tests.commands.test_submission:fail_run_input_with_typed_usage"
            ),
            "arguments": {},
        }
    spec_path.write_text(
        json.dumps({"name": f"typed-{stage}", "spec": spec}),
        encoding="utf-8",
    )

    with pytest.raises(CommandUsageError) as caught:
        submission_mod.prepare_spec(
            weft_harness.context,
            spec_path,
            spec_args=spec_args,
        )

    assert type(caught.value) is CommandUsageError
    assert caught.value.__cause__ is None


def test_prepare_spec_wraps_parameterization_adapter_runtime_error(
    weft_harness: WeftTestHarness,
) -> None:
    spec_path = weft_harness.root / "parameterization-runtime-error.json"
    spec_path.write_text(
        json.dumps(
            {
                "name": "parameterization-runtime-error",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                    "parameterization": {
                        "adapter_ref": (
                            "tests.commands.test_submission:"
                            "materialize_with_runtime_error"
                        ),
                        "arguments": {},
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SubmissionValidationError) as caught:
        submission_mod.prepare_spec(weft_harness.context, spec_path)

    assert type(caught.value.__cause__) is RuntimeError


class ManagerStartupFailure(Exception):
    """Ordinary manager-startup failure that requires queue reconciliation."""


class ManagerStartupSignal(BaseException):
    """Fatal manager-startup signal that reconciliation must not contain."""


@pytest.mark.parametrize(
    "raw_tid",
    ["1777000000000000789", "T1777000000000000789", " T1777000000000000789 "],
)
def test_normalize_tid_removes_at_most_one_task_prefix(raw_tid: str) -> None:
    assert submission_mod.normalize_tid(raw_tid) == "1777000000000000789"


def test_apply_submit_overrides_rejects_invalid_model_dump_spec_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    taskspec = TaskSpec.model_validate(
        {
            "name": "invalid-dump-shape",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
        },
        context={"template": True, "auto_expand": False},
    )

    def invalid_model_dump(_self: TaskSpec, *, mode: str) -> dict[str, object]:
        assert mode == "json"
        return {"spec": [], "metadata": {}}

    monkeypatch.setattr(TaskSpec, "model_dump", invalid_model_dump)

    with pytest.raises(TypeError) as exc_info:
        submission_mod.apply_submit_overrides(taskspec)
    assert type(exc_info.value) is TypeError
    assert "TaskSpec spec section" in str(exc_info.value)
    assert "mapping" in str(exc_info.value)
    assert exc_info.value.__cause__ is None


def test_plain_submission_name_does_not_require_endpoint_syntax(
    weft_harness: WeftTestHarness,
) -> None:
    prepared = submission_mod.prepare(
        weft_harness.context,
        {
            "name": "original",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
        },
        name="nightly report",
    )

    assert prepared.taskspec.name == "nightly report"


def test_ensure_manager_propagates_programmer_runtime_error_without_reconciliation(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unclassified runtime defect remains visible after acceptance."""

    context = weft_harness.context
    tid = "1777000000000000789"
    startup_error = ManagerStartupFailure("startup detail")
    calls: list[str] = []

    def fail_startup(_context: object) -> Never:
        calls.append("ensure")
        raise startup_error

    monkeypatch.setattr(
        submission_mod,
        "reconcile_submitted_spawn",
        lambda *_args, **_kwargs: pytest.fail(
            "programmer errors must not be relabeled as availability"
        ),
    )

    with pytest.raises(ManagerStartupFailure, match="startup detail"):
        submission_mod.ensure_manager_after_submission(
            context,
            submitted_tid=tid,
            ensure_manager_fn=fail_startup,
        )

    assert calls == ["ensure"]


def test_ensure_manager_rejected_result_includes_accepted_tid(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An authoritative rejection remains a typed error keyed by accepted TID."""

    context = weft_harness.context
    tid = "1777000000000000790"
    monkeypatch.setattr(
        core_manager_runtime,
        "observe_manager_availability",
        lambda _context: core_manager_runtime.ManagerAvailabilityObservation(
            outcome="absent",
            manager_record=None,
            first_uncertain_at=None,
            backlog_pending=None,
            reason="absent",
        ),
    )
    monkeypatch.setattr(
        submission_mod,
        "reconcile_submitted_spawn",
        lambda _context, submitted_tid, **_kwargs: SpawnSubmissionReconciliation(
            outcome="rejected",
            tid=submitted_tid,
            error="manager rejection detail",
        ),
    )

    with pytest.raises(
        SubmissionManagerError,
        match=f"{tid}: manager rejection detail",
    ):
        submission_mod.ensure_manager_after_submission(
            context,
            submitted_tid=tid,
        )


def test_ensure_manager_start_failure_preserves_queued_acceptance(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recognized launch failure returns degradation and never deletes work."""
    context = weft_harness.context
    tid = "1777000000000000792"
    observation = core_manager_runtime.ManagerAvailabilityObservation(
        outcome="absent",
        manager_record=None,
        first_uncertain_at=None,
        backlog_pending=None,
        reason="absent",
    )
    decision = core_manager_runtime.ManagerRecoveryDecision(
        outcome="launch",
        manager_record=None,
        reason="confirmed_absent",
    )
    reconciliations = iter(
        [
            SpawnSubmissionReconciliation(outcome="queued", tid=tid),
            SpawnSubmissionReconciliation(outcome="queued", tid=tid),
        ]
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "observe_manager_availability",
        lambda _context: observation,
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "decide_manager_recovery",
        lambda _context, _observation: decision,
    )
    monkeypatch.setattr(
        submission_mod,
        "reconcile_submitted_spawn",
        lambda *_args, **_kwargs: next(reconciliations),
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "start_manager",
        lambda _context: (_ for _ in ()).throw(ManagerStartFailed("read-only")),
    )

    result = submission_mod.ensure_manager_after_submission(
        context,
        submitted_tid=tid,
        delete_spawn_request_fn=lambda *_args, **_kwargs: pytest.fail(
            "accepted work must never be deleted"
        ),
    )

    assert result.outcome == "uncertain"
    assert result.reason == "manager_start_failed:read-only"


def test_accepted_request_executes_after_later_manager_recovery(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A launch failure retains one exact request for a later real manager."""
    context = weft_harness.context
    observation = core_manager_runtime.ManagerAvailabilityObservation(
        outcome="absent",
        manager_record=None,
        first_uncertain_at=None,
        backlog_pending=None,
        reason="absent",
    )
    decision = core_manager_runtime.ManagerRecoveryDecision(
        outcome="launch",
        manager_record=None,
        reason="confirmed_absent",
    )
    with monkeypatch.context() as patch:
        patch.setattr(
            core_manager_runtime,
            "observe_manager_availability",
            lambda _context: observation,
        )
        patch.setattr(
            core_manager_runtime,
            "decide_manager_recovery",
            lambda _context, _observation: decision,
        )
        patch.setattr(
            core_manager_runtime,
            "start_manager",
            lambda _context: (_ for _ in ()).throw(
                ManagerStartFailed("read-only startup directory")
            ),
        )
        receipt = submission_mod.submit(
            context,
            {
                "name": "retained-after-readiness-failure",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                },
            },
            payload="retained",
        )

    weft_harness.register_tid(receipt.tid)
    queue = context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=False)
    try:
        assert queue.peek_one(exact_timestamp=int(receipt.tid)) is not None
    finally:
        queue.close()

    weft_harness.ensure_foreground_manager()
    weft_harness.wait_for_completion(receipt.tid, timeout=30.0)
    queue = context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=False)
    try:
        assert queue.peek_one(exact_timestamp=int(receipt.tid)) is None
    finally:
        queue.close()


def test_concurrent_accepted_requests_converge_and_execute_once(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent recovery retains and executes each exact request once [MF-1]."""

    context = weft_harness.context
    writes_committed = threading.Barrier(2)
    launch_lock = threading.Lock()
    real_submit_spawn_request = submission_mod.submit_spawn_request

    def submit_then_converge(*args: Any, **kwargs: Any) -> int:
        message_id = real_submit_spawn_request(*args, **kwargs)
        writes_committed.wait(timeout=10.0)
        return message_id

    def start_one_inline_manager(
        _context: WeftContext,
    ) -> tuple[dict[str, object], bool, None]:
        with launch_lock:
            record = weft_harness.ensure_foreground_manager()
        return record, False, None

    monkeypatch.setattr(
        submission_mod,
        "submit_spawn_request",
        submit_then_converge,
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "start_manager",
        start_one_inline_manager,
    )

    def submit(value: str) -> str:
        receipt = submission_mod.submit(
            context,
            {
                "name": f"concurrent-recovery-{value}",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                },
            },
            payload=value,
        )
        return receipt.tid

    with ThreadPoolExecutor(max_workers=2) as executor:
        tids = tuple(executor.map(submit, ("one", "two")))

    assert len(set(tids)) == 2
    for tid in tids:
        weft_harness.register_tid(tid)
        weft_harness.wait_for_completion(tid, timeout=30.0)

    with context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=False) as queue:
        for tid in tids:
            assert queue.peek_one(exact_timestamp=int(tid)) is None

    with context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as queue:
        events = [payload for payload, _timestamp in iter_queue_json_entries(queue)]
    for tid in tids:
        spawn_events = [
            event
            for event in events
            if event.get("event") == "task_spawned" and event.get("child_tid") == tid
        ]
        assert len(spawn_events) == 1


def test_ensure_manager_propagates_fatal_startup_signal_without_reconciliation(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BaseException signals bypass the ordinary queue reconciliation policy."""

    context = weft_harness.context
    signal = ManagerStartupSignal()

    def fail_startup(_context: object) -> Never:
        raise signal

    monkeypatch.setattr(
        submission_mod,
        "reconcile_submitted_spawn",
        lambda *_args, **_kwargs: pytest.fail(
            "fatal startup signals must not enter reconciliation"
        ),
    )

    with pytest.raises(ManagerStartupSignal) as exc_info:
        submission_mod.ensure_manager_after_submission(
            context,
            submitted_tid="1777000000000000791",
            ensure_manager_fn=fail_startup,
        )

    assert exc_info.value is signal


def test_submit_prepared_uses_committed_id_for_reconciliation_and_receipt(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = weft_harness.context
    taskspec = TaskSpec.model_validate(
        {
            "name": "client-template",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
                "weft_context": str(context.root),
            },
            "metadata": {},
        },
        context={"template": True, "auto_expand": False},
    )
    prepared = PreparedSubmissionRequest(
        name=taskspec.name,
        taskspec=taskspec,
        payload=None,
    )
    committed_id = 1777000000000000789
    captured: dict[str, Any] = {}

    def fail_preallocation(_context: WeftContext) -> str:
        raise AssertionError("prepared submission must not preallocate a TID")

    def fake_submit(*args: object, **kwargs: object) -> int:
        captured["submit_args"] = args
        captured["submit_kwargs"] = kwargs
        return committed_id

    def fake_ensure(
        _context: WeftContext, *, submitted_tid: str | int
    ) -> core_manager_runtime.ManagerEnsureResult:
        captured["reconciled_tid"] = submitted_tid
        return core_manager_runtime.ManagerEnsureResult(
            outcome="ready",
            manager_record={"tid": "manager"},
            started_here=False,
            process_handle=None,
            reason="ready",
        )

    monkeypatch.setattr(core_manager_runtime, "generate_tid", fail_preallocation)
    monkeypatch.setattr(submission_mod, "submit_spawn_request", fake_submit)
    monkeypatch.setattr(submission_mod, "ensure_manager_after_submission", fake_ensure)

    receipt = submission_mod.submit_prepared(context, prepared)

    assert captured["submit_kwargs"]["tid"] is None
    assert captured["reconciled_tid"] == str(committed_id)
    assert receipt.tid == str(committed_id)
    assert receipt.context_root == str(context.root)


def test_submit_prepared_keeps_explicit_id_on_exact_insert_path(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = weft_harness.context
    explicit_tid = "1777000000000000812"
    taskspec = TaskSpec.model_validate(
        resolve_taskspec_payload(
            {
                "name": "explicit-client-task",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                },
                "metadata": {},
            },
            tid=explicit_tid,
            inherited_weft_context=str(context.root),
        ),
        context={"auto_expand": False},
    )
    prepared = PreparedSubmissionRequest(
        name=taskspec.name,
        taskspec=taskspec,
        payload=None,
    )
    captured: dict[str, Any] = {}

    def fake_submit(*args: object, **kwargs: object) -> int:
        captured["submit_args"] = args
        captured["submit_kwargs"] = kwargs
        return int(explicit_tid)

    def fake_ensure(
        _context: WeftContext, *, submitted_tid: str | int
    ) -> core_manager_runtime.ManagerEnsureResult:
        captured["reconciled_tid"] = submitted_tid
        return core_manager_runtime.ManagerEnsureResult(
            outcome="ready",
            manager_record={"tid": "manager"},
            started_here=False,
            process_handle=None,
            reason="ready",
        )

    monkeypatch.setattr(submission_mod, "submit_spawn_request", fake_submit)
    monkeypatch.setattr(submission_mod, "ensure_manager_after_submission", fake_ensure)

    receipt = submission_mod.submit_prepared(context, prepared)

    assert captured["submit_kwargs"]["tid"] == explicit_tid
    assert captured["reconciled_tid"] == explicit_tid
    assert receipt.tid == explicit_tid


def test_submit_prepared_programmer_error_names_committed_tid(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = weft_harness.context
    tid = "1777000000000000813"
    prepared = submission_mod.prepare(
        context,
        {
            "name": "diagnostic-task",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
        },
    )
    defect = RuntimeError("programmer defect")
    monkeypatch.setattr(
        submission_mod,
        "submit_spawn_request",
        lambda *_args, **_kwargs: int(tid),
    )
    monkeypatch.setattr(
        submission_mod,
        "ensure_manager_after_submission",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(defect),
    )

    with pytest.raises(RuntimeError, match=f"accepted_tid={tid}") as exc_info:
        submission_mod.submit_prepared(context, prepared)

    assert exc_info.value is defect


@pytest.mark.parametrize(
    "raw_tid", ["１" * 19, "123", "TT1777000000000000789", "9999999999999999999"]
)
def test_normalize_tid_uses_exact_message_id_validation(raw_tid: str) -> None:
    with pytest.raises(InvalidTID):
        submission_mod.normalize_tid(raw_tid)
