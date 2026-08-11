"""Tests for shared runtime service convergence primitives."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from simplebroker import Queue
from tests.helpers.test_backend import prepare_project_root
from weft.commands import system as system_commands
from weft.context import build_context
from weft.core import manager_runtime
from weft.core.monitor.task_monitor import TaskMonitor
from weft.core.service_convergence import (
    LIVE_SERVICE_STATUSES,
    SERVICE_OWNER_SCHEMA,
    SERVICE_STATUS_SUPERSEDED,
    ServiceOwnerRecord,
    _service_owner_schema_version,
    build_manager_service_payload,
    build_service_owner_payload,
    collect_service_owner_records,
    discard_v1_service_registry_rows,
    parse_service_owner_record,
    plan_service_owner_history_prune,
    project_manager_service_record,
    reduce_service_ownership,
    select_canonical_live_owner,
)

pytestmark = [pytest.mark.shared]


def _owner_payload(
    tid: str,
    *,
    service_key: str = "manager:weft.spawn.requests:file:/tmp/weft.db",
    service_type: str = "manager",
    status: str = "active",
    queues: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": SERVICE_OWNER_SCHEMA,
        "service_key": service_key,
        "service_type": service_type,
        "owner_tid": tid,
        "name": "manager" if service_type == "manager" else "service",
        "status": status,
        "queues": dict(queues or {}),
        "runtime_handle": {},
        "metadata": {},
    }
    if service_type == "manager":
        payload.update(role="manager", capabilities=[])
    return payload


def _record(
    tid: str,
    *,
    service_key: str = "manager:weft.spawn.requests:file:/tmp/weft.db",
    status: str = "active",
    timestamp: int,
) -> ServiceOwnerRecord:
    payload = _owner_payload(tid, service_key=service_key, status=status)
    parsed = parse_service_owner_record(payload, timestamp=timestamp)
    assert parsed is not None
    return parsed


def test_select_canonical_live_owner_uses_numeric_tid_order() -> None:
    records = (
        _record("10", timestamp=100),
        _record("2", timestamp=101),
    )

    assert select_canonical_live_owner(records).owner_tid == "2"


def test_superseded_owner_is_accepted_but_not_live() -> None:
    record = _record(
        "2",
        status=SERVICE_STATUS_SUPERSEDED,
        timestamp=100,
    )

    assert record.status == SERVICE_STATUS_SUPERSEDED
    assert select_canonical_live_owner((record,)) is None
    assert SERVICE_STATUS_SUPERSEDED not in LIVE_SERVICE_STATUSES


def test_latest_superseded_row_excludes_older_active_owner() -> None:
    decision = reduce_service_ownership(
        "manager:weft.spawn.requests:file:/tmp/weft.db",
        (
            _record("2", status="active", timestamp=100),
            _record("2", status=SERVICE_STATUS_SUPERSEDED, timestamp=200),
            _record("5", status="active", timestamp=150),
        ),
        own_tid=None,
        now_ns=1_000,
        ttl_ns=950,
    )

    assert decision.canonical_live is not None
    assert decision.canonical_live.owner_tid == "5"


@pytest.mark.parametrize("owner_tid", ["not-a-tid", "²", "٢"])
def test_parse_service_owner_rejects_noncanonical_owner_tid(owner_tid: str) -> None:
    payload = _owner_payload(owner_tid)
    assert (
        parse_service_owner_record(
            payload,
            timestamp=1,
        )
        is None
    )


def test_collect_service_owner_records_filters_before_projection() -> None:
    read = collect_service_owner_records(
        (
            (
                _owner_payload(
                    "1",
                    service_key="internal:heartbeat:file:/tmp/weft.db",
                    service_type="internal",
                ),
                1,
            ),
            (
                _owner_payload("2"),
                2,
            ),
        ),
        service_key="manager:weft.spawn.requests:file:/tmp/weft.db",
        service_type="manager",
    )

    assert [record.owner_tid for record in read.records] == ["2"]


def test_reduce_service_ownership_prunes_expired_and_older_self_rows() -> None:
    decision = reduce_service_ownership(
        "manager:weft.spawn.requests:file:/tmp/weft.db",
        (
            _record("5", timestamp=100),
            _record("5", timestamp=200),
            _record("1", timestamp=10),
        ),
        own_tid="5",
        now_ns=1_000,
        ttl_ns=950,
    )

    assert decision.expired_message_ids == (10,)
    assert decision.older_self_message_ids == (100,)
    assert decision.canonical_live.owner_tid == "5"


def test_service_owner_history_prune_prefers_live_owner_over_newer_terminal() -> None:
    service_key = "internal:heartbeat:file:/tmp/weft.db"
    active_id = 100
    terminal_id = 120_000_000_000

    prune_ids = plan_service_owner_history_prune(
        (
            _record(
                "100", service_key=service_key, status="active", timestamp=active_id
            ),
            _record(
                "200",
                service_key=service_key,
                status="terminal",
                timestamp=terminal_id,
            ),
        ),
        service_key=service_key,
        now_ns=130_000_000_000,
        ttl_ns=300_000_000_000,
        keep_recent_per_key=1,
    )

    assert active_id not in prune_ids
    assert terminal_id in prune_ids


def test_service_owner_history_prune_allows_terminal_to_supersede_same_owner() -> None:
    service_key = "internal:heartbeat:file:/tmp/weft.db"
    active_id = 100
    terminal_id = 120_000_000_000

    prune_ids = plan_service_owner_history_prune(
        (
            _record(
                "100", service_key=service_key, status="active", timestamp=active_id
            ),
            _record(
                "100",
                service_key=service_key,
                status="terminal",
                timestamp=terminal_id,
            ),
        ),
        service_key=service_key,
        now_ns=130_000_000_000,
        ttl_ns=300_000_000_000,
        keep_recent_per_key=1,
    )

    assert active_id in prune_ids
    assert terminal_id not in prune_ids


def test_recent_lower_live_owner_suppresses_higher_owner() -> None:
    decision = reduce_service_ownership(
        "manager:weft.spawn.requests:file:/tmp/weft.db",
        (
            _record("2", timestamp=900),
            _record("5", timestamp=950),
        ),
        own_tid="5",
        now_ns=1_000,
        ttl_ns=500,
    )

    assert decision.recent_lower_live_owner is True
    assert decision.canonical_live.owner_tid == "2"
    assert {record.status for record in decision.records} <= LIVE_SERVICE_STATUSES


def test_unsupported_schema_is_ignored() -> None:
    assert (
        parse_service_owner_record(
            {
                "schema": "weft.service_owner.v0",
                "service_key": "manager:weft.spawn.requests:file:/tmp/weft.db",
                "service_type": "manager",
                "owner_tid": "1",
                "status": "active",
            },
            timestamp=1,
        )
        is None
    )


def test_non_manager_service_owner_is_not_projected_as_manager_record() -> None:
    projected = project_manager_service_record(
        _owner_payload(
            "1",
            service_key="internal:heartbeat:file:/tmp/weft.db",
            service_type="internal",
        ),
        timestamp=1,
    )

    assert projected is None


def test_manager_service_payload_is_strict_v2_and_projection_derives_queues(
    tmp_path: Path,
) -> None:
    ctx = build_context(spec_context=tmp_path)
    payload = build_manager_service_payload(
        context=ctx,
        tid="1",
        name="manager",
        status="stopped",
        queues={
            "requests": "weft.spawn.requests",
            "ctrl_in": "old.ctrl_in",
            "ctrl_out": "old.ctrl_out",
            "outbox": "weft.manager.outbox",
        },
        runtime_handle={},
    )
    assert payload["schema"] == "weft.service_owner.v2"
    assert set(payload) == {
        "schema",
        "service_key",
        "service_type",
        "owner_tid",
        "name",
        "status",
        "queues",
        "runtime_handle",
        "metadata",
        "role",
        "capabilities",
    }
    assert "tid" not in payload
    assert "ctrl_in" not in payload
    assert "inbox" not in payload

    projected = project_manager_service_record(payload, timestamp=1)

    assert projected is not None
    assert projected["tid"] == "1"
    assert projected["ctrl_in"] == "old.ctrl_in"
    assert projected["inbox"] == "weft.spawn.requests"


def test_generic_service_payload_has_only_required_v2_keys() -> None:
    payload = build_service_owner_payload(
        service_key="managed:heartbeat",
        service_type="managed",
        owner_tid="2",
        status="active",
        name="heartbeat",
        queues={"ctrl_in": "T2.ctrl_in"},
    )

    assert set(payload) == {
        "schema",
        "service_key",
        "service_type",
        "owner_tid",
        "name",
        "status",
        "queues",
        "runtime_handle",
        "metadata",
    }
    assert payload["queues"] == {"ctrl_in": "T2.ctrl_in"}


@pytest.mark.parametrize("extra_key", ["tid", "ctrl_in", "inbox", "legacy_role"])
def test_parser_rejects_v2_rows_with_noncanonical_compatibility_fields(
    extra_key: str,
) -> None:
    payload = _owner_payload("1")
    payload[extra_key] = "compat"

    assert parse_service_owner_record(payload, timestamp=1) is None


def _write_schema_row(queue: Queue, schema: Any) -> int:
    return int(queue.write(json.dumps({"schema": schema})))


def _all_queue_message_ids(queue: Queue) -> set[int]:
    return {
        int(row[1])
        for row in queue.peek_generator(
            with_timestamps=True,
            include_claimed=True,
        )
        if isinstance(row, tuple)
    }


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ("weft.service_owner.v1", 1),
        ("weft.service_owner.v2", 2),
        ("weft.service_owner.v3", 3),
    ],
)
def test_service_owner_schema_version_accepts_canonical_ascii_suffixes(
    schema: str,
    expected: int,
) -> None:
    assert _service_owner_schema_version(json.dumps({"schema": schema})) == expected


@pytest.mark.parametrize(
    "schema",
    [
        "weft.service_owner.v01",
        "weft.service_owner.v02",
        "weft.service_owner.v١",
        "weft.service_owner.v２",
        "weft.service_owner.v+1",
        "weft.service_owner.v1.0",
    ],
)
def test_service_owner_schema_version_rejects_noncanonical_suffixes(
    schema: str,
) -> None:
    assert _service_owner_schema_version(json.dumps({"schema": schema})) is None


class _TaskMonitorContextStub:
    def __init__(self, context: Any) -> None:
        self._context = context

    def _monitor_context(self) -> Any:
        return self._context


def _read_service_registry_surface(surface: str, context: Any) -> object:
    if surface == "manager-runtime":
        return manager_runtime.list_manager_records(context, prune_stale=False)
    if surface == "system-status":
        return system_commands._collect_service_registry_evidence(context, now_ns=0)
    if surface == "task-monitor":
        monitor = _TaskMonitorContextStub(context)
        return TaskMonitor._latest_service_owner_records(monitor)  # type: ignore[arg-type]
    raise AssertionError(f"unknown service-registry surface: {surface}")


@pytest.mark.parametrize(
    "surface",
    ["manager-runtime", "system-status", "task-monitor"],
)
def test_service_registry_surfaces_discard_v1_before_reading(
    tmp_path: Path,
    surface: str,
) -> None:
    root = prepare_project_root(tmp_path / surface)
    context = build_context(spec_context=root)
    queue = context.queue("weft.state.services", persistent=False)
    try:
        v1_id = _write_schema_row(queue, "weft.service_owner.v1")
    finally:
        queue.close()

    assert not _read_service_registry_surface(surface, context)

    queue = context.queue("weft.state.services", persistent=False)
    try:
        remaining_ids = _all_queue_message_ids(queue)
    finally:
        queue.close()
    assert v1_id not in remaining_ids
    assert remaining_ids == set()


@pytest.mark.parametrize(
    "surface",
    ["manager-runtime", "system-status", "task-monitor"],
)
def test_service_registry_surfaces_propagate_future_schema(
    tmp_path: Path,
    surface: str,
) -> None:
    root = prepare_project_root(tmp_path / surface)
    context = build_context(spec_context=root)
    queue = context.queue("weft.state.services", persistent=False)
    try:
        future_id = _write_schema_row(queue, "weft.service_owner.v3")
    finally:
        queue.close()

    with pytest.raises(ValueError, match="future service-owner schema"):
        _read_service_registry_surface(surface, context)

    queue = context.queue("weft.state.services", persistent=False)
    try:
        remaining_ids = _all_queue_message_ids(queue)
    finally:
        queue.close()
    assert remaining_ids == {future_id}


def test_discard_v1_rows_scans_claimed_and_preserves_other_rows(tmp_path: Path) -> None:
    queue = Queue("weft.state.services", db_path=tmp_path / "weft.db")
    v1_pending = _write_schema_row(queue, "weft.service_owner.v1")
    v1_claimed = _write_schema_row(queue, "weft.service_owner.v1")
    assert queue.read_one(exact_timestamp=v1_claimed) is not None
    preserved_ids = {
        _write_schema_row(queue, SERVICE_OWNER_SCHEMA),
        _write_schema_row(queue, "weft.service_owner.v0"),
        _write_schema_row(queue, "weft.service_owner.v²"),
        _write_schema_row(queue, None),
        int(queue.write("not-json")),
    }

    discard_v1_service_registry_rows(queue)

    rows = list(queue.peek_generator(with_timestamps=True, include_claimed=True))
    assert {int(message_id) for _body, message_id in rows} == preserved_ids
    assert v1_pending not in preserved_ids
    assert v1_claimed not in preserved_ids

    discard_v1_service_registry_rows(queue)

    rows_after_second_bootstrap = list(
        queue.peek_generator(with_timestamps=True, include_claimed=True)
    )
    assert {
        int(message_id) for _body, message_id in rows_after_second_bootstrap
    } == preserved_ids


def test_discard_v1_rows_fails_if_v1_reappears_during_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = Queue("weft.state.services", db_path=tmp_path / "weft.db")
    _write_schema_row(queue, "weft.service_owner.v1")
    original_delete_many = Queue.delete_many
    reappeared_id: int | None = None

    def delete_many_with_race(self: Queue, message_ids: list[int]) -> int:
        nonlocal reappeared_id
        deleted = original_delete_many(self, message_ids)
        reappeared_id = _write_schema_row(self, "weft.service_owner.v1")
        return deleted

    monkeypatch.setattr(Queue, "delete_many", delete_many_with_race)

    with pytest.raises(RuntimeError, match="v1 service-owner rows remain"):
        discard_v1_service_registry_rows(queue)

    assert reappeared_id is not None
    remaining_ids = {
        int(message_id)
        for _body, message_id in queue.peek_generator(
            with_timestamps=True,
            include_claimed=True,
        )
    }
    assert remaining_ids == {reappeared_id}


def test_discard_v1_rows_rejects_future_schema_before_any_delete(
    tmp_path: Path,
) -> None:
    queue = Queue("weft.state.services", db_path=tmp_path / "weft.db")
    v1_id = _write_schema_row(queue, "weft.service_owner.v1")
    future_id = _write_schema_row(queue, "weft.service_owner.v3")

    with pytest.raises(ValueError, match="future service-owner schema"):
        discard_v1_service_registry_rows(queue)

    rows = list(queue.peek_generator(with_timestamps=True, include_claimed=True))
    assert {int(message_id) for _body, message_id in rows} == {v1_id, future_id}


def test_none_and_unknown_registry_states_are_distinct() -> None:
    none_decision = reduce_service_ownership(
        "manager:weft.spawn.requests:file:/tmp/weft.db",
        (),
        own_tid="1",
        now_ns=1_000,
        ttl_ns=500,
    )
    unknown_decision = reduce_service_ownership(
        "manager:weft.spawn.requests:file:/tmp/weft.db",
        (),
        own_tid="1",
        now_ns=1_000,
        ttl_ns=500,
        read_failed=True,
    )

    assert none_decision.state == "none"
    assert unknown_decision.state == "unknown"
