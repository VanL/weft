"""Broker-backed tests for first-class pipeline runtime tasks.

Spec references:
- docs/specifications/07-System_Invariants.md [QUEUE.7]
"""

from __future__ import annotations

import itertools
import json
import signal
import time
from pathlib import Path
from typing import Any

import pytest

import weft.core.tasks.pipeline as pipeline_module
from tests.helpers.test_backend import prepare_project_root
from weft._constants import (
    CONTROL_KILL,
    CONTROL_STOP,
    PIPELINE_EDGE_RUNTIME_METADATA_KEY,
    PIPELINE_OWNER_METADATA_KEY,
    PIPELINE_RUNTIME_METADATA_KEY,
    WEFT_ENDPOINTS_REGISTRY_QUEUE,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE,
    WEFT_PIPELINES_STATE_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    WEFT_STREAMING_SESSIONS_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
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


def _drive_consumer_once_until_idle(
    consumer: Consumer,
    *,
    timeout: float = 20.0,
) -> None:
    deadline = time.monotonic() + timeout
    consumer.process_once()
    while time.monotonic() < deadline:
        if (
            not consumer._active_work_in_flight
            and not consumer._has_pending_worker_results()
            and not consumer._has_active_worker_threads()
        ):
            return
        consumer.wait_for_activity(timeout=0.05)
        consumer.process_once()
    raise AssertionError("consumer worker did not finish")


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


def _compiled_pipeline_taskspec_payload(
    tmp_path: Path,
    *,
    stage_count: int = 1,
) -> dict[str, Any]:
    """Compile stages and their edges through the production path."""

    root = prepare_project_root(tmp_path / "project")
    ctx = build_context(spec_context=root)
    stage_names = tuple(f"stage-{index}" for index in range(stage_count))
    for stage_name in stage_names:
        _write_json(ctx.weft_dir / "tasks" / f"{stage_name}.json", _task_payload())
    compiled = compile_linear_pipeline(
        load_pipeline_spec_payload(
            {
                "name": "pipe",
                "stages": [
                    {"name": stage_name, "task": stage_name}
                    for stage_name in stage_names
                ],
            }
        ),
        context=ctx,
        task_loader=lambda name: _load_task(root, name),
    )
    return compiled.pipeline_taskspec.model_dump(mode="json")


_PIPELINE_FIXED_ROUTE_ROLES = (
    "events",
    "status",
    "pipeline_registry",
    "internal_spawn_requests",
    "stage_ctrl_in",
    "edge_ctrl_in",
)
_PIPELINE_BASE_AND_SUPPORT_ROLES = (
    "inbox",
    "reserved",
    "outbox",
    "ctrl_in",
    "ctrl_out",
    "global_log",
    "tid_mappings",
    "streaming_sessions",
    "endpoints_registry",
)
_PIPELINE_FIXED_ROUTE_COLLISION_PAIRS = tuple(
    (pipeline_role, other_role)
    for pipeline_role in _PIPELINE_FIXED_ROUTE_ROLES
    for other_role in _PIPELINE_BASE_AND_SUPPORT_ROLES
) + tuple(itertools.combinations(_PIPELINE_FIXED_ROUTE_ROLES, 2))
_PAYLOAD_MUTABLE_PIPELINE_ROLES = {
    "inbox",
    "outbox",
    "ctrl_in",
    "ctrl_out",
    "events",
    "status",
    "stage_ctrl_in",
    "edge_ctrl_in",
}


def _pipeline_topology_role_values(payload: dict[str, Any]) -> dict[str, str]:
    runtime = payload["metadata"][PIPELINE_RUNTIME_METADATA_KEY]
    tid = payload["tid"]
    return {
        "inbox": payload["io"]["inputs"]["inbox"],
        "reserved": f"T{tid}.reserved",
        "outbox": payload["io"]["outputs"]["outbox"],
        "ctrl_in": payload["io"]["control"]["ctrl_in"],
        "ctrl_out": payload["io"]["control"]["ctrl_out"],
        "global_log": WEFT_GLOBAL_LOG_QUEUE,
        "tid_mappings": WEFT_TID_MAPPINGS_QUEUE,
        "streaming_sessions": WEFT_STREAMING_SESSIONS_QUEUE,
        "endpoints_registry": WEFT_ENDPOINTS_REGISTRY_QUEUE,
        "events": runtime["queues"]["events"],
        "status": runtime["queues"]["status"],
        "pipeline_registry": WEFT_PIPELINES_STATE_QUEUE,
        "internal_spawn_requests": WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE,
        "stage_ctrl_in": runtime["stages"][0]["ctrl_in_queue"],
        "edge_ctrl_in": runtime["edges"][0]["taskspec"]["io"]["control"]["ctrl_in"],
    }


def _set_pipeline_payload_role(
    payload: dict[str, Any],
    role: str,
    queue_name: str,
) -> None:
    runtime = payload["metadata"][PIPELINE_RUNTIME_METADATA_KEY]
    if role == "inbox":
        payload["io"]["inputs"]["inbox"] = queue_name
    elif role == "outbox":
        payload["io"]["outputs"]["outbox"] = queue_name
    elif role in {"ctrl_in", "ctrl_out"}:
        payload["io"]["control"][role] = queue_name
    elif role in {"events", "status"}:
        runtime["queues"][role] = queue_name
    elif role == "stage_ctrl_in":
        runtime["stages"][0]["ctrl_in_queue"] = queue_name
    elif role == "edge_ctrl_in":
        runtime["edges"][0]["taskspec"]["io"]["control"]["ctrl_in"] = queue_name
    else:  # pragma: no cover - test matrix guard
        raise AssertionError(f"Role {role!r} is not payload-mutable")


def _pipeline_role_error_label(role: str) -> str:
    if role == "stage_ctrl_in":
        return "stage_ctrl_in:"
    if role == "edge_ctrl_in":
        return "edge_ctrl_in:"
    return role


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


@pytest.mark.parametrize("events_queue", ("", "   "))
def test_pipeline_owner_event_route_rejects_blank_queue_before_broker_side_effects(
    tmp_path: Path,
    events_queue: str,
) -> None:
    """Pipeline owner output remains a validated construction route [QUEUE.7]."""

    tid = "1778089999999999199"
    payload = _pipeline_owned_stage_spec(
        tid,
        function_target="tests.tasks.sample_targets:echo_payload",
    ).model_dump(mode="json")
    payload["metadata"][PIPELINE_OWNER_METADATA_KEY]["events_queue"] = events_queue
    db_path = tmp_path / f"blank-owner-events-{len(events_queue)}.sqlite3"

    with pytest.raises(ValueError) as exc_info:
        Consumer(db_path, TaskSpec.model_validate(payload))

    message = str(exc_info.value)
    assert "events_queue" in message
    assert "non-empty" in message
    assert db_path.exists() is False


@pytest.mark.parametrize("other_role", _PIPELINE_BASE_AND_SUPPORT_ROLES)
def test_pipeline_owner_event_route_rejects_every_base_and_support_collision(
    tmp_path: Path,
    other_role: str,
) -> None:
    """Pipeline-owned Consumers isolate owner events from task routes [QUEUE.7]."""

    tid = "1778089999999999198"
    payload = _pipeline_owned_stage_spec(
        tid,
        function_target="tests.tasks.sample_targets:echo_payload",
    ).model_dump(mode="json")
    role_values = {
        "inbox": payload["io"]["inputs"]["inbox"],
        "reserved": f"T{tid}.reserved",
        "outbox": payload["io"]["outputs"]["outbox"],
        "ctrl_in": payload["io"]["control"]["ctrl_in"],
        "ctrl_out": payload["io"]["control"]["ctrl_out"],
        "global_log": WEFT_GLOBAL_LOG_QUEUE,
        "tid_mappings": WEFT_TID_MAPPINGS_QUEUE,
        "streaming_sessions": WEFT_STREAMING_SESSIONS_QUEUE,
        "endpoints_registry": WEFT_ENDPOINTS_REGISTRY_QUEUE,
    }
    duplicate_queue = role_values[other_role]
    payload["metadata"][PIPELINE_OWNER_METADATA_KEY]["events_queue"] = duplicate_queue
    db_path = tmp_path / f"owner-events-{other_role}-alias.sqlite3"

    with pytest.raises(ValueError) as exc_info:
        Consumer(db_path, TaskSpec.model_validate(payload))

    message = str(exc_info.value)
    assert "pipeline_owner_events" in message
    assert other_role in message
    assert duplicate_queue in message
    assert db_path.exists() is False


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


def test_pipeline_edge_allows_compiled_source_target_and_owner_event_aliases(
    tmp_path: Path,
) -> None:
    """The three compiled edge semantic aliases remain valid [QUEUE.7]."""

    edge = PipelineEdgeTask(
        tmp_path / "edge-allowed-aliases.sqlite3",
        _entry_edge_spec("1778089999999999201"),
    )
    edge.cleanup()


@pytest.mark.parametrize("malformation", ("missing", "none", "integer", "blank"))
def test_pipeline_task_rejects_malformed_precompiled_edge_control_route(
    tmp_path: Path,
    malformation: str,
) -> None:
    """Every precompiled edge control output is valid before broker I/O [QUEUE.7]."""

    payload = _compiled_pipeline_taskspec_payload(tmp_path)
    runtime = payload["metadata"][PIPELINE_RUNTIME_METADATA_KEY]
    control = runtime["edges"][0]["taskspec"]["io"]["control"]
    if malformation == "missing":
        control.pop("ctrl_in")
    elif malformation == "none":
        control["ctrl_in"] = None
    elif malformation == "integer":
        control["ctrl_in"] = 7
    else:
        control["ctrl_in"] = "   "
    db_path = tmp_path / f"malformed-edge-control-{malformation}.sqlite3"

    with pytest.raises(ValueError) as exc_info:
        PipelineTask(db_path, TaskSpec.model_validate(payload))

    message = str(exc_info.value)
    assert "edge" in message.lower()
    assert "ctrl_in" in message
    assert "non-empty" in message
    assert db_path.exists() is False


@pytest.mark.parametrize(
    ("extra_role", "other_role"),
    tuple(
        (extra_role, base_role)
        for extra_role in ("source", "target", "events")
        for base_role in ("inbox", "reserved", "outbox", "ctrl_in", "ctrl_out")
        if (extra_role, base_role) not in {("source", "inbox"), ("target", "outbox")}
    )
    + (("source", "target"), ("source", "events"), ("target", "events")),
)
def test_pipeline_edge_rejects_unsafe_route_alias_before_broker_side_effects(
    tmp_path: Path,
    extra_role: str,
    other_role: str,
) -> None:
    """Only the two compiled edge semantic aliases are accepted [QUEUE.7]."""

    tid = "1778089999999999202"
    payload = _entry_edge_spec(tid).model_dump(mode="json")
    runtime = payload["metadata"][PIPELINE_EDGE_RUNTIME_METADATA_KEY]
    base_values = {
        "inbox": payload["io"]["inputs"]["inbox"],
        "reserved": f"T{tid}.reserved",
        "outbox": payload["io"]["outputs"]["outbox"],
        "ctrl_in": payload["io"]["control"]["ctrl_in"],
        "ctrl_out": payload["io"]["control"]["ctrl_out"],
    }
    runtime_keys = {
        "source": "source_queue",
        "target": "target_queue",
        "events": "events_queue",
    }
    if other_role in runtime_keys:
        duplicate_queue = f"edge.shared.{extra_role}-{other_role}"
        runtime[runtime_keys[extra_role]] = duplicate_queue
        runtime[runtime_keys[other_role]] = duplicate_queue
    else:
        duplicate_queue = base_values[other_role]
        runtime[runtime_keys[extra_role]] = duplicate_queue
    db_path = tmp_path / f"edge-{extra_role}-{other_role}-alias.sqlite3"

    with pytest.raises(ValueError) as exc_info:
        PipelineEdgeTask(db_path, TaskSpec.model_validate(payload))

    message = str(exc_info.value)
    assert extra_role in message
    assert other_role in message
    assert duplicate_queue in message
    assert db_path.exists() is False


@pytest.mark.parametrize(
    ("pipeline_role", "other_role"),
    _PIPELINE_FIXED_ROUTE_COLLISION_PAIRS,
)
def test_pipeline_task_rejects_every_fixed_route_collision_before_broker_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pipeline_role: str,
    other_role: str,
) -> None:
    """All PipelineTask fixed routes are pairwise distinct [QUEUE.7]."""

    payload = _compiled_pipeline_taskspec_payload(tmp_path)
    role_values = _pipeline_topology_role_values(payload)
    if pipeline_role in _PAYLOAD_MUTABLE_PIPELINE_ROLES:
        duplicate_queue = role_values[other_role]
        _set_pipeline_payload_role(payload, pipeline_role, duplicate_queue)
    elif other_role in _PAYLOAD_MUTABLE_PIPELINE_ROLES:
        duplicate_queue = role_values[pipeline_role]
        _set_pipeline_payload_role(payload, other_role, duplicate_queue)
    else:
        duplicate_queue = role_values[other_role]
        constant_name = {
            "pipeline_registry": "WEFT_PIPELINES_STATE_QUEUE",
            "internal_spawn_requests": "WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE",
        }[pipeline_role]
        monkeypatch.setattr(pipeline_module, constant_name, duplicate_queue)
    db_path = tmp_path / f"pipeline-{pipeline_role}-{other_role}-alias.sqlite3"

    with pytest.raises(ValueError) as exc_info:
        PipelineTask(db_path, TaskSpec.model_validate(payload))

    message = str(exc_info.value)
    assert _pipeline_role_error_label(pipeline_role) in message
    assert _pipeline_role_error_label(other_role) in message
    assert duplicate_queue in message
    assert db_path.exists() is False


