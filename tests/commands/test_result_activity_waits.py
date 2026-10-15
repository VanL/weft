"""Result observers wait only on activity and original deadlines [MF-5]."""

from __future__ import annotations

import json
import sys
import threading
from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import WEFT_GLOBAL_LOG_QUEUE
from weft._exceptions import CommandTimeoutError
from weft.client import WeftClient
from weft.client._task import _task_with_configuration
from weft.commands import events
from weft.commands.result import _iter_task_result
from weft.commands.types import TaskEvent, TaskResult
from weft.core import task_evidence
from weft.core.queue_wait import QueueChangeMonitor
from weft.core.task_state import task_state_queue_name
from weft.core.tasks.consumer import Consumer
from weft.core.taskspec import TaskSpec
from weft.core.taskspec.transport import validate_taskspec_payload
from weft.helpers import closing_queue_iterator

pytestmark = [pytest.mark.shared, pytest.mark.timeout(30)]

STREAM_PREFIX_REGRESSION_FRAME_COUNT = 501
"""Minimum frame count proving the historical 500-frame prefix cap is gone."""


@pytest.mark.parametrize("surface", ["realtime", "raw"])
def test_idle_event_wait_uses_actual_caller_deadline(
    monkeypatch: pytest.MonkeyPatch, surface: str
) -> None:
    """The first idle wait is the whole supplied budget, not a probe tick."""
    waits: list[float | None] = []

    def wait(_monitor: QueueChangeMonitor, timeout: float | None) -> bool:
        waits.append(timeout)
        assert timeout is not None and 8 < timeout <= 10
        raise RuntimeError("stop after observing the real wait budget")

    with WeftTestHarness() as harness, WeftClient(harness.context) as client:
        with harness.context.queue("test.idle.tid") as queue:
            tid = str(queue.generate_timestamp())
        spec = validate_taskspec_payload(
            {
                "name": "idle",
                "spec": {"type": "function", "function_target": "builtins:dict"},
            },
            resolved_tid=tid,
        )
        task = _task_with_configuration(client, tid, harness.context, spec)
        monkeypatch.setattr(QueueChangeMonitor, "wait", wait)
        iterator = (
            task.realtime_events(timeout=10)
            if surface == "realtime"
            else events.iter_task_events(harness.context, tid, follow=True, timeout=10)
        )
        with pytest.raises(RuntimeError, match="stop after observing"):
            list(iterator)
    assert len(waits) == 1


def test_foreign_cancel_flag_needs_a_real_queue_wake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An is_set-only flag does not introduce a timer or wake an idle observer."""
    waiting = threading.Event()
    returned = threading.Event()
    cancelled = threading.Event()
    waits: list[float | None] = []
    original_wait = QueueChangeMonitor.wait

    def wait(monitor: QueueChangeMonitor, timeout: float | None) -> bool:
        waits.append(timeout)
        waiting.set()
        try:
            return original_wait(monitor, timeout)
        finally:
            returned.set()

    with WeftTestHarness() as harness, WeftClient(harness.context) as client:
        with harness.context.queue("test.foreign.tid") as queue:
            tid = str(queue.generate_timestamp())
        spec = validate_taskspec_payload(
            {
                "name": "idle",
                "spec": {"type": "function", "function_target": "builtins:dict"},
            },
            resolved_tid=tid,
        )
        task = _task_with_configuration(client, tid, harness.context, spec)
        monkeypatch.setattr(QueueChangeMonitor, "wait", wait)
        with ThreadPoolExecutor(max_workers=1) as worker:
            future = worker.submit(
                lambda: list(task.realtime_events(cancel_event=cancelled))
            )
            try:
                assert waiting.wait(10)
                assert waits == [None]
                cancelled.set()
                assert not returned.is_set()
            finally:
                cancelled.set()
                with harness.context.queue(spec.io.outputs["outbox"]) as outbox:
                    outbox.write(json.dumps({"wake": True}))
                emitted = future.result(timeout=10)
                assert [event.event_type for event in emitted] == ["snapshot"]
                assert emitted[0].payload["status"] == "unknown"
        assert returned.is_set()


def test_follow_does_not_renew_expired_budget_for_empty_completed_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real completed task with consumed output cannot earn a second grace."""
    with WeftTestHarness() as harness, WeftClient(harness.context) as client:
        harness.ensure_foreground_manager()
        task = client.submit_command(["echo", "done"])
        assert task.result(timeout=20).status == "completed"
        with harness.context.queue(WEFT_GLOBAL_LOG_QUEUE) as log:
            for body, message_id in list(log.peek_generator(with_timestamps=True)):
                if json.loads(body).get("tid") == task.tid:
                    assert log.delete(message_id=message_id)
        owner_thread = threading.get_ident()
        original_wait = QueueChangeMonitor.wait

        def wait(monitor: QueueChangeMonitor, timeout: float | None) -> bool:
            if threading.get_ident() == owner_thread:
                raise AssertionError("follow restarted the expired caller budget")
            return original_wait(monitor, timeout)

        with monkeypatch.context() as patch:
            patch.setattr(QueueChangeMonitor, "wait", wait)
            with pytest.raises(CommandTimeoutError):
                list(task.follow(timeout=0))


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_realtime_cursor_keeps_adjacent_imported_stream_ids(stream: str) -> None:
    """Exclusive cursors must not skip the next legal imported message ID."""
    with WeftTestHarness() as harness, WeftClient(harness.context) as client:
        with harness.context.queue("test.cursor.tid") as queue:
            tid = str(queue.generate_timestamp())
            first_id = queue.generate_timestamp()
        spec = validate_taskspec_payload(
            {
                "name": "imported-frames",
                "spec": {
                    "type": "function",
                    "function_target": "builtins:dict",
                    "persistent": True,
                    "stream_output": True,
                },
            },
            resolved_tid=tid,
        )
        name = (
            spec.io.outputs["outbox"]
            if stream == "stdout"
            else spec.io.control["ctrl_out"]
        )
        task = _task_with_configuration(client, tid, harness.context, spec)
        with harness.context.queue(name) as queue, queue.get_connection() as broker:
            broker.insert_messages(
                [
                    (
                        name,
                        json.dumps(
                            {"type": "stream", "stream": stream, "data": "first"}
                        ),
                        first_id,
                    )
                ]
            )
            iterator = task.realtime_events(timeout=0.5)
            try:
                snapshot = next(iterator)
                assert snapshot.event_type == "snapshot"
                assert snapshot.payload["status"] == "unknown"
                first = next(iterator)
                assert first.event_type == stream and first.payload["data"] == "first"
                broker.insert_messages(
                    [
                        (
                            name,
                            json.dumps(
                                {"type": "stream", "stream": stream, "data": "second"}
                            ),
                            first_id + 1,
                        )
                    ]
                )
                second = next(iterator)
                assert second.event_type == stream
                assert second.payload["data"] == "second"
            finally:
                close = getattr(iterator, "close", None)
                assert callable(close)
                close()


