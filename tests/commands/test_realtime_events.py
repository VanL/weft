"""Realtime task event iteration regressions.

Covers [MF-5]: the realtime iterator must reuse the shared, non-consuming task
evidence classification for terminal proof that becomes visible after its first
snapshot, instead of recognising only terminal task-log rows.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft.commands import events as events_cmd
from weft.commands.types import TaskEvent
from weft.context import WeftContext, build_context
from weft.core.task_evidence import WRAPPER_LOST_ERROR

pytestmark = [pytest.mark.shared]


def _taskspec_payload(
    tid: str,
    *,
    name: str = "realtime-task",
    persistent: bool = False,
    stream_output: bool = False,
) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "type": "function",
        "function_target": "tests.tasks.sample_targets:echo_payload",
        "runner": {"name": "host", "options": {}},
    }
    if persistent:
        spec["persistent"] = True
    if stream_output:
        spec["stream_output"] = True
    return {
        "tid": tid,
        "name": name,
        "spec": spec,
        "io": {
            "outputs": {"outbox": f"T{tid}.outbox"},
            "control": {
                "ctrl_in": f"T{tid}.ctrl_in",
                "ctrl_out": f"T{tid}.ctrl_out",
            },
        },
        "state": {
            "status": "running",
            "started_at": time.time_ns(),
            "completed_at": None,
        },
        "metadata": {"owner": "tests"},
    }


def _write_log(ctx: Any, payload: dict[str, Any]) -> None:
    queue = ctx.queue("weft.log.tasks", persistent=False)
    try:
        queue.write(json.dumps(payload))
    finally:
        queue.close()


def _write_queue(ctx: Any, name: str, payload: Any, *, persistent: bool) -> None:
    queue = ctx.queue(name, persistent=persistent)
    try:
        queue.write(json.dumps(payload))
    finally:
        queue.close()


def _peek_all(ctx: Any, name: str, *, persistent: bool) -> list[Any]:
    queue = ctx.queue(name, persistent=persistent)
    try:
        return list(queue.peek_many(limit=50))
    finally:
        queue.close()


def _running_task(
    ctx: Any,
    *,
    persistent: bool = False,
    stream_output: bool = False,
) -> tuple[str, dict[str, Any]]:
    tid = str(time.time_ns())
    taskspec = _taskspec_payload(
        tid,
        persistent=persistent,
        stream_output=stream_output,
    )
    _write_log(
        ctx,
        {
            "event": "work_started",
            "status": "running",
            "tid": tid,
            "taskspec": taskspec,
        },
    )
    return tid, taskspec


def _started_iterator(
    ctx: Any,
    tid: str,
    *,
    timeout: float | None,
    follow: bool = True,
) -> Iterator[TaskEvent]:
    """Return an iterator already past its snapshot and its ``running`` state.

    Draining the ``work_started`` state event leaves the generator suspended
    inside the poll loop, which is the position where late terminal proof used
    to be ignored.
    """

    iterator = events_cmd.iter_task_realtime_events(
        ctx,
        tid,
        follow=follow,
        timeout=timeout,
    )
    first = next(iterator)
    assert first.event_type == "snapshot"
    assert first.payload["status"] == "running"
    running = next(iterator)
    assert running.event_type == "state"
    assert running.payload["status"] == "running"
    return iterator


def test_late_task_terminal_ctrl_out_finishes_realtime_stream(tmp_path: Path) -> None:
    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    tid, _taskspec = _running_task(ctx)
    iterator = _started_iterator(ctx, tid, timeout=5.0)

    _write_queue(
        ctx,
        f"T{tid}.ctrl_out",
        {"type": "terminal", "source": "task", "tid": tid, "status": "completed"},
        persistent=False,
    )
    _write_queue(ctx, f"T{tid}.outbox", {"ok": True}, persistent=True)

    events = list(iterator)

    assert [event.event_type for event in events] == ["state", "result", "end"]
    assert events[0].payload == {"status": "completed"}
    assert events[1].payload == {
        "status": "completed",
        "value": {"ok": True},
        "error": None,
    }
    assert events[2].payload == {"status": "completed"}
    assert len(_peek_all(ctx, f"T{tid}.ctrl_out", persistent=False)) == 1
    assert len(_peek_all(ctx, f"T{tid}.outbox", persistent=True)) == 1


def test_late_wrapper_lost_ctrl_out_finishes_realtime_stream(tmp_path: Path) -> None:
    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    tid, _taskspec = _running_task(ctx)
    iterator = _started_iterator(ctx, tid, timeout=5.0)

    _write_queue(
        ctx,
        f"T{tid}.ctrl_out",
        {
            "type": "terminal",
            "source": "manager",
            "tid": tid,
            "status": "failed",
            "error": WRAPPER_LOST_ERROR,
            "return_code": 1,
        },
        persistent=False,
    )

    events = list(iterator)

    assert [event.event_type for event in events] == ["state", "result", "end"]
    assert events[0].payload == {"status": "failed", "error": WRAPPER_LOST_ERROR}
    assert events[1].payload == {
        "status": "failed",
        "value": None,
        "error": WRAPPER_LOST_ERROR,
    }
    assert len(_peek_all(ctx, f"T{tid}.ctrl_out", persistent=False)) == 1


def test_late_task_terminal_outranks_manager_wrapper_lost(tmp_path: Path) -> None:
    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    tid, _taskspec = _running_task(ctx)
    iterator = _started_iterator(ctx, tid, timeout=5.0)

    _write_queue(
        ctx,
        f"T{tid}.ctrl_out",
        {"type": "terminal", "source": "task", "tid": tid, "status": "completed"},
        persistent=False,
    )
    _write_queue(
        ctx,
        f"T{tid}.ctrl_out",
        {
            "type": "terminal",
            "source": "manager",
            "tid": tid,
            "status": "failed",
            "error": WRAPPER_LOST_ERROR,
        },
        persistent=False,
    )
    _write_queue(ctx, f"T{tid}.outbox", {"ok": True}, persistent=True)

    events = list(iterator)

    assert [event.event_type for event in events] == ["state", "result", "end"]
    assert events[2].payload == {"status": "completed"}
    assert len(_peek_all(ctx, f"T{tid}.ctrl_out", persistent=False)) == 2


def test_startup_wrapper_lost_snapshot_is_replaced_by_late_task_terminal(
    tmp_path: Path,
) -> None:
    """A wrapper_lost verdict held from the startup snapshot still yields [MF-5].

    The manager's wrapper_lost envelope is visible before the iterator starts,
    so the startup snapshot already reports ``failed``. A task-authored
    terminal envelope that lands afterwards outranks it and must end the
    stream with the task's status.
    """
    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    tid, _taskspec = _running_task(ctx)
    _write_queue(
        ctx,
        f"T{tid}.ctrl_out",
        {
            "type": "terminal",
            "source": "manager",
            "tid": tid,
            "status": "failed",
            "error": WRAPPER_LOST_ERROR,
        },
        persistent=False,
    )
    iterator = events_cmd.iter_task_realtime_events(ctx, tid, follow=True, timeout=5.0)
    first = next(iterator)
    assert first.event_type == "snapshot"
    assert first.payload["status"] == "failed"

    _write_queue(
        ctx,
        f"T{tid}.ctrl_out",
        {"type": "terminal", "source": "task", "tid": tid, "status": "completed"},
        persistent=False,
    )
    _write_queue(ctx, f"T{tid}.outbox", {"ok": True}, persistent=True)

    events = list(iterator)

    assert events[-1].event_type == "end"
    assert events[-1].payload == {"status": "completed"}
    assert len(_peek_all(ctx, f"T{tid}.ctrl_out", persistent=False)) == 2


def test_late_terminal_log_row_finishes_realtime_stream(tmp_path: Path) -> None:
    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    tid, taskspec = _running_task(ctx)
    iterator = _started_iterator(ctx, tid, timeout=5.0)

    _write_queue(ctx, f"T{tid}.outbox", {"ok": True}, persistent=True)
    completed = dict(taskspec)
    completed["state"] = {
        "status": "completed",
        "started_at": taskspec["state"]["started_at"],
        "completed_at": time.time_ns(),
    }
    _write_log(
        ctx,
        {
            "event": "work_completed",
            "status": "completed",
            "tid": tid,
            "taskspec": completed,
        },
    )

    events = list(iterator)

    assert [event.event_type for event in events] == ["state", "result", "end"]
    assert events[0].payload["status"] == "completed"
    assert events[1].payload == {
        "status": "completed",
        "value": {"ok": True},
        "error": None,
    }
    assert len(_peek_all(ctx, f"T{tid}.outbox", persistent=True)) == 1


def test_late_final_outbox_result_finishes_realtime_stream(tmp_path: Path) -> None:
    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    tid, _taskspec = _running_task(ctx)
    iterator = _started_iterator(ctx, tid, timeout=5.0)

    _write_queue(ctx, f"T{tid}.outbox", "done", persistent=True)

    events = list(iterator)

    assert [event.event_type for event in events] == ["state", "result", "end"]
    assert events[1].payload == {
        "status": "completed",
        "value": "done",
        "error": None,
    }
    assert len(_peek_all(ctx, f"T{tid}.outbox", persistent=True)) == 1
    assert _peek_all(ctx, f"T{tid}.ctrl_out", persistent=False) == []


@pytest.mark.timeout(30)
def test_persistent_stream_output_is_not_realtime_completion(tmp_path: Path) -> None:
    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    tid, _taskspec = _running_task(ctx, persistent=True)
    iterator = _started_iterator(ctx, tid, timeout=None)

    for index, chunk in enumerate(("alpha", "beta")):
        _write_queue(
            ctx,
            f"T{tid}.outbox",
            {
                "type": "stream",
                "stream": "stdout",
                "data": chunk,
                "chunk": index,
                "final": index == 1,
            },
            persistent=True,
        )

    try:
        seen = [next(iterator), next(iterator)]
        assert [event.event_type for event in seen] == ["stdout", "stdout"]

        # A later frame proves the final-marked work item did not end this
        # persistent task's stream. No deadline runs while the test suspends
        # the consumer to perform backend writes.
        _write_queue(
            ctx,
            f"T{tid}.outbox",
            {"type": "stream", "stream": "stdout", "data": "gamma", "chunk": 2},
            persistent=True,
        )
        continued = next(iterator)
        assert continued.event_type == "stdout"
        assert continued.payload["data"] == "gamma"
        assert len(_peek_all(ctx, f"T{tid}.outbox", persistent=True)) == 3
    finally:
        close = getattr(iterator, "close", None)
        assert callable(close)
        close()


def test_staggered_task_terminal_replaces_wrapper_lost_verdict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A task terminal envelope that lands after a held wrapper-lost verdict wins.

    The manager failsafe is classified first, then the task-authored envelope
    becomes durable during the terminal grace. [MF-5] gives the task verdict
    precedence, so the stream must end ``completed``.
    """

    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    tid, _taskspec = _running_task(ctx)
    iterator = _started_iterator(ctx, tid, timeout=5.0)

    _write_queue(
        ctx,
        f"T{tid}.ctrl_out",
        {
            "type": "terminal",
            "source": "manager",
            "tid": tid,
            "status": "failed",
            "error": WRAPPER_LOST_ERROR,
        },
        persistent=False,
    )

    classifications: list[str] = []
    real_evidence = events_cmd.task_local_terminal_evidence

    def _classify_then_publish_task_terminal(*args: Any, **kwargs: Any) -> Any:
        snapshot = real_evidence(*args, **kwargs)
        if snapshot is None:
            return None
        classifications.append(snapshot.classification)
        if len(classifications) == 1 and snapshot.classification == "wrapper_lost":
            _write_queue(
                ctx,
                f"T{tid}.ctrl_out",
                {
                    "type": "terminal",
                    "source": "task",
                    "tid": tid,
                    "status": "completed",
                },
                persistent=False,
            )
            _write_queue(ctx, f"T{tid}.outbox", {"ok": True}, persistent=True)
        return snapshot

    monkeypatch.setattr(
        events_cmd,
        "task_local_terminal_evidence",
        _classify_then_publish_task_terminal,
    )

    events = list(iterator)

    assert classifications[0] == "wrapper_lost"
    assert "terminal_ctrl_out" in classifications
    assert [event.event_type for event in events] == ["state", "result", "end"]
    assert events[0].payload == {"status": "completed"}
    assert events[1].payload == {
        "status": "completed",
        "value": {"ok": True},
        "error": None,
    }
    assert events[2].payload == {"status": "completed"}
    assert len(_peek_all(ctx, f"T{tid}.ctrl_out", persistent=False)) == 2
    assert len(_peek_all(ctx, f"T{tid}.outbox", persistent=True)) == 1


