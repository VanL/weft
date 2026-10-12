"""Consuming result mode and final-drain contracts [MF-5]."""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from threading import get_ident
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest

from simplebroker import Queue
from tests.helpers.reactor_driver import drive_until
from tests.helpers.test_backend import prepare_project_root
from weft._constants import (
    WEFT_COMPLETED_RESULT_GRACE_SECONDS,
    WEFT_GLOBAL_LOG_QUEUE,
)
from weft._exceptions import CommandExecutionError, CommandTimeoutError
from weft.commands import _result_wait as result_wait
from weft.commands import result as result_cmd
from weft.commands.events import follow_task_events
from weft.commands.result import await_task_result
from weft.commands.types import TaskEvent, TaskResult
from weft.context import WeftContext, build_context
from weft.core.monitor.store import MonitorStore
from weft.core.queue_wait import QueueChangeMonitor
from weft.core.tasks.consumer import Consumer
from weft.core.taskspec import TaskSpec
from weft.helpers import closing_queue_iterator

pytestmark = [pytest.mark.shared]


def _accepted_spec(
    context: WeftContext,
    *,
    persistent: bool = False,
    pipeline: bool = False,
    stream_output: bool = False,
    outbox_name: str | None = None,
    function_target: str = "tests.tasks.sample_targets:echo_payload",
) -> TaskSpec:
    with context.queue(WEFT_GLOBAL_LOG_QUEUE) as log:
        tid = str(log.generate_timestamp())
    return TaskSpec.model_validate(
        {
            "tid": tid,
            "name": "collection-contract",
            "spec": {
                "type": "function",
                "function_target": function_target,
                "persistent": persistent,
                "stream_output": stream_output,
                "cleanup_on_exit": False,
                "enable_process_title": False,
            },
            "io": {
                "outputs": {"outbox": outbox_name or f"collection.{tid}.outbox"},
                "control": {"ctrl_out": f"collection.{tid}.ctrl_out"},
            },
            "metadata": {"role": "pipeline"} if pipeline else {},
        }
    )


def _completed_batch_count(log: Queue, tid: str) -> int:
    with closing_queue_iterator(log.peek_generator()) as rows:
        return sum(
            (payload := json.loads(body)).get("tid") == tid
            and payload.get("event") == "work_item_completed"
            for body in rows
        )


def _next_stdout(source: Iterator[TaskEvent | TaskResult]) -> TaskEvent:
    for row in source:
        if isinstance(row, TaskEvent) and row.event_type == "stdout":
            return row
    pytest.fail("first frame was not delivered before its boundary")


@pytest.mark.parametrize(
    "stream_output,rendered",
    [
        (True, "done"),
        (False, ""),
    ],
    ids=["emitted-final", "json-null"],
)
def test_completed_persistent_result_final_drain_at_zero_deadline(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    stream_output: bool,
    rendered: str,
) -> None:
    """A complete final value counts even when assembly returns None.

    A real Consumer publishes both the batch boundary and output. Ordinary
    buffered-value tests do not protect presence when the returned value is None.
    """
    context = build_context(prepare_project_root(tmp_path))
    spec = _accepted_spec(
        context,
        persistent=True,
        stream_output=stream_output,
        function_target=(
            "tests.tasks.sample_targets:echo_payload" if stream_output else "json:loads"
        ),
    )
    assert spec.tid is not None
    with (
        context.queue(spec.io.outputs["outbox"]) as outbox,
        context.queue(spec.io.inputs["inbox"]) as inbox,
        context.queue(WEFT_GLOBAL_LOG_QUEUE) as log,
    ):
        inbox.write(json.dumps({"args": ["done" if stream_output else "null"]}))

        def batch_completed() -> bool:
            with closing_queue_iterator(log.peek_generator()) as rows:
                return any(
                    (payload := json.loads(body)).get("tid") == spec.tid
                    and payload.get("event") == "work_item_completed"
                    for body in rows
                )

        task = Consumer(context.broker_target, spec, config=context.config)
        with task.drive_scope():
            drive_until(
                batch_completed,
                bool,
                step=task.process_once,
                wait=task.wait_for_activity,
                timeout=15.0,
                diagnostics=lambda: task.taskspec.state.status,
            )
        result = await_task_result(
            context,
            spec.tid,
            timeout=0.0,
            emit_stream=True,
            _accepted_taskspec=spec,
        )
        assert result.status == "completed"
        assert result.value is None
        assert result.error is None
        assert outbox.peek_one() is None
    assert capsys.readouterr().out.strip() == rendered


