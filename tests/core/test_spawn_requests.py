"""Regression tests for broker-assigned spawn-request IDs [MF-1]."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from simplebroker import BrokerTarget, Queue
from tests.helpers.typing import BrokerEnv
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import (
    INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY,
    INTERNAL_RUNTIME_TASK_CLASS_KEY,
    TASKSPEC_BUNDLE_ROOT_FIELD,
    WEFT_SPAWN_REQUESTS_QUEUE,
)
from weft.core import runtime_identity
from weft.core.spawn_requests import (
    _submit_spawn_request_with_payload,
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
    weft_harness: WeftTestHarness,
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


def test_explicit_spawn_keeps_supplied_exact_id(weft_harness: WeftTestHarness) -> None:
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


@pytest.mark.parametrize("allow_internal_runtime", [False, True])
def test_retained_spawn_transport_matches_serialized_authority_shaping(
    weft_harness: WeftTestHarness, allow_internal_runtime: bool
) -> None:
    """The run caller receives committed transport rather than earlier metadata.

    Internal runtime authority lives only in the request envelope. Existing
    integer-returning tests cannot guard what the richer run boundary retains.
    """
    context = weft_harness.context
    taskspec = _template_taskspec(context.root)
    taskspec.metadata[INTERNAL_RUNTIME_TASK_CLASS_KEY] = "envelope-only-class"
    submitted_tid, retained_payload = _submit_spawn_request_with_payload(
        context.broker_target,
        taskspec=taskspec,
        work_payload=None,
        config=context.broker_config,
        inherited_weft_context=str(context.root),
        allow_internal_runtime=allow_internal_runtime,
    )
    with context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=True) as queue:
        row = queue.peek_one(exact_timestamp=submitted_tid, with_timestamps=True)
    assert row is not None and row[1] == submitted_tid
    request = json.loads(row[0])
    assert retained_payload == request["taskspec"]
    assert INTERNAL_RUNTIME_TASK_CLASS_KEY not in retained_payload["metadata"]
    if allow_internal_runtime:
        assert (
            request[INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY] == "envelope-only-class"
        )
    else:
        assert INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY not in request
    assert taskspec.metadata[INTERNAL_RUNTIME_TASK_CLASS_KEY] == "envelope-only-class"


def test_pre_context_spawn_ignores_invalid_ambient_broker_config(
    broker_env: BrokerEnv,
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


@pytest.mark.parametrize("explicit_tid", [None, 1777000000000000234])
def test_runtime_origin_routes_same_context_child_with_submitter_envelope(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    explicit_tid: int | None,
) -> None:
    """Only runtime origin selects child capacity, for both ID write paths."""
    context = weft_harness.context
    identity = runtime_identity.RuntimeIdentity(
        tid="1777000000000000123",
        context=runtime_identity.canonical_runtime_context(context.broker_target),
        backend_name=context.backend_name,
    )
    monkeypatch.setattr(runtime_identity, "_runtime_identity", identity)
    submitted_tid = submit_spawn_request(
        context.broker_target,
        taskspec=_template_taskspec(context.root),
        work_payload="child",
        config=context.broker_config,
        tid=explicit_tid,
        inherited_weft_context=str(context.root),
    )
    with context.queue("weft.spawn.children", persistent=True) as child_queue:
        row = child_queue.peek_one(exact_timestamp=submitted_tid, with_timestamps=True)
    assert row is not None
    assert row[1] == submitted_tid
    request = json.loads(row[0])
    assert request["submitted_by"] == {"tid": identity.tid, "context": identity.context}
    assert request["inbox_message"] == "child"
    with context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=True) as root_queue:
        assert root_queue.peek_one(exact_timestamp=submitted_tid) is None


@pytest.mark.parametrize("origin", ["external-ancestry", "foreign", "internal"])
def test_submission_origin_preserves_root_and_explicit_internal_routing(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    origin: str,
) -> None:
    """Ancestry cannot buy child capacity; internal routing keeps precedence."""
    context = weft_harness.context
    identity = None
    if origin != "external-ancestry":
        foreign_target = (
            str(context.root / "foreign.db")
            if context.backend_name == "sqlite"
            else BrokerTarget(
                backend_name=context.backend_name,
                target=context.broker_target.target,
                backend_options={
                    **context.broker_target.backend_options,
                    "schema": "weft_foreign_context",
                },
            )
        )
        identity = runtime_identity.RuntimeIdentity(
            tid="1777000000000000123",
            context=(
                runtime_identity.canonical_runtime_context(foreign_target)
                if origin == "foreign"
                else runtime_identity.canonical_runtime_context(context.broker_target)
            ),
            backend_name=context.backend_name,
        )
    monkeypatch.setattr(runtime_identity, "_runtime_identity", identity)
    taskspec = _template_taskspec(context.root)
    taskspec.metadata["parent_tid"] = "1777000000000000456"
    taskspec.metadata["submitted_by"] = {"tid": "1777000000000000123"}
    queue_name = (
        "weft.spawn.internal" if origin == "internal" else WEFT_SPAWN_REQUESTS_QUEUE
    )
    submitted_tid = submit_spawn_request(
        context.broker_target,
        taskspec=taskspec,
        work_payload="root-or-internal",
        config=context.broker_config,
        spawn_queue_name=queue_name,
    )
    with context.queue(queue_name, persistent=True) as queue:
        row = queue.peek_one(exact_timestamp=submitted_tid)
    assert row is not None
    request = json.loads(row)
    assert "submitted_by" not in request
    assert request["taskspec"]["metadata"]["parent_tid"] == "1777000000000000456"
    with context.queue("weft.spawn.children", persistent=True) as child_queue:
        assert child_queue.peek_one(exact_timestamp=submitted_tid) is None
