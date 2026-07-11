"""Tests for the shared task-submission write boundary [MF-1]."""

from __future__ import annotations

from typing import Any

import pytest

import weft.commands.submission as submission_mod
from weft.commands.types import PreparedSubmissionRequest
from weft.core.taskspec import TaskSpec, resolve_taskspec_payload

pytestmark = [pytest.mark.shared]


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

    monkeypatch.setattr(submission_mod, "generate_tid", fail_preallocation)
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
