"""Runtime endpoint registration tests for BaseTask helpers.

Spec references:
- docs/specifications/01-Core_Components.md [CC-2.4.1]
- docs/specifications/05-Message_Flow_and_State.md [MF-3.1]
"""

from __future__ import annotations

import json
import time

import pytest

from simplebroker import Queue
from tests.helpers.test_backend import prepare_project_root
from tests.tasks.test_task_execution import make_function_taskspec
from weft._constants import (
    INTERNAL_HEARTBEAT_ENDPOINT_NAME,
    INTERNAL_RUNTIME_ENDPOINT_NAME_KEY,
    INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT,
    INTERNAL_RUNTIME_TASK_CLASS_KEY,
    WEFT_ENDPOINTS_REGISTRY_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
)
from weft.context import WeftContext, build_context
from weft.core.endpoints import (
    build_endpoint_record_payload,
    list_resolved_endpoints,
)
from weft.core.tasks import Consumer, HeartbeatTask
from weft.core.taskspec import TaskSpec
from weft.helpers import iter_queue_json_entries


def _entries(queue) -> list[dict[str, object]]:
    return [payload for payload, _message_id in iter_queue_json_entries(queue)]


def _endpoint_record(
    queue: Queue,
    *,
    name: str,
    tid: str,
    status: str = "active",
) -> int:
    payload = build_endpoint_record_payload(
        name=name,
        tid=tid,
        inbox=f"T{tid}.inbox",
        outbox=f"T{tid}.outbox",
        ctrl_in=f"T{tid}.ctrl_in",
        ctrl_out=f"T{tid}.ctrl_out",
    )
    payload["status"] = status
    return queue.write(json.dumps(payload))


def _mark_endpoint_owner_live(ctx: WeftContext, tid: str) -> None:
    queue = ctx.queue(WEFT_TID_MAPPINGS_QUEUE, persistent=False)
    try:
        queue.write(json.dumps({"full": tid, "short": tid[-10:]}))
    finally:
        queue.close()


def _registry_message_ids(queue: Queue) -> set[int]:
    return {int(message_id) for _payload, message_id in iter_queue_json_entries(queue)}


class _CloseTrackingQueue:
    """Forward a real queue while observing close and one injected defect."""

    def __init__(self, queue: Queue, *, delete_defect: bool) -> None:
        self._queue = queue
        self._delete_defect = delete_defect
        self.closed = False

    def __getattr__(self, name: str) -> object:
        return getattr(self._queue, name)

    def delete(self, *, message_id: int | str | None = None) -> bool:
        if self._delete_defect:
            raise AssertionError("unexpected delete defect")
        return self._queue.delete(message_id=message_id)

    def close(self) -> None:
        self.closed = True
        self._queue.close()


@pytest.fixture
def unique_tid() -> str:
    return str(time.time_ns())


