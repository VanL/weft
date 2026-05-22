"""Tests for dead-task cleanup candidate discovery."""

from __future__ import annotations

import pytest

from weft.core.monitor.policies.dead_task import (
    dead_task_queue_cleanup_plan,
    dead_task_tids_from_queue_names,
    select_dead_task_tids_from_queue_names,
    standard_task_queue_identity,
    standard_task_queue_tid,
)

pytestmark = [pytest.mark.shared]


def test_standard_task_queue_identity_accepts_standard_task_queues() -> None:
    assert standard_task_queue_identity("T123.ctrl_in") == ("123", "ctrl_in")
    assert standard_task_queue_identity("T123.ctrl_out") == ("123", "ctrl_out")
    assert standard_task_queue_identity("T123.outbox") == ("123", "outbox")
    assert standard_task_queue_identity("T123.inbox") == ("123", "inbox")
    assert standard_task_queue_identity("T123.reserved") == ("123", "reserved")
    assert standard_task_queue_tid("T123.ctrl_in") == "123"


def test_standard_task_queue_identity_rejects_nonstandard_names() -> None:
    assert standard_task_queue_identity("weft.manager.ctrl_in") is None
    assert standard_task_queue_identity("Tabc.ctrl_in") is None
    assert standard_task_queue_identity("T123.ctrl_in.extra") is None
    assert standard_task_queue_identity("T123.custom") is None
    assert standard_task_queue_identity("T.ctrl_in") is None
    assert standard_task_queue_tid("T123.custom") is None


def test_dead_task_tids_subtracts_live_tids_and_orders_oldest_first() -> None:
    now_ns = 1_778_000_000_000_010_000

    tids = dead_task_tids_from_queue_names(
        (
            "T1778000000000000003.ctrl_in",
            "T1778000000000000001.outbox",
            "T1778000000000000002.ctrl_out",
            "T1778000000000000001.ctrl_in",
            "not-a-task",
        ),
        live_tids={"1778000000000000002"},
        now_ns=now_ns,
        min_age_seconds=0.0,
    )

    assert tids == ("1778000000000000001", "1778000000000000003")


def test_dead_task_tids_skips_too_young_tids() -> None:
    now_ns = 1_778_000_000_000_010_000

    selection = select_dead_task_tids_from_queue_names(
        (
            "T1778000000000000001.ctrl_in",
            "T1778000000000010000.ctrl_out",
        ),
        live_tids=set(),
        now_ns=now_ns,
        min_age_seconds=0.000001,
    )

    assert selection.selected_tids == ("1778000000000000001",)
    assert selection.discovered_tids == 2
    assert selection.skipped_live == 0
    assert selection.skipped_too_young == 1


def test_dead_task_queue_cleanup_plan_keeps_retention_queues_until_old() -> None:
    tid = "1778000000000000001"
    now_ns = int(tid) + 10_000_000_000

    plan = dead_task_queue_cleanup_plan(
        tid,
        now_ns=now_ns,
        retention_seconds=60.0,
    )

    assert plan.queue_names == (
        f"T{tid}.ctrl_in",
        f"T{tid}.ctrl_out",
        f"T{tid}.inbox",
    )
    assert plan.outbox_queue_names == ()
    assert plan.reserved_queue_names == ()
    assert plan.retention_eligible is False


def test_dead_task_queue_cleanup_plan_includes_retention_queues_when_old() -> None:
    tid = "1778000000000000001"
    now_ns = int(tid) + 61_000_000_000

    plan = dead_task_queue_cleanup_plan(
        tid,
        now_ns=now_ns,
        retention_seconds=60.0,
    )

    assert plan.queue_names == (
        f"T{tid}.ctrl_in",
        f"T{tid}.ctrl_out",
        f"T{tid}.inbox",
        f"T{tid}.outbox",
        f"T{tid}.reserved",
    )
    assert plan.outbox_queue_names == (f"T{tid}.outbox",)
    assert plan.reserved_queue_names == (f"T{tid}.reserved",)
    assert plan.retention_eligible is True
