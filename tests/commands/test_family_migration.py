"""Stopped-runtime migration preserves accepted messages [MF-6]."""

from __future__ import annotations

import json
import multiprocessing
import os
import socket
import time

import pytest

from tests.core.test_manager import make_manager_spec
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import load_config
from weft.commands._spawn_submission import (
    LegacyChildMigrationManager,
    prepare_legacy_child_migration,
)
from weft.commands.result import await_task_result
from weft.core.runtime_identity import canonical_runtime_context
from weft.helpers import process_create_time

pytestmark = pytest.mark.shared


def legacy_body(harness: WeftTestHarness, parent_tid: str) -> str:
    return json.dumps(
        {
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
            "inbox_message": {"args": ["migrated"]},
            "submitted_by": {
                "tid": parent_tid,
                "context": canonical_runtime_context(harness.context.broker_target),
            },
        }
    )


def test_legacy_source_move_and_rerun_preserve_id_and_original_body(
    weft_harness: WeftTestHarness,
) -> None:
    ctx = weft_harness.context
    with (
        ctx.queue("weft.spawn.children") as source,
        ctx.queue("weft.spawn.requests") as primary,
    ):
        parent = str(source.generate_timestamp())
        body = legacy_body(weft_harness, parent)
        tid = int(source.write(body))
        inventory = prepare_legacy_child_migration(ctx)
        assert primary.peek_one(exact_timestamp=tid) == body
        assert source.peek_one(exact_timestamp=tid) is None
        assert inventory.cohort == frozenset({tid})
        assert inventory.entries[0].body == body
        again = prepare_legacy_child_migration(ctx)
        assert again.cohort == inventory.cohort
        assert primary.peek_one(exact_timestamp=tid) == body
        with ctx.queue(f"T{parent}.reserved") as reserved:
            assert primary.move_one(reserved.name, exact_timestamp=tid) is not None
            resumed = prepare_legacy_child_migration(ctx)
            assert resumed.cohort == inventory.cohort
            assert reserved.peek_one(exact_timestamp=tid) == body


def test_ambiguous_old_reserved_row_blocks_all_migration_moves(
    weft_harness: WeftTestHarness,
) -> None:
    ctx = weft_harness.context
    with ctx.queue("weft.spawn.children") as source:
        parent = str(source.generate_timestamp())
        body = legacy_body(weft_harness, parent)
        queued = int(source.write(body))
        with ctx.queue(f"T{parent}.children_reserved") as reserved:
            reserved_id = int(reserved.write(body))
            with pytest.raises(ValueError, match="ambiguous"):
                prepare_legacy_child_migration(ctx)
            assert source.peek_one(exact_timestamp=queued) == body
            assert reserved.peek_one(exact_timestamp=reserved_id) == body


def test_delivery_adapter_normalizes_only_inventory_cohort(
    weft_harness: WeftTestHarness,
) -> None:
    ctx = weft_harness.context
    with ctx.queue("weft.spawn.children") as source:
        parent = str(source.generate_timestamp())
        body = legacy_body(weft_harness, parent)
        tid = int(source.write(body))
        inventory = prepare_legacy_child_migration(ctx)
        manager_tid = str(source.generate_timestamp())
    manager = LegacyChildMigrationManager(
        ctx.broker_target,
        make_manager_spec(manager_tid, idle_timeout=0.1),
        migration=inventory,
        config=load_config(
            {
                "WEFT_HEARTBEAT_ENABLED": False,
                "WEFT_TASK_MONITOR_ENABLED": False,
                "WEFT_LIVENESS_MONITOR_ENABLED": False,
                "WEFT_AUTOSTART_TASKS": False,
            }
        ),
    )
    try:
        identity = manager._family_request_identity(body, tid)
        assert identity.root_tid == str(tid)
        assert identity.submitter_tid is None
        assert identity.ancestry_tids == ()
        with pytest.raises(ValueError):
            manager._family_request_identity(body, tid + 1)
        with ctx.queue("weft.spawn.requests") as primary:
            assert primary.peek_one(exact_timestamp=tid) == body
        thread = manager.run_in_thread()
        result = await_task_result(ctx, str(tid), timeout=30.0)
        assert result.status == "completed", result.error
        assert result.value == "migrated"
        thread.join(timeout=30.0)
        assert not thread.is_alive()
    finally:
        manager.stop(join=False)
        manager.cleanup()


@pytest.mark.parametrize(
    ("terminal", "live_scope"), [(False, False), (True, False), (True, True)]
)
def test_old_reserved_requires_terminal_and_exact_process_settlement(
    weft_harness: WeftTestHarness, terminal: bool, live_scope: bool
) -> None:
    ctx = weft_harness.context
    process = multiprocessing.get_context("spawn").Process(
        target=time.sleep, args=(0.0,)
    )
    try:
        process.start()
        assert process.pid is not None
        created = process_create_time(process.pid)
        assert created is not None
        process.join(timeout=30.0)
        assert not process.is_alive()
        with ctx.queue("migration.ids") as ids:
            parent = str(ids.generate_timestamp())
        body = legacy_body(weft_harness, parent)
        with ctx.queue(f"T{parent}.children_reserved") as reserved:
            tid = int(reserved.write(body))
            scope_pid = os.getpid() if live_scope else process.pid
            scope_created = process_create_time(scope_pid) if live_scope else created
            with ctx.queue(f"weft.state.tasks.{tid}") as state:
                state.write(
                    json.dumps(
                        {
                            "full": str(tid),
                            "terminal": terminal,
                            "hostname": socket.gethostname(),
                            "host_processes": [
                                {"pid": scope_pid, "create_time": scope_created}
                            ],
                        }
                    )
                )
            if not terminal or live_scope:
                with pytest.raises(ValueError, match="ambiguous"):
                    prepare_legacy_child_migration(ctx)
                assert reserved.peek_one(exact_timestamp=tid) == body
            else:
                inventory = prepare_legacy_child_migration(ctx)
                assert reserved.peek_one(exact_timestamp=tid) is None
                assert tid not in inventory.cohort
                assert inventory.entries[0].disposition == "settled"
                assert inventory.entries[0].body == body
                with ctx.queue("weft.spawn.requests") as primary:
                    assert primary.peek_one(exact_timestamp=tid) is None
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=30.0)
        process.close()


@pytest.mark.parametrize("malformation", ["wrong_context", "extra_field"])
def test_legacy_normalization_rejects_untrusted_transport_before_move(
    weft_harness: WeftTestHarness, malformation: str
) -> None:
    ctx = weft_harness.context
    with ctx.queue("weft.spawn.children") as source:
        parent = str(source.generate_timestamp())
        payload = json.loads(legacy_body(weft_harness, parent))
        if malformation == "wrong_context":
            payload["submitted_by"]["context"] = "sqlite:/different-context.db"
        else:
            payload["submitted_by"]["root_tid"] = parent
        body = json.dumps(payload)
        tid = int(source.write(body))
        with pytest.raises(ValueError, match="submitter evidence"):
            prepare_legacy_child_migration(ctx)
        assert source.peek_one(exact_timestamp=tid) == body
