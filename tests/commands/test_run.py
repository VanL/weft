"""Tests for `weft run` wait helpers and interactive result assembly."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest

import weft.commands._spawn_submission as spawn_submission_cmd
import weft.commands.run as run_cmd
import weft.commands.submission as submission_cmd
from simplebroker import Config, Queue
from tests.helpers.task_observation import write_task_observation
from tests.helpers.test_backend import prepare_project_root
from tests.helpers.weft_harness import (
    DEFAULT_TASK_COMPLETION_TIMEOUT,
    WeftTestHarness,
)
from weft._constants import (
    INTERNAL_AUTOSTART_ENABLED_METADATA_KEY,
    INTERNAL_AUTOSTART_SOURCE_METADATA_KEY,
    INTERNAL_HEARTBEAT_ENDPOINT_NAME,
    INTERNAL_RUNTIME_ENDPOINT_NAME_KEY,
    INTERNAL_RUNTIME_ENVELOPE_ENDPOINT_NAME_KEY,
    INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY,
    INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT,
    INTERNAL_RUNTIME_TASK_CLASS_KEY,
    INTERNAL_SERVICE_AUTHORITY_MANAGER,
    INTERNAL_SERVICE_AUTHORITY_METADATA_KEY,
    INTERNAL_SERVICE_KEY_HEARTBEAT,
    INTERNAL_SERVICE_KEY_METADATA_KEY,
    INTERNAL_SERVICE_LIFECYCLE_METADATA_KEY,
    MANAGER_LAUNCHER_SIGNAL_SUCCESS,
    TASK_POLL_INTERVAL_NONE_TOKEN,
    TASKSPEC_BUNDLE_ROOT_FIELD,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_MANAGER_OUTBOX_QUEUE,
    WEFT_SERVICES_REGISTRY_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
)
from weft._exceptions import CommandUsageError, ManagerStartFailed, SubmissionError
from weft.cli.run import render_run_result
from weft.commands import tasks as task_cmd
from weft.commands._spawn_submission import (
    SpawnSubmissionReconciliation,
    reconcile_submitted_spawn,
)
from weft.commands.run import (
    RunUsageError,
    _delete_spawn_request,
    _enqueue_taskspec,
    _execute_inline,
    _execute_pipeline,
    _execute_spec_via_manager,
)
from weft.commands.types import RunExecutionResult
from weft.context import WeftContext, build_context
from weft.core import manager_runtime as core_manager_runtime
from weft.core.control_probe import ControlProbeResult, MatchedPong
from weft.core.monitor.collation import MonitorTaskEventUpdate
from weft.core.monitor.store import open_monitor_store
from weft.core.service_convergence import build_manager_service_payload
from weft.core.task_state import task_state_queue_name
from weft.core.taskspec import (
    IOSection,
    SpecSection,
    StateSection,
    TaskSpec,
    decode_taskspec_transport_payload,
)
from weft.helpers import iter_queue_json_entries, process_create_time, tid_short_form
from weft.liveness.models import HostProcessObservation

pytestmark = [pytest.mark.shared]


def _ready_manager_result(
    record: dict[str, Any] | None = None,
    *,
    started_here: bool = False,
) -> core_manager_runtime.ManagerEnsureResult:
    return core_manager_runtime.ManagerEnsureResult(
        outcome="ready",
        manager_record=record,
        started_here=started_here,
        process_handle=None,
        reason="ready",
    )


def test_run_renderer_keeps_json_receipt_clean_when_readiness_degrades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accepted no-wait work keeps stdout JSON stable and warns on stderr."""
    rendered: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        "weft.cli.run.typer.echo",
        lambda message="", err=False, **_kwargs: rendered.append((message, err)),
    )

    exit_code = render_run_result(
        RunExecutionResult(
            tid="1780000000000000999",
            availability_warning="Task accepted; manager readiness is degraded",
        ),
        wait=False,
        json_output=True,
        verbose=False,
    )

    assert exit_code == 0
    stdout = [message for message, is_error in rendered if not is_error]
    stderr = [message for message, is_error in rendered if is_error]
    assert json.loads(stdout[0]) == {
        "tid": "1780000000000000999",
        "status": "queued",
    }
    assert stderr == ["Task accepted; manager readiness is degraded"]


def test_unexpected_post_acceptance_failure_names_committed_tid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Programmer defects stay typed and identify already accepted work."""
    context = build_context(prepare_project_root(tmp_path))
    tid = "1780000000000000998"
    defect = RuntimeError("programmer defect")
    monkeypatch.setattr(
        run_cmd,
        "_ensure_manager_after_submission",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(defect),
    )

    with pytest.raises(RuntimeError, match=f"accepted_tid={tid}") as exc_info:
        run_cmd._run_with_managed_execution(
            context=context,
            submit=lambda: (int(tid), _make_taskspec(tid).model_dump(mode="json")),
            verbose=False,
        )

    assert exc_info.value is defect


def test_run_binding_failure_keeps_committed_tid_after_connection_and_recovery(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run must record acceptance before resolving its submitted configuration."""
    context = weft_harness.context
    taskspec = TaskSpec.model_validate(
        {
            "name": "run-binding-fault",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
        },
        context={"template": True, "auto_expand": False},
    )
    active_operations = 0
    connection_released = False
    recovered_tid: str | None = None
    handed_off: list[str] = []
    original_connection = Queue.get_connection
    original_decode = submission_cmd.decode_taskspec_transport_payload
    fault = RuntimeError("run accepted configuration binding failed")

    @contextmanager
    def tracked_connection(queue: Queue) -> Iterator[Any]:
        nonlocal active_operations, connection_released
        if queue.name != WEFT_SPAWN_REQUESTS_QUEUE:
            with original_connection(queue) as broker:
                yield broker
            return
        active_operations += 1
        try:
            with original_connection(queue) as broker:
                yield broker
        finally:
            active_operations -= 1
            connection_released = True

    def ensure(
        _context: WeftContext, *, submitted_tid: str | int
    ) -> core_manager_runtime.ManagerEnsureResult:
        nonlocal recovered_tid
        assert connection_released and active_operations == 0
        recovered_tid = str(submitted_tid)
        return core_manager_runtime.ManagerEnsureResult(
            outcome="not_needed",
            manager_record=None,
            started_here=False,
            process_handle=None,
            reason="run_custody_test",
        )

    def fail_binding(payload: Mapping[str, Any], **kwargs: Any) -> TaskSpec:
        if kwargs.get("resolved_tid") is not None:
            assert recovered_tid == kwargs["resolved_tid"]
            assert connection_released and active_operations == 0
            raise fault
        return original_decode(payload, **kwargs)

    def on_submitted(tid: str, _context: WeftContext, _taskspec: TaskSpec) -> None:
        handed_off.append(tid)

    monkeypatch.setattr(Queue, "get_connection", tracked_connection)
    monkeypatch.setattr(run_cmd, "_ensure_manager_after_submission", ensure)
    # Only the post-write resolved-TID decode fails. Template shaping and the
    # actual broker write still execute, so moving binding into enqueue loses
    # recovery/acceptance and omitting binding cannot pass this test.
    monkeypatch.setattr(
        submission_cmd, "decode_taskspec_transport_payload", fail_binding
    )
    with pytest.raises(RuntimeError, match="accepted_tid=") as raised:
        run_cmd._run_with_managed_execution(
            context=context,
            submit=lambda: _enqueue_taskspec(context, taskspec, None),
            verbose=False,
            on_submitted=on_submitted,
        )
    assert raised.value is fault
    assert recovered_tid is not None
    assert f"accepted_tid={recovered_tid}" in str(fault)
    assert any(recovered_tid in note for note in fault.__notes__)
    assert handed_off == []
    assert active_operations == 0
    with context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=True) as queue:
        pending = queue.peek_one(exact_timestamp=int(recovered_tid))
    assert isinstance(pending, str)
    committed_spec = json.loads(pending)["taskspec"]
    assert committed_spec["name"] == "run-binding-fault"
    assert committed_spec["tid"] is None


@pytest.mark.parametrize(
    "category",
    (
        "Worker exited before returning a result (exit code 73)",
        "Worker result channel failed before a result was received",
        "Task returned a value that Weft could not serialize: local callable",
    ),
)
def test_run_renderer_preserves_terminal_handoff_categories(
    category: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plain and JSON rendering expose categories without private diagnostics."""

    execution = RunExecutionResult(
        tid="1780000000000000000",
        status="failed",
        error_message=category,
    )
    plain: list[tuple[str, bool]] = []
    json_lines: list[tuple[str, bool]] = []

    monkeypatch.setattr(
        "weft.cli.run.typer.echo",
        lambda message="", err=False, **_kwargs: plain.append((message, err)),
    )
    plain_exit = render_run_result(
        execution,
        wait=True,
        json_output=False,
        verbose=False,
    )
    monkeypatch.setattr(
        "weft.cli.run.typer.echo",
        lambda message="", err=False, **_kwargs: json_lines.append((message, err)),
    )
    json_exit = render_run_result(
        execution,
        wait=True,
        json_output=True,
        verbose=False,
    )

    assert plain_exit == 1
    assert plain == [(f"Error executing task: {category}", True)]
    assert json_exit == 1
    assert len(json_lines) == 1
    payload = json.loads(json_lines[0][0])
    assert set(payload) == {"tid", "status", "error"}
    assert payload == {
        "tid": "1780000000000000000",
        "status": "failed",
        "error": category,
    }
    assert json_lines[0][1] is False


def test_run_renderer_keeps_timeout_exit_124(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Terminal handoff rendering does not alter the timeout exit contract."""

    emitted: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        "weft.cli.run.typer.echo",
        lambda message="", err=False, **_kwargs: emitted.append((message, err)),
    )
    exit_code = render_run_result(
        RunExecutionResult(
            tid="1780000000000000001",
            status="timeout",
            error_message="Target execution timed out",
        ),
        wait=True,
        json_output=False,
        verbose=False,
    )

    assert exit_code == 124
    assert emitted == [("Error executing task: Target execution timed out", True)]


def test_manager_started_payload_formats_registry_message_id() -> None:
    message_id = 1_779_400_000_000_000_001
    record = {
        "tid": "1779400000000000100",
        "timestamp": message_id,
        "requests": "weft.spawn.requests",
    }

    payload = run_cmd._manager_started_payload(record)

    assert payload["timestamp"] == "1779400000000000001"
    assert record["timestamp"] == message_id


def _host_runtime_handle(pid: int) -> dict[str, Any]:
    return {
        "runner": "host",
        "kind": "process",
        "id": str(pid),
        "control": {"authority": "host-pid"},
        "observations": {
            "host_processes": [{"pid": pid, "create_time": process_create_time(pid)}]
        },
        "metadata": {},
    }


