"""Regression tests for broker-assigned spawn-request IDs [MF-1]."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from simplebroker import Queue
from weft._constants import (
    TASKSPEC_BUNDLE_ROOT_FIELD,
    WEFT_SPAWN_REQUESTS_QUEUE,
)
from weft.core.spawn_requests import (
    _write_spawn_request_with_timestamp,
    submit_spawn_request,
)
from weft.core.taskspec import TaskSpec

pytestmark = [pytest.mark.shared]


def _template_taskspec(bundle_root: Path) -> TaskSpec:
    taskspec = TaskSpec.model_validate(
        {
            "name": "implicit-spawn",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
            "metadata": {},
        },
        context={"template": True, "auto_expand": False},
    )
    taskspec.set_bundle_root(bundle_root)
    return taskspec


def test_implicit_spawn_returns_committed_write_id_without_preallocation(
    weft_harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The durable spawn row allocates and returns its own authoritative TID."""

    context = weft_harness.context
    bundle_root = context.root / "task-bundle"
    bundle_root.mkdir()
    taskspec = _template_taskspec(bundle_root)

    def fail_preallocation(_queue: Queue) -> int:
        raise AssertionError("implicit spawn must not preallocate a timestamp")

    monkeypatch.setattr(Queue, "generate_timestamp", fail_preallocation)

    submitted_tid = submit_spawn_request(
        context.broker_target,
        taskspec=taskspec,
        work_payload={"args": ["hello"]},
        config=context.broker_config,
        inherited_weft_context=str(context.root),
    )

    queue = context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=False)
    try:
        row = queue.peek_one(with_timestamps=True)
    finally:
        queue.close()

    assert row is not None
    body, message_id = row
    assert submitted_tid == message_id
    payload = json.loads(body)
    assert payload["taskspec"]["tid"] is None
    assert payload["taskspec"][TASKSPEC_BUNDLE_ROOT_FIELD] == str(bundle_root)
    assert payload["taskspec"]["spec"]["weft_context"] == str(context.root)


def test_explicit_spawn_keeps_supplied_exact_id(weft_harness) -> None:
    """Callers that already own a TID retain exact-ID insertion semantics."""

    context = weft_harness.context
    explicit_tid = 1777000000000000123

    submitted_tid = submit_spawn_request(
        context.broker_target,
        taskspec={
            "name": "explicit-spawn",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
            "metadata": {},
        },
        work_payload=None,
        config=context.broker_config,
        tid=explicit_tid,
        inherited_weft_context=str(context.root),
    )

    queue = context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=False)
    try:
        row = queue.peek_one(with_timestamps=True)
    finally:
        queue.close()

    assert row is not None
    body, message_id = row
    assert submitted_tid == explicit_tid
    assert message_id == explicit_tid
    assert json.loads(body)["taskspec"]["tid"] == str(explicit_tid)


def test_pre_context_spawn_ignores_invalid_ambient_broker_config(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The direct spawn edge builds an isolated config when none is supplied."""

    broker_target, make_queue = broker_env
    monkeypatch.setenv("BROKER_CACHE_MB", "not-an-integer")
    taskspec = _template_taskspec(tmp_path)

    submitted_tid = submit_spawn_request(
        broker_target,
        taskspec=taskspec,
        work_payload=None,
        config=None,
    )

    row = make_queue(WEFT_SPAWN_REQUESTS_QUEUE).peek_one(with_timestamps=True)
    assert row is not None
    assert row[1] == submitted_tid


def test_exact_id_writer_reports_missing_broker_capability_as_runtime_error() -> None:
    with pytest.raises(RuntimeError) as exc_info:
        _write_spawn_request_with_timestamp(
            object(),
            queue_name=WEFT_SPAWN_REQUESTS_QUEUE,
            message="{}",
            timestamp=1777000000000000123,
        )
    assert type(exc_info.value) is RuntimeError
    assert str(exc_info.value) == (
        "exact-ID spawn request writes require a broker connection with "
        "insert_messages support"
    )
