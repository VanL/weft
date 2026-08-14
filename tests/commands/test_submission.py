"""Tests for the shared task-submission write boundary [MF-1]."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

import weft.commands.submission as submission_mod
from weft._exceptions import CommandUsageError, SubmissionValidationError
from weft.commands._spawn_submission import SpawnSubmissionReconciliation
from weft.commands.types import PreparedSubmissionRequest
from weft.core import manager_runtime as core_manager_runtime
from weft.core.taskspec import TaskSpec, resolve_taskspec_payload

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
    payload["name"] = "task-" + request.arguments["provider"]
    return payload


def capture_run_input_context(request: Any) -> dict[str, Any]:
    """Expose the resolved adapter context for seam-order assertions."""

    return {"context_root": request.context_root}


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
    weft_harness,
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
    weft_harness,
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
    weft_harness,
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
    weft_harness,
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
    weft_harness,
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
    weft_harness,
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


def test_prepare_spec_rejects_stdin_when_run_input_declares_no_stdin(
    weft_harness,
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
    weft_harness,
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
    weft_harness,
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
    weft_harness,
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
    weft_harness,
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
    weft_harness,
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
    weft_harness,
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


def test_manager_startup_interfaces_drop_inert_verbose_parameter() -> None:
    assert (
        "verbose"
        not in inspect.signature(core_manager_runtime.start_manager).parameters
    )
    assert (
        "verbose"
        not in inspect.signature(core_manager_runtime.ensure_manager).parameters
    )
    assert (
        "verbose"
        not in inspect.signature(
            submission_mod.ensure_manager_after_submission
        ).parameters
    )


def test_prepare_taskspec_drops_inert_context_parameter() -> None:
    assert (
        "context" not in inspect.signature(submission_mod.prepare_taskspec).parameters
    )


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
    assert str(exc_info.value) == "TaskSpec spec section must be a mapping"
    assert exc_info.value.__cause__ is None


def test_plain_submission_name_does_not_require_endpoint_syntax(
    weft_harness,
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


def test_ensure_manager_reconciles_ordinary_startup_failure_as_spawned(
    weft_harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-enqueue startup failure yields to durable spawned evidence."""

    context = weft_harness.context
    tid = "1777000000000000789"
    startup_error = ManagerStartupFailure("startup detail")
    calls: list[str] = []

    def fail_startup(_context: object) -> object:
        calls.append("ensure")
        raise startup_error

    def reconcile(_context: object, submitted_tid: str, **kwargs: object) -> object:
        assert kwargs == {}
        calls.append(f"reconcile:{submitted_tid}")
        return SpawnSubmissionReconciliation(outcome="spawned", tid=submitted_tid)

    monkeypatch.setattr(submission_mod, "reconcile_submitted_spawn", reconcile)

    result = submission_mod.ensure_manager_after_submission(
        context,
        submitted_tid=tid,
        ensure_manager_fn=fail_startup,
        delete_spawn_request_fn=lambda *_args, **_kwargs: pytest.fail(
            "spawned evidence must not delete the committed request"
        ),
    )

    assert result == (None, False, None)
    assert calls == ["ensure", f"reconcile:{tid}"]


def test_ensure_manager_rejected_result_preserves_startup_failure_as_cause(
    weft_harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A manager rejection stays primary while retaining the startup failure."""

    context = weft_harness.context
    tid = "1777000000000000790"
    startup_error = ManagerStartupFailure("startup detail")

    def fail_startup(_context: object) -> object:
        raise startup_error

    monkeypatch.setattr(
        submission_mod,
        "reconcile_submitted_spawn",
        lambda _context, submitted_tid: SpawnSubmissionReconciliation(
            outcome="rejected",
            tid=submitted_tid,
            error="manager rejection detail",
        ),
    )

    with pytest.raises(RuntimeError, match="^manager rejection detail$") as exc_info:
        submission_mod.ensure_manager_after_submission(
            context,
            submitted_tid=tid,
            ensure_manager_fn=fail_startup,
        )

    assert exc_info.value.__cause__ is startup_error


def test_ensure_manager_propagates_fatal_startup_signal_without_reconciliation(
    weft_harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BaseException signals bypass the ordinary queue reconciliation policy."""

    context = weft_harness.context
    signal = ManagerStartupSignal()

    def fail_startup(_context: object) -> object:
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
    weft_harness,
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

    def fail_preallocation(_context) -> str:
        raise AssertionError("prepared submission must not preallocate a TID")

    def fake_submit(*args: object, **kwargs: object) -> int:
        captured["submit_args"] = args
        captured["submit_kwargs"] = kwargs
        return committed_id

    def fake_ensure(_context, *, submitted_tid: str | int) -> None:
        captured["reconciled_tid"] = submitted_tid

    monkeypatch.setattr(core_manager_runtime, "generate_tid", fail_preallocation)
    monkeypatch.setattr(submission_mod, "submit_spawn_request", fake_submit)
    monkeypatch.setattr(submission_mod, "ensure_manager_after_submission", fake_ensure)

    receipt = submission_mod.submit_prepared(context, prepared)

    assert captured["submit_kwargs"]["tid"] is None
    assert captured["reconciled_tid"] == str(committed_id)
    assert receipt.tid == str(committed_id)
    assert receipt.context_root == str(context.root)


def test_submit_prepared_keeps_explicit_id_on_exact_insert_path(
    weft_harness,
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

    def fake_ensure(_context, *, submitted_tid: str | int) -> None:
        captured["reconciled_tid"] = submitted_tid

    monkeypatch.setattr(submission_mod, "submit_spawn_request", fake_submit)
    monkeypatch.setattr(submission_mod, "ensure_manager_after_submission", fake_ensure)

    receipt = submission_mod.submit_prepared(context, prepared)

    assert captured["submit_kwargs"]["tid"] == explicit_tid
    assert captured["reconciled_tid"] == explicit_tid
    assert receipt.tid == explicit_tid
