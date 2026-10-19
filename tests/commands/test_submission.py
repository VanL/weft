"""Tests for the shared task-submission write boundary [MF-1]."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Never

import pytest

import weft.commands._spawn_submission as spawn_submission
import weft.commands.submission as submission_mod
from simplebroker import BrokerSession
from simplebroker.ext import IntegrityError
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import WEFT_GLOBAL_LOG_QUEUE, WEFT_SPAWN_REQUESTS_QUEUE
from weft._exceptions import (
    CommandUsageError,
    InvalidTID,
    ManagerStartFailed,
    SubmissionManagerError,
    SubmissionValidationError,
)
from weft.client import WeftClient, normalize_taskspec_payload
from weft.commands._spawn_submission import (
    SpawnSubmissionReconciliation,
    _spawn_reconciliation_queue_specs,
    reconcile_submitted_spawn,
)
from weft.commands.types import PreparedSubmissionRequest
from weft.context import WeftContext
from weft.core import manager_runtime as core_manager_runtime
from weft.core import runtime_identity
from weft.core.service_convergence import build_manager_service_payload
from weft.core.spawn_requests import (
    iter_spawn_reserved_routes,
    observe_spawn_request_custody,
)
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
        lambda _context, **_kwargs: observation,
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
            lambda _context, **_kwargs: observation,
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
    real_submit_spawn_request = submission_mod._submit_spawn_request_with_payload

    def submit_then_converge(*args: Any, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        outcome = real_submit_spawn_request(*args, **kwargs)
        writes_committed.wait(timeout=10.0)
        return outcome

    def start_one_inline_manager(
        _context: WeftContext,
    ) -> tuple[dict[str, object], bool, None]:
        with launch_lock:
            record = weft_harness.ensure_foreground_manager()
        return record, False, None

    monkeypatch.setattr(
        submission_mod,
        "_submit_spawn_request_with_payload",
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

    def fake_submit(*args: object, **kwargs: object) -> tuple[int, dict[str, Any]]:
        captured["submit_args"] = args
        captured["submit_kwargs"] = kwargs
        submitted_spec = kwargs["taskspec"]
        assert isinstance(submitted_spec, TaskSpec)
        return committed_id, submitted_spec.model_dump(mode="json")

    def fake_ensure(
        _context: WeftContext, *, submitted_tid: str | int, **_kwargs: Any
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
    monkeypatch.setattr(
        submission_mod, "_submit_spawn_request_with_payload", fake_submit
    )
    monkeypatch.setattr(submission_mod, "ensure_manager_after_submission", fake_ensure)

    receipt = submission_mod.submit_prepared(context, prepared)

    assert captured["submit_kwargs"]["tid"] is None
    assert captured["reconciled_tid"] == str(committed_id)
    assert receipt.tid == str(committed_id)
    assert receipt.context_root == str(context.root)


def test_submit_prepared_borrows_session_without_closing_owner(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = weft_harness.context
    prepared = submission_mod.prepare(
        context,
        {
            "name": "borrowed-session",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
        },
        payload="borrowed",
    )
    close_calls: list[BrokerSession] = []
    original_close = BrokerSession.close

    def counted_close(session: BrokerSession) -> None:
        close_calls.append(session)
        original_close(session)

    monkeypatch.setattr(BrokerSession, "close", counted_close)
    monkeypatch.setattr(
        submission_mod,
        "ensure_manager_after_submission",
        lambda *_args, **_kwargs: core_manager_runtime.ManagerEnsureResult(
            outcome="ready",
            manager_record=None,
            started_here=False,
            process_handle=None,
            reason="test-ready",
        ),
    )

    with context.session() as session:
        outcome = submission_mod._submit_prepared_outcome(
            context,
            prepared,
            session=session,
        )
        assert close_calls == []
        with session.connection() as broker:
            assert (
                broker.peek_one(
                    WEFT_SPAWN_REQUESTS_QUEUE,
                    exact_timestamp=int(outcome.receipt.tid),
                )
                is not None
            )

    assert close_calls == [session]


def test_retained_session_exits_each_operation_before_fresh_readiness(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = weft_harness.context
    prepared = submission_mod.prepare(
        context,
        {
            "name": "operation-boundary",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
        },
    )
    original_connection = BrokerSession.connection
    active_operations = 0
    events: list[tuple[str, int]] = []
    observations = iter(("first-view", "second-view"))
    ensured_reasons: list[str] = []

    @contextmanager
    def counted_connection(session: BrokerSession) -> Iterator[Any]:
        nonlocal active_operations
        events.append(("enter", id(session)))
        active_operations += 1
        try:
            with original_connection(session) as broker:
                yield broker
        finally:
            active_operations -= 1
            events.append(("exit", id(session)))

    def observe(
        _context: WeftContext, *, broker: Any | None = None
    ) -> core_manager_runtime.ManagerAvailabilityObservation:
        assert broker is not None
        assert active_operations == 1
        return core_manager_runtime.ManagerAvailabilityObservation(
            outcome="ready",
            manager_record=None,
            first_uncertain_at=None,
            backlog_pending=None,
            reason=next(observations),
        )

    def ensure(
        _context: WeftContext,
        *,
        submitted_tid: str | int,
        observation: core_manager_runtime.ManagerAvailabilityObservation,
        **_kwargs: Any,
    ) -> core_manager_runtime.ManagerEnsureResult:
        del submitted_tid
        assert active_operations == 0
        ensured_reasons.append(observation.reason)
        return core_manager_runtime.ManagerEnsureResult(
            outcome="ready",
            manager_record=None,
            started_here=False,
            process_handle=None,
            reason=observation.reason,
        )

    monkeypatch.setattr(BrokerSession, "connection", counted_connection)
    monkeypatch.setattr(core_manager_runtime, "observe_manager_availability", observe)
    monkeypatch.setattr(submission_mod, "ensure_manager_after_submission", ensure)

    with context.session() as session:
        for _ in range(2):
            submission_mod._submit_prepared_outcome(
                context,
                prepared,
                session=session,
            )

    assert events == [
        ("enter", id(session)),
        ("exit", id(session)),
        ("enter", id(session)),
        ("exit", id(session)),
    ]
    assert ensured_reasons == ["first-view", "second-view"]


@pytest.mark.parametrize("failure_point", ["insert", "observe", "ensure"])
def test_borrowed_submission_failure_leaves_owner_session_usable(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    context = weft_harness.context
    prepared = submission_mod.prepare(
        context,
        {
            "name": f"borrowed-{failure_point}",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
        },
    )
    failure = RuntimeError(f"{failure_point} failed")
    if failure_point == "insert":
        monkeypatch.setattr(
            submission_mod,
            "_submit_spawn_request_with_payload",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
        )
    elif failure_point == "observe":
        monkeypatch.setattr(
            core_manager_runtime,
            "observe_manager_availability",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
        )
    else:
        monkeypatch.setattr(
            submission_mod,
            "ensure_manager_after_submission",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
        )

    with context.session() as session:
        with pytest.raises(RuntimeError, match=f"{failure_point} failed") as error:
            submission_mod._submit_prepared_outcome(
                context,
                prepared,
                session=session,
            )
        if failure_point != "insert":
            assert "accepted_tid=" in str(error.value)
        with session.connection() as broker:
            timestamp = broker.write("borrowed.owner.probe", failure_point)
            assert broker.peek_one(
                "borrowed.owner.probe",
                exact_timestamp=timestamp,
            ) == (failure_point, timestamp)


def test_borrowed_connection_exit_failure_names_tid_and_leaves_owner_usable(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = weft_harness.context
    prepared = submission_mod.prepare(
        context,
        {
            "name": "borrowed-connection-exit",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
        },
    )
    original_connection = BrokerSession.connection

    with context.session() as session:

        @contextmanager
        def fail_after_yield(owner: BrokerSession) -> Iterator[Any]:
            with original_connection(owner) as broker:
                yield broker
            raise RuntimeError("connection exit failed")

        with monkeypatch.context() as exit_patch:
            exit_patch.setattr(BrokerSession, "connection", fail_after_yield)
            with pytest.raises(
                RuntimeError, match="connection exit failed.*accepted_tid="
            ):
                submission_mod._submit_prepared_outcome(
                    context,
                    prepared,
                    session=session,
                )

        with session.connection() as broker:
            timestamp = broker.write("borrowed.exit.probe", "usable")
            assert broker.peek_one(
                "borrowed.exit.probe", exact_timestamp=timestamp
            ) == ("usable", timestamp)


@pytest.mark.parametrize("lend_facade", [False, True])
def test_retained_session_recovers_after_real_duplicate_tid_rollback(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    lend_facade: bool,
) -> None:
    context = weft_harness.context
    monkeypatch.setattr(
        submission_mod,
        "ensure_manager_after_submission",
        lambda *_args, **_kwargs: core_manager_runtime.ManagerEnsureResult(
            outcome="ready",
            manager_record=None,
            started_here=False,
            process_handle=None,
            reason="test-ready",
        ),
    )
    explicit_tid = "1777000000000000912"
    first = submission_mod.prepare(
        context,
        {
            "name": "first-explicit",
            "tid": explicit_tid,
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
        },
    )
    duplicate = submission_mod.prepare(
        context,
        {
            "name": "duplicate-explicit",
            "tid": explicit_tid,
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
        },
    )
    third = submission_mod.prepare(
        context,
        {
            "name": "third-fresh",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
        },
    )

    with context.session() as session:
        connection_queue = (
            session.queue(WEFT_SPAWN_REQUESTS_QUEUE) if lend_facade else None
        )
        first_outcome = submission_mod._submit_prepared_outcome(
            context, first, session=session, connection_queue=connection_queue
        )
        with pytest.raises(IntegrityError):
            submission_mod._submit_prepared_outcome(
                context, duplicate, session=session, connection_queue=connection_queue
            )
        third_outcome = submission_mod._submit_prepared_outcome(
            context, third, session=session, connection_queue=connection_queue
        )
        with session.connection() as broker:
            assert (
                broker.peek_one(
                    WEFT_SPAWN_REQUESTS_QUEUE,
                    exact_timestamp=int(first_outcome.receipt.tid),
                )
                is not None
            )
            assert (
                broker.peek_one(
                    WEFT_SPAWN_REQUESTS_QUEUE,
                    exact_timestamp=int(third_outcome.receipt.tid),
                )
                is not None
            )


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

    def fake_submit(*args: object, **kwargs: object) -> tuple[int, dict[str, Any]]:
        captured["submit_args"] = args
        captured["submit_kwargs"] = kwargs
        submitted_spec = kwargs["taskspec"]
        assert isinstance(submitted_spec, TaskSpec)
        return int(explicit_tid), submitted_spec.model_dump(mode="json")

    def fake_ensure(
        _context: WeftContext, *, submitted_tid: str | int, **_kwargs: Any
    ) -> core_manager_runtime.ManagerEnsureResult:
        captured["reconciled_tid"] = submitted_tid
        return core_manager_runtime.ManagerEnsureResult(
            outcome="ready",
            manager_record={"tid": "manager"},
            started_here=False,
            process_handle=None,
            reason="ready",
        )

    monkeypatch.setattr(
        submission_mod, "_submit_spawn_request_with_payload", fake_submit
    )
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
        "_submit_spawn_request_with_payload",
        lambda *_args, **_kwargs: (int(tid), {}),
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


@pytest.mark.parametrize(
    ("source", "reserved_suffix"),
    [
        ("weft.spawn.internal", "internal_reserved"),
    ],
)
@pytest.mark.parametrize("reserved", [False, True])
def test_reconciliation_locates_accepted_child_and_internal_lane_requests(
    weft_harness: WeftTestHarness,
    source: str,
    reserved_suffix: str,
    reserved: bool,
) -> None:
    """Accepted work in each added pair keeps its TID through claim transfer.

    Real broker moves prove custody; log/mapping fixtures would bypass the
    source/reserved discovery decision this regression guards.
    """
    context = weft_harness.context
    manager_tid = "1776000000000000001"
    reserved_queue = f"T{manager_tid}.{reserved_suffix}"
    with context.queue(source, persistent=True) as queue:
        tid = str(queue.write(json.dumps({"taskspec": {"name": "accepted-child"}})))
        if reserved:
            assert queue.move_one(reserved_queue, exact_timestamp=int(tid)) is not None
    if reserved:
        payload = build_manager_service_payload(
            context=context,
            tid=manager_tid,
            name="manager",
            status="stopped",
            queues={"requests": WEFT_SPAWN_REQUESTS_QUEUE},
            runtime_handle={},
        )
        with context.queue("weft.state.services", persistent=True) as registry:
            registry.write(json.dumps(payload))
    # The waiter must also subscribe to custody locations before publication;
    # finding an already present row does not protect wakeup routing.
    subscribed = {
        name for name, _persistent in _spawn_reconciliation_queue_specs(context, tid)
    }
    assert source in subscribed
    if reserved:
        assert reserved_queue in subscribed
    outcome = reconcile_submitted_spawn(context, tid, timeout=0.0)
    assert outcome == SpawnSubmissionReconciliation(
        outcome="reserved" if reserved else "queued",
        tid=tid,
        reserved_queue=reserved_queue if reserved else None,
    )


def test_retained_client_facade_routes_runtime_children_on_every_submission(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A warmed root facade supplies a broker, never a cached scheduling lane."""
    context = weft_harness.context
    monkeypatch.setattr(
        core_manager_runtime,
        "observe_manager_availability",
        lambda *_args, **_kwargs: core_manager_runtime.ManagerAvailabilityObservation(
            outcome="ready",
            manager_record=None,
            first_uncertain_at=None,
            backlog_pending=None,
            reason="test-ready",
        ),
    )
    taskspec = {
        "name": "retained-child",
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:echo_payload",
        },
    }
    monkeypatch.setattr(runtime_identity, "_runtime_identity", None)
    with WeftClient(context=context) as client:
        root = client.submit(taskspec, payload="root")
        identity = runtime_identity.RuntimeIdentity(
            tid=root.tid,
            context=runtime_identity.canonical_runtime_context(context.broker_target),
            backend_name=context.backend_name,
        )
        monkeypatch.setattr(runtime_identity, "_runtime_identity", identity)
        children = [client.submit(taskspec, payload=value) for value in ("one", "two")]
    with context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=True) as queue:
        assert queue.peek_one(exact_timestamp=int(root.tid)) is not None
        for child, value in zip(children, ("one", "two"), strict=True):
            row = queue.peek_one(exact_timestamp=int(child.tid))
            assert row is not None
            request = json.loads(row)
            assert request["inbox_message"] == value
            assert request["submitted_by"] == {
                "tid": root.tid,
                "context": identity.context,
                "root_tid": root.tid,
                "ancestry_tids": [root.tid],
            }
    with context.queue("weft.spawn.children", persistent=True) as removed_lane:
        assert removed_lane.peek_one() is None