def test_stale_persistent_boundary_does_not_complete_current_stream_batch(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A prior boundary cannot complete the new observer's current live stream."""
    context = build_context(prepare_project_root(tmp_path))
    spec = _accepted_spec(context, persistent=True)
    assert spec.tid is not None
    next_batch = json.dumps(
        {"type": "stream", "stream": "stdout", "data": "next", "final": True}
    )
    with (
        context.queue(WEFT_GLOBAL_LOG_QUEUE) as log,
        context.queue(spec.io.outputs["outbox"]) as outbox,
    ):
        # A real broker ordering gap after the prior batch. No exact-ID import
        # or fabricated timestamp is needed to expose the old skew fallback.
        log.write(
            json.dumps(
                {
                    "tid": spec.tid,
                    "event": "work_item_completed",
                    "status": "running",
                }
            )
        )
        outbox.write(next_batch)
        with pytest.raises(CommandTimeoutError):
            await_task_result(
                context,
                spec.tid,
                timeout=0.0,
                emit_stream=True,
                _accepted_taskspec=spec,
            )
        # Live unread output may be claimed before its own boundary. The older
        # boundary still cannot confer completion on this first observed batch.
        assert outbox.peek_one() is None
    assert capsys.readouterr().out.strip() == "next"


def test_pipeline_quiet_output_and_running_snapshot_do_not_complete(
    tmp_path: Path,
) -> None:
    """Pipeline output and best-effort status are not lifecycle proof."""
    context = build_context(prepare_project_root(tmp_path))
    spec = _accepted_spec(context, pipeline=True)
    assert spec.tid is not None
    with (
        context.queue(spec.io.outputs["outbox"]) as outbox,
        context.queue(f"P{spec.tid}.status") as status,
    ):
        outbox.write("intermediate")
        status.write(
            json.dumps(
                {
                    "type": "pipeline_status",
                    "pipeline_tid": spec.tid,
                    "status": "running",
                }
            )
        )
        with pytest.raises(CommandTimeoutError):
            await_task_result(context, spec.tid, timeout=0.0, _accepted_taskspec=spec)
        retained = status.peek_one()
        assert retained is not None
        assert json.loads(retained)["status"] == "running"


@pytest.mark.parametrize(
    "timeout", [2.0, 0.6], ids=["original-grace", "original-deadline"]
)
def test_result_observation_keeps_original_completion_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, timeout: float
) -> None:
    """Discovery and collection share the first observed completion grace.

    A real Consumer supplies normal completion. Only caller-thread clock and
    observer dependency cost are controlled; broker reads and registration run
    unchanged. A new grace after discovery incorrectly returns at 0.9 seconds.
    """
    context = build_context(prepare_project_root(tmp_path))
    with context.queue(WEFT_GLOBAL_LOG_QUEUE) as log:
        tid = str(log.generate_timestamp())
    spec = TaskSpec.model_validate(
        {
            "tid": tid,
            "name": "completion-clock",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
                "cleanup_on_exit": False,
                "enable_process_title": False,
            },
        }
    )
    with context.queue(spec.io.inputs["inbox"]) as inbox:
        inbox.write(json.dumps({"args": ["already collected"]}))
    task = Consumer(context.broker_target, spec, config=context.config)
    with task.drive_scope():
        drive_until(
            lambda: task.taskspec.state.status,
            lambda status: status == "completed",
            step=task.process_once,
            wait=task.wait_for_activity,
            timeout=15.0,
            diagnostics=lambda: task.taskspec.state.status,
        )
    with context.queue(spec.io.outputs["outbox"]) as outbox:
        assert outbox.read_one() is not None
        assert outbox.peek_one() is None

    caller = get_ident()
    real_monotonic = time.monotonic
    clock = [0.0]
    constructions = [0]
    waits: list[tuple[float, float]] = []
    original_init = QueueChangeMonitor.__init__

    def monotonic() -> float:
        return clock[0] if get_ident() == caller else real_monotonic()

    def initialize(
        monitor: QueueChangeMonitor,
        queues: Sequence[Queue],
        *,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        original_init(monitor, queues, config=config)
        if get_ident() == caller:
            constructions[0] += 1
            if constructions[0] == 2:
                clock[0] += 0.4

    def wait(_monitor: QueueChangeMonitor, duration: float | None) -> bool:
        assert get_ident() == caller
        assert duration is not None and duration > 0
        waits.append((clock[0], duration))
        clock[0] += duration
        return False

    # Replace these modules' clock facade, never the process-wide time module
    # used by broker/listener threads.
    clock_facade = SimpleNamespace(monotonic=monotonic)
    monkeypatch.setattr(result_cmd, "time", clock_facade)
    monkeypatch.setattr(result_wait, "time", clock_facade)
    monkeypatch.setattr(QueueChangeMonitor, "__init__", initialize)
    monkeypatch.setattr(QueueChangeMonitor, "wait", wait)
    result = await_task_result(context, tid, timeout=timeout)
    assert result.status == "completed"
    assert result.value is None
    assert clock[0] == pytest.approx(WEFT_COMPLETED_RESULT_GRACE_SECONDS)
    assert waits
    assert all(start + duration <= timeout + 1e-9 for start, duration in waits)


def test_follow_pause_preserves_original_persistent_boundary_grace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pause after a newly completed real batch must not restart its grace."""
    context = build_context(prepare_project_root(tmp_path))
    spec = _accepted_spec(context, persistent=True)
    assert spec.tid is not None
    caller = get_ident()
    real_monotonic = time.monotonic
    clock = [0.0]
    waits: list[float | None] = []
    with context.queue(WEFT_GLOBAL_LOG_QUEUE) as log:

        def batch_completed() -> bool:
            with closing_queue_iterator(log.peek_generator()) as rows:
                return any(
                    (payload := json.loads(body)).get("tid") == spec.tid
                    and payload.get("event") == "work_item_completed"
                    for body in rows
                )

        task = Consumer(context.broker_target, spec, config=context.config)

        def monotonic() -> float:
            return clock[0] if get_ident() == caller else real_monotonic()

        def wait(_monitor: QueueChangeMonitor, duration: float | None) -> bool:
            assert get_ident() == caller
            assert duration is not None and duration > 0
            waits.append(duration)
            assert len(waits) == 1, (
                "the original completed-boundary grace expired during the caller pause"
            )
            with context.queue(spec.io.inputs["inbox"]) as inbox:
                inbox.write(json.dumps({"args": ["already collected"]}))
            drive_until(
                batch_completed,
                bool,
                step=task.process_once,
                wait=task.wait_for_activity,
                timeout=15,
                diagnostics=lambda: task.taskspec.state.status,
            )
            with context.queue(spec.io.outputs["outbox"]) as outbox:
                assert outbox.read_one() is not None
                assert outbox.peek_one() is None
            return True

        clock_facade = SimpleNamespace(monotonic=monotonic, time_ns=time.time_ns)
        monkeypatch.setattr(result_cmd, "time", clock_facade)
        monkeypatch.setattr(result_wait, "time", clock_facade)
        monkeypatch.setattr(QueueChangeMonitor, "wait", wait)
        with task.drive_scope():
            source = follow_task_events(
                context, spec.tid, timeout=2, _accepted_taskspec=spec
            )
            with closing_queue_iterator(source) as events:
                for event in events:
                    if event.event_type == "work_item_completed":
                        clock[0] = WEFT_COMPLETED_RESULT_GRACE_SECONDS + 0.25
                        break
                else:
                    pytest.fail("real producer batch boundary was not followed")
                remaining = list(events)
    final = next(event for event in remaining if event.event_type == "result")
    assert final.payload["status"] == "completed"
    assert final.payload["value"] is None
    assert clock[0] == pytest.approx(WEFT_COMPLETED_RESULT_GRACE_SECONDS + 0.25)
    assert waits == [2.0]


def test_persistent_competing_reader_does_not_rewait_expired_quiet_grace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Losing the pending value cannot turn expired grace into repeated wait(0).

    A real Consumer supplies output. Retiring its actual batch boundary exposes
    the supported quiet fallback, and a second reader wins the exact claim.
    The caller clock makes the former hot loop fail on its first zero wait.
    """
    context = build_context(prepare_project_root(tmp_path))
    spec = _accepted_spec(context, persistent=True)
    assert spec.tid is not None
    caller = get_ident()
    real_monotonic = time.monotonic
    clock = [0.0]
    waits: list[float] = []
    competing_values: list[str | tuple[str, int] | None] = []
    with context.broker() as broker:
        broker_type = type(broker)
    original_claim = broker_type.claim_one
    with (
        context.queue(WEFT_GLOBAL_LOG_QUEUE) as log,
        context.queue(spec.io.inputs["inbox"]) as inbox,
        context.queue(spec.io.outputs["outbox"]) as competitor,
    ):
        inbox.write(json.dumps({"args": ["taken by another reader"]}))
        task = Consumer(context.broker_target, spec, config=context.config)
        with task.drive_scope():
            drive_until(
                competitor.peek_one,
                lambda value: value is not None,
                step=task.process_once,
                wait=task.wait_for_activity,
                timeout=15.0,
                diagnostics=lambda: task.taskspec.state.status,
            )
            with closing_queue_iterator(
                log.peek_generator(with_timestamps=True)
            ) as rows:
                boundaries = [
                    message_id
                    for body, message_id in rows
                    if (payload := json.loads(body)).get("tid") == spec.tid
                    and payload.get("event") == "work_item_completed"
                ]
            assert boundaries
            for message_id in boundaries:
                log.delete(message_id=message_id)

            def monotonic() -> float:
                return clock[0] if get_ident() == caller else real_monotonic()

            def wait(_monitor: QueueChangeMonitor, duration: float | None) -> bool:
                assert duration is not None and duration > 0, (
                    "an expired quiet grace must not be offered as wait(0)"
                )
                waits.append(duration)
                clock[0] += duration
                return False

            def claim(
                broker: Any,
                queue: str,
                *,
                exact_timestamp: Any = None,
                with_timestamps: bool = True,
                order: str = "oldest",
            ) -> tuple[str, int] | str | None:
                if (
                    get_ident() == caller
                    and queue == competitor.name
                    and exact_timestamp is not None
                    and not competing_values
                ):
                    # The competing public reader wins after the collector's
                    # horizon and peek, before its committed exact-ID claim.
                    competing_values.append(competitor.read_one())
                return cast(
                    tuple[str, int] | str | None,
                    original_claim(
                        broker,
                        queue,
                        exact_timestamp=exact_timestamp,
                        with_timestamps=with_timestamps,
                        order=order,
                    ),
                )

            clock_facade = SimpleNamespace(monotonic=monotonic)
            monkeypatch.setattr(result_cmd, "time", clock_facade)
            monkeypatch.setattr(result_wait, "time", clock_facade)
            monkeypatch.setattr(QueueChangeMonitor, "wait", wait)
            monkeypatch.setattr(broker_type, "claim_one", claim)
            with pytest.raises(CommandTimeoutError):
                await_task_result(
                    context, spec.tid, timeout=2.0, _accepted_taskspec=spec
                )
        assert competing_values == ["taken by another reader"]
        assert competitor.peek_one() is None
    assert waits == [WEFT_COMPLETED_RESULT_GRACE_SECONDS, 1.5]
    assert clock[0] == 2.0


def test_unavailable_monitor_waits_for_stronger_task_evidence_before_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An optional store failure cannot end a finite bare-TID observation early.

    The old diagnostic-only test accepts immediate and deadline returns alike.
    Here the first registered wait drives a real task, so early unknown loses
    its actual configuration, response, and completed lifecycle publication.
    """
    context = build_context(prepare_project_root(tmp_path))
    spec = _accepted_spec(context)
    assert spec.tid is not None
    lookups: list[str] = []
    waits: list[float | None] = []

    def unavailable(_store: MonitorStore, tid: str) -> Any:
        lookups.append(tid)
        raise RuntimeError("retained store read failed")

    def wait(_monitor: QueueChangeMonitor, duration: float | None) -> bool:
        assert duration is not None and duration > 0
        waits.append(duration)
        assert len(waits) == 1, "actual task completion must settle the observation"
        with context.queue(spec.io.inputs["inbox"]) as inbox:
            inbox.write(json.dumps({"args": ["briefing"]}))
        task = Consumer(context.broker_target, spec, config=context.config)
        with task.drive_scope():
            drive_until(
                lambda: task.taskspec.state.status,
                lambda status: status == "completed",
                step=task.process_once,
                wait=task.wait_for_activity,
                timeout=15.0,
                diagnostics=lambda: task.taskspec.state.status,
            )
        return True

    monkeypatch.setattr(MonitorStore, "get_task", unavailable)
    monkeypatch.setattr(QueueChangeMonitor, "wait", wait)
    result = await_task_result(context, spec.tid, timeout=20.0)
    assert result.status == "completed"
    assert result.value == "briefing"
    assert result.error is None
    assert waits
    assert waits[0] is not None and 0 < waits[0] <= 20.0
    assert lookups == [spec.tid]


def test_live_persistent_prefix_keeps_next_batch_after_early_final(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real final frame can be followed before its boundary without batch theft.

    Advance the consumer at the producer's public log-write boundary. Its first
    output is already committed, but work_item_completed is not. Then let two
    real batches finish before resuming the suspended result reader.
    """
    context = build_context(prepare_project_root(tmp_path))
    spec = _accepted_spec(context, persistent=True, stream_output=True)
    tid = spec.tid
    assert tid is not None
    first_frames: list[TaskEvent] = []
    original_write = Queue.write
    with (
        context.queue(WEFT_GLOBAL_LOG_QUEUE) as log,
        context.queue(spec.io.inputs["inbox"]) as inbox,
        context.queue(spec.io.outputs["outbox"]) as outbox,
        closing_queue_iterator(
            result_cmd._iter_task_result(
                context,
                tid,
                timeout=2.0,
                include_stream_events=True,
                _accepted_taskspec=spec,
            )
        ) as results,
    ):

        def write(queue: Queue, message: str, *, keep_newest: int | None = None) -> int:
            if queue.name == log.name:
                payload = json.loads(message)
                if (
                    payload.get("tid") == spec.tid
                    and payload.get("event") == "work_item_completed"
                    and not first_frames
                ):
                    first_frames.append(_next_stdout(iter(results)))
            return original_write(queue, message, keep_newest=keep_newest)

        def no_wait(_monitor: QueueChangeMonitor, _duration: float | None) -> bool:
            pytest.fail("committed live output must be followed before work completes")

        monkeypatch.setattr(Queue, "write", write)
        monkeypatch.setattr(QueueChangeMonitor, "wait", no_wait)
        task = Consumer(context.broker_target, spec, config=context.config)
        with task.drive_scope():
            for count, value in enumerate(["first", "second"], start=1):
                inbox.write(json.dumps({"args": [value]}))

                def completed_batch(completed: int, target: int = count) -> bool:
                    return completed == target

                drive_until(
                    lambda: _completed_batch_count(log, tid),
                    completed_batch,
                    step=task.process_once,
                    wait=task.wait_for_activity,
                    timeout=15.0,
                    diagnostics=lambda: task.taskspec.state.status,
                )
            remaining = list(results)
        final = next(row for row in remaining if isinstance(row, TaskResult))
        assert first_frames[0].payload["final"] is True
        assert final.status == "completed"
        assert final.value == "first"
        assert not any(
            isinstance(row, TaskEvent) and row.event_type == "stdout"
            for row in remaining
        )
        pending = outbox.peek_one()
        assert isinstance(pending, str)
        envelope = json.loads(pending)
        assert envelope["final"] is True
        assert base64.b64decode(envelope["data"]).decode() == "second"


@pytest.mark.parametrize(
    "persistent,pipeline,stream_output,surface,mixed",
    [
        (False, False, True, "result", False),
        (True, False, True, "result", False),
        (False, True, True, "result", False),
        (False, True, False, "result", False),
        (False, True, False, "emit-text", False),
        (False, True, False, "stream-events", False),
        (False, True, False, "cmd-stream", False),
        (False, True, False, "cmd-stream", True),
    ],
    ids=[
        "one-shot-stream",
        "persistent-stream",
        "pipeline-stream",
        "pipeline-value",
        "pipeline-value-emit-text",
        "pipeline-value-stream-events",
        "pipeline-value-cmd-stream",
        "pipeline-mixed-cmd-stream",
    ],
)
def test_nonrendering_timeout_preserves_output_until_actual_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    persistent: bool,
    pipeline: bool,
    stream_output: bool,
    surface: Literal["result", "emit-text", "stream-events", "cmd-stream"],
    mixed: bool,
) -> None:
    """Observer timeout must leave unqualified buffered output readable.

    The producer's real output is committed before its completion log write.
    Intercept only that public write: the first result call must time out while
    its buffered value stays pending; a call after publication gets that value.
    Live presentation flags do not deliver ordinary values. In the mixed case,
    another real Consumer shares the pipeline outbox: its frame must be delivered
    while the earlier ordinary value remains readable until pipeline completion.
    """
    context = build_context(prepare_project_root(tmp_path))
    spec = _accepted_spec(
        context,
        persistent=persistent,
        pipeline=pipeline,
        stream_output=stream_output,
    )
    tid = spec.tid
    assert tid is not None
    original_write = Queue.write
    interceptions: list[str] = []
    delivered: list[TaskEvent] = []
    with context.queue(spec.io.outputs["outbox"]) as outbox:

        def write(queue: Queue, message: str, *, keep_newest: int | None = None) -> int:
            if queue.name == WEFT_GLOBAL_LOG_QUEUE:
                payload = json.loads(message)
                if (
                    payload.get("tid") == tid
                    and payload.get("event")
                    in {"work_item_completed", "work_completed"}
                    and not interceptions
                ):
                    before = outbox.peek_one(with_timestamps=True)
                    assert before is not None
                    if mixed:
                        stream_spec = _accepted_spec(
                            context, stream_output=True, outbox_name=outbox.name
                        )
                        producer = Consumer(
                            context.broker_target, stream_spec, config=context.config
                        )
                        with producer.drive_scope():
                            assert producer.run_work_item({"args": ["live"]}) == "live"
                    with pytest.raises(CommandTimeoutError):
                        if surface in {"result", "emit-text"}:
                            await_task_result(
                                context,
                                tid,
                                timeout=0.0,
                                emit_stream=surface == "emit-text",
                                _accepted_taskspec=spec,
                            )
                        else:
                            source = (
                                result_cmd.cmd_result(
                                    tid,
                                    stream=True,
                                    timeout=0.0,
                                    context=context.root,
                                )
                                if surface == "cmd-stream"
                                else result_cmd._iter_task_result(
                                    context,
                                    tid,
                                    timeout=0.0,
                                    include_stream_events=True,
                                    _accepted_taskspec=spec,
                                )
                            )
                            assert not isinstance(source, (TaskResult, tuple))
                            with closing_queue_iterator(source) as rows:
                                delivered.extend(
                                    row
                                    for row in rows
                                    if isinstance(row, TaskEvent)
                                    and row.event_type == "stdout"
                                )
                    assert outbox.peek_one(with_timestamps=True) == before
                    if mixed:
                        assert len(delivered) == 1
                        assert base64.b64decode(delivered[0].payload["data"]) == b"live"
                    interceptions.append(payload["event"])
            return original_write(queue, message, keep_newest=keep_newest)

        monkeypatch.setattr(Queue, "write", write)
        task = Consumer(context.broker_target, spec, config=context.config)
        with task.drive_scope():
            assert task.run_work_item({"args": ["briefing"]}) == "briefing"
            result = await_task_result(
                context, tid, timeout=0.0, _accepted_taskspec=spec
            )
        assert interceptions == [
            "work_item_completed" if persistent else "work_completed"
        ]
        assert result.status == "completed"
        assert result.value == "briefing"
        assert result.error is None
        assert outbox.peek_one() is None