def test_shared_collector_keeps_complete_large_stream_prefix() -> None:
    """The consuming result collector preserves more than 500 stream frames."""
    frame_count = STREAM_PREFIX_REGRESSION_FRAME_COUNT
    with WeftTestHarness() as harness:
        with harness.context.queue("test.prefix.tid") as queue:
            tid = str(queue.generate_timestamp())
            first_id = queue.generate_timestamp()
        spec = validate_taskspec_payload(
            {
                "name": "prefix",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                    "cleanup_on_exit": False,
                    "enable_process_title": False,
                },
            },
            resolved_tid=tid,
        )
        outbox_name = spec.io.outputs["outbox"]
        ctrl_out_name = spec.io.control["ctrl_out"]
        stream_rows = [
            (
                outbox_name,
                json.dumps(
                    {
                        "type": "stream",
                        "chunk": index,
                        "final": index == frame_count - 1,
                        "encoding": "base64",
                        "size": 1,
                        "data": b64encode(b"x").decode("ascii"),
                    }
                ),
                first_id + index,
            )
            for index in range(frame_count)
        ]
        terminal_row = (
            ctrl_out_name,
            json.dumps(
                {
                    "type": "terminal",
                    "source": "task",
                    "tid": tid,
                    "status": "cancelled",
                }
            ),
            first_id + frame_count,
        )
        with harness.context.queue(outbox_name, persistent=True) as outbox:
            with outbox.get_connection() as broker:
                broker.insert_messages([*stream_rows, terminal_row])
            observed = list(
                _iter_task_result(
                    harness.context,
                    tid,
                    timeout=0,
                    include_stream_events=True,
                    _accepted_taskspec=spec,
                )
            )
            frames = [
                item
                for item in observed
                if isinstance(item, TaskEvent) and item.event_type == "stdout"
            ]
            assert len(frames) == frame_count
            assert [frame.payload["chunk"] for frame in frames] == list(
                range(frame_count)
            )
            final = observed[-1]
            assert isinstance(final, TaskResult)
            assert final.status == "cancelled" and final.value == "x" * frame_count
            assert outbox.peek_one() is None


