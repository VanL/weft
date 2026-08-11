"""Tests for the shared task-submission write boundary [MF-1]."""

from __future__ import annotations

import inspect
from typing import Any

import pytest

import weft.commands.submission as submission_mod
from weft.commands._spawn_submission import SpawnSubmissionReconciliation
from weft.commands.types import PreparedSubmissionRequest
from weft.core import manager_runtime as core_manager_runtime
from weft.core.taskspec import TaskSpec, resolve_taskspec_payload

pytestmark = [pytest.mark.shared]


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
