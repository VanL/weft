"""Heartbeat helper tests."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from weft._constants import (
    HEARTBEAT_MAX_LEASE_SECONDS,
    INTERNAL_HEARTBEAT_ENDPOINT_NAME,
    INTERNAL_RUNTIME_ENDPOINT_NAME_KEY,
    INTERNAL_RUNTIME_ENVELOPE_ENDPOINT_NAME_KEY,
    INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY,
    INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT,
    INTERNAL_RUNTIME_TASK_CLASS_KEY,
    WEFT_ENDPOINTS_REGISTRY_QUEUE,
    WEFT_HEARTBEAT_REGISTRATIONS_QUEUE,
    WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
)
from weft.context import build_context
from weft.core import manager_runtime as core_manager_runtime
from weft.core.endpoints import (
    EndpointRecord,
    ResolvedEndpoint,
    endpoint_record_owner_is_live,
)
from weft.core.heartbeat import cancel_heartbeat, upsert_heartbeat
from weft.core.spawn_requests import submit_spawn_request
from weft.core.taskspec import IOSection, SpecSection, StateSection, TaskSpec

pytestmark = [pytest.mark.shared]


def _resolved_endpoint(tid: str) -> ResolvedEndpoint:
    return ResolvedEndpoint(
        record=EndpointRecord(
            name=INTERNAL_HEARTBEAT_ENDPOINT_NAME,
            tid=tid,
            status="active",
            inbox=f"T{tid}.inbox",
            outbox=f"T{tid}.outbox",
            ctrl_in=f"T{tid}.ctrl_in",
            ctrl_out=f"T{tid}.ctrl_out",
            registered_at=int(time.time_ns()),
            last_seen=int(time.time_ns()),
            metadata={},
        )
    )


def _host_runtime_handle(pid: int, create_time: float | None = None) -> dict[str, Any]:
    return {
        "runner": "host",
        "kind": "process",
        "id": str(pid),
        "control": {"authority": "host-pid"},
        "observations": {
            "host_pids": [pid],
            "host_processes": [{"pid": pid, "create_time": create_time}],
        },
        "metadata": {},
    }


def _heartbeat_service_taskspec(tid: str, root: Path) -> TaskSpec:
    return TaskSpec(
        tid=tid,
        name="heartbeat-service",
        spec=SpecSection(
            type="function",
            function_target="weft.tasks:noop",
            persistent=True,
            weft_context=str(root),
        ),
        io=IOSection(
            inputs={"inbox": f"T{tid}.inbox"},
            outputs={"outbox": f"T{tid}.outbox"},
            control={"ctrl_in": f"T{tid}.ctrl_in", "ctrl_out": f"T{tid}.ctrl_out"},
        ),
        state=StateSection(),
        metadata={
            INTERNAL_RUNTIME_TASK_CLASS_KEY: INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT,
            INTERNAL_RUNTIME_ENDPOINT_NAME_KEY: INTERNAL_HEARTBEAT_ENDPOINT_NAME,
        },
    )


def test_endpoint_owner_uses_host_runtime_handle_liveness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tid = "1777000000000000901"
    record = _resolved_endpoint(tid).record
    observed: list[tuple[int, ...]] = []

    def _handle_has_live_host_process(handle: Any) -> bool:
        observed.append(handle.scoped_host_pids())
        return True

    monkeypatch.setattr(
        "weft.core.endpoints.handle_has_live_host_process",
        _handle_has_live_host_process,
    )

    assert endpoint_record_owner_is_live(
        record,
        task_statuses={},
        tid_states={tid: {"runtime_handle": _host_runtime_handle(4242, 1.5)}},
    )
    assert observed == [(4242,)]


def test_endpoint_owner_rejects_dead_host_runtime_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tid = "1777000000000000902"
    record = _resolved_endpoint(tid).record
    monkeypatch.setattr(
        "weft.core.endpoints.handle_has_live_host_process",
        lambda handle: False,
    )

    assert not endpoint_record_owner_is_live(
        record,
        task_statuses={},
        tid_states={tid: {"runtime_handle": _host_runtime_handle(4242, 1.5)}},
    )


def test_external_supervisor_endpoint_owner_uses_runtime_liveness_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tid = "1777000000000000904"
    record = _resolved_endpoint(tid).record
    handle_payload = {
        "runner": "manager-supervisor",
        "kind": "supervised-process",
        "id": "docker:container123",
        "control": {"authority": "external-supervisor"},
        "observations": {
            "container_runtime": "docker",
            "container_id": "container123",
            "host_processes": [{"pid": 57, "create_time": 111.0}],
        },
        "metadata": {},
    }

    def fail_host_liveness(_handle: Any) -> bool:
        raise AssertionError("external-supervisor endpoint used host PID liveness")

    monkeypatch.setattr(
        "weft.core.endpoints.handle_has_live_host_process", fail_host_liveness
    )
    monkeypatch.setattr(
        "weft.core.endpoints.runtime_liveness_from_registered_probe",
        lambda handle: "live",
    )

    assert endpoint_record_owner_is_live(
        record,
        task_statuses={},
        tid_states={tid: {"runtime_handle": handle_payload}},
    )


def test_external_supervisor_endpoint_owner_rejects_unknown_runtime_liveness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tid = "1777000000000000905"
    record = _resolved_endpoint(tid).record
    handle_payload = {
        "runner": "manager-supervisor",
        "kind": "supervised-process",
        "id": "docker:container123",
        "control": {"authority": "external-supervisor"},
        "observations": {
            "container_runtime": "docker",
            "container_id": "container123",
            "host_processes": [{"pid": 57, "create_time": 111.0}],
        },
        "metadata": {},
    }

    monkeypatch.setattr(
        "weft.core.endpoints.runtime_liveness_from_registered_probe",
        lambda handle: "unknown",
    )

    assert not endpoint_record_owner_is_live(
        record,
        task_statuses={},
        tid_states={tid: {"runtime_handle": handle_payload}},
    )


def test_internal_submit_spawn_request_preserves_internal_runtime_envelope(
    tmp_path: Path,
) -> None:
    context = build_context(spec_context=tmp_path)
    tid = str(time.time_ns())

    submit_spawn_request(
        context.broker_target,
        taskspec=_heartbeat_service_taskspec(tid, tmp_path),
        work_payload=None,
        config=context.broker_config,
        tid=tid,
        inherited_weft_context=str(context.root),
        allow_internal_runtime=True,
    )

    queue = context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=False)
    try:
        raw_message = queue.read_one()
    finally:
        queue.close()

    assert isinstance(raw_message, str)
    payload = json.loads(raw_message)
    taskspec_payload = payload["taskspec"]
    assert taskspec_payload["metadata"].get(INTERNAL_RUNTIME_TASK_CLASS_KEY) is None
    assert taskspec_payload["metadata"].get(INTERNAL_RUNTIME_ENDPOINT_NAME_KEY) is None
    assert (
        payload[INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY]
        == INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT
    )
    assert (
        payload[INTERNAL_RUNTIME_ENVELOPE_ENDPOINT_NAME_KEY]
        == INTERNAL_HEARTBEAT_ENDPOINT_NAME
    )


def test_internal_submit_spawn_request_can_target_internal_queue(
    tmp_path: Path,
) -> None:
    context = build_context(spec_context=tmp_path)
    tid = str(time.time_ns())

    submit_spawn_request(
        context.broker_target,
        taskspec=_heartbeat_service_taskspec(tid, tmp_path),
        work_payload=None,
        config=context.broker_config,
        tid=tid,
        inherited_weft_context=str(context.root),
        allow_internal_runtime=True,
        spawn_queue_name=WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE,
    )

    internal_queue = context.queue(WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE, persistent=False)
    public_queue = context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=False)
    try:
        assert public_queue.read_one() is None
        raw_message = internal_queue.read_one()
    finally:
        internal_queue.close()
        public_queue.close()

    assert isinstance(raw_message, str)
    payload = json.loads(raw_message)
    assert (
        payload[INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY]
        == INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT
    )


def _registration_rows(context: Any) -> list[tuple[dict[str, Any], int]]:
    with context.broker() as reader:
        return [
            (json.loads(body), int(timestamp))
            for body, timestamp in reader.peek_generator(
                WEFT_HEARTBEAT_REGISTRATIONS_QUEUE, with_timestamps=True
            )
        ]


def test_upsert_heartbeat_writes_one_row_and_returns_without_manager(
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Registration is one row write; the helper starts nothing [MF-3.2]."""

    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())

    def fail(*args: object, **kwargs: object) -> None:
        del args, kwargs
        pytest.fail("helper must not start a manager")

    monkeypatch.setattr(core_manager_runtime, "ensure_manager", fail)
    # Typed as returning object so the literal None check is expressible.
    write_and_return: Callable[..., object] = upsert_heartbeat

    assert (
        write_and_return(
            context,
            registrant_tid=tid,
            heartbeat_id="probe",
            interval_seconds=60,
            lease_seconds=3600,
            destination_queue="probe.dest",
            message="tick",
        )
        is None
    )

    rows = _registration_rows(context)
    assert [row for row, _timestamp in rows] == [
        {
            "heartbeat_id": "probe",
            "registrant_tid": tid,
            "interval_seconds": 60,
            "lease_seconds": 3600,
            "destination_queue": "probe.dest",
            "message": "tick",
        }
    ]
    with context.broker() as reader:
        assert reader.peek_one(WEFT_ENDPOINTS_REGISTRY_QUEUE) is None
        assert reader.peek_one(WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE) is None


