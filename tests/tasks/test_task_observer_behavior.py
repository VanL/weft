"""Tests for Observer behavior."""

from __future__ import annotations

import time

from weft._constants import (
    CONTROL_STOP,
    QUEUE_CTRL_IN_SUFFIX,
    QUEUE_INBOX_SUFFIX,
    QUEUE_OUTBOX_SUFFIX,
)
from weft.core.tasks import Monitor, Observer, SamplingObserver, SelectiveConsumer
from weft.core.taskspec import IOSection, SpecSection, StateSection, TaskSpec


def make_observer_spec(tid: str) -> TaskSpec:
    prefix = f"T{tid}"
    return TaskSpec(
        tid=tid,
        name="observer",
        spec=SpecSection(
            type="function", function_target="tests.tasks.sample_targets:echo_payload"
        ),
        io=IOSection(
            inputs={"inbox": f"{prefix}.{QUEUE_INBOX_SUFFIX}"},
            outputs={"outbox": f"{prefix}.{QUEUE_OUTBOX_SUFFIX}"},
            control={
                "ctrl_in": f"{prefix}.{QUEUE_CTRL_IN_SUFFIX}",
                "ctrl_out": f"{prefix}.ctrl_out",
            },
        ),
        state=StateSection(),
    )


def test_observer_peek_without_ack(broker_env) -> None:
    db_path, make_queue = broker_env
    spec = make_observer_spec("1761013000000000000")
    inbox = make_queue(spec.io.inputs["inbox"])
    inbox.write("sample")

    seen: list[tuple[str, int]] = []

    task = Observer(db_path, spec, observer=lambda msg, ts: seen.append((msg, ts)))
    task._drain_queue()

    assert seen and seen[0][0] == "sample"
    assert inbox.peek_one() == "sample"


def test_selective_consumer_consumes_when_selector_true(broker_env) -> None:
    db_path, make_queue = broker_env
    spec = make_observer_spec("1761013000000000001")
    inbox = make_queue(spec.io.inputs["inbox"])
    inbox.write("sample")

    task = SelectiveConsumer(
        db_path,
        spec,
        selector=lambda msg, ts: True,
    )
    task._drain_queue()

    assert inbox.peek_one() is None


def test_selective_consumer_leaves_when_selector_false(broker_env) -> None:
    db_path, make_queue = broker_env
    spec = make_observer_spec("1761013000000000003")
    inbox = make_queue(spec.io.inputs["inbox"])
    inbox.write("sample")

    task = SelectiveConsumer(
        db_path,
        spec,
        selector=lambda msg, ts: False,
    )
    task._drain_queue()

    assert inbox.peek_one() == "sample"


def test_monitor_forwards_message(broker_env) -> None:
    db_path, make_queue = broker_env
    spec = make_observer_spec("1761013000000000100")
    inbox = make_queue(spec.io.inputs["inbox"])
    outbox = make_queue(spec.io.outputs["outbox"])
    inbox.write("sample")

    seen: list[str] = []
    monitor = Monitor(db_path, spec, observer=lambda msg, ts: seen.append(msg))
    monitor._drain_queue()

    assert outbox.read_one() == "sample"
    assert inbox.peek_one() is None
    assert seen == ["sample"]


def test_monitor_custom_target_queue(broker_env) -> None:
    db_path, make_queue = broker_env
    spec = make_observer_spec("1761013000000000200")
    inbox = make_queue(spec.io.inputs["inbox"])
    inbox.write("sample")

    target_queue = "custom.downstream"
    monitor = Monitor(
        db_path,
        spec,
        observer=lambda msg, ts: None,
        downstream_queue=target_queue,
    )
    monitor._drain_queue()

    downstream = make_queue(target_queue)
    assert downstream.read_one() == "sample"


def test_monitor_stop_command(broker_env) -> None:
    db_path, make_queue = broker_env
    spec = make_observer_spec("1761013000000000300")
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_in.write(CONTROL_STOP)

    monitor = Monitor(db_path, spec, observer=lambda msg, ts: None)
    monitor._drain_queue()

    assert monitor.should_stop is True


def test_sampling_observer_interval(broker_env) -> None:
    db_path, make_queue = broker_env
    spec = make_observer_spec("1761013000000000400")
    inbox = make_queue(spec.io.inputs["inbox"])
    calls: list[str] = []

    observer = SamplingObserver(
        db_path,
        spec,
        observer=lambda msg, ts: calls.append(msg),
        interval_seconds=0.05,
    )

    inbox.write("sample-1")
    observer._drain_queue()
    inbox.read_one()

    time.sleep(0.06)
    inbox.write("sample-2")
    observer._drain_queue()
    inbox.read_one()

    inbox.write("sample-3")
    observer._drain_queue()
    inbox.read_one()

    assert calls == ["sample-1", "sample-2"]


def test_observer_handles_stop(broker_env) -> None:
    db_path, make_queue = broker_env
    spec = make_observer_spec("1761013000000000002")
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_in.write(CONTROL_STOP)

    task = Observer(db_path, spec, observer=lambda msg, ts: None)
    task._drain_queue()

    assert task.should_stop is True