def _mock_live_host_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give synthetic launcher PIDs exact matching process evidence."""
    monkeypatch.setattr(
        sys.modules[__name__], "process_create_time", lambda pid: 1234.0
    )

    def observe(pid: int, create_time: float | None) -> HostProcessObservation:
        assert pid > 0
        assert create_time == 1234.0
        return HostProcessObservation("live", "identity_match")

    monkeypatch.setattr(core_manager_runtime, "inspect_host_process", observe)


def _external_supervisor_runtime_handle() -> dict[str, Any]:
    return {
        "runner": "manager-supervisor",
        "kind": "supervised-process",
        "id": "docker:container123",
        "control": {"authority": "external-supervisor"},
        "observations": {
            "container_runtime": "docker",
            "container_pid": 1,
            "container_id": "container123",
        },
        "metadata": {},
    }


def _write_active_manager_registry_record(
    ctx: WeftContext,
    *,
    tid: str,
    runtime_handle: dict[str, Any],
    requests: str = WEFT_SPAWN_REQUESTS_QUEUE,
    timestamp: int | None = None,
) -> None:
    registry = ctx.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
    try:
        message = json.dumps(
            _manager_service_payload(
                ctx,
                tid=tid,
                status="active",
                runtime_handle=runtime_handle or {},
                requests=requests,
            )
        )
        if timestamp is None:
            registry.write(message)
        else:
            registry.insert_messages([(message, timestamp)])
    finally:
        registry.close()


def _manager_service_payload(
    ctx: WeftContext,
    *,
    tid: str,
    status: Literal["active", "draining", "stopped", "superseded"] = "active",
    name: str = "manager",
    runtime_handle: dict[str, Any] | None = None,
    requests: str = WEFT_SPAWN_REQUESTS_QUEUE,
    ctrl_in: str | None = None,
    ctrl_out: str | None = None,
    outbox: str = WEFT_MANAGER_OUTBOX_QUEUE,
    service_key: str | None = None,
) -> dict[str, Any]:
    payload = build_manager_service_payload(
        context=ctx,
        tid=tid,
        name=name,
        status=status,
        queues={
            "requests": requests,
            "ctrl_in": ctrl_in or f"T{tid}.ctrl_in",
            "ctrl_out": ctrl_out or f"T{tid}.ctrl_out",
            "outbox": outbox,
        },
        runtime_handle=runtime_handle or {},
    )
    if service_key is not None:
        payload["service_key"] = service_key
    return payload


def _select_active_manager_while_answering_probe(
    ctx: WeftContext,
    *,
    tid: str,
    monkeypatch: pytest.MonkeyPatch,
    pong_fields: dict[str, Any] | None = None,
    probed_tids: list[str] | None = None,
) -> dict[str, Any] | None:
    monkeypatch.setattr(
        core_manager_runtime,
        "runtime_liveness_from_registered_probe",
        lambda handle: "unknown",
    )
    target_tid = tid

    def _fake_probe(
        _ctx: WeftContext,
        *,
        tid: str,
        ctrl_in_name: str,
        timeout: float,
        request_id: str | None = None,
        broker: Any | None = None,
    ) -> ControlProbeResult:
        if probed_tids is not None:
            probed_tids.append(tid)
        del _ctx, timeout, broker
        probe_request_id = request_id or "test-probe"
        if tid != target_tid:
            return ControlProbeResult(request_id=probe_request_id, timed_out=True)
        pong_payload = {
            "command": "PING",
            "status": "ok",
            "message": "PONG",
            "tid": tid,
            "request_id": probe_request_id,
            "task_status": "running",
            "role": "manager",
            "requests": WEFT_SPAWN_REQUESTS_QUEUE,
            "ctrl_in": ctrl_in_name,
            "ctrl_out": f"T{tid}.ctrl_out",
            "outbox": WEFT_MANAGER_OUTBOX_QUEUE,
            "weft_context": str(ctx.root),
            "should_stop": False,
        }
        if pong_fields is not None:
            pong_payload.update(pong_fields)
        return ControlProbeResult(
            request_id=probe_request_id,
            matched=MatchedPong(
                payload=pong_payload,
                observed_at=time.time_ns(),
                request_id=probe_request_id,
            ),
        )

    monkeypatch.setattr(core_manager_runtime, "send_keyed_ping_probe", _fake_probe)

    return core_manager_runtime.select_active_manager(ctx, probe_stale=True)


def _read_all_queue_messages(
    ctx: WeftContext, queue_name: str, *, persistent: bool = False
) -> list[str]:
    queue = ctx.queue(queue_name, persistent=persistent)
    messages: list[str] = []
    try:
        while True:
            raw = queue.read_one()
            if raw is None:
                return messages
            messages.append(str(raw))
    finally:
        queue.close()


def _make_taskspec(tid: str) -> TaskSpec:
    return TaskSpec(
        tid=tid,
        name="wait-task",
        spec=SpecSection(
            type="function",
            function_target="tests.tasks.sample_targets:echo_payload",
        ),
        io=IOSection(
            inputs={"inbox": f"T{tid}.inbox"},
            outputs={"outbox": f"T{tid}.outbox"},
            control={
                "ctrl_in": f"T{tid}.ctrl_in",
                "ctrl_out": f"T{tid}.ctrl_out",
            },
        ),
        state=StateSection(),
        metadata={},
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _wait_for_task_snapshot(
    root: Path,
    tid: str,
    *,
    timeout: float = 10.0,
) -> task_cmd.system_cmd.TaskSnapshot:
    deadline = time.time() + timeout
    last_snapshot: task_cmd.system_cmd.TaskSnapshot | None = None
    while time.time() < deadline:
        snapshot = task_cmd.task_status(tid, context_path=root)
        if snapshot is not None:
            last_snapshot = snapshot
            return snapshot
        time.sleep(0.05)
    raise AssertionError(
        f"Timed out waiting for task snapshot for {tid}: "
        f"{last_snapshot.to_dict() if last_snapshot is not None else None}"
    )


def _wait_for_task_status(
    root: Path,
    tid: str,
    *,
    expected_status: str,
    timeout: float = 30.0,
) -> task_cmd.system_cmd.TaskSnapshot:
    deadline = time.time() + timeout
    last_snapshot: task_cmd.system_cmd.TaskSnapshot | None = None
    while time.time() < deadline:
        snapshot = task_cmd.task_status(tid, context_path=root)
        if snapshot is not None:
            last_snapshot = snapshot
            if snapshot.status == expected_status:
                return snapshot
        time.sleep(0.05)
    raise AssertionError(
        f"Timed out waiting for {expected_status} snapshot for {tid}: "
        f"{last_snapshot.to_dict() if last_snapshot is not None else None}"
    )


def _seed_terminal_monitor_store_record(
    context: WeftContext,
    *,
    tid: str,
    status: str = "failed",
    event: str = "work_failed",
    error: str | None = "retired failure",
    return_code: int | None = 1,
) -> None:
    store = open_monitor_store(context)
    store.ensure_schema()
    observed_at = int(tid) + 279
    started_at = int(tid) - 500
    store.upsert_task_event(
        MonitorTaskEventUpdate(
            tid=tid,
            queue_name=WEFT_GLOBAL_LOG_QUEUE,
            message_id=observed_at,
            event=event,
            status=status,
            observed_at_ns=observed_at,
            name="retired-terminal-task",
            runner="host",
            terminal_seen=True,
            terminal_event=event,
            terminal_status=status,
            return_code=return_code,
            first_seen_at_ns=started_at,
            last_seen_at_ns=observed_at,
            started_at_ns=started_at,
            completed_at_ns=observed_at,
            taskspec_summary={
                "tid": tid,
                "name": "retired-terminal-task",
                "metadata": {"kind": "terminal-fallback"},
            },
            state={"status": status, "error": error, "return_code": return_code},
            lifecycle={"event": event, "status": status},
            resources={},
            diagnostics={},
            bookkeeping={},
        )
    )


@pytest.mark.parametrize("surface", ["terminal", "status"])
def test_retired_monitor_record_does_not_restore_ordinary_observation(
    tmp_path: Path,
    surface: str,
) -> None:
    """Private telemetry cannot provide a public lifecycle after queue evidence expires."""
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    tid = "1779226615233821720"
    _seed_terminal_monitor_store_record(context, tid=tid)
    store = open_monitor_store(context)
    retained_before = store.get_task(tid)
    assert retained_before is not None and retained_before.terminal_seen
    if surface == "terminal":
        snapshot = task_cmd.task_terminal_snapshot(tid, context=context)
        assert snapshot.status == "unknown" and not snapshot.terminal
    else:
        current = task_cmd.task_status(tid, context_path=root)
        assert current is not None and current.status == "unknown"
        assert current.reconciliation == {
            "classification": "observation_metadata_unavailable"
        }
    assert store.get_task(tid) == retained_before


@pytest.mark.parametrize("status", ["running", "completed"])
def test_task_status_uses_task_owned_snapshot_without_raw_history(
    tmp_path: Path,
    status: str,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    tid = "1779226615233822720"
    event = "work_completed" if status == "completed" else "task_started"
    write_task_observation(
        context,
        {
            "tid": tid,
            "event": event,
            "status": status,
            "taskspec": {
                "tid": tid,
                "name": "current-task",
                "metadata": {"kind": "current"},
                "state": {"status": status},
            },
        },
    )
    snapshot = task_cmd.task_status(tid, context_path=root)
    assert snapshot is not None
    assert snapshot.name == "current-task" and snapshot.status == status
    assert snapshot.event == event and snapshot.metadata == {"kind": "current"}


def _stop_active_manager(context: WeftContext) -> None:
    record = core_manager_runtime.select_active_manager(context)
    if record is None:
        return
    stopped, error = core_manager_runtime.stop_manager(
        context,
        record,
        timeout=30.0,
        force=True,
        stop_if_absent=True,
    )
    assert stopped, error


def _make_post_success_ack_failure(
    *,
    submitted_tid: dict[str, str],
) -> Callable[[Any], None]:
    """Return an ack seam that sends launcher SUCCESS, then raises.

    The detached manager launcher treats missing SUCCESS as an abort request.
    These tests exercise failure after the success signal, so the seam must
    preserve that ordering instead of replacing the whole acknowledgement
    protocol.
    """

    def _fail_after_launcher_success(launch: Any) -> None:
        deadline = time.time() + 8.0
        while time.time() < deadline:
            tid = submitted_tid.get("value")
            if tid:
                sent, error = core_manager_runtime._send_launcher_signal(
                    launch.launcher_process,
                    MANAGER_LAUNCHER_SIGNAL_SUCCESS,
                )
                core_manager_runtime._communicate_launcher(
                    launch.launcher_process,
                    timeout=2.0,
                )
                if not sent:
                    raise AssertionError(
                        f"test setup failed to send launcher SUCCESS: {error}"
                    )
                raise core_manager_runtime._ManagerLaunchAcknowledgementError(
                    "synthetic late acknowledgement failure",
                    success_signal_sent=True,
                )
            time.sleep(0.05)
        raise RuntimeError("timed out waiting for submitted task id")

    return _fail_after_launcher_success


def _registry_view(
    *,
    active: dict[str, Any] | None = None,
    target: dict[str, Any] | None = None,
    records: dict[str, dict[str, Any]] | None = None,
) -> core_manager_runtime.ManagerRegistryView:
    return core_manager_runtime.ManagerRegistryView(
        records={} if records is None else records,
        active_manager=active,
        target_record=target,
    )


class _FakeQueueChangeMonitor:
    def __init__(self, queues: Sequence[Queue], *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self.queue_names = [queue.name for queue in queues]
        self.wait_calls: list[float | None] = []

    def wait(self, timeout: float | None) -> bool:
        self.wait_calls.append(timeout)
        return False

    def close(self) -> None:
        return


class _FakeManagerSpec:
    """Serialization-only double; casts are confined to launcher-boundary tests."""

    def model_dump_json(self) -> str:
        return "{}"


def test_build_manager_process_command_preserves_bundle_transport_provenance(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    taskspec = _make_taskspec("1777000000000000123")
    bundle_root = tmp_path / "manager-bundle"
    bundle_root.mkdir()
    taskspec.set_bundle_root(bundle_root)
    invocation = core_manager_runtime.ManagerRuntimeInvocation(
        task_cls_path="weft.core.manager.Manager",
        tid=str(taskspec.tid),
        spec=taskspec,
    )

    command = core_manager_runtime._build_manager_process_command(
        context,
        invocation,
    )

    payload = json.loads(base64.b64decode(command[5]).decode("utf-8"))
    assert payload[TASKSPEC_BUNDLE_ROOT_FIELD] == str(bundle_root.resolve())
    decoded = decode_taskspec_transport_payload(payload)
    assert decoded.get_bundle_root() == str(bundle_root.resolve())
    assert TASKSPEC_BUNDLE_ROOT_FIELD not in taskspec.model_dump(mode="json")
    config_payload = json.loads(base64.b64decode(command[6]).decode("utf-8"))
    assert config_payload == {"prefix": "WEFT", "values": dict(context.config)}
    assert command[7] == TASK_POLL_INTERVAL_NONE_TOKEN


class _FakePopen:
    """Deterministic launcher double; nominal Popen casts stay at injection sites."""

    def __init__(
        self,
        *,
        pid: int = 4242,
        poll_results: Sequence[int | None] | None = None,
        communicate_result: tuple[str, str] = ("", ""),
    ) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self._poll_calls = 0
        self._poll_results = list(poll_results or [None, 0])
        self._communicate_result = communicate_result
        self.stdin = None

    def poll(self) -> int | None:
        if self._poll_calls < len(self._poll_results):
            result = self._poll_results[self._poll_calls]
        else:
            result = self._poll_results[-1]
        self._poll_calls += 1
        self.returncode = result
        return result

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        del timeout
        if self.returncode is None:
            self.returncode = 0
        return self._communicate_result


def test_start_manager_does_not_terminate_competing_startup_manager(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    fake_process = _FakePopen(poll_results=[1])
    competing_record = {"tid": "1775619800000000000", "pid": 31337}
    launch = core_manager_runtime.DetachedManagerLaunch(
        pid=4242,
        stderr_path=root / ".weft" / "logs" / "manager-startup" / "manager.stderr",
        launcher_process=cast(subprocess.Popen[str], fake_process),
    )

    monkeypatch.setattr(
        "weft.core.manager_runtime._build_manager_runtime_invocation",
        lambda context: core_manager_runtime.ManagerRuntimeInvocation(
            task_cls_path="weft.core.manager.Manager",
            tid="9" * 19,
            spec=cast(TaskSpec, _FakeManagerSpec()),
        ),
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime._launch_detached_manager",
        lambda context, invocation: launch,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.QueueChangeMonitor",
        _FakeQueueChangeMonitor,
    )

    records = iter([competing_record, competing_record])
    monkeypatch.setattr(
        "weft.core.manager_runtime._registry_view",
        lambda context, *, target_tid=None, prune_stale=True, probe_stale=False, probe_cache=None, queue=None: (
            _registry_view(
                active=next(records),
            )
        ),
    )

    terminated = False

    def _unexpected_terminate(*args: Any, **kwargs: Any) -> None:
        nonlocal terminated
        terminated = True

    monkeypatch.setattr(
        "weft.core.manager_runtime._terminate_manager_process",
        _unexpected_terminate,
    )

    record, started_here, handle = core_manager_runtime.start_manager(ctx)

    assert record == competing_record
    assert started_here is False
    assert handle is None
    assert terminated is False


@pytest.mark.parametrize("launched_status", ["active", "draining"])
def test_start_manager_detaches_registered_startup_manager_after_losing_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    launched_status: str,
) -> None:
    _mock_live_host_identity(monkeypatch)
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    fake_process = _FakePopen(poll_results=[None, None])
    invocation = core_manager_runtime.ManagerRuntimeInvocation(
        task_cls_path="weft.core.manager.Manager",
        tid="9" * 19,
        spec=cast(TaskSpec, _FakeManagerSpec()),
    )
    competing_record = {
        "tid": "1775619800000000000",
        "status": "active",
        "runtime_handle": _host_runtime_handle(31337),
        "requests": WEFT_SPAWN_REQUESTS_QUEUE,
        "role": "manager",
    }
    launched_record = {
        "tid": invocation.tid,
        "status": launched_status,
        "runtime_handle": _host_runtime_handle(fake_process.pid),
        "requests": WEFT_SPAWN_REQUESTS_QUEUE,
        "role": "manager",
    }
    launch = core_manager_runtime.DetachedManagerLaunch(
        pid=fake_process.pid,
        stderr_path=root / ".weft" / "logs" / "manager-startup" / "manager.stderr",
        launcher_process=cast(subprocess.Popen[str], fake_process),
    )
    acknowledged: list[str] = []
    launcher_signals: list[str] = []

    monkeypatch.setattr(
        "weft.core.manager_runtime._build_manager_runtime_invocation",
        lambda context: invocation,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime._launch_detached_manager",
        lambda context, invocation_arg: launch,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.QueueChangeMonitor",
        _FakeQueueChangeMonitor,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime._registry_view",
        lambda context, *, target_tid=None, prune_stale=True, probe_stale=False, probe_cache=None, queue=None: (
            _registry_view(active=competing_record, target=launched_record)
        ),
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.pid_is_live",
        lambda pid: True,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime._acknowledge_competing_launched_manager",
        lambda launch_arg, *, manager_tid: acknowledged.append(manager_tid),
    )

    def _record_launcher_signals(
        process: object, signal_name: str
    ) -> tuple[bool, str | None]:
        launcher_signals.append(signal_name)
        return (True, None)

    monkeypatch.setattr(
        "weft.core.manager_runtime._send_launcher_signal",
        _record_launcher_signals,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime._cleanup_startup_stderr",
        lambda path: None,
    )

    record, started_here, handle = core_manager_runtime.start_manager(ctx)

    assert record == competing_record
    assert started_here is False
    assert handle is None
    assert acknowledged == [invocation.tid]
    assert launcher_signals == []


def test_start_manager_adopts_competing_manager_after_losing_pid_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    fake_process = _FakePopen(poll_results=[None, None, None])
    competing_record = {
        "tid": "1775619800000000000",
        "pid": 31337,
        "status": "active",
        "requests": WEFT_SPAWN_REQUESTS_QUEUE,
        "role": "manager",
    }
    launch = core_manager_runtime.DetachedManagerLaunch(
        pid=4242,
        stderr_path=root / ".weft" / "logs" / "manager-startup" / "manager.stderr",
        launcher_process=cast(subprocess.Popen[str], fake_process),
    )
    cleaned_paths: list[Path] = []

    monkeypatch.setattr(
        "weft.core.manager_runtime._build_manager_runtime_invocation",
        lambda context: core_manager_runtime.ManagerRuntimeInvocation(
            task_cls_path="weft.core.manager.Manager",
            tid="9" * 19,
            spec=cast(TaskSpec, _FakeManagerSpec()),
        ),
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime._launch_detached_manager",
        lambda context, invocation: launch,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.QueueChangeMonitor",
        _FakeQueueChangeMonitor,
    )

    monkeypatch.setattr(
        "weft.core.manager_runtime._registry_view",
        lambda context, *, target_tid=None, prune_stale=True, probe_stale=False, probe_cache=None, queue=None: (
            _registry_view()
        ),
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime._await_manager_start_settlement",
        lambda context, *, manager_tid, deadline, session_owned=False: competing_record,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.pid_is_live",
        lambda pid: False,
    )
    monotonic_values = iter([0.0, 0.0, 0.1, 0.2])
    monkeypatch.setattr(
        "weft.core.manager_runtime.time.monotonic",
        lambda: next(monotonic_values, 1.0),
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.time.sleep",
        lambda seconds: None,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime._cleanup_startup_stderr",
        lambda path: cleaned_paths.append(path),
    )

    record, started_here, handle = core_manager_runtime.start_manager(ctx)

    assert record == competing_record
    assert started_here is False
    assert handle is None
    assert cleaned_paths == [launch.stderr_path]


def test_start_manager_adopts_competing_manager_after_startup_timeout_settles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    fake_process = _FakePopen(poll_results=[None, None, None])
    competing_record = {
        "tid": "1775619800000000000",
        "pid": 31337,
        "status": "active",
        "requests": WEFT_SPAWN_REQUESTS_QUEUE,
        "role": "manager",
    }
    launch = core_manager_runtime.DetachedManagerLaunch(
        pid=4242,
        stderr_path=root / ".weft" / "logs" / "manager-startup" / "manager.stderr",
        launcher_process=cast(subprocess.Popen[str], fake_process),
    )
    cleaned_paths: list[Path] = []
    settlement_calls: list[str] = []

    monkeypatch.setattr(
        "weft.core.manager_runtime._build_manager_runtime_invocation",
        lambda context: core_manager_runtime.ManagerRuntimeInvocation(
            task_cls_path="weft.core.manager.Manager",
            tid="9" * 19,
            spec=cast(TaskSpec, _FakeManagerSpec()),
        ),
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime._launch_detached_manager",
        lambda context, invocation: launch,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.QueueChangeMonitor",
        _FakeQueueChangeMonitor,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime._registry_view",
        lambda context, *, target_tid=None, prune_stale=True, probe_stale=False, probe_cache=None, queue=None: (
            _registry_view()
        ),
    )

    def _settle(
        context: WeftContext, *, manager_tid: str, deadline: float
    ) -> dict[str, object]:
        del context, deadline
        settlement_calls.append(manager_tid)
        return competing_record

    monkeypatch.setattr(
        "weft.core.manager_runtime._await_manager_start_settlement",
        _settle,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.pid_is_live",
        lambda pid: True,
    )
    monotonic_values = iter([0.0, 0.0, 11.0, 11.0])
    monkeypatch.setattr(
        "weft.core.manager_runtime.time.monotonic",
        lambda: next(monotonic_values, 11.0),
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.time.sleep",
        lambda seconds: None,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime._cleanup_startup_stderr",
        lambda path: cleaned_paths.append(path),
    )

    record, started_here, handle = core_manager_runtime.start_manager(ctx)

    assert record == competing_record
    assert started_here is False
    assert handle is None
    assert settlement_calls == ["9" * 19]
    assert cleaned_paths == [launch.stderr_path]


def test_start_manager_builds_detached_launch_from_shared_runtime_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_live_host_identity(monkeypatch)
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    fake_process = _FakePopen(poll_results=[None, None])
    invocation = core_manager_runtime.ManagerRuntimeInvocation(
        task_cls_path="weft.core.manager.Manager",
        tid="9" * 19,
        spec=cast(TaskSpec, _FakeManagerSpec()),
    )
    helper_calls: list[tuple[object, object]] = []
    launch_calls: list[tuple[object, object]] = []
    acked: list[object] = []
    launch = core_manager_runtime.DetachedManagerLaunch(
        pid=fake_process.pid,
        stderr_path=root / ".weft" / "logs" / "manager-startup" / "manager.stderr",
        launcher_process=cast(subprocess.Popen[str], fake_process),
    )

    def _fake_build_invocation(
        context_arg: WeftContext, *, idle_timeout_override: float | None = None
    ) -> core_manager_runtime.ManagerRuntimeInvocation:
        helper_calls.append((context_arg, idle_timeout_override))
        return invocation

    def _fake_launch(
        context_arg: WeftContext,
        invocation_arg: core_manager_runtime.ManagerRuntimeInvocation,
    ) -> core_manager_runtime.DetachedManagerLaunch:
        launch_calls.append((context_arg, invocation_arg))
        return launch

    monkeypatch.setattr(
        "weft.core.manager_runtime._build_manager_runtime_invocation",
        _fake_build_invocation,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime._launch_detached_manager",
        _fake_launch,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.QueueChangeMonitor",
        _FakeQueueChangeMonitor,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime._acknowledge_manager_launch_success",
        lambda launch_arg: acked.append(launch_arg),
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime._registry_view",
        lambda context, *, target_tid=None, prune_stale=True, probe_stale=False, probe_cache=None, queue=None: (
            _registry_view(
                active={
                    "tid": invocation.tid,
                    "runtime_handle": _host_runtime_handle(fake_process.pid),
                    "status": "active",
                    "requests": WEFT_SPAWN_REQUESTS_QUEUE,
                    "role": "manager",
                }
            )
        ),
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.pid_is_live",
        lambda pid: True,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime._cleanup_startup_stderr",
        lambda path: None,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.time.sleep",
        lambda seconds: None,
    )

    record, started_here, handle = core_manager_runtime.start_manager(ctx)

    assert record["tid"] == invocation.tid
    assert record["runtime_handle"] == _host_runtime_handle(fake_process.pid)
    assert started_here is True
    assert handle is None
    assert helper_calls == [(ctx, None)]
    assert launch_calls == [(ctx, invocation)]
    assert acked == [launch]


def test_start_manager_treats_post_proof_ack_failure_as_nonfatal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_live_host_identity(monkeypatch)
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    fake_process = _FakePopen(poll_results=[None, None, 0])
    invocation = core_manager_runtime.ManagerRuntimeInvocation(
        task_cls_path="weft.core.manager.Manager",
        tid="9" * 19,
        spec=cast(TaskSpec, _FakeManagerSpec()),
    )
    launch = core_manager_runtime.DetachedManagerLaunch(
        pid=fake_process.pid,
        stderr_path=root / ".weft" / "logs" / "manager-startup" / "manager.stderr",
        launcher_process=cast(subprocess.Popen[str], fake_process),
    )
    warnings: list[str] = []
    debug_messages: list[str] = []

    monkeypatch.setattr(
        "weft.core.manager_runtime._build_manager_runtime_invocation",
        lambda context: invocation,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime._launch_detached_manager",
        lambda context, invocation_arg: launch,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.QueueChangeMonitor",
        _FakeQueueChangeMonitor,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime._acknowledge_manager_launch_success",
        lambda launch_arg: (_ for _ in ()).throw(
            core_manager_runtime._ManagerLaunchAcknowledgementError(
                "post-proof acknowledgement failed",
                success_signal_sent=True,
            )
        ),
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime._registry_view",
        lambda context, *, target_tid=None, prune_stale=True, probe_stale=False, probe_cache=None, queue=None: (
            _registry_view(
                active={
                    "tid": invocation.tid,
                    "runtime_handle": _host_runtime_handle(fake_process.pid),
                    "status": "active",
                    "requests": WEFT_SPAWN_REQUESTS_QUEUE,
                    "role": "manager",
                }
            )
        ),
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.pid_is_live",
        lambda pid: True,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.logger.warning",
        lambda message, *args, **kwargs: warnings.append(str(message)),
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.logger.debug",
        lambda message, *args, **kwargs: debug_messages.append(str(message)),
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.time.sleep",
        lambda seconds: None,
    )

    record, started_here, handle = core_manager_runtime.start_manager(ctx)

    assert record["tid"] == invocation.tid
    assert record["runtime_handle"] == _host_runtime_handle(fake_process.pid)
    assert started_here is True
    assert handle is None
    assert warnings == []
    assert debug_messages


def test_start_manager_rejects_ack_failure_before_success_signal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_live_host_identity(monkeypatch)
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    fake_process = _FakePopen(poll_results=[None, None, 0])
    invocation = core_manager_runtime.ManagerRuntimeInvocation(
        task_cls_path="weft.core.manager.Manager",
        tid="9" * 19,
        spec=cast(TaskSpec, _FakeManagerSpec()),
    )
    launch = core_manager_runtime.DetachedManagerLaunch(
        pid=fake_process.pid,
        stderr_path=root / ".weft" / "logs" / "manager-startup" / "manager.stderr",
        launcher_process=cast(subprocess.Popen[str], fake_process),
    )

    monkeypatch.setattr(
        "weft.core.manager_runtime._build_manager_runtime_invocation",
        lambda context: invocation,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime._launch_detached_manager",
        lambda context, invocation_arg: launch,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.QueueChangeMonitor",
        _FakeQueueChangeMonitor,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime._acknowledge_manager_launch_success",
        lambda launch_arg: (_ for _ in ()).throw(
            core_manager_runtime._ManagerLaunchAcknowledgementError(
                "success signal was not sent",
                success_signal_sent=False,
            )
        ),
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime._registry_view",
        lambda context, *, target_tid=None, prune_stale=True, probe_stale=False, probe_cache=None, queue=None: (
            _registry_view(
                active={
                    "tid": invocation.tid,
                    "runtime_handle": _host_runtime_handle(fake_process.pid),
                    "status": "active",
                    "requests": WEFT_SPAWN_REQUESTS_QUEUE,
                    "role": "manager",
                }
            )
        ),
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.pid_is_live",
        lambda pid: True,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.time.sleep",
        lambda seconds: None,
    )

    with pytest.raises(ManagerStartFailed, match="success signal was not sent"):
        core_manager_runtime.start_manager(ctx)


@pytest.mark.parametrize("liveness", ["live", "stale", "unknown"])
@pytest.mark.parametrize("age_seconds", [30, 120])
def test_manager_start_record_matches_external_supervisor_launch_pid(
    monkeypatch: pytest.MonkeyPatch,
    liveness: str,
    age_seconds: int,
) -> None:
    monkeypatch.setattr("weft.core.manager_runtime.pid_is_live", lambda pid: True)
    monkeypatch.setattr(
        core_manager_runtime, "MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS", 60.0
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "runtime_liveness_from_registered_probe",
        lambda handle: liveness,
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "_manager_record_has_matched_pong",
        lambda *args, **kwargs: pytest.fail("owned startup proof must not need PING"),
    )

    matches = core_manager_runtime._manager_start_record_matches_launch(
        {
            "tid": "1775622400000000000",
            "status": "active",
            "timestamp": time.time_ns() - age_seconds * 1_000_000_000,
            "runtime_handle": _external_supervisor_runtime_handle(),
            "requests": WEFT_SPAWN_REQUESTS_QUEUE,
            "role": "manager",
        },
        launch_pid=4321,
    )
    assert matches is (
        liveness == "live" or (liveness == "unknown" and age_seconds < 60)
    )


@pytest.mark.parametrize("record_seen", [False, True])
def test_start_manager_timeout_reports_existing_observation_without_new_probes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, record_seen: bool
) -> None:
    context = build_context(spec_context=prepare_project_root(tmp_path))
    tid = "9" * 19
    launch = core_manager_runtime.DetachedManagerLaunch(
        pid=4242,
        stderr_path=tmp_path / "startup.stderr",
        launcher_process=cast(subprocess.Popen[str], _FakePopen(poll_results=[None])),
    )
    target = {"tid": tid, "status": "active"} if record_seen else None
    view = core_manager_runtime.ManagerRegistryView(
        records={tid: target} if target is not None else {},
        active_manager=target,
        target_record=target,
    )
    probe_calls: list[int] = []
    monkeypatch.setattr(
        core_manager_runtime,
        "_build_manager_runtime_invocation",
        lambda _: core_manager_runtime.ManagerRuntimeInvocation(
            task_cls_path="weft.core.manager.Manager",
            tid=tid,
            spec=cast(TaskSpec, _FakeManagerSpec()),
        ),
    )
    monkeypatch.setattr(
        core_manager_runtime, "_launch_detached_manager", lambda *_: launch
    )
    monkeypatch.setattr(
        core_manager_runtime, "QueueChangeMonitor", _FakeQueueChangeMonitor
    )
    clock = [0.0]

    def observe_view(
        *_args: Any, **_kwargs: Any
    ) -> core_manager_runtime.ManagerRegistryView:
        clock[0] = 11.0
        return view

    monkeypatch.setattr(core_manager_runtime, "_registry_view", observe_view)
    monkeypatch.setattr(
        core_manager_runtime,
        "_manager_start_record_matches_launch",
        lambda *_a, **_k: False,
    )
    monkeypatch.setattr(
        core_manager_runtime, "_await_manager_start_settlement", lambda *_a, **_k: None
    )

    def _record_probe_calls(pid: int) -> bool:
        probe_calls.append(pid)
        return True

    monkeypatch.setattr(core_manager_runtime, "pid_is_live", _record_probe_calls)
    monkeypatch.setattr(core_manager_runtime.time, "monotonic", lambda: clock[0])

    def fail_start(
        *,
        launch: core_manager_runtime.DetachedManagerLaunch,
        message: str,
        abort_launcher: bool,
    ) -> None:
        assert abort_launcher is True
        raise ManagerStartFailed(message)

    monkeypatch.setattr(core_manager_runtime, "_fail_manager_start", fail_start)
    with pytest.raises(ManagerStartFailed) as caught:
        core_manager_runtime.start_manager(context)

    message = str(caught.value)
    assert "startup_phase=registry_readiness" in message
    observation = json.loads(message.split("last_observation=", 1)[1])
    assert observation["launch_pid"] == 4242
    assert observation["last_child_pid_live"] is True
    assert observation["last_target_record"] == target
    assert observation["last_selected_proof"] is (False if record_seen else None)
    assert probe_calls == [4242]


def test_start_manager_surfaces_detached_launch_stderr_when_manager_exits_early(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    stderr_path = root / ".weft" / "logs" / "manager-startup" / "manager.stderr"
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.write_text("boom on startup\ntraceback details\n", encoding="utf-8")
    fake_process = _FakePopen(
        poll_results=[10],
        communicate_result=(
            json.dumps({"event": "child_exit", "returncode": 23}) + "\n",
            "",
        ),
    )
    launch = core_manager_runtime.DetachedManagerLaunch(
        pid=4242,
        stderr_path=stderr_path,
        launcher_process=cast(subprocess.Popen[str], fake_process),
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime._build_manager_runtime_invocation",
        lambda context: core_manager_runtime.ManagerRuntimeInvocation(
            task_cls_path="weft.core.manager.Manager",
            tid="9" * 19,
            spec=cast(TaskSpec, _FakeManagerSpec()),
        ),
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime._launch_detached_manager",
        lambda context, invocation: launch,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.QueueChangeMonitor",
        _FakeQueueChangeMonitor,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime._registry_view",
        lambda context, *, target_tid=None, prune_stale=True, probe_stale=False, probe_cache=None, queue=None: (
            _registry_view()
        ),
    )

    with pytest.raises(ManagerStartFailed) as exc_info:
        core_manager_runtime.start_manager(ctx)

    message = str(exc_info.value)
    assert "return code 23" in message
    assert "traceback details" in message


def test_run_inline_enqueues_task_before_ensuring_manager(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    calls: list[str] = []

    monkeypatch.setattr(
        "weft.commands.run.build_context",
        lambda spec_context=None, autostart=True: context,
    )
    monkeypatch.setattr("weft.commands.run._echo", lambda *args, **kwargs: None)

    def _fake_enqueue(
        context_arg: WeftContext, taskspec: TaskSpec, work_payload: str | None
    ) -> tuple[int, dict[str, Any]]:
        calls.append("enqueue")
        return 1775679597297004544, taskspec.model_dump(mode="json")

    def _fake_ensure(
        context_arg: WeftContext, *, submitted_tid: str | int
    ) -> core_manager_runtime.ManagerEnsureResult:
        del context_arg, submitted_tid
        calls.append("ensure")
        return _ready_manager_result(
            {"tid": "1775679596841701376", "ctrl_in": "Tmanager.ctrl_in"}
        )

    monkeypatch.setattr("weft.commands.run._enqueue_taskspec", _fake_enqueue)
    monkeypatch.setattr(
        "weft.commands.run._ensure_manager_after_submission", _fake_ensure
    )

    _execute_inline(
        command=(),
        function_target="tests.tasks.sample_targets:echo_payload",
        args=(),
        kwargs=(),
        env=(),
        name=None,
        interactive=False,
        stream_output=None,
        timeout=None,
        memory=None,
        cpu=None,
        tags=(),
        context_dir=root,
        json_output=False,
        verbose=False,
        autostart_enabled=True,
    )

    assert calls == ["enqueue", "ensure"]


@pytest.mark.parametrize("function_target", [None, "missing-separator"])
def test_execute_inline_rejects_invalid_function_target_before_context(
    function_target: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        run_cmd,
        "build_context",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError(f"unexpected context construction: {kwargs}")
        ),
    )

    with pytest.raises(RunUsageError, match="module:callable"):
        run_cmd._execute_inline(
            command=(),
            function_target=function_target,
            args=(),
            kwargs=(),
            env=(),
            name=None,
            interactive=False,
            stream_output=None,
            timeout=None,
            memory=None,
            cpu=None,
            tags=(),
            context_dir=None,
            json_output=False,
            verbose=False,
            autostart_enabled=True,
        )


def test_execute_run_inline_returns_structured_result_without_rendering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)

    monkeypatch.setattr(
        "weft.commands.run.build_context",
        lambda spec_context=None, autostart=True: context,
    )
    monkeypatch.setattr(
        "weft.commands.run._echo",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("execute_run must not render")
        ),
    )
    monkeypatch.setattr(
        "weft.commands.run._enqueue_taskspec",
        lambda context_arg, taskspec, work_payload: (
            1775679597297004544,
            taskspec.model_dump(mode="json"),
        ),
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.ensure_manager",
        lambda context_arg: (
            {"tid": "1775679596841701376", "ctrl_in": "Tmanager.ctrl_in"},
            False,
            None,
        ),
    )

    execution = run_cmd.execute_run(
        (),
        spec_run_args=(),
        spec=None,
        pipeline=None,
        pipeline_input=None,
        function="tests.tasks.sample_targets:echo_payload",
        args=(),
        kwargs=(),
        env=(),
        name=None,
        interactive=False,
        stream_output=None,
        timeout=None,
        memory=None,
        cpu=None,
        tags=(),
        context_dir=root,
        json_output=False,
        verbose=False,
        persistent_override=None,
        autostart_enabled=True,
    )

    assert execution.tid == "1775679597297004544"


def test_execute_inline_contains_unexpected_command_boundary_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The command boundary reports an ordinary adapter error without traceback."""

    class UnexpectedSubmissionFailure(Exception):
        pass

    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    monkeypatch.setattr(run_cmd, "build_context", lambda **kwargs: context)
    monkeypatch.setattr(
        run_cmd,
        "_run_with_managed_execution",
        lambda **kwargs: (_ for _ in ()).throw(
            UnexpectedSubmissionFailure("adapter detail")
        ),
    )

    with pytest.raises(SubmissionError, match="Error submitting task: adapter detail"):
        run_cmd._execute_inline(
            command=(),
            function_target="tests.tasks.sample_targets:echo_payload",
            args=(),
            kwargs=(),
            env=(),
            name=None,
            interactive=False,
            stream_output=None,
            timeout=None,
            memory=None,
            cpu=None,
            tags=(),
            context_dir=root,
            json_output=False,
            verbose=False,
            autostart_enabled=True,
        )