@pytest.mark.parametrize("emit_stream", [False, True])
def test_one_shot_buffered_value_survives_observer_failure_before_quiet_grace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, emit_stream: bool
) -> None:
    """Real ordinary output remains readable while its final proof is unpublished."""
    context = build_context(prepare_project_root(tmp_path))
    spec = _accepted_spec(context, stream_output=False)
    tid = spec.tid
    assert tid is not None
    original_write = Queue.write
    inspected: list[int] = []
    waits: list[float | None] = []
    with context.queue(spec.io.outputs["outbox"]) as outbox:

        def fail_wait(_monitor: QueueChangeMonitor, timeout: float | None) -> bool:
            waits.append(timeout)
            raise OSError("observer unavailable before delivery")

        def write(queue: Queue, message: str, *, keep_newest: int | None = None) -> int:
            payload = json.loads(message) if queue.name == WEFT_GLOBAL_LOG_QUEUE else {}
            if (
                payload.get("tid") == tid
                and payload.get("event") == "work_completed"
                and not inspected
            ):
                pending = outbox.peek_one(with_timestamps=True)
                assert pending is not None
                inspected.append(pending[1])
                with monkeypatch.context() as failure:
                    failure.setattr(QueueChangeMonitor, "wait", fail_wait)
                    with pytest.raises(
                        CommandExecutionError,
                        match="observer unavailable before delivery",
                    ):
                        await_task_result(
                            context,
                            tid,
                            timeout=2.0,
                            emit_stream=emit_stream,
                            _accepted_taskspec=spec,
                        )
                assert waits, "the real pending value must reach its quiet-grace wait"
                assert outbox.peek_one(with_timestamps=True) == pending
            return original_write(queue, message, keep_newest=keep_newest)

        monkeypatch.setattr(Queue, "write", write)
        task = Consumer(context.broker_target, spec, config=context.config)
        with task.drive_scope():
            assert task.run_work_item({"args": ["briefing"]}) == "briefing"
            result = await_task_result(
                context, tid, timeout=0.0, _accepted_taskspec=spec
            )
        assert inspected
        assert result.status == "completed" and result.value == "briefing"
        assert outbox.peek_one() is None


