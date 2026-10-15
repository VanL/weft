"""Completion-to-next-launch Manager work is independent of sibling backlog.

Spec: [MA-1.8], [MF-5], [MF-6]. Count real broker calls and yielded rows
through reaping, due maintenance, exact reservation, checkpoint and the next
actual launch commit. Worker writes run in another process; system/PG tests
own workload progress and total session accounting. This is the Manager's
backlog-scaling oracle, not a wall-clock benchmark.
"""

from __future__ import annotations

import json
import multiprocessing
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from tests.core.test_manager import make_manager_spec
from tests.core.test_manager_family_maintenance import _publish_waiting_task
from tests.helpers.reactor_driver import drive_until
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import (
    MANAGER_FAMILY_CLASSIFY_PAGE_SIZE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    load_config,
)
from weft.core.manager import Manager
from weft.core.runtime_identity import (
    RuntimeIdentity,
    runtime_identity_for_task,
    submission_lineage,
)
from weft.core.taskspec import TaskSpec

pytestmark = [pytest.mark.shared, pytest.mark.timeout(180)]


def _counted_entries(rows: Iterator[Any], counts: dict[str, int]) -> Iterator[Any]:
    try:
        for row in rows:
            counts["entries"] += 1
            yield row
    finally:
        close = getattr(rows, "close", None)
        if close is not None:
            close()


def _counted_call(
    original: Callable[..., Any], name: str, counts: dict[str, int]
) -> Callable[..., Any]:
    def call(*args: Any, **kwargs: Any) -> Any:
        counts["calls"] += 1
        result = original(*args, **kwargs)
        if isinstance(result, Iterator):
            return _counted_entries(result, counts)
        if isinstance(result, list):
            counts["entries"] += len(result)
        return result

    return call


def _task(
    manager: Manager, request_id: int, parent: RuntimeIdentity | None = None
) -> tuple[TaskSpec, RuntimeIdentity]:
    payload: dict[str, Any] = {
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:echo_payload",
        },
        "inbox_message": {"args": ["scaling-leaf"]},
    }
    lineage = submission_lineage(parent) if parent else None
    if lineage:
        payload["submitted_by"] = lineage
    spec = manager._build_child_spec(payload, request_id)
    assert spec is not None
    identity = runtime_identity_for_task(
        spec,
        manager._db_path,
        submitted_by=lineage,
        config=manager._manager_context().config,
    )
    return spec, identity