@pytest.mark.parametrize("route_family", ("stage", "edge"))
def test_pipeline_task_rejects_duplicate_precompiled_child_control_routes(
    tmp_path: Path,
    route_family: str,
) -> None:
    """Distinct precompiled children retain distinct control outputs [QUEUE.7]."""

    payload = _compiled_pipeline_taskspec_payload(tmp_path, stage_count=2)
    runtime = payload["metadata"][PIPELINE_RUNTIME_METADATA_KEY]
    if route_family == "stage":
        routes = runtime["stages"]
        duplicate_queue = routes[0]["ctrl_in_queue"]
        routes[1]["ctrl_in_queue"] = duplicate_queue
        role_prefix = "stage_ctrl_in"
    else:
        routes = runtime["edges"]
        first_control = routes[0]["taskspec"]["io"]["control"]
        second_control = routes[1]["taskspec"]["io"]["control"]
        duplicate_queue = first_control["ctrl_in"]
        second_control["ctrl_in"] = duplicate_queue
        role_prefix = "edge_ctrl_in"
    db_path = tmp_path / f"duplicate-{route_family}-controls.sqlite3"

    with pytest.raises(ValueError) as exc_info:
        PipelineTask(db_path, TaskSpec.model_validate(payload))

    message = str(exc_info.value)
    assert f"{role_prefix}:0:" in message
    assert f"{role_prefix}:1:" in message
    assert duplicate_queue in message
    assert db_path.exists() is False


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

    _drive_consumer_once_until_idle(task)

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

    _drive_consumer_once_until_idle(task)

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

    _drive_consumer_once_until_idle(consumer)

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


