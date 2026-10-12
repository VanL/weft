"""Accepted configuration custody at the real submission boundary [MF-5]."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from simplebroker import BrokerSession
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import (
    INTERNAL_RUNTIME_TASK_CLASS_KEY,
    PUBLIC_RESERVED_SERVICE_METADATA_KEYS,
    TASKSPEC_BUNDLE_ROOT_FIELD,
    WEFT_SPAWN_REQUESTS_QUEUE,
)
from weft.commands import submission
from weft.core import manager_runtime
from weft.core.taskspec import TaskSpec

pytestmark = [pytest.mark.shared]


def _ready_manager() -> manager_runtime.ManagerEnsureResult:
    """Keep manager startup outside the configuration-custody boundary."""
    return manager_runtime.ManagerEnsureResult(
        outcome="ready",
        manager_record=None,
        started_here=False,
        process_handle=None,
        reason="custody-test",
    )


@pytest.mark.parametrize("explicit_tid", [None, "1777000000000000821"])
def test_submitted_configuration_matches_committed_shaping_and_routes(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    explicit_tid: str | None,
) -> None:
    """Custody must preserve serialized shaping, not the earlier template.

    Existing receipt tests do not retain configuration. Rebuilding from the
    prepared template would restore stripped runtime authority metadata.
    """
    context = weft_harness.context
    bundle_root = context.root / "custody-bundle"
    bundle_root.mkdir()
    payload: dict[str, Any] = {
        "name": "custody",
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:echo_payload",
            "persistent": True,
            "weft_context": str(context.root),
        },
        "io": {
            "outputs": {"outbox": "custody.custom.output"},
            "control": {
                "ctrl_in": "custody.custom.input-control",
                "ctrl_out": "custody.custom.control",
            },
        },
        "metadata": {
            INTERNAL_RUNTIME_TASK_CLASS_KEY: "not-public-authority",
            PUBLIC_RESERVED_SERVICE_METADATA_KEYS[0]: "not-public-service",
            "caller_note": "retained",
        },
    }
    if explicit_tid is not None:
        payload["tid"] = explicit_tid
    original = TaskSpec.model_validate(
        payload,
        context={"template": explicit_tid is None, "auto_expand": False},
    )
    original.set_bundle_root(bundle_root)
    prepared = submission.prepare_taskspec(original, payload="work")
    monkeypatch.setattr(
        submission,
        "ensure_manager_after_submission",
        lambda *_args, **_kwargs: _ready_manager(),
    )

    outcome = submission._submit_prepared_outcome(context, prepared)
    with context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=True) as queue:
        row = queue.peek_one(
            exact_timestamp=int(outcome.receipt.tid), with_timestamps=True
        )
    assert row is not None
    serialized = json.loads(row[0])["taskspec"]
    accepted = outcome.taskspec
    assert accepted.tid == outcome.receipt.tid == str(row[1])
    if explicit_tid is not None:
        assert accepted.tid == explicit_tid
    assert accepted.spec.persistent is True
    assert accepted.spec.weft_context == str(context.root)
    assert accepted.io.outputs["outbox"] == serialized["io"]["outputs"]["outbox"]
    assert accepted.io.control["ctrl_out"] == serialized["io"]["control"]["ctrl_out"]
    assert accepted.io.inputs["inbox"] == f"T{accepted.tid}.inbox"
    assert accepted.get_bundle_root() == serialized[TASKSPEC_BUNDLE_ROOT_FIELD]
    assert accepted.metadata == serialized["metadata"]
    assert accepted.metadata["caller_note"] == "retained"
    assert INTERNAL_RUNTIME_TASK_CLASS_KEY not in accepted.metadata
    assert PUBLIC_RESERVED_SERVICE_METADATA_KEYS[0] not in accepted.metadata
    assert INTERNAL_RUNTIME_TASK_CLASS_KEY in prepared.taskspec.metadata


def test_binding_failure_keeps_committed_tid_after_connection_and_recovery(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-write resolver fault must not lose acceptance or imply rollback."""
    context = weft_harness.context
    prepared = submission.prepare_taskspec(
        {
            "name": "binding-fault",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
        }
    )
    active = False
    recovered = False
    original_connection = BrokerSession.connection
    original_decode = submission.decode_taskspec_transport_payload
    fault = RuntimeError("accepted configuration binding failed")

    @contextmanager
    def tracked_connection(session: BrokerSession) -> Iterator[Any]:
        nonlocal active
        with original_connection(session) as broker:
            active = True
            try:
                yield broker
            finally:
                active = False

    def ensure(*_args: Any, **_kwargs: Any) -> manager_runtime.ManagerEnsureResult:
        nonlocal recovered
        assert not active
        recovered = True
        return _ready_manager()

    def fail_binding(payload: Any, **kwargs: Any) -> TaskSpec:
        if kwargs.get("resolved_tid") is not None:
            assert recovered and not active
            raise fault
        return original_decode(payload, **kwargs)

    monkeypatch.setattr(BrokerSession, "connection", tracked_connection)
    monkeypatch.setattr(submission, "ensure_manager_after_submission", ensure)
    monkeypatch.setattr(submission, "decode_taskspec_transport_payload", fail_binding)
    with pytest.raises(RuntimeError, match="accepted_tid=") as raised:
        submission._submit_prepared_outcome(context, prepared)
    assert raised.value is fault
    with context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=True) as queue:
        rows = list(queue.peek_generator(with_timestamps=True))
    assert len(rows) == 1
    assert f"accepted_tid={rows[0][1]}" in str(fault)
    assert any(str(rows[0][1]) in note for note in fault.__notes__)