def test_run_inline_no_wait_succeeds_when_post_proof_acknowledgement_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    submitted_tid: dict[str, str] = {}
    original_enqueue = _enqueue_taskspec

    def _recording_enqueue(*args: Any, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        tid, payload = original_enqueue(*args, **kwargs)
        submitted_tid["value"] = str(tid)
        return tid, payload

    monkeypatch.setattr(
        "weft.commands.run.build_context",
        lambda spec_context=None, autostart=True: context,
    )
    monkeypatch.setattr("weft.commands.run._enqueue_taskspec", _recording_enqueue)
    monkeypatch.setattr(
        "weft.core.manager_runtime._acknowledge_manager_launch_success",
        _make_post_success_ack_failure(submitted_tid=submitted_tid),
    )
    monkeypatch.setattr("weft.commands.run._echo", lambda *args, **kwargs: None)

    try:
        _execute_inline(
            command=(),
            function_target="tests.tasks.sample_targets:provide_payload",
            args=(),
            kwargs=(),
            env=(),
            name="provide-payload",
            interactive=False,
            stream_output=None,
            timeout=None,
            memory=None,
            cpu=None,
            tags=(),
            context_dir=root,
            json_output=False,
            verbose=False,
            autostart_enabled=True,
        )

        tid = submitted_tid["value"]
        snapshot = _wait_for_task_status(root, tid, expected_status="completed")
        assert snapshot.tid == tid
    finally:
        _stop_active_manager(context)


def test_run_inline_wait_succeeds_when_post_proof_acknowledgement_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    submitted_tid: dict[str, str] = {}
    accepted_taskspec: list[TaskSpec] = []
    original_enqueue = _enqueue_taskspec

    def _recording_enqueue(*args: Any, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        tid, payload = original_enqueue(*args, **kwargs)
        submitted_tid["value"] = str(tid)
        return tid, payload

    def _capture_submission(
        tid: str, _context: WeftContext, taskspec: TaskSpec
    ) -> None:
        assert taskspec.tid == tid
        accepted_taskspec.append(taskspec)

    monkeypatch.setattr(
        "weft.commands.run.build_context",
        lambda spec_context=None, autostart=True: context,
    )
    monkeypatch.setattr("weft.commands.run._enqueue_taskspec", _recording_enqueue)
    monkeypatch.setattr(
        "weft.core.manager_runtime._acknowledge_manager_launch_success",
        _make_post_success_ack_failure(submitted_tid=submitted_tid),
    )
    monkeypatch.setattr("weft.commands.run._echo", lambda *args, **kwargs: None)

    try:
        execution = _execute_inline(
            command=(),
            function_target="tests.tasks.sample_targets:provide_payload",
            args=(),
            kwargs=(),
            env=(),
            name="provide-payload",
            interactive=False,
            stream_output=None,
            timeout=None,
            memory=None,
            cpu=None,
            tags=(),
            context_dir=root,
            json_output=False,
            verbose=False,
            autostart_enabled=True,
            on_submitted=_capture_submission,
        )

        assert len(accepted_taskspec) == 1
        session = run_cmd._LiveRunSession(
            execution,
            context,
            _accepted_taskspec=accepted_taskspec[0],
        )
        try:
            assert (
                session.wait(timeout=DEFAULT_TASK_COMPLETION_TIMEOUT).status
                == "completed"
            )
        finally:
            session.close()
        tid = submitted_tid["value"]
        snapshot = _wait_for_task_status(root, tid, expected_status="completed")
        assert snapshot.tid == tid
    finally:
        _stop_active_manager(context)


def test_delete_spawn_request_removes_queued_message(tmp_path: Path) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    tid = str(time.time_ns())
    taskspec = _make_taskspec(tid)

    message_timestamp, _payload = _enqueue_taskspec(context, taskspec, None)

    deleted = _delete_spawn_request(context, message_timestamp)

    queue = context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=False)
    try:
        assert queue.read_one() is None
    finally:
        queue.close()
    assert deleted is True


def test_enqueue_template_uses_committed_submission_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    taskspec = TaskSpec.model_validate(
        {
            "name": "implicit-id",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
                "weft_context": str(root),
            },
            "metadata": {},
        },
        context={"template": True, "auto_expand": False},
    )
    committed_id = 1777000000000000456
    captured: dict[str, Any] = {}

    def fail_preallocation(_context: WeftContext) -> str:
        raise AssertionError("template enqueue must not preallocate a TID")

    def fake_submit(*args: object, **kwargs: object) -> tuple[int, dict[str, Any]]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return committed_id, taskspec.model_dump(mode="json")

    monkeypatch.setattr(core_manager_runtime, "generate_tid", fail_preallocation)
    monkeypatch.setattr(run_cmd, "_submit_spawn_request_with_payload", fake_submit)

    submitted_id, _payload = _enqueue_taskspec(context, taskspec, None)

    assert submitted_id == committed_id
    assert captured["kwargs"]["tid"] is None