def test_stage_output_edge_uses_override_payload_when_downstream_defaults_input_is_set(
    broker_env,
) -> None:
    db_path, make_queue = broker_env
    tid = str(time.time_ns())
    edge = PipelineEdgeTask(
        db_path, _stage_output_edge_spec(tid, override_input={"payload": "override"})
    )
    make_queue("T1775000000000000001.outbox").write(json.dumps({"payload": "hello"}))

    edge.process_once()

    assert make_queue("P1775000000000000000.stage-to-next").read_one() == json.dumps(
        {"payload": "override"}
    )


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


def test_stage_output_edge_fails_when_source_has_extra_payload(
    broker_env,
) -> None:
    db_path, make_queue = broker_env
    tid = str(time.time_ns())
    edge = PipelineEdgeTask(db_path, _stage_output_edge_spec(tid))
    source = make_queue("T1775000000000000001.outbox")
    source.write(json.dumps({"payload": "first"}))
    source.write(json.dumps({"payload": "second"}))

    edge.process_once()

    assert edge.taskspec.state.status == "failed"
    assert make_queue("P1775000000000000000.stage-to-next").read_one() is None
    events = _drain_json(make_queue("P1775000000000000000.events"))
    terminal = next(event for event in events if event.get("type") == "edge_terminal")
    assert terminal["status"] == "failed"
    assert "single handoff" in terminal["error"]


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

    spawn_queue = ctx.queue(WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE, persistent=False)
    spawned = _drain_json(spawn_queue)
    assert len(spawned) == len(compiled.runtime.stages) + len(compiled.runtime.edges)
    assert [item["taskspec"]["tid"] for item in spawned] == [
        compiled.runtime.edges[0].tid,
        compiled.runtime.stages[0].tid,
        compiled.runtime.edges[1].tid,
        compiled.runtime.stages[1].tid,
        compiled.runtime.edges[2].tid,
    ]
    assert _drain_json(ctx.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=False)) == []
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
    assert latest["stages"][0]["waiting_on"] == WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE
    assert latest["edges"][0]["status"] == "created"
    assert latest["edges"][0]["activity"] == "queued"
    assert latest["edges"][0]["waiting_on"] == WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE


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

    task.wait_for_activity(timeout=0.05)
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

    task.wait_for_activity(timeout=0.05)
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

    task.wait_for_activity(timeout=0.05)
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

    task.wait_for_activity(timeout=0.05)
    task.process_once()

    assert task.taskspec.state.status == "failed"
    ctrl_queue = ctx.queue(compiled.runtime.stages[0].ctrl_in_queue, persistent=True)
    assert ctrl_queue.read_one() == CONTROL_STOP
    snapshots = _drain_json(ctx.queue(compiled.runtime.queues.status, persistent=True))
    latest = snapshots[-1]
    assert latest["status"] == "failed"
    assert latest["failure"]["child_kind"] == "stage"
    assert latest["failure"]["child_name"] == "first"