def test_upsert_heartbeat_replaces_by_heartbeat_id(workdir: Path) -> None:
    """An upsert with the same id replaces the row and its lease [MF-3.2]."""

    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())
    upsert_heartbeat(
        context,
        registrant_tid=tid,
        heartbeat_id="probe",
        interval_seconds=60,
        lease_seconds=3600,
        destination_queue="probe.dest",
        message="tick",
    )
    first_id = _registration_rows(context)[0][1]
    upsert_heartbeat(
        context,
        registrant_tid=tid,
        heartbeat_id="probe",
        interval_seconds=120,
        lease_seconds=7200,
        destination_queue="probe.dest",
        message="tick",
    )

    rows = _registration_rows(context)
    assert len(rows) == 1
    row, second_id = rows[0]
    assert row["interval_seconds"] == 120
    assert row["lease_seconds"] == 7200
    assert second_id > first_id

    upsert_heartbeat(
        context,
        registrant_tid=tid,
        heartbeat_id="other",
        interval_seconds=60,
        lease_seconds=3600,
        destination_queue="probe.dest",
        message="tick",
    )
    assert sorted(row["heartbeat_id"] for row, _ in _registration_rows(context)) == [
        "other",
        "probe",
    ]


def test_cancel_heartbeat_deletes_every_row_for_its_id(
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancel deletes every row of its canonical id and nothing else [MF-3.2]."""

    context = build_context(spec_context=workdir)
    tid = str(time.time_ns())

    def fail(*args: object, **kwargs: object) -> None:
        del args, kwargs
        pytest.fail("helper must not start a manager")

    monkeypatch.setattr(core_manager_runtime, "ensure_manager", fail)

    def row(heartbeat_id: str) -> str:
        return json.dumps(
            {
                "heartbeat_id": heartbeat_id,
                "registrant_tid": tid,
                "interval_seconds": 60,
                "lease_seconds": 3600,
                "destination_queue": "probe.dest",
                "message": "tick",
            }
        )

    with context.broker() as writer:
        writer.write(WEFT_HEARTBEAT_REGISTRATIONS_QUEUE, row("a"))
        writer.write(WEFT_HEARTBEAT_REGISTRATIONS_QUEUE, row("a"))
        writer.write(WEFT_HEARTBEAT_REGISTRATIONS_QUEUE, row("b"))

    # Typed as returning object so the literal None check is expressible.
    delete_and_return: Callable[..., object] = cancel_heartbeat
    assert delete_and_return(context, heartbeat_id="a") is None
    assert [row["heartbeat_id"] for row, _ in _registration_rows(context)] == ["b"]
    assert delete_and_return(context, heartbeat_id="unknown") is None
    assert [row["heartbeat_id"] for row, _ in _registration_rows(context)] == ["b"]

    upsert_heartbeat(
        context,
        registrant_tid=tid,
        heartbeat_id=" probe ",
        interval_seconds=60,
        lease_seconds=3600,
        destination_queue="probe.dest",
        message="tick",
    )
    assert sorted(row["heartbeat_id"] for row, _ in _registration_rows(context)) == [
        "b",
        "probe",
    ]
    cancel_heartbeat(context, heartbeat_id=" probe ")
    assert [row["heartbeat_id"] for row, _ in _registration_rows(context)] == ["b"]
    with pytest.raises(ValueError):
        cancel_heartbeat(context, heartbeat_id="  ")


@pytest.mark.parametrize(
    "overrides",
    [
        {"interval_seconds": 59},
        {"interval_seconds": 10**400, "lease_seconds": 10**400},
        {
            "interval_seconds": HEARTBEAT_MAX_LEASE_SECONDS + 1,
            "lease_seconds": HEARTBEAT_MAX_LEASE_SECONDS + 1,
        },
        {"interval_seconds": 60, "lease_seconds": 59},
        {"interval_seconds": 60, "lease_seconds": HEARTBEAT_MAX_LEASE_SECONDS + 1},
        {"interval_seconds": 60, "lease_seconds": 10**400},
        {"destination_queue": "weft.x"},
        {"destination_queue": ""},
        {"registrant_tid": "nope"},
        {"heartbeat_id": ""},
        {"message": object()},
    ],
    ids=[
        "interval-below-minimum",
        "interval-overflow",
        "interval-above-maximum",
        "lease-below-interval",
        "lease-above-maximum",
        "lease-overflow",
        "weft-destination",
        "empty-destination",
        "bad-registrant",
        "empty-id",
        "unserializable-message",
    ],
)
def test_upsert_heartbeat_rejects_invalid_rows_before_writing(
    workdir: Path,
    overrides: dict[str, Any],
) -> None:
    """The writer rejects every unschedulable or unbounded row [MF-3.2]."""

    context = build_context(spec_context=workdir)
    arguments: dict[str, Any] = {
        "registrant_tid": str(time.time_ns()),
        "heartbeat_id": "probe",
        "interval_seconds": 60,
        "lease_seconds": 3600,
        "destination_queue": "probe.dest",
        "message": "tick",
    }
    arguments.update(overrides)

    with pytest.raises(ValueError):
        upsert_heartbeat(context, **arguments)

    assert _registration_rows(context) == []