def test_enqueue_taskspec_strips_reserved_internal_runtime_metadata_from_public_submission(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    tid = str(time.time_ns())
    taskspec = _make_taskspec(tid)
    taskspec.metadata[INTERNAL_RUNTIME_TASK_CLASS_KEY] = (
        INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT
    )
    taskspec.metadata[INTERNAL_RUNTIME_ENDPOINT_NAME_KEY] = (
        INTERNAL_HEARTBEAT_ENDPOINT_NAME
    )
    taskspec.metadata[INTERNAL_SERVICE_KEY_METADATA_KEY] = (
        INTERNAL_SERVICE_KEY_HEARTBEAT
    )
    taskspec.metadata[INTERNAL_SERVICE_LIFECYCLE_METADATA_KEY] = "ensure"
    taskspec.metadata[INTERNAL_SERVICE_AUTHORITY_METADATA_KEY] = (
        INTERNAL_SERVICE_AUTHORITY_MANAGER
    )
    taskspec.metadata[INTERNAL_AUTOSTART_SOURCE_METADATA_KEY] = str(root / "auto.json")
    taskspec.metadata[INTERNAL_AUTOSTART_ENABLED_METADATA_KEY] = True
    taskspec.metadata["role"] = "public-role"
    taskspec.metadata["internal"] = True

    _enqueue_taskspec(context, taskspec, None)

    queue = context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=False)
    try:
        raw_message = queue.read_one()
    finally:
        queue.close()

    assert isinstance(raw_message, str)
    payload = json.loads(raw_message)
    taskspec_payload = payload["taskspec"]
    assert INTERNAL_RUNTIME_TASK_CLASS_KEY not in taskspec_payload["metadata"]
    assert INTERNAL_RUNTIME_ENDPOINT_NAME_KEY not in taskspec_payload["metadata"]
    assert INTERNAL_SERVICE_KEY_METADATA_KEY not in taskspec_payload["metadata"]
    assert INTERNAL_SERVICE_LIFECYCLE_METADATA_KEY not in taskspec_payload["metadata"]
    assert INTERNAL_SERVICE_AUTHORITY_METADATA_KEY not in taskspec_payload["metadata"]
    assert INTERNAL_AUTOSTART_SOURCE_METADATA_KEY not in taskspec_payload["metadata"]
    assert INTERNAL_AUTOSTART_ENABLED_METADATA_KEY not in taskspec_payload["metadata"]
    assert taskspec_payload["metadata"]["role"] == "public-role"
    assert taskspec_payload["metadata"]["internal"] is True
    assert INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY not in payload
    assert INTERNAL_RUNTIME_ENVELOPE_ENDPOINT_NAME_KEY not in payload