def test_pipeline_task_fails_fast_when_child_edge_fails(tmp_path: Path) -> None:
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
                "type": "edge_terminal",
                "pipeline_tid": compiled.pipeline_tid,
                "edge_name": compiled.runtime.edges[-1].name,
                "child_tid": compiled.runtime.edges[-1].tid,
                "status": "failed",
                "error": "single handoff contract violated",
            }
        )
    )

    task.wait_for_activity(timeout=0.05)
    task.process_once()

    assert task.taskspec.state.status == "failed"
    snapshots = _drain_json(ctx.queue(compiled.runtime.queues.status, persistent=True))
    latest = snapshots[-1]
    assert latest["status"] == "failed"
    assert latest["failure"]["child_kind"] == "edge"
    assert latest["failure"]["child_name"] == compiled.runtime.edges[-1].name


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
    task.wait_for_activity(timeout=0.05)
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
    task.wait_for_activity(timeout=0.05)
    task.process_once()

    assert task.taskspec.state.status == "killed"
    assert (
        ctx.queue(compiled.runtime.stages[0].ctrl_in_queue, persistent=True).read_one()
        == CONTROL_KILL
    )


def test_pipeline_task_pending_termination_signal_skips_bootstrap(
    tmp_path: Path,
) -> None:
    """A pending termination signal noted before the first turn must stop the
    pipeline before it submits any child spawn requests.

    Spec: docs/specifications/05-Message_Flow_and_State.md [MF-3]
    """
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

    task.note_termination_signal(signal.SIGTERM)
    task.process_once()

    assert task.taskspec.state.status == "cancelled"
    assert task._bootstrapped is False

    spawn_queue = ctx.queue(WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE, persistent=False)
    assert _drain_json(spawn_queue) == []
