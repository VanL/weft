"""Exact primary ownership and cursor holes [MF-6], [OBS.6b]."""

from __future__ import annotations

import json
import time

import pytest

import tests.core.test_admission_recovery as recovery_fixtures
import tests.core.test_child_spawn_validation as validation_fixtures
from tests.helpers.weft_harness import WeftTestHarness
from weft.core.manager import Manager
from weft.core.manager_runtime import build_manager_spec
from weft.core.spawn_requests import submit_spawn_request
from weft.core.taskspec import ReservedPolicy

pytestmark = pytest.mark.shared
recovery_manager = recovery_fixtures.recovery_manager
validating_manager = validation_fixtures.validating_manager


def _accept(harness: WeftTestHarness, *, tid: int | None = None) -> int:
    return submit_spawn_request(
        harness.context.broker_target,
        taskspec={
            "name": "accepted",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
        },
        work_payload=None,
        tid=tid,
        config=harness.context.config,
    )


def test_two_managers_exact_reservation_is_exclusive_and_missed_hint_disappears(
    recovery_manager: Manager, weft_harness: WeftTestHarness
) -> None:
    first = recovery_manager
    ctx = weft_harness.context
    with ctx.queue("race.ids", persistent=True) as ids:
        peer_tid = str(ids.generate_timestamp())
    peer = Manager(
        ctx.broker_target,
        build_manager_spec(ctx, peer_tid, idle_timeout_override=0.0),
        config=first._manager_context().config,
    )
    try:
        request_id = _accept(weft_harness)
        for task in (first, peer):
            task._refresh_scheduler_evidence()
            task._classify_family_requests()
            assert task._family_scheduler.pending(request_id) is not None
        candidate = peer._family_scheduler.pending(request_id)
        assert candidate is not None
        first_config = first._queues[first._queue_names["inbox"]]
        peer_config = peer._queues[peer._queue_names["inbox"]]
        assert first._fetch_next_message(first_config) is not None
        assert peer._fetch_next_message(peer_config) is None
        assert peer._family_scheduler.pending(request_id) is None
        with ctx.queue(candidate.queue_name, persistent=True) as hints:
            assert hints.peek_one() is None
        with ctx.queue(first._queue_names["reserved"], persistent=True) as reserved:
            assert reserved.peek_one(exact_timestamp=request_id) is not None
        with ctx.queue(peer._queue_names["reserved"], persistent=True) as reserved:
            assert reserved.peek_one(exact_timestamp=request_id) is None
        # A separate accepted row remains available to the losing Manager.
        other_id = _accept(weft_harness)
        peer._classify_family_requests()
        row = peer._fetch_next_message(peer_config)
        assert row is not None and row[1] == other_id
    finally:
        peer.stop(join=False)
        peer.cleanup()


def test_old_explicit_tid_and_reserved_return_rejoin_without_watermark_loss(
    recovery_manager: Manager, weft_harness: WeftTestHarness
) -> None:
    manager = recovery_manager
    manager._refresh_scheduler_evidence()
    newest = _accept(weft_harness)
    manager._classify_family_requests()
    old = _accept(weft_harness, tid=newest - 100000)
    manager._family_repair_due = 0.0
    manager._classify_family_requests()
    assert manager._family_scheduler.pending(old) is not None
    config = manager._queues[manager._queue_names["inbox"]]
    row = manager._fetch_next_message(config)
    assert row is not None and row[1] == old
    manager._apply_spawn_reserved_policy(
        ReservedPolicy.REQUEUE,
        reserved_queue=manager._queue_names["reserved"],
        source_queue=manager._queue_names["inbox"],
        message_timestamp=old,
    )
    assert manager._family_scheduler.pending(old) is not None
    row = manager._fetch_next_message(config)
    assert row is not None and row[1] == old
    assert manager._family_scheduler.pending(newest) is not None


@pytest.mark.parametrize("bad", ["{", "[]", json.dumps({"spec": {"type": "bogus"}})])
def test_invalid_primary_settles_without_using_frontier_or_launching_valid_work(
    validating_manager: Manager, weft_harness: WeftTestHarness, bad: str
) -> None:
    manager = validating_manager
    source = manager._queue(manager._queue_names["inbox"])
    invalid_id = int(source.write(bad))
    valid_id = _accept(weft_harness)
    manager._refresh_scheduler_evidence()
    manager._classify_family_requests()
    # An unavailable observation closes valid launch admission, not rejection.
    manager._admission_blocked_lanes = {"public"}
    manager._admission_retry_after_ns = time.time_ns() + 1000000000
    assert manager._process_queue_message(source.name, set())
    assert source.peek_one(exact_timestamp=invalid_id) is None
    assert source.peek_one(exact_timestamp=valid_id) is not None
    assert not manager._family_scheduler.frontiers
    assert not manager._active_child_launches
    with weft_harness.context.queue(
        manager._queue_names["reserved"], persistent=True
    ) as reserved:
        assert reserved.peek_one(exact_timestamp=invalid_id) is None


def test_full_frontiers_do_not_mistake_nonempty_primary_for_idle_gap(
    recovery_manager: Manager, weft_harness: WeftTestHarness
) -> None:
    """No eligible launch is not proof that a blocked drain episode ended."""
    manager = recovery_manager
    manager._refresh_scheduler_evidence()
    accepted = _accept(weft_harness)
    manager._classify_family_requests()
    for offset in range(
        int(manager._manager_context().config["SCHEDULER_MAX_FRONTIERS"])
    ):
        tid = str(accepted - offset - 1)
        manager._family_scheduler.recover(tid, tid, ())
    first = manager._admission_controller.evaluate(
        used=None, backend="postgres", now=100.0
    )
    assert first.wait_phase == "waiting"
    assert manager._peek_family_candidate() is None
    assert not manager._has_pending_messages()
    later = manager._admission_controller.evaluate(
        used=None, backend="postgres", now=140.0
    )
    assert later.wait_phase == "stalled" and later.elapsed_seconds == 40.0