@pytest.mark.parametrize("backlog", [100, 1000, 10000])
def test_completion_to_next_real_launch_does_not_scan_sibling_backlog(
    weft_harness: WeftTestHarness, monkeypatch: pytest.MonkeyPatch, backlog: int
) -> None:
    ctx = weft_harness.context
    with ctx.queue("scaling.ids") as ids:
        manager_tid, parent_id, leaf_id = (
            str(ids.generate_timestamp()),
            ids.generate_timestamp(),
            ids.generate_timestamp(),
        )
    manager = Manager(
        ctx.broker_target,
        make_manager_spec(manager_tid, idle_timeout=0.0),
        config=load_config(
            {
                "WEFT_ADMISSION_MAX_CONNECTIONS": 0,
                "WEFT_HEARTBEAT_ENABLED": False,
                "WEFT_TASK_MONITOR_ENABLED": False,
                "WEFT_LIVENESS_MONITOR_ENABLED": False,
                "WEFT_AUTOSTART_TASKS": False,
            }
        ),
    )
    parent_spec, parent_identity = _task(manager, parent_id)
    leaf_spec, leaf_identity = _task(manager, leaf_id, parent_identity)
    spawn = multiprocessing.get_context("spawn")
    parent_ready, parent_release = spawn.Event(), spawn.Event()
    leaf_ready, leaf_release = spawn.Event(), spawn.Event()
    parent = spawn.Process(
        target=_publish_waiting_task,
        args=(
            parent_spec.model_dump(mode="json"),
            parent_identity.to_dict(),
            parent_ready,
            parent_release,
        ),
    )
    leaf = spawn.Process(
        target=_publish_waiting_task,
        args=(
            leaf_spec.model_dump(mode="json"),
            leaf_identity.to_dict(),
            leaf_ready,
            leaf_release,
        ),
    )
    try:
        manager._refresh_scheduler_evidence()
        # Accepted requests need canonical ancestry, not live producer processes.
        # Finish amortized setup before starting the finite actor lifetime.
        body = json.dumps(
            {
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                },
                "inbox_message": {"args": ["scaling-leaf"]},
                "submitted_by": submission_lineage(parent_identity),
            }
        )
        with ctx.queue(WEFT_SPAWN_REQUESTS_QUEUE) as source:
            first = source.generate_timestamp()
        final = first + backlog
        with ctx.broker() as broker:
            broker.insert_messages(
                (WEFT_SPAWN_REQUESTS_QUEUE, body, first + index)
                for index in range(1, backlog + 1)
            )
        while manager._family_classify_after < final:
            manager._classify_family_requests()
        assert manager._family_scheduler.pending(final) is not None
        parent.start()
        leaf.start()
        assert parent_ready.wait(timeout=30.0) and leaf_ready.wait(timeout=30.0)
        # Post-bootstrap maintenance revisits known residents. Exact production
        # reconciliation adopts these newly published actor snapshots.
        manager._reconcile_launch_now(str(parent_id))
        manager._reconcile_launch_now(str(leaf_id))
        assert manager._family_scheduler.frontiers == ((str(parent_id), str(leaf_id)),)
        assert parent.is_alive() and leaf.is_alive()
        sibling = manager._peek_family_candidate()
        assert sibling is not None and sibling.request_id == first + 1
        # Work sharing is already eligible while this leaf lives. The measured
        # interval still starts at its exit and must choose the newest tip child.
        leaf_release.set()
        leaf.join(timeout=30.0)
        assert not leaf.is_alive()
        # Force both source and label maintenance due inside the measured
        # interval, so a per-exit replay cannot hide in maintenance work.
        manager._family_repair_due = 0.0
        manager._family_repair_after = 0
        manager._family_label_names.clear()
        manager._family_label_after = 0
        manager._admission_refresh_after = 0.0
        counts = {"calls": 0, "entries": 0}
        with ctx.broker() as broker:
            broker_type = type(broker)
        with monkeypatch.context() as patch:
            for name in (
                "write",
                "insert_messages",
                "peek_one",
                "peek_many",
                "peek_generator",
                "move_one",
                "move_many",
                "move_generator",
                "delete_message_ids",
                "delete_queue",
                "list_queues",
                "find_message_ids",
                "has_pending_messages",
                "latest_pending_timestamp",
                "get_queue_stats",
                "list_queue_stats",
                "broadcast",
            ):
                original = getattr(broker_type, name, None)
                if callable(original):
                    patch.setattr(
                        broker_type, name, _counted_call(original, name, counts)
                    )
            manager._record_reaped_admission(str(leaf_id))
            drive_until(
                lambda: str(final) in manager._child_processes,
                bool,
                step=manager.process_once,
                wait=manager.wait_for_activity,
                timeout=30.0,
            )
        print(
            f"family dispatch backlog={backlog}: calls={counts['calls']}, entries={counts['entries']}"
        )
        # Eight discovery pages allow bounded actor/startup maintenance. The
        # same ceiling applies at every N and rejects a whole 1k/10k replay.
        assert counts["entries"] <= 8 * MANAGER_FAMILY_CLASSIFY_PAGE_SIZE
        assert counts["calls"] <= 32 * MANAGER_FAMILY_CLASSIFY_PAGE_SIZE
        assert manager._child_processes[str(final)].process.pid is not None
    finally:
        parent_release.set()
        leaf_release.set()
        manager.stop(join=False)
        manager.cleanup()
        for process in (parent, leaf):
            if process.pid is not None:
                process.join(timeout=30.0)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=30.0)
            process.close()