@pytest.mark.timeout(30)
def test_final_stream_frame_alone_is_not_realtime_completion(tmp_path: Path) -> None:
    """A one-shot streaming task's final frame is output, not terminal proof.

    [MF-5] treats streaming output as observation only, so the iterator keeps
    following until terminal ctrl_out or task-log proof arrives.
    """

    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    tid, _taskspec = _running_task(ctx, stream_output=True)
    iterator = _started_iterator(ctx, tid, timeout=None)

    _write_queue(
        ctx,
        f"T{tid}.outbox",
        {
            "type": "stream",
            "stream": "stdout",
            "data": "alpha",
            "chunk": 0,
            "final": True,
        },
        persistent=True,
    )

    try:
        assert next(iterator).event_type == "stdout"
        # A subsequent frame proves that the final marker alone did not
        # terminate observation, without timing the backend writes.
        _write_queue(
            ctx,
            f"T{tid}.outbox",
            {"type": "stream", "stream": "stdout", "data": "beta", "chunk": 1},
            persistent=True,
        )
        continued = next(iterator)
        assert continued.event_type == "stdout"
        assert continued.payload["data"] == "beta"
        assert len(_peek_all(ctx, f"T{tid}.outbox", persistent=True)) == 2
    finally:
        close = getattr(iterator, "close", None)
        assert callable(close)
        close()