def test_enqueue_taskspec_preserves_public_endpoint_name_on_public_submission(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    tid = str(time.time_ns())
    taskspec = _make_taskspec(tid)
    taskspec.metadata[INTERNAL_RUNTIME_ENDPOINT_NAME_KEY] = "mayor"

    _enqueue_taskspec(context, taskspec, None)

    queue = context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=False)
    try:
        raw_message = queue.read_one()
    finally:
        queue.close()

    assert isinstance(raw_message, str)
    payload = json.loads(raw_message)
    taskspec_payload = payload["taskspec"]
    assert taskspec_payload["metadata"][INTERNAL_RUNTIME_ENDPOINT_NAME_KEY] == "mayor"
    assert INTERNAL_RUNTIME_ENVELOPE_ENDPOINT_NAME_KEY not in payload


def test_delete_spawn_request_swallows_delete_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    with (
        context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=True) as queue,
        queue.get_connection() as broker,
    ):
        broker_type = type(broker)
    attempts: list[tuple[str, list[int]]] = []
    closed: list[str] = []
    original_close = Queue.close

    def fail_delete(db: Any, queue_name: str, message_ids: list[int]) -> int:
        attempts.append((queue_name, message_ids))
        raise RuntimeError("delete failed")

    def close(queue: Queue) -> None:
        original_close(queue)
        closed.append(queue.name)

    with monkeypatch.context() as patch:
        patch.setattr(broker_type, "delete_message_ids", fail_delete)
        patch.setattr(Queue, "close", close)
        deleted = _delete_spawn_request(context, 1775679597297004544)

    assert attempts == [(WEFT_SPAWN_REQUESTS_QUEUE, [1775679597297004544])]
    assert closed == [WEFT_SPAWN_REQUESTS_QUEUE]
    assert deleted is False


