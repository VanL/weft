"""Queue-owned observation contracts [MF-5], independent of telemetry."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from simplebroker import Queue
from tests.helpers.test_backend import prepare_project_root
from weft._exceptions import CommandExecutionError, CommandTimeoutError
from weft.commands import _result_wait as result_wait
from weft.commands import result as result_cmd
from weft.commands import tasks as task_cmd
from weft.context import WeftContext, build_context
from weft.core.queue_wait import QueueChangeMonitor
from weft.core.task_state import task_state_queue_name
from weft.helpers import tid_short_form

pytestmark = [pytest.mark.shared]


def test_unresolved_result_does_not_claim_standard_outbox(tmp_path: Path) -> None:
    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    tid = str(time.time_ns())
    queue = ctx.queue(f"T{tid}.outbox", persistent=True)
    try:
        queue.write(json.dumps({"value": "must remain"}))
        result = result_cmd.await_task_result(ctx, tid)
        assert result.status == "unknown"
        assert result.reconciliation is not None
        assert (
            result.reconciliation["classification"]
            == "observation_metadata_unavailable"
        )
        assert queue.peek_one() is not None
    finally:
        queue.close()


def test_unresolved_control_rejects_before_write(tmp_path: Path) -> None:
    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    tid = str(time.time_ns())
    with pytest.raises(CommandExecutionError, match="observation metadata"):
        task_cmd.stop_task(tid, context=ctx)
    with ctx.broker() as broker:
        assert broker.peek_one(f"T{tid}.ctrl_in") is None


@pytest.mark.parametrize("timeout", [None, 0.0])
def test_terminal_projection_without_routes_cannot_collect_result(
    tmp_path: Path,
    timeout: float | None,
) -> None:
    """Lifecycle proof cannot choose unknown routes or a collection mode [MF-5]."""
    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    tid = str(time.time_ns())
    with ctx.queue(task_state_queue_name(tid)) as state:
        state.write(
            json.dumps(
                {
                    "full": tid,
                    "short": tid_short_form(tid),
                    "terminal": True,
                    "projection": {
                        "version": 1,
                        "status": "failed",
                        "event": "work_failed",
                        "started_at": None,
                        "metadata": {},
                        "error": "task failed",
                    },
                }
            )
        )
    if timeout is not None:
        with pytest.raises(CommandTimeoutError):
            result_cmd.await_task_result(ctx, tid, timeout=timeout)
    else:
        result = result_cmd.await_task_result(ctx, tid)
        assert result.status == "unknown"
        assert result.reconciliation is not None
        assert (
            result.reconciliation["classification"]
            == "observation_metadata_unavailable"
        )
        assert result.reconciliation["observed_terminal_status"] == "failed"
    status = task_cmd.task_status(tid, context=ctx)
    assert status is not None and status.status == "failed"


def _observation_snapshot(
    tid: str,
    *,
    outbox: str,
    ctrl_out: str,
    terminal: bool,
) -> dict[str, Any]:
    return {
        "full": tid,
        "short": tid_short_form(tid),
        "terminal": terminal,
        "runtime_handle": None,
        "projection": {
            "version": 1,
            "status": "completed" if terminal else "running",
            "event": "work_completed" if terminal else "work_started",
            "started_at": None,
            "metadata": {},
        },
        "observation": {
            "version": 1,
            "mode": "one_shot",
            "stream_output": False,
            "routes": {
                "inbox": f"T{tid}.inbox",
                "outbox": outbox,
                "ctrl_in": f"T{tid}.ctrl_in",
                "ctrl_out": ctrl_out,
                "pipeline_status": None,
            },
        },
    }


def test_late_custom_routes_are_registered_before_result_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A publish between acquisition and classification cannot bypass registration [MF-5]."""
    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    tid = str(time.time_ns())
    outbox, ctrl_out = f"custom.{tid}.result", f"custom.{tid}.completion"
    caller = threading.get_ident()
    registered: set[str] = set()
    read_custom: set[str] = set()
    published = False
    original_init = QueueChangeMonitor.__init__
    original_acquire = result_wait._acquire_result_observation
    with ctx.broker() as broker:
        broker_type = type(broker)
    original_peek = broker_type.peek_one
    original_generator = broker_type.peek_generator

    def initialize(
        monitor: QueueChangeMonitor, queues: Sequence[Queue], **kwargs: Any
    ) -> None:
        registered.update(queue.name for queue in queues)
        original_init(monitor, queues, **kwargs)

    def check_read(queue_name: str) -> None:
        if threading.get_ident() == caller and queue_name in {outbox, ctrl_out}:
            assert queue_name in registered, (
                "custom result route read before subscription"
            )
            read_custom.add(queue_name)

    def peek(db: Any, queue_name: str, **kwargs: Any) -> Any:
        check_read(queue_name)
        return original_peek(db, queue_name, **kwargs)

    def generator(db: Any, queue_name: str, **kwargs: Any) -> Any:
        check_read(queue_name)
        return original_generator(db, queue_name, **kwargs)

    def acquire(
        context: WeftContext,
        task_tid: str,
        materialized: result_wait.ResultMaterialization,
        queue: Queue,
        broker: Any,
        **kwargs: Any,
    ) -> tuple[
        result_wait.ResultMaterialization, tuple[tuple[dict[str, Any], int], ...]
    ]:
        nonlocal published
        acquired = original_acquire(
            context, task_tid, materialized, queue, broker, **kwargs
        )
        if not published:
            published = True
            broker.write(
                task_state_queue_name(tid),
                json.dumps(
                    _observation_snapshot(
                        tid, outbox=outbox, ctrl_out=ctrl_out, terminal=False
                    )
                ),
            )
            broker.write(outbox, json.dumps("late custom result"))
            broker.write(
                ctrl_out,
                json.dumps(
                    {
                        "type": "terminal",
                        "source": "task",
                        "tid": tid,
                        "status": "completed",
                    }
                ),
            )
        return acquired

    monkeypatch.setattr(QueueChangeMonitor, "__init__", initialize)
    monkeypatch.setattr(broker_type, "peek_one", peek)
    monkeypatch.setattr(broker_type, "peek_generator", generator)
    monkeypatch.setattr(result_wait, "_acquire_result_observation", acquire)
    result = result_cmd.await_task_result(ctx, tid, timeout=5)
    assert published
    assert result.status == "completed" and result.value == "late custom result"
    assert read_custom == {outbox, ctrl_out}