def _retire_actual_terminal_proof(
    harness: WeftTestHarness, *, stream_output: bool, command_output: bool = False
) -> TaskSpec:
    """Keep a real producer's result and live history, retire its terminal surfaces."""
    context = harness.context
    with context.queue("test.retired-terminal.tid") as queue:
        tid = str(queue.generate_timestamp())
    spec = validate_taskspec_payload(
        {
            "name": "retired-terminal",
            "spec": {
                "type": "command" if command_output else "function",
                **(
                    {"process_target": sys.executable}
                    if command_output
                    else {"function_target": "tests.tasks.sample_targets:echo_payload"}
                ),
                "stream_output": stream_output,
                "cleanup_on_exit": False,
                "enable_process_title": False,
            },
        },
        resolved_tid=tid,
    )
    consumer = Consumer(context.broker_target, spec, config=context.broker_config)
    with consumer.drive_scope():
        value = consumer.run_work_item(
            {"args": ["-c", "print('response')"]}
            if command_output
            else {"args": ["response"]}
        )
        assert value == "response"
    with context.queue(WEFT_GLOBAL_LOG_QUEUE) as log:
        for raw, message_id in list(log.peek_generator(with_timestamps=True)):
            payload = json.loads(raw)
            if payload.get("tid") == tid and payload.get("status") in {
                "completed",
                "failed",
                "cancelled",
                "timeout",
                "killed",
            }:
                assert log.delete(message_id=message_id)
    for name in (spec.io.control["ctrl_out"], task_state_queue_name(tid)):
        with context.queue(name) as queue:
            for _raw, message_id in list(queue.peek_generator(with_timestamps=True)):
                assert queue.delete(message_id=message_id)
    return spec


def test_realtime_claimed_result_settles_without_repeated_diagnostic_reclassification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh diagnostic timestamp is not new completion evidence or a new drain."""
    real_classify = events._classify_result_observation
    classifications = 0

    def classify(*args: Any, **kwargs: Any) -> Any:
        nonlocal classifications
        classifications += 1
        assert classifications <= 8, "unchanged claimed residue caused a hot loop"
        return real_classify(*args, **kwargs)

    def wait(_monitor: QueueChangeMonitor, timeout: float | None) -> bool:
        raise AssertionError("claimed recovery must settle after its last drain")

    with WeftTestHarness() as harness:
        spec = _retire_actual_terminal_proof(harness, stream_output=False)
        assert spec.tid is not None
        with harness.context.queue(spec.io.outputs["outbox"]) as outbox:
            assert outbox.read_one() is not None
            assert outbox.stats().total == 1 and outbox.stats().pending == 0
        monkeypatch.setattr(
            task_evidence, "STATUS_RUNTIMELESS_STALE_AFTER_SECONDS", -1.0
        )
        monkeypatch.setattr(events, "_classify_result_observation", classify)
        monkeypatch.setattr(QueueChangeMonitor, "wait", wait)
        observed = list(
            events.iter_task_realtime_events(
                harness.context,
                spec.tid,
                follow=True,
                timeout=1.0,
                _accepted_taskspec=spec,
            )
        )
        result = next(item for item in observed if item.event_type == "result")
        assert result.payload["status"] == "failed"
        assert "claimed" in result.payload["error"]
        assert observed[-1].event_type == "end"


@pytest.mark.parametrize("surface", ["consuming", "realtime"])
def test_completed_stream_frames_without_lifecycle_do_not_prove_task_completion(
    surface: str,
) -> None:
    """Retired terminal proof leaves stream output, which MF-5 excludes as fallback."""
    with WeftTestHarness() as harness:
        spec = _retire_actual_terminal_proof(harness, stream_output=True)
        assert spec.tid is not None
        with pytest.raises(CommandTimeoutError):
            if surface == "consuming":
                list(
                    _iter_task_result(
                        harness.context,
                        spec.tid,
                        timeout=0.0,
                        include_stream_events=True,
                        _accepted_taskspec=spec,
                    )
                )
            else:
                list(
                    events.iter_task_realtime_events(
                        harness.context,
                        spec.tid,
                        follow=True,
                        timeout=0.0,
                        _accepted_taskspec=spec,
                    )
                )


def test_realtime_releases_stream_iterator_before_yielding_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A paused caller holds decoded frames, not an active broker iterator/operation."""
    with WeftTestHarness() as harness:
        spec = _retire_actual_terminal_proof(
            harness, stream_output=True, command_output=True
        )
        assert spec.tid is not None
        with (
            harness.context.queue(WEFT_GLOBAL_LOG_QUEUE) as queue,
            queue.get_connection() as broker,
        ):
            broker_type = type(broker)
        real_peek = broker_type.peek_generator
        active = 0
        observation_thread = threading.get_ident()

        def peek(db: Any, queue_name: str, **kwargs: Any) -> Any:
            nonlocal active
            rows = real_peek(db, queue_name, **kwargs)
            tracked = threading.get_ident() == observation_thread and queue_name in {
                spec.io.outputs["outbox"],
                spec.io.control["ctrl_out"],
            }
            if tracked:
                active += 1
            try:
                with closing_queue_iterator(rows) as entries:
                    yield from entries
            finally:
                if tracked:
                    active -= 1

        monkeypatch.setattr(broker_type, "peek_generator", peek)
        observed = events.iter_task_realtime_events(
            harness.context, spec.tid, follow=True, timeout=0.0, _accepted_taskspec=spec
        )
        with closing_queue_iterator(observed) as entries:
            for event in entries:
                if event.event_type == "stdout":
                    assert active == 0, (
                        "stream frame yielded with broker iterator still active"
                    )
                    break
            else:
                raise AssertionError("real producer's stdout frame was not yielded")
        assert active == 0