def _publish_after_initial_materialization(
    monkeypatch: pytest.MonkeyPatch,
    publish: Callable[[], None],
) -> None:
    """Place real queue writes after the observer's initial metadata read."""
    original = events_cmd._await_result_materialization

    def materialize(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        publish()
        return result

    monkeypatch.setattr(events_cmd, "_await_result_materialization", materialize)


@pytest.mark.parametrize("persistent", [False, True])
@pytest.mark.parametrize("custom_routes", [False, True])
def test_realtime_refreshes_late_metadata_before_final_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    persistent: bool,
    custom_routes: bool,
) -> None:
    """Late persistent results stay open; late one-shot/custom output completes."""
    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    tid = str(time.time_ns())
    spec = _taskspec_payload(tid, persistent=persistent)
    outbox = f"audit.{tid}.outbox" if custom_routes else f"T{tid}.outbox"
    ctrl_out = f"audit.{tid}.ctrl_out" if custom_routes else f"T{tid}.ctrl_out"
    spec["io"]["outputs"]["outbox"] = outbox
    spec["io"]["control"]["ctrl_out"] = ctrl_out

    def publish() -> None:
        _write_log(
            ctx,
            {
                "tid": tid,
                "event": "work_started",
                "status": "running",
                "taskspec": spec,
            },
        )
        _write_queue(ctx, outbox, {"item": "done"}, persistent=True)

    _publish_after_initial_materialization(monkeypatch, publish)
    events = list(events_cmd.iter_task_realtime_events(ctx, tid, follow=False))
    terminals = [event for event in events if event.event_type in {"result", "end"}]
    if persistent:
        assert not terminals
        assert all(event.payload.get("status") != "completed" for event in events)
    else:
        assert [event.event_type for event in terminals] == ["result", "end"]
        assert terminals[0].payload["value"] == {"item": "done"}
    assert len(_peek_all(ctx, outbox, persistent=True)) == 1


