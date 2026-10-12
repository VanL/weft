"""Filtered task history and evidence loans (Spec: [MF-5], [SB-0.4])."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import ExitStack
from typing import Any, cast

import pytest

from simplebroker.ext import BrokerError
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import WEFT_GLOBAL_LOG_QUEUE
from weft.context import WeftContext
from weft.core import task_evidence
from weft.core.tasks.consumer import Consumer
from weft.core.taskspec.transport import validate_taskspec_payload

pytestmark = [pytest.mark.shared]


@pytest.fixture
def history_broker(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[WeftContext, Any, pytest.MonkeyPatch]]:
    """Keep real broker operations; retire read-fault hooks before cleanup."""
    with (
        WeftTestHarness() as harness,
        harness.context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as queue,
        queue.get_connection() as broker,
        monkeypatch.context() as fault_patch,
    ):
        yield harness.context, broker, fault_patch


def _track_transferred_bodies(
    broker: Any, monkeypatch: pytest.MonkeyPatch
) -> list[str]:
    transferred: list[str] = []
    broker_type = type(broker)
    original_generator = broker_type.peek_generator
    original_one = broker_type.peek_one

    def generator(self: Any, queue: str, **kwargs: Any) -> Iterator[Any]:
        source = original_generator(self, queue, **kwargs)
        try:
            for row in source:
                if queue == WEFT_GLOBAL_LOG_QUEUE:
                    transferred.append(str(row[0] if isinstance(row, tuple) else row))
                yield row
        finally:
            source.close()

    def one(self: Any, queue: str, **kwargs: Any) -> Any:
        row = original_one(self, queue, **kwargs)
        if queue == WEFT_GLOBAL_LOG_QUEUE and row is not None:
            transferred.append(str(row[0] if isinstance(row, tuple) else row))
        return row

    monkeypatch.setattr(broker_type, "peek_generator", generator)
    monkeypatch.setattr(broker_type, "peek_one", one)
    return transferred


def test_bounded_terminal_evidence_fetches_only_matching_candidate_bodies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ordinary unrelated task bodies must stay at the broker filter boundary."""
    with WeftTestHarness() as harness, ExitStack() as producers:
        ctx = harness.context
        consumers: list[Consumer] = []
        with ctx.queue("test.history.identities") as allocator:
            for name in ("target", "unrelated"):
                spec = validate_taskspec_payload(
                    {
                        "name": name,
                        "spec": {
                            "type": "function",
                            "function_target": "tests.tasks.sample_targets:echo_payload",
                            "weft_context": str(ctx.root),
                            "cleanup_on_exit": False,
                            "enable_process_title": False,
                        },
                    },
                    resolved_tid=str(allocator.generate_timestamp()),
                )
                consumer = Consumer(ctx.broker_target, spec, config=ctx.broker_config)
                producers.enter_context(consumer.drive_scope())
                consumers.append(consumer)
        target, unrelated = consumers
        # The actual writer escapes a literal backslash in ordinary user text.
        # That text is not an alternate encoding of the target's identity.
        unrelated._report_state_change(event="task_activity", note=r"\u0031")
        target.run_work_item("history result")
        with (
            ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as owner,
            owner.get_connection() as broker,
            monkeypatch.context() as patch,
        ):
            target_rows = [
                (body, message_id)
                for body, message_id in cast(
                    Iterator[tuple[str, int]],
                    broker.peek_generator(WEFT_GLOBAL_LOG_QUEUE, with_timestamps=True),
                )
                if json.loads(body).get("tid") == target.tid
            ]
            transferred = _track_transferred_bodies(broker, patch)
            fold = task_evidence.bounded_log_terminal_evidence(
                ctx, tid=target.tid, broker=broker
            )
            assert fold.terminal is not None and fold.terminal.status == "completed"
            assert fold.terminal.observed_at == target_rows[-1][1]
            assert fold.latest_live_at is not None
            assert transferred == [body for body, _message_id in target_rows]
            # A later diagnostic is real writer output but cannot replace the
            # completed lifecycle. Only the newly acquired suffix is folded.
            unrelated._report_state_change(event="task_activity", note="later")
            target._report_state_change(event="task_activity", note="after completion")
            rows, _horizon = task_evidence.read_task_history(
                ctx, target.tid, broker=broker, after_timestamp=target_rows[-1][1]
            )
            assert rows
            updated = fold.fold(rows, tid=target.tid)
            assert updated.terminal == fold.terminal
            assert updated.latest_live_at == fold.latest_live_at


