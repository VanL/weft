"""Public result waits avoid shared-history replay and stale routing [MF-5]."""

from __future__ import annotations

import json
import threading
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft._constants import WEFT_GLOBAL_LOG_QUEUE
from weft.commands import result
from weft.context import build_context
from weft.core.queue_wait import QueueChangeMonitor
from weft.core.tasks import Consumer
from weft.core.taskspec import TaskSpec
from weft.helpers import closing_queue_iterator

pytestmark = [pytest.mark.shared]


def _child_spec(tid: str, *, persistent: bool = False) -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "tid": tid,
            "name": "queued-child",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
                "persistent": persistent,
            },
            "io": {
                "inputs": {"inbox": f"T{tid}.inbox"},
                "outputs": {"outbox": f"T{tid}.outbox"},
                "control": {
                    "ctrl_in": f"T{tid}.ctrl_in",
                    "ctrl_out": f"T{tid}.ctrl_out",
                },
            },
        }
    )


def _record_history_body(
    queue_name: str,
    row: Any,
    *,
    collector_thread: int,
    tracked: set[str],
    transferred: Counter[str],
) -> None:
    """Count bodies crossing the real collector's broker boundary."""
    body = row[0] if isinstance(row, tuple) else row
    if (
        threading.get_ident() == collector_thread
        and queue_name == WEFT_GLOBAL_LOG_QUEUE
        and isinstance(body, str)
        and body in tracked
    ):
        transferred[body] += 1


@pytest.mark.parametrize("row_kind", ["unrelated", "malformed", "non-object"])
def test_result_wait_does_not_fetch_or_decode_unrelated_history_bodies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, row_kind: str
) -> None:
    """Ordinary global traffic wakes the real observer without body replay.

    Count the collector's actual exact and iterator peeks and JSON decodes.
    SQLite's broker-owned fallback watcher may transfer those bodies itself;
    its separate thread is deliberately outside this application-level oracle.
    """
    ctx = build_context(prepare_project_root(tmp_path))
    with ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as log:
        tid = str(log.generate_timestamp())
        spec = _child_spec(tid).model_dump(mode="json")
        metadata_body = json.dumps({"tid": tid, "taskspec": spec})
        bodies = [
            {
                "unrelated": json.dumps(
                    {"tid": str(int(tid) + 1), "burst_marker": index}
                ),
                "malformed": f"{{malformed-{index}",
                "non-object": json.dumps(["non-object", index]),
            }[row_kind]
            for index in range(2)
        ]
        log.write(bodies[0])
        collector_thread = threading.get_ident()
        transferred: Counter[str] = Counter()
        decoded: Counter[str] = Counter()
        tracked = {*bodies, metadata_body}
        with log.get_connection() as broker:
            broker_type = type(broker)
            original_one = broker_type.peek_one
            original_generator = broker_type.peek_generator
        original_loads = json.loads
        original_wait = QueueChangeMonitor.wait
        turn = 0

        def peek_one(db: Any, queue_name: str, **kwargs: Any) -> Any:
            row = original_one(db, queue_name, **kwargs)
            _record_history_body(
                queue_name,
                row,
                collector_thread=collector_thread,
                tracked=tracked,
                transferred=transferred,
            )
            return row

        def peek_generator(db: Any, queue_name: str, **kwargs: Any) -> Iterator[Any]:
            iterator = original_generator(db, queue_name, **kwargs)
            with closing_queue_iterator(iterator) as rows:
                for row in rows:
                    _record_history_body(
                        queue_name,
                        row,
                        collector_thread=collector_thread,
                        tracked=tracked,
                        transferred=transferred,
                    )
                    yield row

        def loads(body: Any, *args: Any, **kwargs: Any) -> Any:
            if (
                threading.get_ident() == collector_thread
                and isinstance(body, str)
                and body in tracked
            ):
                decoded[body] += 1
            return original_loads(body, *args, **kwargs)

        def wait(monitor: QueueChangeMonitor, timeout: float | None = None) -> bool:
            nonlocal turn
            if threading.get_ident() == collector_thread:
                turn += 1
                if turn == 1:
                    log.write(bodies[1])
                elif turn == 2:
                    log.write(metadata_body)
                    with ctx.queue(f"T{tid}.outbox", persistent=True) as outbox:
                        outbox.write(json.dumps("response"))
            return original_wait(monitor, timeout)

        monkeypatch.setattr(broker_type, "peek_one", peek_one)
        monkeypatch.setattr(broker_type, "peek_generator", peek_generator)
        monkeypatch.setattr(json, "loads", loads)
        monkeypatch.setattr(QueueChangeMonitor, "wait", wait)

        observed = result.await_task_result(ctx, tid, timeout=5)

        assert observed.status == "completed"
        assert observed.value == "response"
        assert turn >= 2
        assert transferred == Counter({metadata_body: 1})
        assert decoded == Counter({metadata_body: 1})


@pytest.mark.parametrize("persistent", [False, True])
def test_result_uses_metadata_committed_before_observed_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, persistent: bool
) -> None:
    """A late real Consumer's metadata must determine output batch custody.

    The first outbox peek follows an empty history snapshot. Produce metadata
    and output inside that dependency boundary, then let the real peek observe
    them. Persistent execution produces two real work-item boundaries: treating
    this as one-shot would wrongly collect the next batch as well.
    """
    ctx = build_context(prepare_project_root(tmp_path))
    with ctx.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as log:
        tid = str(log.generate_timestamp())
        spec = _child_spec(tid, persistent=persistent)
        with log.get_connection() as broker:
            broker_type = type(broker)
            original_peek = broker_type.peek_one
        outbox_name = spec.io.outputs["outbox"]
        collector_thread = threading.get_ident()
        published = False
        consumer: Consumer | None = None

        def peek(db: Any, queue_name: str, **kwargs: Any) -> Any:
            nonlocal published, consumer
            if (
                not published
                and threading.get_ident() == collector_thread
                and queue_name == outbox_name
            ):
                published = True
                consumer = Consumer(ctx.broker_target, spec, config=ctx.config)
                assert consumer.run_work_item({"args": ["first"]}) == "first"
                if persistent:
                    assert consumer.run_work_item({"args": ["second"]}) == "second"
            return original_peek(db, queue_name, **kwargs)

        monkeypatch.setattr(broker_type, "peek_one", peek)
        try:
            observed = result.await_task_result(ctx, tid, timeout=0)
            assert published
            assert observed.status == "completed"
            assert observed.value == "first"
            with ctx.queue(outbox_name, persistent=True) as outbox:
                assert outbox.read_one() == ("second" if persistent else None)
                assert outbox.read_one() is None
        finally:
            if consumer is not None:
                consumer.cleanup()