@pytest.mark.parametrize("borrowed", [False, True])
def test_manager_availability_backlog_includes_primary_accepted_work(
    weft_harness: WeftTestHarness,
    borrowed: bool,
) -> None:
    """Accepted ordinary work is visible to owned and borrowed startup reads."""
    context = weft_harness.context
    with context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=True) as child_queue:
        tid = child_queue.write(json.dumps({"taskspec": {"name": "accepted-child"}}))
        with context.session() as session, session.connection() as broker:
            lent_broker = broker if borrowed else None
            assert core_manager_runtime._read_public_spawn_backlog(
                context, broker=lent_broker
            )
            assert core_manager_runtime._public_spawn_backlog_pending(
                context, broker=lent_broker
            )
            child_queue.delete(message_id=tid)
            assert not core_manager_runtime._read_public_spawn_backlog(
                context, broker=lent_broker
            )


@pytest.mark.parametrize("schema", ["weft.service_owner.v1", "weft.service_owner.v3"])
def test_read_only_reserved_route_discovery_preserves_legacy_registry_rows(
    weft_harness: WeftTestHarness,
    schema: str,
) -> None:
    """Custody route discovery must not take bootstrap's registry-delete custody."""
    context = weft_harness.context
    manager_tid = "1776000000000000001"
    payload = build_manager_service_payload(
        context=context,
        tid=manager_tid,
        name="manager",
        status="stopped",
        queues={"requests": WEFT_SPAWN_REQUESTS_QUEUE},
        runtime_handle={},
    )
    legacy = json.dumps({"schema": schema})
    with context.queue("weft.state.services", persistent=True) as registry:
        legacy_id = registry.write(legacy)
        registry.write(json.dumps(payload))
        routes = tuple(iter_spawn_reserved_routes(context))
        assert f"T{manager_tid}.reserved" in routes
        assert registry.peek_one(exact_timestamp=legacy_id) == legacy