def test_bounded_terminal_evidence_includes_imported_history_older_than_tid(
    history_broker: tuple[WeftContext, Any, pytest.MonkeyPatch],
) -> None:
    """A task ID is identity, not an earliest retained-history timestamp."""
    ctx, broker, _patch = history_broker
    tid = str(broker.generate_timestamp())
    imported_id = int(tid) - 1_000_000
    broker.insert_messages(
        [
            (
                WEFT_GLOBAL_LOG_QUEUE,
                json.dumps({"tid": tid, "status": "failed"}),
                imported_id,
            )
        ]
    )
    fold = task_evidence.bounded_log_terminal_evidence(ctx, tid=tid, broker=broker)
    assert fold.terminal is not None and fold.terminal.status == "failed"
    assert fold.terminal.observed_at == imported_id


def test_history_reader_pages_canonical_candidates_and_exact_top_level_identity(
    history_broker: tuple[WeftContext, Any, pytest.MonkeyPatch],
) -> None:
    """A matching row beyond several pages survives nested and malformed hits."""
    ctx, broker, patch = history_broker
    tid = str(broker.generate_timestamp())
    base = int(tid) - 10_000
    canonical = json.dumps({"tid": tid, "index": "canonical"})
    with_text = json.dumps({"tid": tid, "note": r"\u0031", "index": "text"})
    candidates = [
        json.dumps({"nested": {"tid": tid}, "index": index}) for index in range(1_005)
    ]
    candidates += [
        canonical,
        with_text,
        json.dumps({"tid": tid + "0"}),
        json.dumps({"tid": int(tid)}),
        json.dumps([tid]),
        '{"tid":"' + tid + '"',
    ]
    broker.insert_messages(
        [
            (WEFT_GLOBAL_LOG_QUEUE, body, base + index)
            for index, body in enumerate(candidates)
        ]
    )
    transferred = _track_transferred_bodies(broker, patch)
    rows, horizon = task_evidence.read_task_history(ctx, tid, broker=broker)
    assert [timestamp for _payload, timestamp in rows] == [base + 1_005, base + 1_006]
    assert [payload["index"] for payload, _timestamp in rows] == ["canonical", "text"]
    assert horizon == base + len(candidates) - 1
    assert transferred == candidates


def test_history_reader_retains_zero_match_progress_and_empty_queue_horizon(
    history_broker: tuple[WeftContext, Any, pytest.MonkeyPatch],
) -> None:
    ctx, broker, patch = history_broker
    tid = str(broker.generate_timestamp())
    unrelated_id = broker.write(
        WEFT_GLOBAL_LOG_QUEUE, json.dumps({"tid": "1780000000000000001"})
    )
    transferred = _track_transferred_bodies(broker, patch)
    rows, horizon = task_evidence.read_task_history(ctx, tid, broker=broker)
    assert rows == [] and horizon == unrelated_id
    assert transferred == []
    rows, repeated = task_evidence.read_task_history(
        ctx, tid, broker=broker, after_timestamp=horizon
    )
    assert rows == [] and repeated == horizon
    broker.delete_message_ids(WEFT_GLOBAL_LOG_QUEUE, [unrelated_id])
    rows, empty = task_evidence.read_task_history(
        ctx, tid, broker=broker, after_timestamp=horizon
    )
    assert rows == [] and empty == horizon
    assert transferred == []


def test_history_reader_excludes_append_after_captured_horizon_until_next_read(
    history_broker: tuple[WeftContext, Any, pytest.MonkeyPatch],
) -> None:
    ctx, broker, patch = history_broker
    tid = str(broker.generate_timestamp())
    first_id = broker.write(WEFT_GLOBAL_LOG_QUEUE, json.dumps({"tid": tid, "index": 1}))
    original_latest = type(broker).latest_pending_timestamp
    appended: list[int] = []

    def latest(self: Any, queue: str) -> int | None:
        horizon = cast(int | None, original_latest(self, queue))
        if not appended:
            appended.append(self.write(queue, json.dumps({"tid": tid, "index": 2})))
        return horizon

    patch.setattr(type(broker), "latest_pending_timestamp", latest)
    rows, horizon = task_evidence.read_task_history(ctx, tid, broker=broker)
    assert [timestamp for _payload, timestamp in rows] == [first_id]
    assert horizon == first_id
    rows, horizon = task_evidence.read_task_history(
        ctx, tid, broker=broker, after_timestamp=horizon
    )
    assert [timestamp for _payload, timestamp in rows] == appended
    assert horizon == appended[0]


