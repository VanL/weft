"""Child spawn envelope custody (Spec: [MF-1], [MF-6], [MA-1.8])."""

from __future__ import annotations

import json
import multiprocessing
from collections.abc import Iterator
from multiprocessing.connection import Connection
from typing import Any

import pytest

from tests.helpers.reactor_driver import drive_until
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import (
    QUEUE_INTERNAL_RESERVED_SUFFIX,
    QUEUE_RESERVED_SUFFIX,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    resolve_runtime_config,
)
from weft.core.manager import Manager
from weft.core.manager_runtime import build_manager_spec
from weft.core.runtime_identity import (
    RuntimeIdentity,
    canonical_runtime_context,
    install_runtime_identity,
)
from weft.core.spawn_requests import submit_spawn_request
from weft.core.taskspec import TaskSpec
from weft.helpers import iter_queue_json_entries

pytestmark = [pytest.mark.shared, pytest.mark.timeout(60)]


@pytest.fixture
def validating_manager(weft_harness: WeftTestHarness) -> Iterator[Manager]:
    """Run the real reservation/validation/disposition owners without services."""
    context = weft_harness.context
    with context.queue("child-validation-fixture-ids", persistent=True) as ids:
        tid = str(ids.generate_timestamp())
    payload = build_manager_spec(context, tid, idle_timeout_override=0.0).model_dump(
        mode="json"
    )
    payload["spec"]["reserved_policy_on_error"] = "clear"
    spec = TaskSpec.model_validate(payload, context={"auto_expand": False})
    config = resolve_runtime_config(
        {
            **dict(context.config),
            "ADMISSION_MAX_CONNECTIONS": 0,
            "TASK_MONITOR_ENABLED": False,
            "LIVENESS_MONITOR_ENABLED": False,
            "AUTOSTART_TASKS": False,
        }
    )
    manager = Manager(context.broker_target, spec, config=config)
    try:
        yield manager
    finally:
        manager.stop(join=False)
        manager.cleanup()


def _envelope(context_identity: str) -> dict[str, Any]:
    return {
        "taskspec": {
            "name": "validated-child",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
        },
        "inbox_message": "accepted-after-parent-exit",
        "submitted_by": {
            "tid": "1777000000000000123",
            "context": context_identity,
            "root_tid": "1777000000000000123",
            "ancestry_tids": ["1777000000000000123"],
        },
    }


def _assert_rejected_and_cleared(
    manager: Manager,
    harness: WeftTestHarness,
    source: str,
    suffix: str,
    payload: Any,
) -> None:
    """One actual reactor turn must reject and dispose the exact reserved row."""
    context = harness.context
    with context.queue(source, persistent=True) as queue:
        tid = queue.write(json.dumps(payload))
    manager._mark_pending_messages_prechecked()
    manager.process_once()
    with context.queue(source, persistent=True) as queue:
        assert queue.peek_one(exact_timestamp=tid) is None
    with context.queue(f"T{manager.tid}.{suffix}", persistent=True) as reserved:
        assert reserved.peek_one(exact_timestamp=tid) is None
    with context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as log:
        rejected = [
            event
            for event, _timestamp in iter_queue_json_entries(log)
            if event.get("event") == "task_spawn_rejected"
            and event.get("child_tid") == str(tid)
        ]
    assert len(rejected) == 1
    assert isinstance(rejected[0].get("error"), str) and rejected[0]["error"]


@pytest.mark.parametrize(
    "malformation", ["missing", "short-tid", "foreign-context", "extra-key"]
)
def test_child_spawn_requires_a_valid_same_context_submitter(
    validating_manager: Manager,
    weft_harness: WeftTestHarness,
    malformation: str,
) -> None:
    """Malformed runtime lineage cannot bypass canonical rejection/CLEAR."""
    payload = _envelope(canonical_runtime_context(weft_harness.context.broker_target))
    if malformation == "missing":
        payload["submitted_by"].pop("ancestry_tids")
    elif malformation == "short-tid":
        payload["submitted_by"]["tid"] = "123"
    elif malformation == "foreign-context":
        payload["submitted_by"]["context"] = "postgres:another-context"
    else:
        payload["submitted_by"]["parent_tid"] = "1777000000000000456"
    _assert_rejected_and_cleared(
        validating_manager,
        weft_harness,
        WEFT_SPAWN_REQUESTS_QUEUE,
        QUEUE_RESERVED_SUFFIX,
        payload,
    )