@pytest.mark.parametrize("terminal_source", [None, "log", "control"])
def test_realtime_unknown_metadata_requires_explicit_terminal_evidence(
    tmp_path: Path, terminal_source: str | None
) -> None:
    """Outbox-only startup must not leak a terminal snapshot or end event."""
    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    tid = str(time.time_ns())
    _write_queue(ctx, f"T{tid}.outbox", {"item": "done"}, persistent=True)
    if terminal_source == "log":
        _write_log(ctx, {"tid": tid, "event": "work_completed", "status": "completed"})
    elif terminal_source == "control":
        _write_queue(
            ctx,
            f"T{tid}.ctrl_out",
            {"type": "terminal", "source": "task", "tid": tid, "status": "completed"},
            persistent=True,
        )
    events = list(events_cmd.iter_task_realtime_events(ctx, tid, follow=False))
    if terminal_source is None:
        assert not any(event.payload.get("status") == "completed" for event in events)
        assert not any(event.event_type in {"result", "end"} for event in events)
    else:
        assert events[-1].event_type == "end"
        assert events[-1].payload["status"] == "completed"
    assert len(_peek_all(ctx, f"T{tid}.outbox", persistent=True)) == 1


@pytest.mark.parametrize("changed_route", ["outbox", "control"])
def test_realtime_route_refresh_preserves_unchanged_queue_cursor(
    tmp_path: Path, changed_route: str
) -> None:
    """Replay discovered queues, without replaying the route already observed."""
    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    tid = str(time.time_ns())
    default_outbox, default_control = f"T{tid}.outbox", f"T{tid}.ctrl_out"
    _write_queue(
        ctx,
        default_outbox,
        {"type": "stream", "stream": "stdout", "data": "old-out"},
        persistent=True,
    )
    _write_queue(
        ctx,
        default_control,
        {"type": "stream", "stream": "stderr", "data": "old-err"},
        persistent=True,
    )
    iterator = events_cmd.iter_task_realtime_events(ctx, tid, follow=False)
    try:
        assert next(iterator).payload["data"] == "old-out"
        assert next(iterator).payload["data"] == "old-err"
        spec = _taskspec_payload(tid)
        new_route = f"audit.{tid}.{changed_route}"
        if changed_route == "outbox":
            spec["io"]["outputs"]["outbox"] = new_route
            # Old outbox stream evidence must not suppress the new one-shot value.
            _write_queue(ctx, new_route, {"final": "new-out"}, persistent=True)
        else:
            spec["io"]["control"]["ctrl_out"] = new_route
            _write_queue(
                ctx,
                new_route,
                {"type": "stream", "stream": "stderr", "data": "new-err"},
                persistent=True,
            )
            _write_queue(
                ctx,
                new_route,
                {
                    "type": "terminal",
                    "source": "task",
                    "tid": tid,
                    "status": "completed",
                },
                persistent=True,
            )
        _write_log(
            ctx,
            {
                "tid": tid,
                "event": "work_started",
                "status": "running",
                "taskspec": spec,
            },
        )
        remaining = list(iterator)
        streams = [
            event.payload["data"]
            for event in remaining
            if event.event_type in {"stdout", "stderr"}
        ]
        assert streams == ([] if changed_route == "outbox" else ["new-err"])
        assert remaining[-1].event_type == "end"
        assert remaining[-1].payload["status"] == "completed"
        result = next(event for event in remaining if event.event_type == "result")
        if changed_route == "outbox":
            assert result.payload["value"] == {"final": "new-out"}
        assert len(_peek_all(ctx, default_outbox, persistent=True)) == 1
        assert len(_peek_all(ctx, default_control, persistent=True)) == 1
    finally:
        close = getattr(iterator, "close", None)
        assert callable(close)
        close()