def test_retained_terminal_projection_beats_later_supervisor_after_snapshot_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A continuing result reader keeps the authority of its acquired task proof [MF-5]."""
    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    tid = str(time.time_ns())
    outbox, ctrl_out = f"T{tid}.outbox", f"T{tid}.ctrl_out"
    with ctx.broker() as broker:
        snapshot_id = broker.write(
            task_state_queue_name(tid),
            json.dumps(
                _observation_snapshot(
                    tid, outbox=outbox, ctrl_out=ctrl_out, terminal=True
                )
            ),
        )
    advanced = False
    original_wait = QueueChangeMonitor.wait

    def wait(monitor: QueueChangeMonitor, timeout: float | None) -> bool:
        nonlocal advanced
        if not advanced:
            advanced = True
            with ctx.broker() as broker:
                # Model retirement after this reader retained the task-authored proof.
                broker.delete_message_ids(task_state_queue_name(tid), [snapshot_id])
                broker.write(
                    ctrl_out,
                    json.dumps(
                        {
                            "type": "terminal",
                            "source": "manager",
                            "tid": tid,
                            "status": "failed",
                            "error": "wrapper_lost",
                        }
                    ),
                )
                broker.write(outbox, json.dumps("retained result"))
            return True
        return original_wait(monitor, timeout)

    monkeypatch.setattr(QueueChangeMonitor, "wait", wait)
    result = result_cmd.await_task_result(ctx, tid, timeout=5)
    assert advanced
    assert result.status == "completed" and result.value == "retained result"