@pytest.mark.parametrize(
    ("source", "suffix"),
    [
        (WEFT_SPAWN_REQUESTS_QUEUE, QUEUE_RESERVED_SUFFIX),
        (WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE, QUEUE_INTERNAL_RESERVED_SUFFIX),
    ],
)
def test_primary_and_internal_sources_reject_malformed_lineage(
    validating_manager: Manager,
    weft_harness: WeftTestHarness,
    source: str,
    suffix: str,
) -> None:
    """Both source validators reject a malformed system lineage envelope."""
    payload = _envelope(canonical_runtime_context(weft_harness.context.broker_target))
    payload["submitted_by"]["extra"] = "not-supported"
    _assert_rejected_and_cleared(
        validating_manager,
        weft_harness,
        source,
        suffix,
        payload,
    )


@pytest.mark.parametrize("payload", [[], None], ids=["array", "null"])
def test_child_spawn_nonobject_envelope_is_rejected_and_cleared(
    validating_manager: Manager,
    weft_harness: WeftTestHarness,
    payload: Any,
) -> None:
    """JSON validity alone must not let malformed child requests break the handler."""
    _assert_rejected_and_cleared(
        validating_manager,
        weft_harness,
        WEFT_SPAWN_REQUESTS_QUEUE,
        QUEUE_RESERVED_SUFFIX,
        payload,
    )


def submit_child_then_exit(identity_payload: dict[str, Any], reply: Connection) -> None:
    """A dedicated runtime accepts a child durably and exits without waiting."""
    identity = RuntimeIdentity.from_mapping(identity_payload)
    install_runtime_identity(identity)
    assert identity.broker_target is not None
    try:
        tid = submit_spawn_request(
            identity.broker_target,
            taskspec=_envelope(identity.context)["taskspec"],
            work_payload="accepted-after-parent-exit",
            config=identity.config,
            inherited_weft_context=identity.context_root,
        )
        reply.send(str(tid))
    finally:
        reply.close()


def test_accepted_child_executes_after_its_submitter_process_exits(
    validating_manager: Manager, weft_harness: WeftTestHarness
) -> None:
    """Durable child acceptance survives parent death and needs no live-parent gate."""
    context = weft_harness.context
    identity = RuntimeIdentity(
        tid="1777000000000000123",
        context=canonical_runtime_context(context.broker_target),
        backend_name=context.backend_name,
        broker_target=context.broker_target,
        context_root=str(context.root),
        config=context.config,
    )
    spawn = multiprocessing.get_context("spawn")
    received, sent = spawn.Pipe(duplex=False)
    parent = spawn.Process(
        target=submit_child_then_exit, args=(identity.to_dict(), sent)
    )
    parent.start()
    sent.close()
    try:
        assert received.poll(20.0), "submitter did not return its accepted child TID"
        tid = received.recv()
        parent.join(20.0)
        assert not parent.is_alive() and parent.exitcode == 0
        weft_harness.register_tid(tid)
        with context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=True) as source:
            assert source.peek_one(exact_timestamp=int(tid)) is not None
        with context.queue(f"T{tid}.outbox", persistent=True) as outbox:
            result = drive_until(
                outbox.peek_one,
                lambda body: body is not None,
                step=validating_manager.process_once,
                wait=validating_manager.wait_for_activity,
                timeout=20.0,
                diagnostics=lambda: weft_harness.dump_completion_timeout_state(tid),
            )
        assert result == "accepted-after-parent-exit"
        with context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as log:
            spawned = [
                event
                for event, _timestamp in iter_queue_json_entries(log)
                if event.get("event") == "task_spawned"
                and event.get("child_tid") == tid
            ]
        assert len(spawned) == 1 and spawned[0]["submitter_tid"] == identity.tid
    finally:
        received.close()
        if parent.is_alive():
            parent.terminate()
        parent.join(10.0)
        if parent.is_alive():
            parent.kill()
            parent.join(10.0)
        parent.close()