def test_reconcile_submitted_spawn_reports_queued_for_unclaimed_request(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    tid = str(time.time_ns())
    taskspec = _make_taskspec(tid)
    submitted_id, _payload = _enqueue_taskspec(context, taskspec, None)
    submitted_tid = str(submitted_id)

    result = reconcile_submitted_spawn(context, submitted_tid, timeout=0.0)

    assert result == SpawnSubmissionReconciliation(
        outcome="queued",
        tid=submitted_tid,
    )


def test_reconcile_submitted_spawn_reports_reserved_for_claimed_request(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    manager_tid = "1776000000000000001"
    registry = context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
    try:
        registry.write(
            json.dumps(
                _manager_service_payload(
                    context,
                    tid=manager_tid,
                    runtime_handle=_host_runtime_handle(424242),
                )
            )
        )
    finally:
        registry.close()

    tid = str(time.time_ns())
    taskspec = _make_taskspec(tid)
    submitted_id, _payload = _enqueue_taskspec(context, taskspec, None)
    submitted_tid = str(submitted_id)
    spawn_queue = context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=False)
    try:
        moved = spawn_queue.move_one(
            f"T{manager_tid}.reserved",
            exact_timestamp=int(submitted_tid),
            with_timestamps=False,
        )
    finally:
        spawn_queue.close()
    assert moved is not None

    result = reconcile_submitted_spawn(context, submitted_tid, timeout=0.0)

    assert result == SpawnSubmissionReconciliation(
        outcome="reserved",
        tid=submitted_tid,
        reserved_queue=f"T{manager_tid}.reserved",
    )


def test_reconcile_submitted_spawn_can_wait_past_reserved_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    submitted_tid = str(time.time_ns())
    created_monitors: list[_FakeQueueChangeMonitor] = []
    results = iter(
        [
            SpawnSubmissionReconciliation(
                outcome="reserved",
                tid=submitted_tid,
                reserved_queue="T1776000000000000001.reserved",
            ),
            SpawnSubmissionReconciliation(outcome="spawned", tid=submitted_tid),
        ]
    )

    def _fake_monitor(
        queues: Sequence[Queue], *, config: Config | None = None
    ) -> _FakeQueueChangeMonitor:
        monitor = _FakeQueueChangeMonitor(queues, config=config)
        created_monitors.append(monitor)
        return monitor

    monkeypatch.setattr(spawn_submission_cmd, "QueueChangeMonitor", _fake_monitor)
    monkeypatch.setattr(
        spawn_submission_cmd,
        "time",
        SimpleNamespace(monotonic=lambda: 0.0),
    )
    monkeypatch.setattr(
        spawn_submission_cmd,
        "_reconcile_submitted_spawn_once",
        lambda *_args, **_kwargs: next(results),
    )
    monkeypatch.setattr(
        spawn_submission_cmd,
        "_spawn_reconciliation_queue_specs",
        lambda _context, _tid, **_kwargs: (
            (task_state_queue_name(submitted_tid), False),
            (WEFT_GLOBAL_LOG_QUEUE, False),
            (WEFT_SPAWN_REQUESTS_QUEUE, False),
            (WEFT_SERVICES_REGISTRY_QUEUE, False),
            ("T1776000000000000001.reserved", False),
        ),
    )

    result = reconcile_submitted_spawn(
        context,
        submitted_tid,
        timeout=0.1,
        reserved_is_terminal=False,
    )

    assert result == SpawnSubmissionReconciliation(
        outcome="spawned",
        tid=submitted_tid,
    )
    assert len(created_monitors) == 1
    assert created_monitors[0].wait_calls


def test_reconcile_submitted_spawn_can_wait_past_queued_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recovery can keep watching while the accepted request stays queued."""
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    submitted_tid = str(time.time_ns())
    results = iter(
        [
            SpawnSubmissionReconciliation(outcome="queued", tid=submitted_tid),
            SpawnSubmissionReconciliation(outcome="spawned", tid=submitted_tid),
        ]
    )

    monkeypatch.setattr(
        spawn_submission_cmd,
        "QueueChangeMonitor",
        _FakeQueueChangeMonitor,
    )
    monkeypatch.setattr(
        spawn_submission_cmd,
        "time",
        SimpleNamespace(monotonic=lambda: 0.0),
    )
    monkeypatch.setattr(
        spawn_submission_cmd,
        "_reconcile_submitted_spawn_once",
        lambda *_args, **_kwargs: next(results),
    )
    monkeypatch.setattr(
        spawn_submission_cmd,
        "_spawn_reconciliation_queue_specs",
        lambda _context, _tid, **_kwargs: ((WEFT_SPAWN_REQUESTS_QUEUE, False),),
    )

    result = reconcile_submitted_spawn(
        context,
        submitted_tid,
        timeout=0.1,
        queued_is_terminal=False,
    )

    assert result.outcome == "spawned"


def test_reconcile_submitted_spawn_reports_rejected_from_manager_log(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    submitted_tid = str(time.time_ns())
    log_queue = context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    try:
        log_queue.write(
            json.dumps(
                {
                    "event": "task_spawn_rejected",
                    "tid": "1776000000000000002",
                    "status": "running",
                    "child_tid": submitted_tid,
                    "error": "manager rejected child task",
                }
            )
        )
    finally:
        log_queue.close()

    result = reconcile_submitted_spawn(context, submitted_tid, timeout=0.0)

    assert result == SpawnSubmissionReconciliation(
        outcome="rejected",
        tid=submitted_tid,
        error="manager rejected child task",
    )


def test_reconcile_submitted_spawn_uses_queue_monitor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    submitted_tid = str(time.time_ns())
    created_monitors: list[_FakeQueueChangeMonitor] = []
    results = iter(
        [
            SpawnSubmissionReconciliation(outcome="unknown", tid=submitted_tid),
            SpawnSubmissionReconciliation(outcome="queued", tid=submitted_tid),
        ]
    )

    def _fake_monitor(
        queues: Sequence[Queue], *, config: Config | None = None
    ) -> _FakeQueueChangeMonitor:
        monitor = _FakeQueueChangeMonitor(queues, config=config)
        created_monitors.append(monitor)
        return monitor

    monkeypatch.setattr(spawn_submission_cmd, "QueueChangeMonitor", _fake_monitor)
    monkeypatch.setattr(
        spawn_submission_cmd,
        "time",
        SimpleNamespace(monotonic=lambda: 0.0),
    )
    monkeypatch.setattr(
        spawn_submission_cmd,
        "_reconcile_submitted_spawn_once",
        lambda *_args, **_kwargs: next(results),
    )
    monkeypatch.setattr(
        spawn_submission_cmd,
        "_spawn_reconciliation_queue_specs",
        lambda _context, _tid, **_kwargs: (
            (task_state_queue_name(submitted_tid), False),
            (WEFT_GLOBAL_LOG_QUEUE, False),
            (WEFT_SPAWN_REQUESTS_QUEUE, False),
            (WEFT_SERVICES_REGISTRY_QUEUE, False),
        ),
    )

    result = reconcile_submitted_spawn(context, submitted_tid, timeout=0.1)

    assert result == SpawnSubmissionReconciliation(
        outcome="queued",
        tid=submitted_tid,
    )
    assert len(created_monitors) == 1
    assert created_monitors[0].queue_names == [
        task_state_queue_name(submitted_tid),
        WEFT_GLOBAL_LOG_QUEUE,
        WEFT_SPAWN_REQUESTS_QUEUE,
        WEFT_SERVICES_REGISTRY_QUEUE,
    ]
    assert created_monitors[0].wait_calls


def test_reconcile_submitted_spawn_rebuilds_monitor_when_reserved_queues_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    submitted_tid = str(time.time_ns())
    created_monitors: list[_FakeQueueChangeMonitor] = []
    results = iter(
        [
            SpawnSubmissionReconciliation(outcome="unknown", tid=submitted_tid),
            SpawnSubmissionReconciliation(outcome="unknown", tid=submitted_tid),
            SpawnSubmissionReconciliation(
                outcome="reserved",
                tid=submitted_tid,
                reserved_queue="T1776000000000000001.reserved",
            ),
        ]
    )
    queue_specs = iter(
        [
            (
                (task_state_queue_name(submitted_tid), False),
                (WEFT_GLOBAL_LOG_QUEUE, False),
                (WEFT_SPAWN_REQUESTS_QUEUE, False),
                (WEFT_SERVICES_REGISTRY_QUEUE, False),
            ),
            (
                (task_state_queue_name(submitted_tid), False),
                (WEFT_GLOBAL_LOG_QUEUE, False),
                (WEFT_SPAWN_REQUESTS_QUEUE, False),
                (WEFT_SERVICES_REGISTRY_QUEUE, False),
                ("T1776000000000000001.reserved", False),
            ),
            (
                (task_state_queue_name(submitted_tid), False),
                (WEFT_GLOBAL_LOG_QUEUE, False),
                (WEFT_SPAWN_REQUESTS_QUEUE, False),
                (WEFT_SERVICES_REGISTRY_QUEUE, False),
                ("T1776000000000000001.reserved", False),
            ),
        ]
    )

    def _fake_monitor(
        queues: Sequence[Queue], *, config: Config | None = None
    ) -> _FakeQueueChangeMonitor:
        monitor = _FakeQueueChangeMonitor(queues, config=config)
        created_monitors.append(monitor)
        return monitor

    monkeypatch.setattr(spawn_submission_cmd, "QueueChangeMonitor", _fake_monitor)
    monkeypatch.setattr(
        spawn_submission_cmd,
        "time",
        SimpleNamespace(monotonic=lambda: 0.0),
    )
    monkeypatch.setattr(
        spawn_submission_cmd,
        "_reconcile_submitted_spawn_once",
        lambda *_args, **_kwargs: next(results),
    )
    monkeypatch.setattr(
        spawn_submission_cmd,
        "_spawn_reconciliation_queue_specs",
        lambda _context, _tid, **_kwargs: next(queue_specs),
    )

    result = reconcile_submitted_spawn(context, submitted_tid, timeout=0.1)

    assert result == SpawnSubmissionReconciliation(
        outcome="reserved",
        tid=submitted_tid,
        reserved_queue="T1776000000000000001.reserved",
    )
    assert len(created_monitors) == 2
    assert created_monitors[0].queue_names == [
        task_state_queue_name(submitted_tid),
        WEFT_GLOBAL_LOG_QUEUE,
        WEFT_SPAWN_REQUESTS_QUEUE,
        WEFT_SERVICES_REGISTRY_QUEUE,
    ]
    assert created_monitors[1].queue_names == [
        task_state_queue_name(submitted_tid),
        WEFT_GLOBAL_LOG_QUEUE,
        WEFT_SPAWN_REQUESTS_QUEUE,
        WEFT_SERVICES_REGISTRY_QUEUE,
        "T1776000000000000001.reserved",
    ]


def test_run_inline_retains_spawn_request_when_manager_readiness_degrades(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)

    monkeypatch.setattr(
        "weft.commands.run.build_context",
        lambda spec_context=None, autostart=True: context,
    )
    monkeypatch.setattr("weft.commands.run._echo", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "weft.commands.run._ensure_manager_after_submission",
        lambda context_arg, *, submitted_tid: core_manager_runtime.ManagerEnsureResult(
            outcome="uncertain",
            manager_record=None,
            started_here=False,
            process_handle=None,
            reason="manager_start_failed:read-only",
        ),
    )

    execution = _execute_inline(
        command=(),
        function_target="tests.tasks.sample_targets:echo_payload",
        args=(),
        kwargs=(),
        env=(),
        name=None,
        interactive=False,
        stream_output=None,
        timeout=None,
        memory=None,
        cpu=None,
        tags=(),
        context_dir=root,
        json_output=False,
        verbose=False,
        autostart_enabled=True,
    )

    queue = context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=False)
    try:
        assert queue.peek_one(exact_timestamp=int(execution.tid)) is not None
    finally:
        queue.close()
    assert execution.availability_warning is not None


def test_run_spec_via_manager_retains_request_when_readiness_degrades(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    spec_path = root / "task.json"
    _write_json(
        spec_path,
        {
            "name": "demo-task",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
            "metadata": {},
        },
    )

    monkeypatch.setattr(
        "weft.commands.run.build_context",
        lambda spec_context=None, autostart=True: context,
    )
    monkeypatch.setattr("weft.commands.run._echo", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "weft.commands.run._ensure_manager_after_submission",
        lambda context_arg, *, submitted_tid: core_manager_runtime.ManagerEnsureResult(
            outcome="uncertain",
            manager_record=None,
            started_here=False,
            process_handle=None,
            reason="manager_start_failed:read-only",
        ),
    )

    execution = _execute_spec_via_manager(
        spec_path,
        name=None,
        verbose=False,
        json_output=False,
        autostart_enabled=True,
        persistent_override=None,
    )

    queue = context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=False)
    try:
        assert queue.peek_one(exact_timestamp=int(execution.tid)) is not None
    finally:
        queue.close()


def test_execute_spec_contains_unexpected_command_boundary_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The TaskSpec boundary preserves ordinary adapter detail without traceback."""

    class UnexpectedSubmissionFailure(Exception):
        pass

    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    spec_path = root / "task.json"
    _write_json(
        spec_path,
        {
            "name": "demo-task",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
            "metadata": {},
        },
    )
    monkeypatch.setattr(run_cmd, "build_context", lambda *args, **kwargs: context)
    monkeypatch.setattr(
        run_cmd,
        "_run_with_managed_execution",
        lambda **kwargs: (_ for _ in ()).throw(
            UnexpectedSubmissionFailure("adapter detail")
        ),
    )

    with pytest.raises(
        SubmissionError, match="Error submitting TaskSpec: adapter detail"
    ):
        run_cmd._execute_spec_via_manager(
            spec_path,
            name=None,
            verbose=False,
            json_output=False,
            autostart_enabled=True,
            persistent_override=None,
        )


def test_run_spec_via_manager_explicit_name_overrides_name_and_claims_endpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    spec_path = root / "persistent_named_task.json"
    _write_json(
        spec_path,
        {
            "name": "demo-task",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
                "persistent": True,
                "weft_context": str(root),
            },
            "metadata": {},
        },
    )

    captured: dict[str, Any] = {}

    def _capture_enqueue(
        context_arg: Any,
        taskspec: TaskSpec,
        work_payload: Any,
        *,
        seed_start_envelope: bool = True,
        allow_internal_runtime: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        del context_arg, work_payload, seed_start_envelope, allow_internal_runtime
        captured["name"] = taskspec.name
        captured["metadata"] = dict(taskspec.metadata)
        return (
            int(taskspec.tid) if taskspec.tid is not None else 1777000000000000001,
            taskspec.model_dump(mode="json"),
        )

    monkeypatch.setattr(
        "weft.commands.run.build_context",
        lambda spec_context=None, autostart=True: context,
    )
    monkeypatch.setattr(
        "weft.commands.run._ensure_manager_after_submission",
        lambda context, *, submitted_tid: _ready_manager_result(),
    )
    monkeypatch.setattr("weft.commands.run._enqueue_taskspec", _capture_enqueue)
    monkeypatch.setattr("weft.commands.run._echo", lambda *args, **kwargs: None)

    _execute_spec_via_manager(
        spec_path,
        name="mayor",
        verbose=False,
        json_output=False,
        autostart_enabled=True,
        persistent_override=None,
    )

    assert captured["name"] == "mayor"
    assert captured["metadata"][INTERNAL_RUNTIME_ENDPOINT_NAME_KEY] == "mayor"


def test_run_spec_via_manager_explicit_name_keeps_nonpersistent_tasks_label_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    spec_path = root / "named_task.json"
    _write_json(
        spec_path,
        {
            "name": "demo-task",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
                "weft_context": str(root),
            },
            "metadata": {},
        },
    )

    captured: dict[str, Any] = {}

    def _capture_enqueue(
        context_arg: Any,
        taskspec: TaskSpec,
        work_payload: Any,
        *,
        seed_start_envelope: bool = True,
        allow_internal_runtime: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        del context_arg, work_payload, seed_start_envelope, allow_internal_runtime
        captured["name"] = taskspec.name
        captured["metadata"] = dict(taskspec.metadata)
        return (
            int(taskspec.tid) if taskspec.tid is not None else 1777000000000000002,
            taskspec.model_dump(mode="json"),
        )

    monkeypatch.setattr(
        "weft.commands.run.build_context",
        lambda spec_context=None, autostart=True: context,
    )
    monkeypatch.setattr(
        "weft.commands.run._ensure_manager_after_submission",
        lambda context, *, submitted_tid: _ready_manager_result(),
    )
    monkeypatch.setattr("weft.commands.run._enqueue_taskspec", _capture_enqueue)
    monkeypatch.setattr("weft.commands.run._echo", lambda *args, **kwargs: None)

    _execute_spec_via_manager(
        spec_path,
        name="mayor",
        verbose=False,
        json_output=False,
        autostart_enabled=True,
        persistent_override=None,
    )

    assert captured["name"] == "mayor"
    assert INTERNAL_RUNTIME_ENDPOINT_NAME_KEY not in captured["metadata"]


@pytest.mark.parametrize("persistent", [False, True])
def test_run_spec_via_manager_rejects_reserved_internal_name_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    persistent: bool,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    spec_path = root / "named_task.json"
    _write_json(
        spec_path,
        {
            "name": "demo-task",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
                "weft_context": str(root),
                **({"persistent": True} if persistent else {}),
            },
            "metadata": {},
        },
    )

    monkeypatch.setattr(
        "weft.commands.run.build_context",
        lambda spec_context=None, autostart=True: context,
    )

    with pytest.raises(
        CommandUsageError, match="reserved for internal runtime services"
    ):
        _execute_spec_via_manager(
            spec_path,
            name="_weft.heartbeat",
            verbose=False,
            json_output=False,
            autostart_enabled=True,
            persistent_override=None,
        )


def test_run_pipeline_retains_request_when_manager_readiness_degrades(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    task_spec_path = context.weft_dir / "tasks" / "stage-task.json"
    pipeline_path = root / "pipeline.json"
    _write_json(
        task_spec_path,
        {
            "name": "stage-task",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
            "metadata": {},
        },
    )
    _write_json(
        pipeline_path,
        {
            "name": "demo-pipeline",
            "stages": [{"name": "stage-one", "task": "stage-task"}],
        },
    )

    monkeypatch.setattr(
        "weft.commands.run.build_context",
        lambda spec_context=None, autostart=True: context,
    )
    monkeypatch.setattr(
        "weft.commands.run._ensure_manager_after_submission",
        lambda context_arg, *, submitted_tid: core_manager_runtime.ManagerEnsureResult(
            outcome="uncertain",
            manager_record=None,
            started_here=False,
            process_handle=None,
            reason="manager_start_failed:read-only",
        ),
    )

    execution = _execute_pipeline(
        pipeline_path,
        name=None,
        pipeline_input=None,
        context_dir=root,
        json_output=False,
        verbose=False,
        autostart_enabled=True,
    )

    queue = context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=False)
    try:
        assert queue.peek_one(exact_timestamp=int(execution.tid)) is not None
    finally:
        queue.close()