@pytest.mark.parametrize("newer", ["noncanonical", "malformed"])
def test_reserved_custody_folds_latest_valid_owner_before_canonical_filter(
    weft_harness: WeftTestHarness,
    newer: str,
) -> None:
    """A newer valid route supersedes old authority; malformed data cannot."""
    context = weft_harness.context
    owner = "1776000000000000001"
    reserved = f"T{owner}.reserved"
    payload = build_manager_service_payload(
        context=context,
        tid=owner,
        name="manager",
        status="stopped",
        queues={"requests": WEFT_SPAWN_REQUESTS_QUEUE},
        runtime_handle={},
    )
    with context.queue(WEFT_SPAWN_REQUESTS_QUEUE) as requests:
        tid = str(requests.write("retained request"))
        assert requests.move_one(reserved, exact_timestamp=int(tid)) is not None
    with context.queue("weft.state.services") as registry:
        registry.write(json.dumps(payload))
        update = json.loads(json.dumps(payload))
        if newer == "noncanonical":
            update["queues"]["requests"] = "other.requests"
        else:
            update["name"] = None
        registry.write(json.dumps(update))
    custody = observe_spawn_request_custody(
        context, tid, reserved_routes=iter_spawn_reserved_routes(context)
    )
    assert custody.state == ("absent" if newer == "noncanonical" else "reserved")
    with context.queue(reserved) as queue:
        assert queue.peek_one(exact_timestamp=int(tid)) == "retained request"