def test_abandoned_consuming_stream_keeps_buffered_ordinary_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closing a yielded log subscription cannot claim its buffered result."""
    context = build_context(prepare_project_root(tmp_path))
    spec = _accepted_spec(context)
    tid = spec.tid
    assert tid is not None
    original_write = Queue.write
    abandoned: list[int] = []
    with context.queue(spec.io.outputs["outbox"]) as outbox:
        task = Consumer(context.broker_target, spec, config=context.config)

        def publish_activity(
            _monitor: QueueChangeMonitor, _timeout: float | None
        ) -> bool:
            task._report_state_change(
                "task_activity", activity="observer-close-boundary"
            )
            return True

        def write(queue: Queue, message: str, *, keep_newest: int | None = None) -> int:
            payload = json.loads(message) if queue.name == WEFT_GLOBAL_LOG_QUEUE else {}
            if payload.get("tid") == tid and payload.get("event") == "work_completed":
                pending = outbox.peek_one(with_timestamps=True)
                assert pending is not None
                source = result_cmd._iter_task_result(
                    context,
                    tid,
                    timeout=2.0,
                    include_stream_events=True,
                    _accepted_taskspec=spec,
                )
                with (
                    monkeypatch.context() as wake,
                    closing_queue_iterator(source) as rows,
                ):
                    wake.setattr(QueueChangeMonitor, "wait", publish_activity)
                    for row in rows:
                        if (
                            isinstance(row, TaskEvent)
                            and row.payload.get("activity") == "observer-close-boundary"
                        ):
                            abandoned.append(pending[1])
                            break
                    else:
                        pytest.fail(
                            "the real activity publication must suspend the result stream"
                        )
                assert outbox.peek_one(with_timestamps=True) == pending
            return original_write(queue, message, keep_newest=keep_newest)

        monkeypatch.setattr(Queue, "write", write)
        with task.drive_scope():
            assert task.run_work_item({"args": ["briefing"]}) == "briefing"
            result = await_task_result(
                context, tid, timeout=0.0, _accepted_taskspec=spec
            )
        assert abandoned
        assert result.status == "completed" and result.value == "briefing"
        assert outbox.peek_one() is None


def test_live_runtime_outranks_claimed_result_without_terminal_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Actual live runtime remains authoritative after another reader's claim."""
    context = build_context(prepare_project_root(tmp_path))
    spec = _accepted_spec(context)
    tid = spec.tid
    assert tid is not None
    original_write = Queue.write
    observed: list[str] = []
    with context.queue(spec.io.outputs["outbox"]) as competitor:

        def write(queue: Queue, message: str, *, keep_newest: int | None = None) -> int:
            payload = json.loads(message) if queue.name == WEFT_GLOBAL_LOG_QUEUE else {}
            if payload.get("tid") == tid and payload.get("event") == "work_completed":
                assert competitor.read_one() is not None
                assert competitor.stats().pending == 0 and competitor.stats().total == 1
                mapping = result_wait.read_task_state_snapshot(context, tid)
                assert mapping is not None
                live = result_wait.task_evidence.runtime_evidence(
                    tid=tid, mapping_entry=mapping[1]
                )
                assert live is not None and live.classification == "live"
                with monkeypatch.context() as age:
                    age.setattr(
                        result_wait.task_evidence,
                        "STATUS_RUNTIMELESS_STALE_AFTER_SECONDS",
                        -1.0,
                    )
                    with pytest.raises(CommandTimeoutError):
                        await_task_result(
                            context, tid, timeout=0.0, _accepted_taskspec=spec
                        )
                observed.append(live.classification)
            return original_write(queue, message, keep_newest=keep_newest)

        monkeypatch.setattr(Queue, "write", write)
        task = Consumer(context.broker_target, spec, config=context.config)
        with task.drive_scope():
            assert (
                task.run_work_item({"args": ["competitor-owned"]}) == "competitor-owned"
            )
        assert observed == ["live"]