def test_run_pipeline_explicit_name_overrides_pipeline_task_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    task_spec_path = context.weft_dir / "tasks" / "stage-task.json"
    pipeline_path = root / "pipeline.json"
    _write_json(
        task_spec_path,
        {
            "name": "stage-task",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
            "metadata": {},
        },
    )
    _write_json(
        pipeline_path,
        {
            "name": "demo-pipeline",
            "stages": [{"name": "stage-one", "task": "stage-task"}],
        },
    )

    captured: dict[str, Any] = {}

    def _capture_enqueue(
        context_arg: Any,
        taskspec: TaskSpec,
        work_payload: Any,
        *,
        seed_start_envelope: bool = True,
        allow_internal_runtime: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        del context_arg, work_payload, seed_start_envelope, allow_internal_runtime
        captured["name"] = taskspec.name
        captured["metadata"] = dict(taskspec.metadata)
        return (
            int(taskspec.tid) if taskspec.tid is not None else 1777000000000000003,
            taskspec.model_dump(mode="json"),
        )

    monkeypatch.setattr(
        "weft.commands.run._ensure_manager_after_submission",
        lambda context, *, submitted_tid: _ready_manager_result(),
    )
    monkeypatch.setattr("weft.commands.run._enqueue_taskspec", _capture_enqueue)
    monkeypatch.setattr("weft.commands.run._echo", lambda *args, **kwargs: None)

    _execute_pipeline(
        pipeline_path,
        name="nightly",
        pipeline_input=None,
        context_dir=root,
        json_output=False,
        verbose=False,
        autostart_enabled=True,
    )

    assert captured["name"] == "nightly"
    assert INTERNAL_RUNTIME_ENDPOINT_NAME_KEY not in captured["metadata"]


def test_run_pipeline_without_input_does_not_inject_work_envelope_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    task_spec_path = context.weft_dir / "tasks" / "stage-task.json"
    pipeline_path = root / "pipeline.json"
    _write_json(
        task_spec_path,
        {
            "name": "stage-task",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:provide_payload",
            },
            "metadata": {},
        },
    )
    _write_json(
        pipeline_path,
        {
            "name": "demo-pipeline",
            "stages": [{"name": "stage-one", "task": "stage-task"}],
        },
    )

    monkeypatch.setattr(
        "weft.commands.run.build_context",
        lambda spec_context=None, autostart=True: context,
    )
    monkeypatch.setattr(
        "weft.commands.run._ensure_manager_after_submission",
        lambda context_arg, *, submitted_tid: core_manager_runtime.ManagerEnsureResult(
            outcome="uncertain",
            manager_record=None,
            started_here=False,
            process_handle=None,
            reason="manager_start_failed:read-only",
        ),
    )

    _execute_pipeline(
        pipeline_path,
        name=None,
        pipeline_input=None,
        context_dir=root,
        json_output=False,
        verbose=False,
        autostart_enabled=True,
    )

    queue = context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=False)
    try:
        queued = queue.read_one()
    finally:
        queue.close()
    assert queued is not None
    payload = json.loads(queued)
    assert payload["inbox_message"] is None