@pytest.mark.parametrize("lane", ["ordinary", "internal"])
@pytest.mark.parametrize("reserved", [False, True])
def test_custody_reads_only_exact_pending_id_and_defers_registry(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    lane: str,
    reserved: bool,
) -> None:
    """Backlog and claimed residue cannot replace the requested pending row."""
    context = weft_harness.context
    source = WEFT_SPAWN_REQUESTS_QUEUE if lane == "ordinary" else "weft.spawn.internal"
    suffix = "reserved" if lane == "ordinary" else "internal_reserved"
    owner = "1776000000000000001"
    route = f"T{owner}.{suffix}"
    with context.queue(source) as queue:
        tid = str(queue.write("wanted"))
        for _ in range(40):
            queue.write("distractor")
        if reserved:
            assert queue.move_one(route, exact_timestamp=int(tid)) is not None
    payload = build_manager_service_payload(
        context=context,
        tid=owner,
        name="manager",
        status="superseded",
        queues={"requests": WEFT_SPAWN_REQUESTS_QUEUE},
        runtime_handle={},
    )
    with context.queue("weft.state.services") as registry:
        registry.write(json.dumps(payload))
    calls: list[str] = []
    with context.broker() as broker:
        original_peek = type(broker).peek_one
        original_scan = type(broker).peek_generator
        original_many = type(broker).peek_many

        def exact_peek(db: Any, queue: str, **kwargs: Any) -> Any:
            if queue != "weft.state.services":
                assert kwargs.get("exact_timestamp") == int(tid)
                calls.append(queue)
            return original_peek(db, queue, **kwargs)

        def registry_scan(db: Any, queue: str, **kwargs: Any) -> Any:
            assert queue == "weft.state.services", "custody must never scan requests"
            assert reserved, "source hit must not acquire registry evidence"
            return original_scan(db, queue, **kwargs)

        def registry_many(db: Any, queue: str, *args: Any, **kwargs: Any) -> Any:
            assert queue == "weft.state.services", "custody must never batch requests"
            return original_many(db, queue, *args, **kwargs)

        with monkeypatch.context() as patches:
            patches.setattr(type(broker), "peek_one", exact_peek)
            patches.setattr(type(broker), "peek_generator", registry_scan)
            patches.setattr(type(broker), "peek_many", registry_many)
            custody = observe_spawn_request_custody(
                context,
                tid,
                reserved_routes=iter_spawn_reserved_routes(context, broker=broker),
                broker=broker,
            )
        assert custody.state == ("reserved" if reserved else "queued")
        expected_calls = (
            (3 if lane == "ordinary" else 4)
            if reserved
            else (1 if lane == "ordinary" else 2)
        )
        assert len(calls) == expected_calls
        location = route if reserved else source
        assert broker.claim_one(location, exact_timestamp=int(tid)) is not None
        claimed = observe_spawn_request_custody(
            context,
            tid,
            reserved_routes=iter_spawn_reserved_routes(context, broker=broker),
            broker=broker,
        )
        assert claimed.state == "absent"
        # Borrowed connection ownership survives both lookup and exhausted routes.
        assert broker.write("borrowed.connection.still.usable", "ok") > 0


