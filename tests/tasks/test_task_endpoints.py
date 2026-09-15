"""Runtime endpoint registration tests for BaseTask helpers.

Spec references:
- docs/specifications/01-Core_Components.md [CC-2.4.1]
- docs/specifications/05-Message_Flow_and_State.md [MF-3.1]
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import pytest

import weft.core.endpoints as endpoints_module
from simplebroker import Queue
from tests.helpers.test_backend import prepare_project_root
from tests.helpers.typing import BrokerEnv
from tests.tasks.test_task_execution import make_function_taskspec
from weft._constants import (
    INTERNAL_HEARTBEAT_ENDPOINT_NAME,
    INTERNAL_RUNTIME_ENDPOINT_NAME_KEY,
    INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT,
    INTERNAL_RUNTIME_TASK_CLASS_KEY,
    WEFT_ENDPOINTS_REGISTRY_QUEUE,
)
from weft.context import WeftContext, build_context
from weft.core.endpoints import (
    build_endpoint_record_payload,
    list_resolved_endpoints,
)
from weft.core.task_state import (
    task_state_queue_name,
)
from weft.core.tasks import Consumer, HeartbeatTask
from weft.core.taskspec import TaskSpec
from weft.helpers import iter_queue_json_entries, tid_short_form


def _entries(queue: Queue) -> list[dict[str, object]]:
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
    queue = ctx.queue(task_state_queue_name(tid), persistent=False)
    try:
        queue.write(json.dumps({"full": tid, "short": tid_short_form(tid)}))
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

    def delete(self, *, message_id: int | str) -> bool:
        if self._delete_defect:
            raise AssertionError("unexpected delete defect")
        return self._queue.delete(message_id=message_id)

    def close(self) -> None:
        self.closed = True
        self._queue.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


@pytest.fixture
def unique_tid() -> str:
    return str(time.time_ns())


@pytest.mark.parametrize("already_absent", [False, True])
def test_task_can_register_and_unregister_named_endpoint(
    broker_env: BrokerEnv,
    unique_tid: str,
    already_absent: bool,
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

        if already_absent:
            assert task._endpoint_registration_message_id is not None
            registry.delete(message_id=task._endpoint_registration_message_id)
        task.unregister_endpoint_name()
        assert _entries(registry) == []
        assert task._endpoint_registration_message_id is None
        assert task._endpoint_registration_name is None
    finally:
        task.cleanup()
        registry.close()


@pytest.mark.parametrize("second_name", ["mayor", "supervisor.daily"])
def test_task_second_registration_is_rejected_until_unregister(
    broker_env: BrokerEnv,
    unique_tid: str,
    second_name: str,
) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload")
    task = Consumer(db_path, spec)
    registry = make_queue(WEFT_ENDPOINTS_REGISTRY_QUEUE)

    try:
        task.register_endpoint_name("mayor")
        with pytest.raises(RuntimeError, match="claim"):
            task.register_endpoint_name(second_name)
        assert [row["name"] for row in _entries(registry)] == ["mayor"]
        task.unregister_endpoint_name()
        task.register_endpoint_name("supervisor.daily")
        records = _entries(registry)
        assert len(records) == 1
        assert records[0]["name"] == "supervisor.daily"
        assert records[0]["tid"] == unique_tid
    finally:
        task.cleanup()
        registry.close()


def test_task_endpoint_name_validation_rejects_invalid_names(
    broker_env: BrokerEnv,
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
    broker_env: BrokerEnv,
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
    broker_env: BrokerEnv,
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


def test_endpoint_resolution_uses_latest_owner_row_without_deleting_history(
    tmp_path: Path,
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
            newest_stale_id,
        }
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
    tmp_path: Path,
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
            message_ids["stale"],
        }
    finally:
        registry.close()


def test_endpoint_resolution_never_attempts_stale_row_deletion(
    tmp_path: Path,
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
        message_id: int | str,
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
        assert delete_calls == []
        assert _registry_message_ids(registry) == stale_ids | {live_id}
    finally:
        registry.close()


def test_endpoint_resolution_keeps_rows_and_closes_queue_after_liveness_defect(
    tmp_path: Path,
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

    def liveness_defect(*_args: object, **_kwargs: object) -> bool:
        raise AssertionError("unexpected liveness defect")

    monkeypatch.setattr(WeftContext, "queue", tracking_queue)
    monkeypatch.setattr(
        "weft.core.endpoints._record_owner_is_live",
        liveness_defect,
    )

    try:
        with pytest.raises(AssertionError, match="unexpected liveness defect"):
            list_resolved_endpoints(ctx)

        assert _registry_message_ids(registry) == {live_id, stale_id}
        assert len(acquired) == 1
        assert acquired[0].closed is True
    finally:
        registry.close()


def test_endpoint_resolution_pattern_preserves_all_stale_rows(
    tmp_path: Path,
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
        assert _registry_message_ids(registry) == {outside_id, matching_id}
    finally:
        registry.close()


@pytest.mark.parametrize(
    "delete_defect",
    [False, True],
    ids=["success", "unexpected-delete-defect"],
)
def test_endpoint_resolution_closes_acquired_registry_queue(
    tmp_path: Path,
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
        resolved = list_resolved_endpoints(ctx)
        expected = [] if delete_defect else [("close-proof", tid)]
        assert [(item.record.name, item.record.tid) for item in resolved] == expected

        assert len(acquired) == 1
        assert acquired[0].closed is True
    finally:
        registry.close()


def test_endpoint_claim_survives_resolution_before_mapping_publication(
    tmp_path: Path,
) -> None:
    context = build_context(spec_context=prepare_project_root(tmp_path))
    registry = context.queue(WEFT_ENDPOINTS_REGISTRY_QUEUE, persistent=False)
    tid = "1770000000000000200"
    try:
        message_id = _endpoint_record(registry, name="starting", tid=tid)
        assert list_resolved_endpoints(context) == []
        assert _registry_message_ids(registry) == {message_id}
        _mark_endpoint_owner_live(context, tid)
        assert [row.record.tid for row in list_resolved_endpoints(context)] == [tid]
        assert _registry_message_ids(registry) == {message_id}
    finally:
        registry.close()


def test_endpoint_claim_retains_append_id_across_interleaved_peer_append(
    broker_env: BrokerEnv, unique_tid: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, make_queue = broker_env
    task = Consumer(
        db_path,
        make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload"),
    )
    registry = make_queue(WEFT_ENDPOINTS_REGISTRY_QUEUE)
    original_queue = task._queue
    written_ids: list[int] = []
    scans = []

    class InterleavedQueue:
        def __getattr__(self, name: str) -> Any:
            return getattr(registry, name)

        def write(self, body: str) -> int:
            own_id = registry.write(body)
            peer = json.loads(body)
            peer["metadata"] = {"writer": "peer"}
            peer_id = registry.write(json.dumps(peer))
            written_ids.extend((own_id, peer_id))
            return own_id

        def peek_generator(self, **kwargs: Any) -> Any:
            scans.append(True)
            return registry.peek_generator(**kwargs)

    monkeypatch.setattr(
        task,
        "_queue",
        lambda name: (
            InterleavedQueue()
            if name == WEFT_ENDPOINTS_REGISTRY_QUEUE
            else original_queue(name)
        ),
    )
    try:
        task.register_endpoint_name("mayor")
        assert task._endpoint_registration_message_id == written_ids[0]
        assert scans == []
        task.unregister_endpoint_name()
        assert _registry_message_ids(registry) == {written_ids[1]}
        assert scans == []
    finally:
        task.cleanup()


def test_failed_endpoint_append_holds_no_claim_and_can_retry(
    broker_env: BrokerEnv, unique_tid: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, make_queue = broker_env
    task = Consumer(
        db_path,
        make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload"),
    )
    registry = make_queue(WEFT_ENDPOINTS_REGISTRY_QUEUE)
    original_queue = task._queue
    attempts = []

    class FailedAppendQueue:
        def __getattr__(self, name: str) -> Any:
            return getattr(registry, name)

        def write(self, body: str) -> int:
            attempts.append(body)
            if len(attempts) == 1:
                raise RuntimeError("injected endpoint append failure")
            return registry.write(body)

    monkeypatch.setattr(
        task,
        "_queue",
        lambda name: (
            FailedAppendQueue()
            if name == WEFT_ENDPOINTS_REGISTRY_QUEUE
            else original_queue(name)
        ),
    )
    try:
        task.register_endpoint_name("mayor")
        assert task._endpoint_registration_name is None
        assert task._endpoint_registration_message_id is None
        assert _entries(registry) == []
        task.register_endpoint_name("mayor")
        assert len(_entries(registry)) == 1
        task.unregister_endpoint_name()
        assert _entries(registry) == []
    finally:
        task.cleanup()


@pytest.mark.parametrize(
    "invalid",
    [
        "{broken-json",
        "[]",
        "null",
        {"full": "1770000000000000300"},
        {"full": "1770000000000000300", "short": "", "terminal": True},
        {"full": "1770000000000000300", "short": 1, "terminal": True},
        {"full": "", "short": "valid"},
        {"full": 3, "short": "valid"},
    ],
)
def test_latest_mapping_fold_skips_malformed_newer_rows_and_keeps_valid_neighbors(
    tmp_path: Path, invalid: object
) -> None:
    context = build_context(spec_context=prepare_project_root(tmp_path))
    queue = context.queue(
        task_state_queue_name("1770000000000000300"), persistent=False
    )
    valid = {"full": "1770000000000000300", "short": "0000000300", "terminal": False}
    neighbor = {"full": "1770000000000000301", "short": "required-display-field"}
    neighbor_queue = context.queue(
        task_state_queue_name(neighbor["full"]), persistent=False
    )
    try:
        queue.write(json.dumps({**valid, "terminal": True}))
        valid_id = queue.write(json.dumps(valid))
        queue.write(invalid if isinstance(invalid, str) else json.dumps(invalid))
        neighbor_id = neighbor_queue.write(json.dumps(neighbor))
        expected = {
            valid["full"]: (valid_id, valid),
            neighbor["full"]: (neighbor_id, neighbor),
        }
        assert endpoints_module.latest_tid_mapping_entries_for_endpoint_resolution(
            context
        ) == {full: row for full, (_timestamp, row) in expected.items()}
    finally:
        queue.close()
        neighbor_queue.close()


def test_failed_endpoint_unregister_retains_claim_for_exact_retry(
    broker_env: BrokerEnv, unique_tid: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, make_queue = broker_env
    task = Consumer(
        db_path,
        make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload"),
    )
    registry = make_queue(WEFT_ENDPOINTS_REGISTRY_QUEUE)
    task.register_endpoint_name("mayor")
    held_id = task._endpoint_registration_message_id
    original_queue = task._queue
    deletes = []

    class FailedDeleteQueue:
        def __getattr__(self, name: str) -> Any:
            return getattr(registry, name)

        def delete(self, *, message_id: int) -> bool:
            deletes.append(message_id)
            if len(deletes) == 1:
                raise RuntimeError("injected endpoint release failure")
            return registry.delete(message_id=message_id)

    monkeypatch.setattr(
        task,
        "_queue",
        lambda name: (
            FailedDeleteQueue()
            if name == WEFT_ENDPOINTS_REGISTRY_QUEUE
            else original_queue(name)
        ),
    )
    try:
        task.unregister_endpoint_name()
        assert task._endpoint_registration_message_id == held_id
        assert _registry_message_ids(registry) == {held_id}
        with pytest.raises(RuntimeError, match="claim"):
            task.register_endpoint_name("replacement")
        task.unregister_endpoint_name()
        assert deletes == [held_id, held_id]
        assert _registry_message_ids(registry) == set()
        task.register_endpoint_name("replacement")
        assert [row["name"] for row in _entries(registry)] == ["replacement"]
    finally:
        task.cleanup()


def test_endpoint_owner_snapshot_read_errors_propagate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed read after broker acquisition is never an empty state view."""
    context = build_context(spec_context=prepare_project_root(tmp_path))
    tid = "1770000000000000300"
    _mark_endpoint_owner_live(context, tid)
    with context.broker() as db:
        broker_type = type(db)
    original = broker_type.peek_many

    def failed_peek(self: Any, name: str, *args: Any, **kwargs: Any) -> Any:
        if name == task_state_queue_name(tid):
            raise RuntimeError("injected mapping read failure")
        return original(self, name, *args, **kwargs)

    monkeypatch.setattr(broker_type, "peek_many", failed_peek)
    with pytest.raises(RuntimeError, match="injected mapping read failure"):
        endpoints_module.latest_tid_mapping_entries_for_endpoint_resolution(
            context, tids=[tid]
        )