def test_history_reader_disappearing_candidate_advances_without_global_replay(
    history_broker: tuple[WeftContext, Any, pytest.MonkeyPatch],
) -> None:
    ctx, broker, patch = history_broker
    tid = str(broker.generate_timestamp())
    candidate_id = broker.write(WEFT_GLOBAL_LOG_QUEUE, json.dumps({"tid": tid}))
    original_one = type(broker).peek_one

    def one(self: Any, queue: str, **kwargs: Any) -> Any:
        if queue == WEFT_GLOBAL_LOG_QUEUE:
            self.delete_message_ids(queue, [candidate_id])
        return original_one(self, queue, **kwargs)

    def no_replay(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("disappearing candidate must not trigger whole-log replay")

    patch.setattr(type(broker), "peek_one", one)
    patch.setattr(type(broker), "peek_generator", no_replay)
    rows, horizon = task_evidence.read_task_history(ctx, tid, broker=broker)
    assert rows == [] and horizon == candidate_id


def test_history_reader_never_overflows_greatest_signed_horizon(
    history_broker: tuple[WeftContext, Any, pytest.MonkeyPatch],
) -> None:
    ctx, broker, patch = history_broker
    tid = "1780000000000000099"
    last_id = (1 << 63) - 1
    message_id = broker.write(WEFT_GLOBAL_LOG_QUEUE, json.dumps({"tid": tid}))
    # Exact imports advance allocation above the imported ID, so inserting MAX
    # would itself overflow. Control only the external horizon probe; real
    # filtered queries must accept the reader's resulting bounds.
    patch.setattr(type(broker), "latest_pending_timestamp", lambda self, queue: last_id)
    rows, horizon = task_evidence.read_task_history(ctx, tid, broker=broker)
    assert rows == [({"tid": tid}, message_id)] and horizon == last_id


@pytest.mark.parametrize("failure_point", ["search", "exact_read"])
def test_history_reader_failure_is_not_partial_or_silent_absence(
    history_broker: tuple[WeftContext, Any, pytest.MonkeyPatch],
    failure_point: str,
) -> None:
    ctx, broker, patch = history_broker
    tid = str(broker.generate_timestamp())
    broker.write(WEFT_GLOBAL_LOG_QUEUE, json.dumps({"tid": tid}))
    failure = BrokerError("task history read failed")

    def exact(*args: Any, **kwargs: Any) -> Any:
        raise failure

    if failure_point == "search":
        patch.setattr(type(broker), "find_message_ids", exact)
    else:
        patch.setattr(type(broker), "peek_one", exact)
    with pytest.raises(BrokerError):
        task_evidence.read_task_history(ctx, tid, broker=broker)


@pytest.mark.parametrize("loan", ["empty", "terminal"])
def test_known_tid_evidence_uses_acquired_fold_without_rereading_history(
    history_broker: tuple[WeftContext, Any, pytest.MonkeyPatch],
    loan: str,
) -> None:
    ctx, broker, patch = history_broker
    tid = str(broker.generate_timestamp())
    broker.write(WEFT_GLOBAL_LOG_QUEUE, json.dumps({"tid": tid, "status": "failed"}))
    outbox_id = broker.write(f"T{tid}.outbox", "completed output")

    def no_history(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("acquired task events must not trigger history rediscovery")

    patch.setattr(task_evidence, "read_task_history", no_history)
    task_events = (
        [] if loan == "empty" else [({"tid": tid, "status": "cancelled"}, outbox_id)]
    )
    evidence = task_evidence.known_tid_evidence(
        ctx,
        tid=tid,
        broker=broker,
        log_evidence=task_evidence.LogFold().fold(task_events, tid=tid),
    )
    assert evidence is not None
    assert evidence.status == ("completed" if loan == "empty" else "cancelled")
    assert evidence.source == ("outbox" if loan == "empty" else "log")
