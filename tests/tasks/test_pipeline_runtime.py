"""Broker-backed tests for first-class pipeline runtime tasks."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft._constants import (
    CONTROL_KILL,
    CONTROL_STOP,
    PIPELINE_EDGE_RUNTIME_METADATA_KEY,
    PIPELINE_OWNER_METADATA_KEY,
    WEFT_PIPELINES_STATE_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
)
from weft.commands import specs as spec_cmd
from weft.context import build_context
from weft.core.pipelines import compile_linear_pipeline, load_pipeline_spec_payload
from weft.core.tasks import Consumer
from weft.core.tasks.pipeline import PipelineEdgeTask, PipelineTask
from weft.core.taskspec import IOSection, SpecSection, StateSection, TaskSpec


def _drain(queue) -> list[str]:
    values: list[str] = []
    while True:
        value = queue.read_one()
        if value is None:
            return values
        values.append(value)


def _drain_json(queue) -> list[dict[str, Any]]:
    return [json.loads(item) for item in _drain(queue)]


def _task_payload(
    *,
    function_target: str = "tests.tasks.sample_targets:echo_payload",
    persistent: bool = False,
    interactive: bool = False,
    stream_output: bool = False,
) -> dict[str, Any]:
    return {
        "name": "task",
        "spec": {
            "type": "function",
            "function_target": function_target,
            "persistent": persistent,
            "interactive": interactive,
            "stream_output": stream_output,
        },
        "metadata": {},
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_task(root: Path, name: str) -> dict[str, Any]:
    _kind, _path, payload = spec_cmd.load_spec(
        name,
        spec_type=spec_cmd.SPEC_TYPE_TASK,
        context_path=root,
    )
    return payload


def _pipeline_owned_stage_spec(tid: str, *, function_target: str) -> TaskSpec:
    return TaskSpec(
        tid=tid,
        name="stage-task",
        spec=SpecSection(type="function", function_target=function_target),
        io=IOSection(
            inputs={"inbox": f"T{tid}.inbox"},
            outputs={"outbox": f"T{tid}.outbox"},
            control={"ctrl_in": f"T{tid}.ctrl_in", "ctrl_out": f"T{tid}.ctrl_out"},
        ),
        state=StateSection(),
        metadata={
            "role": "pipeline_stage",
            PIPELINE_OWNER_METADATA_KEY: {
                "pipeline_tid": "1775000000000000000",
                "events_queue": "P1775000000000000000.events",
                "role": "pipeline_stage",
                "stage_name": "transform",
            },
        },
    )


def _entry_edge_spec(tid: str, *, override_input: Any = None) -> TaskSpec:
    return TaskSpec(
        tid=tid,
        name="edge-task",
        spec=SpecSection(
            type="function",
            function_target="weft.core.tasks.pipeline:runtime",
        ),
        io=IOSection(
            inputs={"inbox": "P1775000000000000000.inbox"},
            outputs={"outbox": "P1775000000000000000.pipeline-to-stage"},
            control={"ctrl_in": f"T{tid}.ctrl_in", "ctrl_out": f"T{tid}.ctrl_out"},
        ),
        state=StateSection(),
        metadata={
            "role": "pipeline_edge",
            PIPELINE_OWNER_METADATA_KEY: {
                "pipeline_tid": "1775000000000000000",
                "events_queue": "P1775000000000000000.events",
                "role": "pipeline_edge",
                "edge_name": "pipeline-to-stage",
            },
            PIPELINE_EDGE_RUNTIME_METADATA_KEY: {
                "pipeline_tid": "1775000000000000000",
                "edge_name": "pipeline-to-stage",
                "source_kind": "pipeline_input",
                "source_queue": "P1775000000000000000.inbox",
                "target_queue": "P1775000000000000000.pipeline-to-stage",
                "events_queue": "P1775000000000000000.events",
                "downstream_stage": "stage",
                "downstream_tid": "1775000000000000001",
                "override_input": override_input,
                "emits_pipeline_result": False,
            },
        },
    )


def _stage_output_edge_spec(tid: str, *, override_input: Any = None) -> TaskSpec:
    base_payload = _entry_edge_spec(tid, override_input=override_input).model_dump(
        mode="json"
    )
    base_payload["io"]["inputs"]["inbox"] = "T1775000000000000001.outbox"
    base_payload["io"]["outputs"]["outbox"] = "P1775000000000000000.stage-to-next"
    base_payload["metadata"][PIPELINE_EDGE_RUNTIME_METADATA_KEY] = {
        "pipeline_tid": "1775000000000000000",
        "edge_name": "stage-to-next",
        "source_kind": "stage_output",
        "source_queue": "T1775000000000000001.outbox",
        "target_queue": "P1775000000000000000.stage-to-next",
        "events_queue": "P1775000000000000000.events",
        "upstream_stage": "stage",
        "upstream_tid": "1775000000000000001",
        "downstream_stage": "next",
        "downstream_tid": "1775000000000000002",
        "override_input": override_input,
        "emits_pipeline_result": False,
    }
    base_payload["metadata"][PIPELINE_OWNER_METADATA_KEY]["edge_name"] = "stage-to-next"
    return TaskSpec.model_validate(base_payload)


def test_pipeline_owned_stage_emits_success_owner_event_after_outbox_write(
    broker_env,
) -> None:
    db_path, make_queue = broker_env
    tid = str(time.time_ns())
    task = Consumer(
        db_path,
        _pipeline_owned_stage_spec(
            tid,
            function_target="tests.tasks.sample_targets:echo_payload",
        ),
    )

    inbox = make_queue(f"T{tid}.inbox")
    inbox.write(json.dumps({"args": ["hello"], "kwargs": {"suffix": "!"}}))

    task.process_once()

    assert make_queue(f"T{tid}.outbox").read_one() == "hello!"
    events = _drain_json(make_queue("P1775000000000000000.events"))
    assert any(
        event.get("type") == "stage_terminal"
        and event.get("status") == "completed"
        and event.get("stage_name") == "transform"
        for event in events
    )


def test_pipeline_owned_stage_emits_failure_owner_event(broker_env) -> None:
    db_path, make_queue = broker_env
    tid = str(time.time_ns())
    task = Consumer(
        db_path,
        _pipeline_owned_stage_spec(
            tid,
            function_target="tests.tasks.sample_targets:fail_payload",
        ),
    )

    make_queue(f"T{tid}.inbox").write(json.dumps({"args": ["hello"]}))

    task.process_once()

    events = _drain_json(make_queue("P1775000000000000000.events"))
    assert any(
        event.get("type") == "stage_terminal"
        and event.get("status") == "failed"
        and event.get("stage_name") == "transform"
        for event in events
    )


def test_non_pipeline_task_emits_no_owner_event(broker_env) -> None:
    db_path, make_queue = broker_env
    tid = str(time.time_ns())
    task = TaskSpec(
        tid=tid,
        name="plain-task",
        spec=SpecSection(
            type="function",
            function_target="tests.tasks.sample_targets:echo_payload",
        ),
        io=IOSection(
            inputs={"inbox": f"T{tid}.inbox"},
            outputs={"outbox": f"T{tid}.outbox"},
            control={"ctrl_in": f"T{tid}.ctrl_in", "ctrl_out": f"T{tid}.ctrl_out"},
        ),
        state=StateSection(),
        metadata={},
    )
    consumer = Consumer(db_path, task)
    make_queue(f"T{tid}.inbox").write(json.dumps({"args": ["hello"]}))

    consumer.process_once()

    assert make_queue("P1775000000000000000.events").read_one() is None


def test_entry_edge_moves_pipeline_input_and_emits_checkpoint(broker_env) -> None:
    db_path, make_queue = broker_env
    tid = str(time.time_ns())
    edge = PipelineEdgeTask(db_path, _entry_edge_spec(tid))
    make_queue("P1775000000000000000.inbox").write(json.dumps({"payload": "hello"}))

    edge.process_once()

    assert make_queue(
        "P1775000000000000000.pipeline-to-stage"
    ).read_one() == json.dumps({"payload": "hello"})
    events = _drain_json(make_queue("P1775000000000000000.events"))
    assert any(event.get("type") == "edge_checkpoint" for event in events)


def test_edge_uses_override_payload_when_downstream_defaults_input_is_set(
    broker_env,
) -> None:
    db_path, make_queue = broker_env
    tid = str(time.time_ns())
    edge = PipelineEdgeTask(
        db_path, _entry_edge_spec(tid, override_input={"payload": "override"})
    )
    make_queue("P1775000000000000000.inbox").write(json.dumps({"payload": "hello"}))

    edge.process_once()

    assert make_queue(
        "P1775000000000000000.pipeline-to-stage"
    ).read_one() == json.dumps({"payload": "override"})


def test_stage_output_edge_moves_single_payload_without_rewriting_it(
    broker_env,
) -> None:
    db_path, make_queue = broker_env
    tid = str(time.time_ns())
    edge = PipelineEdgeTask(db_path, _stage_output_edge_spec(tid))
    make_queue("T1775000000000000001.outbox").write(json.dumps({"payload": "hello"}))

    edge.process_once()

    assert make_queue("P1775000000000000000.stage-to-next").read_one() == json.dumps(
        {"payload": "hello"}
    )


def test_edge_keeps_successful_handoff_when_checkpoint_emit_fails(
    broker_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    tid = str(time.time_ns())
    edge = PipelineEdgeTask(db_path, _entry_edge_spec(tid))
    make_queue("P1775000000000000000.inbox").write(json.dumps({"payload": "hello"}))

    original_queue = edge._queue

    def broken_queue(name: str):
        queue = original_queue(name)
        if name != "P1775000000000000000.events":
            return queue

        class QueueProxy:
            def __init__(self, delegate) -> None:
                self._delegate = delegate

            def write(self, *_args, **_kwargs):
                raise RuntimeError("boom")

            def __getattr__(self, attr: str):
                return getattr(self._delegate, attr)

        return QueueProxy(queue)

    monkeypatch.setattr(edge, "_queue", broken_queue)

    edge.process_once()

    assert make_queue(
        "P1775000000000000000.pipeline-to-stage"
    ).read_one() == json.dumps({"payload": "hello"})
    assert edge.taskspec.state.status == "completed"


def test_pipeline_task_bootstraps_all_children_before_any_stage_runs(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    _write_json(ctx.weft_dir / "tasks" / "first.json", _task_payload())
    _write_json(ctx.weft_dir / "tasks" / "second.json", _task_payload())
    compiled = compile_linear_pipeline(
        load_pipeline_spec_payload(
            {
                "name": "pipe",
                "stages": [
                    {"name": "first", "task": "first"},
                    {"name": "second", "task": "second"},
                ],
            }
        ),
        context=ctx,
        task_loader=lambda name: _load_task(root, name),
    )
    task = PipelineTask(
        ctx.broker_target, compiled.pipeline_taskspec, config=ctx.broker_config
    )

    task.process_once()

    spawn_queue = ctx.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=False)
    spawned = _drain_json(spawn_queue)
    assert len(spawned) == len(compiled.runtime.stages) + len(compiled.runtime.edges)
    assert ctx.queue(compiled.runtime.queues.events, persistent=True).read_one() is None


def test_pipeline_task_writes_active_registry_record_before_child_progress(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    _write_json(ctx.weft_dir / "tasks" / "first.json", _task_payload())
    compiled = compile_linear_pipeline(
        load_pipeline_spec_payload(
            {"name": "pipe", "stages": [{"name": "first", "task": "first"}]}
        ),
        context=ctx,
        task_loader=lambda name: _load_task(root, name),
    )
    task = PipelineTask(
        ctx.broker_target, compiled.pipeline_taskspec, config=ctx.broker_config
    )

    task.process_once()

    registry_entries = _drain_json(
        ctx.queue(WEFT_PIPELINES_STATE_QUEUE, persistent=False)
    )
    assert registry_entries
    assert registry_entries[-1]["tid"] == compiled.pipeline_tid


def test_pipeline_task_publishes_initial_waiting_activity_snapshot(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    _write_json(ctx.weft_dir / "tasks" / "first.json", _task_payload())
    compiled = compile_linear_pipeline(
        load_pipeline_spec_payload(
            {"name": "pipe", "stages": [{"name": "first", "task": "first"}]}
        ),
        context=ctx,
        task_loader=lambda name: _load_task(root, name),
    )
    task = PipelineTask(
        ctx.broker_target, compiled.pipeline_taskspec, config=ctx.broker_config
    )

    task.process_once()

    snapshots = _drain_json(ctx.queue(compiled.runtime.queues.status, persistent=True))
    assert snapshots
    latest = snapshots[-1]
    assert latest["type"] == "pipeline_status"
    assert latest["status"] == "running"
    assert latest["activity"] == "waiting"
    assert "waiting_on" not in latest
    assert latest["current_stage"] is None
    assert latest["current_edge"] is None
    assert latest["last_checkpoint"] is None
    assert latest["bindings"] == [
        {
            "edge_name": edge.name,
            "queue": edge.target_queue,
        }
        for edge in compiled.runtime.edges
    ]
    assert latest["stages"][0]["status"] == "created"
    assert latest["stages"][0]["activity"] == "queued"
    assert latest["stages"][0]["waiting_on"] == WEFT_SPAWN_REQUESTS_QUEUE
    assert latest["edges"][0]["status"] == "created"
    assert latest["edges"][0]["activity"] == "queued"
    assert latest["edges"][0]["waiting_on"] == WEFT_SPAWN_REQUESTS_QUEUE


def test_pipeline_task_updates_child_status_from_started_owner_events(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    _write_json(ctx.weft_dir / "tasks" / "first.json", _task_payload())
    compiled = compile_linear_pipeline(
        load_pipeline_spec_payload(
            {"name": "pipe", "stages": [{"name": "first", "task": "first"}]}
        ),
        context=ctx,
        task_loader=lambda name: _load_task(root, name),
    )
    task = PipelineTask(
        ctx.broker_target, compiled.pipeline_taskspec, config=ctx.broker_config
    )

    task.process_once()
    events_queue = ctx.queue(compiled.runtime.queues.events, persistent=True)
    events_queue.write(
        json.dumps(
            {
                "type": "stage_started",
                "pipeline_tid": compiled.pipeline_tid,
                "child_tid": compiled.runtime.stages[0].tid,
                "stage_name": "first",
                "status": "running",
                "activity": "waiting",
                "waiting_on": compiled.runtime.stages[0].input_queue,
            }
        )
    )
    events_queue.write(
        json.dumps(
            {
                "type": "edge_started",
                "pipeline_tid": compiled.pipeline_tid,
                "child_tid": compiled.runtime.edges[0].tid,
                "edge_name": compiled.runtime.edges[0].name,
                "status": "running",
                "activity": "waiting",
                "waiting_on": compiled.runtime.edges[0].source_queue,
            }
        )
    )

    task.process_once()
    task.process_once()

    snapshots = _drain_json(ctx.queue(compiled.runtime.queues.status, persistent=True))
    latest = snapshots[-1]
    assert latest["current_stage"] is None
    assert latest["current_edge"] is None
    assert latest["stages"][0]["status"] == "running"
    assert latest["stages"][0]["activity"] == "waiting"
    assert latest["stages"][0]["waiting_on"] == compiled.runtime.stages[0].input_queue
    assert latest["edges"][0]["status"] == "running"
    assert latest["edges"][0]["activity"] == "waiting"
    assert latest["edges"][0]["waiting_on"] == compiled.runtime.edges[0].source_queue


def test_pipeline_task_sets_current_edge_after_successful_stage_terminal(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    _write_json(ctx.weft_dir / "tasks" / "first.json", _task_payload())
    compiled = compile_linear_pipeline(
        load_pipeline_spec_payload(
            {"name": "pipe", "stages": [{"name": "first", "task": "first"}]}
        ),
        context=ctx,
        task_loader=lambda name: _load_task(root, name),
    )
    task = PipelineTask(
        ctx.broker_target, compiled.pipeline_taskspec, config=ctx.broker_config
    )

    task.process_once()
    ctx.queue(compiled.runtime.queues.events, persistent=True).write(
        json.dumps(
            {
                "type": "stage_terminal",
                "pipeline_tid": compiled.pipeline_tid,
                "stage_name": "first",
                "child_tid": compiled.runtime.stages[0].tid,
                "status": "completed",
            }
        )
    )

    task.process_once()

    snapshots = _drain_json(ctx.queue(compiled.runtime.queues.status, persistent=True))
    latest = snapshots[-1]
    assert latest["current_stage"] is None
    assert latest["current_edge"] == compiled.runtime.edges[-1].name
    assert latest["stages"][0]["status"] == "completed"


def test_pipeline_task_marks_completed_when_exit_edge_checks_in(tmp_path: Path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    _write_json(ctx.weft_dir / "tasks" / "first.json", _task_payload())
    compiled = compile_linear_pipeline(
        load_pipeline_spec_payload(
            {"name": "pipe", "stages": [{"name": "first", "task": "first"}]}
        ),
        context=ctx,
        task_loader=lambda name: _load_task(root, name),
    )
    task = PipelineTask(
        ctx.broker_target, compiled.pipeline_taskspec, config=ctx.broker_config
    )
    task.process_once()

    ctx.queue(compiled.runtime.queues.events, persistent=True).write(
        json.dumps(
            {
                "type": "edge_checkpoint",
                "pipeline_tid": compiled.pipeline_tid,
                "edge_name": compiled.runtime.edges[-1].name,
                "edge_tid": compiled.runtime.edges[-1].tid,
                "checkpoint": "queued_for_downstream",
                "queue": compiled.runtime.queues.outbox,
                "message_id": "1",
            }
        )
    )

    task.process_once()

    assert task.taskspec.state.status == "completed"
    snapshots = _drain_json(ctx.queue(compiled.runtime.queues.status, persistent=True))
    latest = snapshots[-1]
    assert latest["status"] == "completed"
    assert latest["current_edge"] is None
    assert latest["last_checkpoint"]["edge_name"] == compiled.runtime.edges[-1].name


def test_pipeline_task_fails_fast_when_child_stage_fails(tmp_path: Path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    _write_json(ctx.weft_dir / "tasks" / "first.json", _task_payload())
    compiled = compile_linear_pipeline(
        load_pipeline_spec_payload(
            {"name": "pipe", "stages": [{"name": "first", "task": "first"}]}
        ),
        context=ctx,
        task_loader=lambda name: _load_task(root, name),
    )
    task = PipelineTask(
        ctx.broker_target, compiled.pipeline_taskspec, config=ctx.broker_config
    )
    task.process_once()

    ctx.queue(compiled.runtime.queues.events, persistent=True).write(
        json.dumps(
            {
                "type": "stage_terminal",
                "pipeline_tid": compiled.pipeline_tid,
                "stage_name": "first",
                "child_tid": compiled.runtime.stages[0].tid,
                "status": "failed",
                "error": "boom",
            }
        )
    )

    task.process_once()

    assert task.taskspec.state.status == "failed"
    ctrl_queue = ctx.queue(compiled.runtime.stages[0].ctrl_in_queue, persistent=True)
    assert ctrl_queue.read_one() == CONTROL_STOP
    snapshots = _drain_json(ctx.queue(compiled.runtime.queues.status, persistent=True))
    latest = snapshots[-1]
    assert latest["status"] == "failed"
    assert latest["failure"]["child_kind"] == "stage"
    assert latest["failure"]["child_name"] == "first"


def test_pipeline_task_stop_propagates_to_waiting_children(tmp_path: Path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    _write_json(ctx.weft_dir / "tasks" / "first.json", _task_payload())
    compiled = compile_linear_pipeline(
        load_pipeline_spec_payload(
            {"name": "pipe", "stages": [{"name": "first", "task": "first"}]}
        ),
        context=ctx,
        task_loader=lambda name: _load_task(root, name),
    )
    task = PipelineTask(
        ctx.broker_target, compiled.pipeline_taskspec, config=ctx.broker_config
    )
    task.process_once()

    ctx.queue(compiled.runtime.queues.ctrl_in, persistent=True).write(CONTROL_STOP)
    task.process_once()

    assert task.taskspec.state.status == "cancelled"
    assert (
        ctx.queue(compiled.runtime.stages[0].ctrl_in_queue, persistent=True).read_one()
        == CONTROL_STOP
    )


def test_pipeline_task_kill_propagates_to_waiting_children(tmp_path: Path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    _write_json(ctx.weft_dir / "tasks" / "first.json", _task_payload())
    compiled = compile_linear_pipeline(
        load_pipeline_spec_payload(
            {"name": "pipe", "stages": [{"name": "first", "task": "first"}]}
        ),
        context=ctx,
        task_loader=lambda name: _load_task(root, name),
    )
    task = PipelineTask(
        ctx.broker_target, compiled.pipeline_taskspec, config=ctx.broker_config
    )
    task.process_once()

    ctx.queue(compiled.runtime.queues.ctrl_in, persistent=True).write(CONTROL_KILL)
    task.process_once()

    assert task.taskspec.state.status == "killed"
    assert (
        ctx.queue(compiled.runtime.stages[0].ctrl_in_queue, persistent=True).read_one()
        == CONTROL_KILL
    )
