"""Tests for shared runtime service convergence primitives."""

from __future__ import annotations

import pytest

from weft.context import build_context
from weft.core.service_convergence import (
    LIVE_SERVICE_STATUSES,
    SERVICE_OWNER_SCHEMA,
    SERVICE_STATUS_SUPERSEDED,
    ServiceOwnerRecord,
    build_manager_service_payload,
    collect_service_owner_records,
    parse_service_owner_record,
    plan_service_owner_history_prune,
    project_manager_service_record,
    reduce_service_ownership,
    select_canonical_live_owner,
    sync_manager_service_payload_top_level_queues,
)

pytestmark = [pytest.mark.shared]


def _record(
    tid: str,
    *,
    service_key: str = "manager:weft.spawn.requests:file:/tmp/weft.db",
    status: str = "active",
    timestamp: int,
) -> ServiceOwnerRecord:
    payload = {
        "schema": SERVICE_OWNER_SCHEMA,
        "service_key": service_key,
        "service_type": "manager",
        "owner_tid": tid,
        "status": status,
    }
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


def test_parse_service_owner_rejects_non_numeric_owner_tid() -> None:
    assert (
        parse_service_owner_record(
            {
                "schema": SERVICE_OWNER_SCHEMA,
                "service_key": "manager:weft.spawn.requests:file:/tmp/weft.db",
                "service_type": "manager",
                "owner_tid": "not-a-tid",
                "status": "active",
            },
            timestamp=1,
        )
        is None
    )


def test_collect_service_owner_records_filters_before_projection() -> None:
    read = collect_service_owner_records(
        (
            (
                {
                    "schema": SERVICE_OWNER_SCHEMA,
                    "service_key": "internal:heartbeat:file:/tmp/weft.db",
                    "service_type": "internal",
                    "owner_tid": "1",
                    "tid": "1",
                    "role": "manager",
                    "status": "active",
                },
                1,
            ),
            (
                {
                    "schema": SERVICE_OWNER_SCHEMA,
                    "service_key": "manager:weft.spawn.requests:file:/tmp/weft.db",
                    "service_type": "manager",
                    "owner_tid": "2",
                    "status": "active",
                },
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
        {
            "schema": SERVICE_OWNER_SCHEMA,
            "service_key": "internal:heartbeat:file:/tmp/weft.db",
            "service_type": "internal",
            "owner_tid": "1",
            "status": "active",
        },
        timestamp=1,
    )

    assert projected is None


def test_sync_manager_service_payload_top_level_queues(tmp_path) -> None:
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
    payload["queues"]["ctrl_in"] = "new.ctrl_in"

    sync_manager_service_payload_top_level_queues(payload)

    assert payload["ctrl_in"] == "new.ctrl_in"
    assert payload["inbox"] == "weft.spawn.requests"


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