@pytest.mark.parametrize("failure", [None, "outbox", "control", "monitor"])
def test_realtime_rebind_releases_subscriptions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    """A completed or failed rebind closes real leases and watcher owners."""
    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    tid = str(time.time_ns())
    spec = _taskspec_payload(tid, persistent=True)
    spec["io"]["outputs"]["outbox"] = f"audit.{tid}.outbox"
    spec["io"]["control"]["ctrl_out"] = f"audit.{tid}.control"
    original_queue = WeftContext.queue
    original_routes = events_cmd._open_realtime_routes
    original_monitor = events_cmd.QueueChangeMonitor
    opened: list[Any] = []
    closed: set[int] = set()
    monitors: list[Any] = []
    closed_monitors: set[int] = set()
    rebinding = False

    def routes(*args: Any, **kwargs: Any) -> Any:
        nonlocal rebinding
        rebinding = args[1] == spec["io"]["outputs"]["outbox"]
        try:
            return original_routes(*args, **kwargs)
        finally:
            rebinding = False

    def queue(self: WeftContext, name: str, **kwargs: Any) -> Any:
        if (
            rebinding
            and failure in {"outbox", "control"}
            and name.endswith(f".{failure}")
        ):
            raise RuntimeError("injected rebind failure")
        handle = original_queue(self, name, **kwargs)
        opened.append(handle)
        original_close = handle.close

        def close() -> None:
            original_close()
            closed.add(id(handle))

        monkeypatch.setattr(handle, "close", close)
        return handle

    def monitor(*args: Any, **kwargs: Any) -> Any:
        if rebinding and failure == "monitor":
            raise RuntimeError("injected rebind failure")
        owner = original_monitor(*args, **kwargs)
        monitors.append(owner)
        original_close = owner.close

        def close() -> None:
            original_close()
            closed_monitors.add(id(owner))

        monkeypatch.setattr(owner, "close", close)
        return owner

    def publish() -> None:
        _write_log(
            ctx,
            {
                "tid": tid,
                "event": "work_started",
                "status": "running",
                "taskspec": spec,
            },
        )

    _publish_after_initial_materialization(monkeypatch, publish)
    monkeypatch.setattr(WeftContext, "queue", queue)
    monkeypatch.setattr(events_cmd, "_open_realtime_routes", routes)
    monkeypatch.setattr(events_cmd, "QueueChangeMonitor", monitor)
    iterator = events_cmd.iter_task_realtime_events(ctx, tid, follow=False)
    try:
        if failure is None:
            list(iterator)
        else:
            with pytest.raises(RuntimeError, match="injected rebind failure"):
                list(iterator)
    finally:
        close = getattr(iterator, "close", None)
        assert callable(close)
        close()
    assert len(monitors) == (2 if failure is None else 1)
    assert {id(owner) for owner in monitors} == closed_monitors
    assert {id(handle) for handle in opened} == closed