def test_build_manager_spec_uses_tid_scoped_control_queues(tmp_path: Path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1775622400000000001"

    spec = core_manager_runtime.build_manager_spec(ctx, tid)

    assert spec.io.control["ctrl_in"] == f"T{tid}.ctrl_in"
    assert spec.io.control["ctrl_out"] == f"T{tid}.ctrl_out"


# POSIX exposes dead-child PID state in the way manager registry pruning relies on;
# Windows process handles do not provide an equivalent zombie/dead-PID contract here.
@pytest.mark.skipif(os.name == "nt", reason="POSIX dead-PID semantics only")
def test_select_active_manager_ignores_zombie_registry_pid(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)

    process = subprocess.Popen([sys.executable, "-c", "import os; os._exit(0)"])
    try:
        process.wait(timeout=2.0)
        registry = ctx.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
        try:
            registry.write(
                json.dumps(
                    _manager_service_payload(
                        ctx,
                        tid="1775622400000000001",
                        runtime_handle=_host_runtime_handle(process.pid),
                    )
                )
            )
        finally:
            registry.close()

        assert core_manager_runtime.select_active_manager(ctx) is None
    finally:
        process.wait()


def test_select_active_manager_ignores_noncanonical_request_queue(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    registry = ctx.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
    try:
        payload = _manager_service_payload(
            ctx,
            tid="1775622400000000002",
            runtime_handle=_host_runtime_handle(os.getpid()),
            requests="custom.manager.requests",
            ctrl_in="custom.manager.ctrl_in",
            ctrl_out="custom.manager.ctrl_out",
            outbox="custom.manager.outbox",
            service_key="manager:custom.manager.requests:test",
        )
        registry.write(json.dumps(payload))
    finally:
        registry.close()

    assert core_manager_runtime.select_active_manager(ctx) is None


@pytest.mark.parametrize(
    ("pong_fields", "expected_selected"),
    [({}, True), ({"role": "consumer"}, False), ({"task_status": "draining"}, False)],
)
def test_select_active_manager_uses_matched_pong_for_fresh_unobservable_host_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pong_fields: dict[str, Any],
    expected_selected: bool,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1775622400000000200"
    monkeypatch.setattr(
        core_manager_runtime,
        "MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS",
        60.0,
    )
    _write_active_manager_registry_record(
        ctx,
        tid=tid,
        runtime_handle=_host_runtime_handle(987654321),
    )
    probed_tids: list[str] = []

    record = _select_active_manager_while_answering_probe(
        ctx,
        tid=tid,
        monkeypatch=monkeypatch,
        pong_fields=pong_fields,
        probed_tids=probed_tids,
    )

    assert probed_tids == [tid]
    assert (record is not None) is expected_selected
    if record is not None:
        assert record["tid"] == tid
        assert record["_pong_live_at"] > 0


def test_select_active_manager_omits_expired_supervised_record_without_ping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1775622400000000201"
    monkeypatch.setattr(
        "weft.core.manager_runtime.MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS",
        60.0,
    )
    _write_active_manager_registry_record(
        ctx,
        tid=tid,
        runtime_handle=_external_supervisor_runtime_handle(),
        timestamp=time.time_ns() - 120_000_000_000,
    )

    probed_tids: list[str] = []
    record = _select_active_manager_while_answering_probe(
        ctx,
        tid=tid,
        monkeypatch=monkeypatch,
        probed_tids=probed_tids,
    )

    assert probed_tids == []
    assert record is None


def test_select_active_manager_rejects_non_manager_pong_for_stale_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1775622400000000205"
    monkeypatch.setattr(
        "weft.core.manager_runtime.MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS",
        60.0,
    )
    _write_active_manager_registry_record(
        ctx,
        tid=tid,
        runtime_handle=_external_supervisor_runtime_handle(),
        timestamp=time.time_ns() - 120_000_000_000,
    )

    record = _select_active_manager_while_answering_probe(
        ctx,
        tid=tid,
        monkeypatch=monkeypatch,
        pong_fields={"role": "consumer"},
    )

    assert record is None


def test_select_active_manager_rejects_draining_pong_for_stale_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1775622400000000209"
    monkeypatch.setattr(
        "weft.core.manager_runtime.MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS",
        60.0,
    )
    _write_active_manager_registry_record(
        ctx,
        tid=tid,
        runtime_handle=_external_supervisor_runtime_handle(),
        timestamp=time.time_ns() - 120_000_000_000,
    )

    record = _select_active_manager_while_answering_probe(
        ctx,
        tid=tid,
        monkeypatch=monkeypatch,
        pong_fields={"task_status": "draining"},
    )

    assert record is None


def test_select_active_manager_preserves_expired_unknown_row_without_ping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1775622400000000206"
    monkeypatch.setattr(
        "weft.core.manager_runtime.MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS",
        60.0,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.MANAGER_COMPETING_STARTUP_GRACE_SECONDS",
        0.01,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.runtime_liveness_from_registered_probe",
        lambda handle: "unknown",
    )
    _write_active_manager_registry_record(
        ctx,
        tid=tid,
        runtime_handle=_external_supervisor_runtime_handle(),
        timestamp=time.time_ns() - 120_000_000_000,
    )

    record = core_manager_runtime.select_active_manager(ctx, probe_stale=True)

    assert record is None
    assert _read_all_queue_messages(ctx, f"T{tid}.ctrl_in", persistent=True) == []
    assert (
        len(
            _read_all_queue_messages(
                ctx, WEFT_SERVICES_REGISTRY_QUEUE, persistent=False
            )
        )
        == 1
    )


def test_select_active_manager_fresh_hard_live_beats_expired_lower_tid_without_ping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    lower_tid = "1775622400000000207"
    higher_tid = "1775622400000000208"
    monkeypatch.setattr(
        "weft.core.manager_runtime.MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS",
        60.0,
    )
    _write_active_manager_registry_record(
        ctx,
        tid=lower_tid,
        runtime_handle=_external_supervisor_runtime_handle(),
        timestamp=time.time_ns() - 120_000_000_000,
    )
    _write_active_manager_registry_record(
        ctx,
        tid=higher_tid,
        runtime_handle=_host_runtime_handle(os.getpid()),
    )

    probed_tids: list[str] = []
    record = _select_active_manager_while_answering_probe(
        ctx,
        tid=lower_tid,
        monkeypatch=monkeypatch,
        probed_tids=probed_tids,
    )

    assert record is not None
    assert probed_tids == []
    assert record["tid"] == higher_tid


def test_select_active_manager_lower_hard_live_record_beats_higher_pong(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    lower_tid = "1775622400000000209"
    higher_tid = "1775622400000000210"
    monkeypatch.setattr(
        "weft.core.manager_runtime.MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS",
        60.0,
    )
    _write_active_manager_registry_record(
        ctx,
        tid=lower_tid,
        runtime_handle=_host_runtime_handle(os.getpid()),
    )
    _write_active_manager_registry_record(
        ctx,
        tid=higher_tid,
        runtime_handle=_external_supervisor_runtime_handle(),
        timestamp=time.time_ns() - 120_000_000_000,
    )

    record = _select_active_manager_while_answering_probe(
        ctx,
        tid=higher_tid,
        monkeypatch=monkeypatch,
    )

    assert record is not None
    assert record["tid"] == lower_tid


def test_select_active_manager_rescues_fresh_unknown_supervisor_with_matched_pong(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1775622400000000202"
    monkeypatch.setattr(
        "weft.core.manager_runtime.MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS",
        60.0,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.runtime_liveness_from_registered_probe",
        lambda handle: "unknown",
    )
    _write_active_manager_registry_record(
        ctx,
        tid=tid,
        runtime_handle=_external_supervisor_runtime_handle(),
    )

    probed_tids: list[str] = []
    record = _select_active_manager_while_answering_probe(
        ctx, tid=tid, monkeypatch=monkeypatch, probed_tids=probed_tids
    )

    assert record is not None
    assert record["tid"] == tid
    assert probed_tids == [tid]


def test_select_active_manager_prunes_missing_docker_supervised_record_immediately(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1775622400000000211"
    monkeypatch.setattr(
        "weft.core.manager_runtime.MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS",
        60.0,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.runtime_liveness_from_registered_probe",
        lambda handle: "stale",
    )
    _write_active_manager_registry_record(
        ctx,
        tid=tid,
        runtime_handle=_external_supervisor_runtime_handle(),
    )

    record = core_manager_runtime.select_active_manager(ctx, probe_stale=True)

    assert record is None
    assert _read_all_queue_messages(ctx, f"T{tid}.ctrl_in", persistent=True) == []


def test_select_active_manager_uses_supervisor_liveness_before_host_pid_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1775622400000000212"
    handle = _external_supervisor_runtime_handle()
    handle["observations"] = {
        **handle["observations"],
        "host_processes": [{"pid": 57, "create_time": 111.0}],
    }
    monkeypatch.setattr(
        "weft.core.manager_runtime.runtime_liveness_from_registered_probe",
        lambda runtime_handle: "live",
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.inspect_host_process",
        lambda *args: (_ for _ in ()).throw(
            AssertionError("supervised manager used host PID identity")
        ),
    )
    _write_active_manager_registry_record(ctx, tid=tid, runtime_handle=handle)

    record = core_manager_runtime.select_active_manager(ctx, probe_stale=True)

    assert record is not None
    assert record["tid"] == tid


def test_await_manager_start_settlement_never_probes_expired_unknown_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    stale_tid = "1775622400000000203"
    new_tid = "1775622400000000204"
    monkeypatch.setattr(
        "weft.core.manager_runtime.MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS",
        60.0,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.MANAGER_COMPETING_STARTUP_GRACE_SECONDS",
        0.01,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.MANAGER_REGISTRY_POLL_INTERVAL",
        0.001,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.runtime_liveness_from_registered_probe",
        lambda handle: "unknown",
    )
    _write_active_manager_registry_record(
        ctx,
        tid=stale_tid,
        runtime_handle=_external_supervisor_runtime_handle(),
        timestamp=time.time_ns() - 120_000_000_000,
    )

    record = core_manager_runtime._await_manager_start_settlement(
        ctx,
        manager_tid=new_tid,
        deadline=time.monotonic() + 0.05,
    )

    ping_messages = _read_all_queue_messages(
        ctx,
        f"T{stale_tid}.ctrl_in",
        persistent=True,
    )
    assert record is None
    assert ping_messages == []


# This test validates pruning of active records whose PID has exited while keeping
# stopped history. The dead-PID probe is POSIX-specific.
@pytest.mark.skipif(os.name == "nt", reason="POSIX dead-PID semantics only")
def test_list_manager_records_omits_dead_active_and_preserves_all_history(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    registry = ctx.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
    dead_tid = "1775622400000000003"
    stopped_tid = "1775622400000000004"

    dead_process = subprocess.Popen([sys.executable, "-c", "import os; os._exit(0)"])
    try:
        dead_process.wait(timeout=2.0)
        registry.write(
            json.dumps(
                _manager_service_payload(
                    ctx,
                    tid=dead_tid,
                    name="dead-manager",
                    status="active",
                    runtime_handle=_host_runtime_handle(dead_process.pid),
                )
            )
        )
        registry.write(
            json.dumps(
                _manager_service_payload(
                    ctx,
                    tid=stopped_tid,
                    name="stopped-manager",
                    status="stopped",
                    runtime_handle=_host_runtime_handle(dead_process.pid),
                )
            )
        )
    finally:
        registry.close()

    first = core_manager_runtime.list_manager_records(
        ctx,
        include_stopped=True,
        canonical_only=False,
    )
    second = core_manager_runtime.list_manager_records(
        ctx,
        include_stopped=True,
        canonical_only=False,
    )

    assert {record["tid"] for record in first} == {stopped_tid}
    assert {record["tid"] for record in second} == {stopped_tid}

    registry_reader = ctx.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
    try:
        entries = [
            payload for payload, _timestamp in iter_queue_json_entries(registry_reader)
        ]
    finally:
        registry_reader.close()

    assert [entry["owner_tid"] for entry in entries] == [dead_tid, stopped_tid]


def test_list_manager_records_omits_host_pid_identity_mismatch_without_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    registry = ctx.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
    stale_tid = "17756224000000000045"

    monkeypatch.setattr(
        core_manager_runtime,
        "inspect_host_process",
        lambda pid, create_time: HostProcessObservation("stale", "identity_mismatch"),
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "_manager_record_has_matched_pong",
        lambda *args, **kwargs: pytest.fail(
            "definitive mismatch must not be PING-probed"
        ),
    )
    try:
        registry.write(
            json.dumps(
                _manager_service_payload(
                    ctx,
                    tid=stale_tid,
                    name="stale-container-manager",
                    status="active",
                    runtime_handle={
                        "runner": "host",
                        "kind": "process",
                        "id": "1",
                        "control": {"authority": "host-pid"},
                        "observations": {
                            "host_pids": [1],
                            "host_processes": [{"pid": 1, "create_time": 111.0}],
                        },
                        "metadata": {},
                    },
                )
            )
        )
    finally:
        registry.close()

    assert (
        core_manager_runtime.list_manager_records(
            ctx,
            include_stopped=True,
            canonical_only=False,
        )
        == []
    )

    registry_reader = ctx.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
    try:
        entries = [
            payload for payload, _timestamp in iter_queue_json_entries(registry_reader)
        ]
    finally:
        registry_reader.close()

    assert len(entries) == 1
    assert entries[0]["owner_tid"] == stale_tid


def test_stop_manager_waits_for_pid_exit_after_stopped_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1775622400000000005"
    seen_pids: list[int | None] = []

    responses = iter(
        [
            _registry_view(
                target={
                    "tid": tid,
                    "status": "active",
                    "runtime_handle": _host_runtime_handle(4321),
                }
            ),
            _registry_view(
                target={
                    "tid": tid,
                    "status": "stopped",
                    "runtime_handle": _host_runtime_handle(4321),
                }
            ),
            _registry_view(
                target={
                    "tid": tid,
                    "status": "stopped",
                    "runtime_handle": _host_runtime_handle(4321),
                }
            ),
            _registry_view(),
        ]
    )
    pid_states = iter([True, True, False, False])
    monkeypatch.setattr(core_manager_runtime, "_record_pid", lambda record: 4321)
    monkeypatch.setattr(
        core_manager_runtime,
        "manager_registry_record_liveness",
        lambda record: "stale" if record.get("status") == "stopped" else "live",
    )

    monkeypatch.setattr(
        "weft.core.manager_runtime._send_stop", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.QueueChangeMonitor",
        _FakeQueueChangeMonitor,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime._registry_view",
        lambda *args, **kwargs: next(responses),
    )

    def fake_pid_alive(pid: int | None) -> bool:
        seen_pids.append(pid)
        return next(pid_states)

    monkeypatch.setattr("weft.core.manager_runtime.pid_is_live", fake_pid_alive)

    stopped, message = core_manager_runtime.stop_manager(
        ctx,
        None,
        tid=tid,
        timeout=1.0,
        stop_if_absent=True,
    )

    assert stopped is True
    assert message is None
    assert seen_pids.count(4321) >= 2


def test_stop_manager_confirms_observed_absence_in_evidence_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "17756224000000000051"
    initial_record = {
        "tid": tid,
        "status": "active",
        "runtime_handle": _host_runtime_handle(4321),
    }
    evidence: list[str] = []

    def runtime_stale(record: dict[str, Any] | None) -> str:
        assert record == initial_record
        evidence.append("runtime-stale")
        return "stale"

    monkeypatch.setattr(
        core_manager_runtime, "manager_registry_record_liveness", runtime_stale
    )

    def absent_registry_view(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        evidence.append("registry-absent")
        return _registry_view()

    def recorded_pid(record: dict[str, Any] | None) -> int:
        assert record == initial_record
        evidence.append("recorded-pid")
        return 4321

    def dead_recorded_pid(pid: int | None) -> bool:
        assert pid == 4321
        evidence.append("recorded-pid-dead")
        return False

    def local_process_absent_or_exited(
        process: subprocess.Popen[Any] | None,
        *,
        deadline: float,
    ) -> bool:
        del deadline
        assert process is None
        evidence.append("local-process-gate")
        return True

    monkeypatch.setattr(
        core_manager_runtime,
        "QueueChangeMonitor",
        _FakeQueueChangeMonitor,
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "_registry_view",
        absent_registry_view,
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "_record_pid",
        recorded_pid,
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "pid_is_live",
        dead_recorded_pid,
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "_wait_for_process_exit",
        local_process_absent_or_exited,
    )

    stopped, record = core_manager_runtime._await_manager_stop_confirmation(
        ctx,
        target_tid=tid,
        deadline=time.monotonic() + 1.0,
        initial_record=initial_record,
        process=None,
        stop_if_absent=False,
    )

    assert stopped is True
    assert record == initial_record
    assert evidence == [
        "registry-absent",
        "runtime-stale",
        "recorded-pid",
        "recorded-pid-dead",
        "local-process-gate",
    ]


def test_stop_manager_accepts_foreground_serve_stopped_registry_before_pid_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1775622400000000006"
    stopped_handle = _host_runtime_handle(4321)
    stopped_handle["metadata"] = {"foreground_serve": True}

    responses = iter(
        [
            _registry_view(
                target={
                    "tid": tid,
                    "status": "active",
                    "runtime_handle": _host_runtime_handle(4321),
                }
            ),
            _registry_view(
                target={
                    "tid": tid,
                    "status": "stopped",
                    "runtime_handle": stopped_handle,
                }
            ),
        ]
    )

    monkeypatch.setattr(
        "weft.core.manager_runtime._send_stop", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.QueueChangeMonitor",
        _FakeQueueChangeMonitor,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime._registry_view",
        lambda *args, **kwargs: next(responses),
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.pid_is_live",
        lambda pid: True,
    )

    stopped, message = core_manager_runtime.stop_manager(
        ctx,
        None,
        tid=tid,
        timeout=1.0,
        stop_if_absent=True,
    )

    assert stopped is True
    assert message is None


def test_stop_manager_stop_if_absent_short_circuits_after_stop_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "17756224000000000055"
    sent: list[str] = []

    monkeypatch.setattr(
        "weft.core.manager_runtime._send_stop",
        lambda _context, target_tid, *, record=None: sent.append(target_tid),
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime._await_manager_stop_confirmation",
        lambda *args, **kwargs: pytest.fail(
            "absent stop_if_absent path should not wait on registry confirmation"
        ),
    )

    stopped, message = core_manager_runtime.stop_manager(
        ctx,
        None,
        tid=tid,
        timeout=1.0,
        stop_if_absent=True,
    )

    assert stopped is True
    assert message is None
    assert sent == [tid]


def test_stop_manager_force_prefers_process_tree_kill_when_pid_known(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1775622400000000006"
    killed: list[tuple[int, float]] = []

    monkeypatch.setattr(
        "weft.core.manager_runtime._send_stop", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.QueueChangeMonitor",
        _FakeQueueChangeMonitor,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime._registry_view",
        lambda *args, **kwargs: _registry_view(),
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime._lookup_manager_pid",
        lambda *args, **kwargs: 8765,
    )
    liveness = iter((True, False))
    monkeypatch.setattr(
        "weft.core.manager_runtime.pid_is_live",
        lambda pid: pid == 8765 and next(liveness),
    )

    def fake_terminate_process_tree(
        pid: int, *, timeout: float, kill_after: bool = True
    ) -> set[int]:
        killed.append((pid, timeout))
        return {pid}

    monkeypatch.setattr(
        "weft.core.manager_runtime.terminate_process_tree",
        fake_terminate_process_tree,
    )

    stopped, message = core_manager_runtime.stop_manager(
        ctx,
        None,
        tid=tid,
        timeout=0.0,
        force=True,
    )

    assert stopped is True
    assert message is None
    assert killed == [(8765, 0.0)]


def test_stop_manager_force_refuses_stopped_state_while_tree_root_is_live(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1775622400000000008"
    marked: list[str] = []
    liveness = iter((True, True))

    monkeypatch.setattr(
        "weft.core.manager_runtime._send_stop", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.QueueChangeMonitor",
        _FakeQueueChangeMonitor,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime._registry_view",
        lambda *args, **kwargs: _registry_view(),
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime._lookup_manager_pid",
        lambda *args, **kwargs: 8765,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.pid_is_live",
        lambda pid: pid == 8765 and next(liveness),
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.terminate_process_tree",
        lambda *args, **kwargs: set(),
    )

    def _record_marked(*args: object, **kwargs: object) -> bool:
        marked.append(tid)
        return True

    monkeypatch.setattr(
        "weft.core.manager_runtime._mark_manager_stopped",
        _record_marked,
    )

    stopped, message = core_manager_runtime.stop_manager(
        ctx,
        None,
        tid=tid,
        timeout=0.0,
        force=True,
    )

    assert stopped is False
    assert message == "Failed to terminate Manager PID 8765"
    assert marked == []


@pytest.mark.parametrize("control_path", ["process-tree", "popen"])
def test_stop_manager_force_does_not_publish_stopped_after_permission_denial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control_path: str,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1775622400000000007"
    marked: list[str] = []

    monkeypatch.setattr(
        "weft.core.manager_runtime._send_stop", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime.QueueChangeMonitor",
        _FakeQueueChangeMonitor,
    )
    monkeypatch.setattr(
        "weft.core.manager_runtime._registry_view",
        lambda *args, **kwargs: _registry_view(),
    )

    def _record_marked(*args: object, **kwargs: object) -> bool:
        marked.append(tid)
        return True

    monkeypatch.setattr(
        "weft.core.manager_runtime._mark_manager_stopped",
        _record_marked,
    )

    class PermissionDeniedProcess:
        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            raise PermissionError("operation not permitted")

    process: Any | None = None
    if control_path == "process-tree":
        monkeypatch.setattr(
            "weft.core.manager_runtime._lookup_manager_pid",
            lambda *args, **kwargs: 8765,
        )
        monkeypatch.setattr(
            "weft.core.manager_runtime.pid_is_live",
            lambda pid: pid == 8765,
        )
        monkeypatch.setattr(
            "weft.core.manager_runtime.terminate_process_tree",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                PermissionError("operation not permitted")
            ),
        )
    else:
        monkeypatch.setattr(
            "weft.core.manager_runtime._lookup_manager_pid",
            lambda *args, **kwargs: None,
        )
        process = PermissionDeniedProcess()

    stopped, message = core_manager_runtime.stop_manager(
        ctx,
        None,
        process=process,
        tid=tid,
        timeout=0.0,
        force=True,
    )

    assert stopped is False
    assert message is not None
    assert "Permission denied" in message
    assert marked == []


def test_spawn_state_subscription_observes_first_write(tmp_path: Path) -> None:
    """A subscription created before the state queue exists sees publication."""
    context = build_context(spec_context=prepare_project_root(tmp_path))
    tid = str(time.time_ns())
    specs = spawn_submission_cmd._spawn_reconciliation_queue_specs(context, tid)
    state_name = task_state_queue_name(tid)
    assert (state_name, True) in specs
    with (
        spawn_submission_cmd._open_spawn_reconciliation_monitor(context, specs) as (
            _queues,
            monitor,
        ),
        context.queue(state_name, persistent=True) as publisher,
    ):
        assert publisher.peek_many() == []
        publisher.write(json.dumps({"full": tid, "short": tid_short_form(tid)}))
        assert monitor.wait(5.0)
        assert spawn_submission_cmd._mapping_exists_for_tid(context, tid)
