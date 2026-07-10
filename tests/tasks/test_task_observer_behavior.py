"""Tests for Observer behavior.

Spec references:
- docs/specifications/07-System_Invariants.md [QUEUE.7]
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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


def test_monitor_allows_explicit_downstream_outbox_alias(tmp_path: Path) -> None:
    """The forwarding monitor's downstream/outbox alias is intentional [QUEUE.7]."""

    spec = make_observer_spec("1778089999999999301")
    monitor = Monitor(
        tmp_path / "monitor-outbox-alias.sqlite3",
        spec,
        observer=lambda _message, _timestamp: None,
        downstream_queue=spec.io.outputs["outbox"],
    )
    monitor.cleanup()


@pytest.mark.parametrize("local_role", ("inbox", "ctrl_in"))
def test_monitor_rejects_unsafe_downstream_alias_before_broker_side_effects(
    tmp_path: Path,
    local_role: str,
) -> None:
    """Downstream forwarding cannot alias watched input/control lanes [QUEUE.7]."""

    spec = make_observer_spec("1778089999999999302")
    duplicate_queue = (
        spec.io.inputs["inbox"] if local_role == "inbox" else spec.io.control["ctrl_in"]
    )
    db_path = tmp_path / f"monitor-{local_role}-alias.sqlite3"

    with pytest.raises(ValueError) as exc_info:
        Monitor(
            db_path,
            spec,
            observer=lambda _message, _timestamp: None,
            downstream_queue=duplicate_queue,
        )

    message = str(exc_info.value)
    assert "downstream" in message
    assert local_role in message
    assert duplicate_queue in message
    assert db_path.exists() is False


def test_monitor_stop_command(broker_env) -> None:
    db_path, make_queue = broker_env
    spec = make_observer_spec("1761013000000000300")
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])
    ctrl_in.write(CONTROL_STOP)

    monitor = Monitor(db_path, spec, observer=lambda msg, ts: None)
    monitor._drain_queue()

    assert monitor.should_stop is True
    responses = [json.loads(message) for message in ctrl_out.read_generator()]
    control_responses = [item for item in responses if item.get("type") != "terminal"]
    terminal_responses = [item for item in responses if item.get("type") == "terminal"]
    response = next(item for item in control_responses if item["command"] == "STOP")
    assert response["command"] == "STOP"
    assert response["status"] == "ack"
    terminal = next(item for item in terminal_responses if item["tid"] == spec.tid)
    assert terminal["source"] == "task"
    assert terminal["status"] == "cancelled"


def test_sampling_observer_interval(broker_env, monkeypatch) -> None:
    db_path, make_queue = broker_env
    spec = make_observer_spec("1761013000000000400")
    inbox = make_queue(spec.io.inputs["inbox"])
    calls: list[str] = []
    current_time = 100.0

    def fake_monotonic() -> float:
        return current_time

    monkeypatch.setattr("weft.core.tasks.observer.time.monotonic", fake_monotonic)

    observer = SamplingObserver(
        db_path,
        spec,
        observer=lambda msg, ts: calls.append(msg),
        interval_seconds=0.05,
    )

    inbox.write("sample-1")
    observer._drain_queue()
    inbox.read_one()

    current_time += 0.06
    inbox.write("sample-2")
    observer._drain_queue()
    inbox.read_one()

    current_time += 0.01
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