def test_task_can_register_and_unregister_named_endpoint(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload")
    task = Consumer(db_path, spec)
    registry = make_queue(WEFT_ENDPOINTS_REGISTRY_QUEUE)

    try:
        task.register_endpoint_name(
            "mayor",
            metadata={"role": "operator-facing"},
        )
        records = _entries(registry)
        assert len(records) == 1
        assert records[0]["name"] == "mayor"
        assert records[0]["tid"] == unique_tid
        assert records[0]["inbox"] == spec.io.inputs["inbox"]
        assert records[0]["ctrl_in"] == spec.io.control["ctrl_in"]
        assert records[0]["metadata"] == {"role": "operator-facing"}

        task.unregister_endpoint_name()
        assert _entries(registry) == []
    finally:
        task.cleanup()
        registry.close()


def test_task_reregistration_replaces_prior_endpoint_claim(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload")
    task = Consumer(db_path, spec)
    registry = make_queue(WEFT_ENDPOINTS_REGISTRY_QUEUE)

    try:
        task.register_endpoint_name("mayor")
        task.register_endpoint_name("supervisor.daily")

        records = _entries(registry)
        assert len(records) == 1
        assert records[0]["name"] == "supervisor.daily"
        assert records[0]["tid"] == unique_tid
    finally:
        task.cleanup()
        registry.close()


def test_task_endpoint_name_validation_rejects_invalid_names(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload")
    task = Consumer(db_path, spec)
    registry = make_queue(WEFT_ENDPOINTS_REGISTRY_QUEUE)

    try:
        try:
            task.register_endpoint_name("bad name")
        except ValueError as exc:
            assert "endpoint name" in str(exc)
        else:  # pragma: no cover - guard
            raise AssertionError("register_endpoint_name should reject invalid names")

        assert _entries(registry) == []
    finally:
        task.cleanup()
        registry.close()


def test_task_endpoint_name_validation_rejects_reserved_internal_names(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload")
    task = Consumer(db_path, spec)
    registry = make_queue(WEFT_ENDPOINTS_REGISTRY_QUEUE)

    try:
        with pytest.raises(ValueError, match="reserved for internal runtime services"):
            task.register_endpoint_name("_weft.heartbeat")

        assert _entries(registry) == []
    finally:
        task.cleanup()
        registry.close()


def test_internal_runtime_task_can_claim_reserved_internal_endpoint_name(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    spec_payload = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    ).model_dump(mode="json")
    spec_payload["spec"]["persistent"] = True
    spec = TaskSpec.model_validate(spec_payload)
    spec.metadata[INTERNAL_RUNTIME_TASK_CLASS_KEY] = (
        INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT
    )
    spec.metadata[INTERNAL_RUNTIME_ENDPOINT_NAME_KEY] = INTERNAL_HEARTBEAT_ENDPOINT_NAME
    task = HeartbeatTask(db_path, spec)
    registry = make_queue(WEFT_ENDPOINTS_REGISTRY_QUEUE)

    try:
        records = _entries(registry)
        assert len(records) == 1
        assert records[0]["name"] == INTERNAL_HEARTBEAT_ENDPOINT_NAME
        assert records[0]["tid"] == unique_tid
    finally:
        task.stop(join=False)
        task.cleanup()
        registry.close()


def test_endpoint_resolution_uses_latest_owner_row_for_view_and_stale_delete(
    tmp_path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    registry = ctx.queue(WEFT_ENDPOINTS_REGISTRY_QUEUE, persistent=False)
    stale_tid = "1770000000000000010"
    inactive_tid = "1770000000000000011"

    try:
        older_stale_id = _endpoint_record(registry, name="ghost", tid=stale_tid)
        newest_stale_id = _endpoint_record(registry, name="ghost", tid=stale_tid)
        older_live_id = _endpoint_record(registry, name="retired", tid=inactive_tid)
        newest_inactive_id = _endpoint_record(
            registry,
            name="retired",
            tid=inactive_tid,
            status="inactive",
        )
        _mark_endpoint_owner_live(ctx, inactive_tid)

        assert list_resolved_endpoints(ctx) == []
        assert _registry_message_ids(registry) == {
            older_stale_id,
            older_live_id,
            newest_inactive_id,
        }
        assert newest_stale_id not in _registry_message_ids(registry)
    finally:
        registry.close()


@pytest.mark.parametrize(
    "owner_order",
    [
        ("high", "stale", "low", "alpha"),
        ("alpha", "low", "stale", "high"),
    ],
)
def test_endpoint_resolution_is_order_independent_and_preserves_live_claimants(
    tmp_path,
    owner_order: tuple[str, ...],
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    registry = ctx.queue(WEFT_ENDPOINTS_REGISTRY_QUEUE, persistent=False)
    low_tid = "1770000000000000020"
    high_tid = "1770000000000000030"
    stale_tid = "1770000000000000040"
    alpha_tid = "1770000000000000050"
    owners = {
        "low": ("mayor", low_tid),
        "high": ("mayor", high_tid),
        "stale": ("mayor", stale_tid),
        "alpha": ("alpha", alpha_tid),
    }
    message_ids: dict[str, int] = {}

    try:
        for owner in owner_order:
            name, tid = owners[owner]
            message_ids[owner] = _endpoint_record(registry, name=name, tid=tid)
        for tid in (low_tid, high_tid, alpha_tid):
            _mark_endpoint_owner_live(ctx, tid)

        resolved = list_resolved_endpoints(ctx)

        assert [item.record.name for item in resolved] == ["alpha", "mayor"]
        assert resolved[0].record.tid == alpha_tid
        assert resolved[0].live_candidates == 1
        assert resolved[1].record.tid == low_tid
        assert resolved[1].live_candidates == 2
        assert _registry_message_ids(registry) == {
            message_ids["alpha"],
            message_ids["high"],
            message_ids["low"],
        }
    finally:
        registry.close()


def test_endpoint_resolution_continues_after_operational_delete_failure(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    registry = ctx.queue(WEFT_ENDPOINTS_REGISTRY_QUEUE, persistent=False)
    stale_ids = {
        _endpoint_record(
            registry,
            name="ghost.one",
            tid="1770000000000000060",
        ),
        _endpoint_record(
            registry,
            name="ghost.two",
            tid="1770000000000000070",
        ),
    }
    live_tid = "1770000000000000080"
    live_id = _endpoint_record(registry, name="live", tid=live_tid)
    _mark_endpoint_owner_live(ctx, live_tid)
    queue_type = type(registry)
    original_delete = queue_type.delete
    delete_calls: list[int] = []

    def flaky_delete(
        queue: Queue,
        *,
        message_id: int | str | None = None,
    ) -> bool:
        if queue.name != WEFT_ENDPOINTS_REGISTRY_QUEUE:
            return original_delete(queue, message_id=message_id)
        assert message_id is not None
        delete_calls.append(int(message_id))
        if len(delete_calls) == 1:
            raise RuntimeError("transient delete failure")
        return original_delete(queue, message_id=message_id)

    monkeypatch.setattr(queue_type, "delete", flaky_delete)

    try:
        resolved = list_resolved_endpoints(ctx)

        assert [(item.record.name, item.record.tid) for item in resolved] == [
            ("live", live_tid)
        ]
        assert set(delete_calls) == stale_ids
        assert len(delete_calls) == 2
        assert _registry_message_ids(registry) == {live_id, delete_calls[0]}
    finally:
        registry.close()


def test_endpoint_resolution_propagates_unexpected_delete_defect(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    registry = ctx.queue(WEFT_ENDPOINTS_REGISTRY_QUEUE, persistent=False)
    stale_id = _endpoint_record(
        registry,
        name="ghost",
        tid="1770000000000000090",
    )
    queue_type = type(registry)
    original_delete = queue_type.delete

    def defective_delete(
        queue: Queue,
        *,
        message_id: int | str | None = None,
    ) -> bool:
        if queue.name == WEFT_ENDPOINTS_REGISTRY_QUEUE:
            raise AssertionError("unexpected delete defect")
        return original_delete(queue, message_id=message_id)

    monkeypatch.setattr(queue_type, "delete", defective_delete)

    try:
        with pytest.raises(AssertionError, match="unexpected delete defect"):
            list_resolved_endpoints(ctx)
        assert _registry_message_ids(registry) == {stale_id}
    finally:
        registry.close()


def test_endpoint_resolution_deletes_stale_rows_before_selection_defect(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    registry = ctx.queue(WEFT_ENDPOINTS_REGISTRY_QUEUE, persistent=False)
    stale_id = _endpoint_record(
        registry,
        name="ghost",
        tid="1770000000000000091",
    )
    live_tid = "1770000000000000092"
    live_id = _endpoint_record(registry, name="live", tid=live_tid)
    _mark_endpoint_owner_live(ctx, live_tid)
    original_queue = WeftContext.queue
    acquired: list[_CloseTrackingQueue] = []

    def tracking_queue(
        context: WeftContext,
        name: str,
        *,
        persistent: bool = False,
    ) -> Queue | _CloseTrackingQueue:
        queue = original_queue(context, name, persistent=persistent)
        if context is ctx and name == WEFT_ENDPOINTS_REGISTRY_QUEUE:
            tracked = _CloseTrackingQueue(queue, delete_defect=False)
            acquired.append(tracked)
            return tracked
        return queue

    def selection_defect(_tids: object) -> str | None:
        raise AssertionError("unexpected selection defect")

    monkeypatch.setattr(WeftContext, "queue", tracking_queue)
    monkeypatch.setattr(
        "weft.core.endpoints.canonical_owner_tid",
        selection_defect,
    )

    try:
        with pytest.raises(AssertionError, match="unexpected selection defect"):
            list_resolved_endpoints(ctx)

        assert _registry_message_ids(registry) == {live_id}
        assert stale_id not in _registry_message_ids(registry)
        assert len(acquired) == 1
        assert acquired[0].closed is True
    finally:
        registry.close()


def test_endpoint_resolution_pattern_does_not_delete_nonmatching_stale_row(
    tmp_path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    registry = ctx.queue(WEFT_ENDPOINTS_REGISTRY_QUEUE, persistent=False)

    try:
        matching_id = _endpoint_record(
            registry,
            name="wanted.ghost",
            tid="1770000000000000100",
        )
        outside_id = _endpoint_record(
            registry,
            name="outside.ghost",
            tid="1770000000000000110",
        )

        assert list_resolved_endpoints(ctx, pattern="wanted.*") == []
        assert _registry_message_ids(registry) == {outside_id}
        assert matching_id not in _registry_message_ids(registry)
    finally:
        registry.close()


@pytest.mark.parametrize(
    "delete_defect",
    [False, True],
    ids=["success", "unexpected-delete-defect"],
)
def test_endpoint_resolution_closes_acquired_registry_queue(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    delete_defect: bool,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    registry = ctx.queue(WEFT_ENDPOINTS_REGISTRY_QUEUE, persistent=False)
    tid = "1770000000000000120"
    _endpoint_record(registry, name="close-proof", tid=tid)
    if not delete_defect:
        _mark_endpoint_owner_live(ctx, tid)

    original_queue = WeftContext.queue
    acquired: list[_CloseTrackingQueue] = []

    def tracking_queue(
        context: WeftContext,
        name: str,
        *,
        persistent: bool = False,
    ) -> Queue | _CloseTrackingQueue:
        queue = original_queue(context, name, persistent=persistent)
        if context is ctx and name == WEFT_ENDPOINTS_REGISTRY_QUEUE:
            tracked = _CloseTrackingQueue(queue, delete_defect=delete_defect)
            acquired.append(tracked)
            return tracked
        return queue

    monkeypatch.setattr(WeftContext, "queue", tracking_queue)

    try:
        if delete_defect:
            with pytest.raises(AssertionError, match="unexpected delete defect"):
                list_resolved_endpoints(ctx)
        else:
            resolved = list_resolved_endpoints(ctx)
            assert [(item.record.name, item.record.tid) for item in resolved] == [
                ("close-proof", tid)
            ]

        assert len(acquired) == 1
        assert acquired[0].closed is True
    finally:
        registry.close()