def test_new_ordinary_row_cancels_quiet_wake_and_preserves_ambiguous_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two real producers turn an eligible ordinary value into ambiguous output.

    A second durable value arrives before the first value's quiet grace. Neither
    value may be claimed, and that obsolete grace must not cause a timer wake.
    """
    context = build_context(prepare_project_root(tmp_path))
    spec = _accepted_spec(context)
    tid = spec.tid
    assert tid is not None
    second_spec = _accepted_spec(context, outbox_name=spec.io.outputs["outbox"])
    original_write = Queue.write
    original_monotonic = time.monotonic
    caller = get_ident()
    clock = [0.0]
    waits: list[float] = []
    observed: list[tuple[str, int]] = []
    interceptions: list[int] = []
    with context.queue(spec.io.outputs["outbox"]) as outbox:

        def monotonic() -> float:
            return clock[0] if get_ident() == caller else original_monotonic()

        def wait(_monitor: QueueChangeMonitor, duration: float | None) -> bool:
            assert duration is not None
            waits.append(duration)
            if len(waits) == 1:
                assert duration == WEFT_COMPLETED_RESULT_GRACE_SECONDS
                producer = Consumer(
                    context.broker_target, second_spec, config=context.config
                )
                with producer.drive_scope():
                    assert producer.run_work_item({"args": ["second"]}) == "second"
                clock[0] = WEFT_COMPLETED_RESULT_GRACE_SECONDS / 2
                with closing_queue_iterator(
                    outbox.peek_generator(with_timestamps=True)
                ) as rows:
                    observed.extend(rows)
                assert len(observed) == 2
                return True
            clock[0] += duration
            return False

        def write(queue: Queue, message: str, *, keep_newest: int | None = None) -> int:
            payload = json.loads(message) if queue.name == WEFT_GLOBAL_LOG_QUEUE else {}
            if (
                payload.get("tid") == tid
                and payload.get("event") == "work_completed"
                and not interceptions
            ):
                pending = outbox.peek_one(with_timestamps=True)
                assert pending is not None
                interceptions.append(pending[1])
                with monkeypatch.context() as observation:
                    clock_facade = SimpleNamespace(monotonic=monotonic)
                    observation.setattr(result_cmd, "time", clock_facade)
                    observation.setattr(result_wait, "time", clock_facade)
                    observation.setattr(QueueChangeMonitor, "wait", wait)
                    with pytest.raises(CommandTimeoutError):
                        await_task_result(
                            context, tid, timeout=2.0, _accepted_taskspec=spec
                        )
                with closing_queue_iterator(
                    outbox.peek_generator(with_timestamps=True)
                ) as rows:
                    assert list(rows) == observed
            return original_write(queue, message, keep_newest=keep_newest)

        monkeypatch.setattr(Queue, "write", write)
        task = Consumer(context.broker_target, spec, config=context.config)
        with task.drive_scope():
            assert task.run_work_item({"args": ["first"]}) == "first"
    assert interceptions
    assert waits == [WEFT_COMPLETED_RESULT_GRACE_SECONDS, 1.75], (
        "ambiguous output has no quiet-completion deadline"
    )
    assert clock[0] == 2.0