@pytest.mark.parametrize("borrowed", [False, True])
@pytest.mark.parametrize("future_claimed", [False, True])
def test_private_reserved_routes_preserve_bootstrap_schema_rules(
    weft_harness: WeftTestHarness,
    borrowed: bool,
    future_claimed: bool,
) -> None:
    """Private recovery rejects future rows, even claimed, before deleting v1."""
    context = weft_harness.context
    with context.broker() as broker:
        legacy = json.dumps({"schema": "weft.service_owner.v1"})
        future = json.dumps({"schema": "weft.service_owner.v3"})
        legacy_id = broker.write("weft.state.services", legacy)
        future_id = broker.write("weft.state.services", future)
        if future_claimed:
            assert broker.claim_one("weft.state.services", exact_timestamp=future_id)
        with pytest.raises(ValueError, match="future service-owner schema v3"):
            spawn_submission._reserved_spawn_request_queue_names(
                context, broker=broker if borrowed else None
            )
        assert (
            broker.peek_one(
                "weft.state.services", exact_timestamp=legacy_id, with_timestamps=False
            )
            == legacy
        )
        broker.delete_message_ids("weft.state.services", [future_id])
        assert (
            spawn_submission._reserved_spawn_request_queue_names(
                context, broker=broker if borrowed else None
            )
            == ()
        )
        assert broker.peek_one("weft.state.services", exact_timestamp=legacy_id) is None


@pytest.mark.parametrize("failure_at", ["source", "registry"])
def test_custody_propagates_broker_failures_without_closing_borrowed_connection(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    failure_at: str,
) -> None:
    """An unavailable evidence read is an error, never absent custody."""
    context = weft_harness.context
    with context.broker() as broker:
        operation = "peek_one" if failure_at == "source" else "peek_generator"
        original = getattr(type(broker), operation)

        def fail(db: Any, queue: str, *args: Any, **kwargs: Any) -> Any:
            if (failure_at == "source" and queue == WEFT_SPAWN_REQUESTS_QUEUE) or (
                failure_at == "registry" and queue == "weft.state.services"
            ):
                raise OSError("evidence read unavailable")
            return original(db, queue, *args, **kwargs)

        with monkeypatch.context() as patches:
            patches.setattr(type(broker), operation, fail)
            with pytest.raises(OSError, match="evidence read unavailable"):
                observe_spawn_request_custody(
                    context,
                    "1777000000000000821",
                    reserved_routes=iter_spawn_reserved_routes(context, broker=broker),
                    broker=broker,
                )
        assert broker.write("borrowed.after.failure", "ok") > 0
