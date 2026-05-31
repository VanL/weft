"""Tests for task stop/kill helpers against launched task processes."""

from __future__ import annotations

import json
import time
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import Any

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft._constants import CONTROL_KILL, WEFT_TID_MAPPINGS_QUEUE
from weft.commands import tasks as task_cmd
from weft.commands.control_convergence import (
    ControlConvergenceAction,
    ControlConvergenceEvidence,
    ControlConvergenceState,
    control_convergence_machine,
    reduce_control_convergence,
)
from weft.context import build_context
from weft.core import (
    IOSection,
    SpecSection,
    StateSection,
    TaskSpec,
    launch_task_process,
)
from weft.core.control_probe import ControlProbeResult, MatchedPong
from weft.core.tasks import Consumer
from weft.ext import RunnerHandle
from weft.helpers import iter_queue_json_entries, kill_process_tree, pid_is_live

pytestmark = [pytest.mark.shared]


class _FakeQueueChangeMonitor:
    def __init__(self, queues, *, config=None) -> None:
        del config
        self.queue_names = [queue.name for queue in queues]
        self.wait_calls: list[float | None] = []

    def wait(self, timeout: float | None) -> bool:
        self.wait_calls.append(timeout)
        return False

    def close(self) -> None:
        return


def _runtime_handle(
    runner: str,
    runtime_id: str,
    *,
    kind: str = "process",
    authority: str = "host-pid",
    host_pids: list[int] | None = None,
    observations: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    observed = dict(observations or {})
    if host_pids is not None:
        observed["host_pids"] = host_pids
    return {
        "runner": runner,
        "kind": kind,
        "id": runtime_id,
        "control": {"authority": authority},
        "observations": observed,
        "metadata": metadata or {},
    }


def _make_taskspec(tid: str) -> TaskSpec:
    return TaskSpec(
        tid=tid,
        name="task-func",
        spec=SpecSection(
            type="function",
            function_target="tests.tasks.sample_targets:simulate_work",
        ),
        io=IOSection(
            inputs={"inbox": f"T{tid}.inbox"},
            outputs={"outbox": f"T{tid}.outbox"},
            control={"ctrl_in": f"T{tid}.ctrl_in", "ctrl_out": f"T{tid}.ctrl_out"},
        ),
        state=StateSection(),
    )


def _write_logged_pipeline_task(ctx, tid: str) -> None:
    log_queue = ctx.queue("weft.log.tasks", persistent=False)
    log_queue.write(
        json.dumps(
            {
                "event": "task_started",
                "status": "running",
                "tid": tid,
                "taskspec": {
                    "tid": tid,
                    "name": "demo-pipeline",
                    "spec": {
                        "type": "function",
                        "function_target": "weft.core.tasks.pipeline:runtime",
                    },
                    "io": {
                        "outputs": {"outbox": f"P{tid}.outbox"},
                        "control": {
                            "ctrl_in": f"P{tid}.ctrl_in",
                            "ctrl_out": f"P{tid}.ctrl_out",
                        },
                    },
                    "state": {"status": "running"},
                    "metadata": {
                        "role": "pipeline",
                        "_weft_pipeline_runtime": {
                            "queues": {"status": f"P{tid}.status"},
                        },
                    },
                },
            }
        )
    )


def test_terminal_snapshot_reads_outbox_without_consuming(tmp_path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    outbox = ctx.queue(f"T{tid}.outbox", persistent=True)
    outbox.write(json.dumps({"ok": True}))

    snapshot = task_cmd.task_terminal_snapshot(tid, context=ctx)

    assert snapshot.status == "completed"
    assert snapshot.source == "outbox"
    assert snapshot.value == {"ok": True}
    assert snapshot.terminal is True
    assert len(snapshot.ack_targets) == 1
    assert outbox.peek_one() is not None


def test_terminal_snapshot_reads_only_typed_terminal_ctrl_out(tmp_path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    ctrl_out = ctx.queue(f"T{tid}.ctrl_out", persistent=False)
    ctrl_out.write(json.dumps({"command": "PING", "status": "ok", "tid": tid}))
    ctrl_out.write(json.dumps({"type": "stream", "stream": "stderr", "data": "x"}))
    ctrl_out.write(
        json.dumps(
            {
                "type": "terminal",
                "source": "task",
                "tid": tid,
                "status": "failed",
                "error": "boom",
                "return_code": 1,
            }
        )
    )

    snapshot = task_cmd.task_terminal_snapshot(tid, context=ctx)

    assert snapshot.status == "failed"
    assert snapshot.source == "ctrl_out"
    assert snapshot.error == "boom"
    assert snapshot.return_code == 1
    assert len(snapshot.ack_targets) == 1
    assert ctrl_out.peek_many(limit=3)


def test_ack_terminal_snapshot_deletes_exact_message_only(tmp_path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    ctrl_out = ctx.queue(f"T{tid}.ctrl_out", persistent=False)
    ctrl_out.write(json.dumps({"command": "PING", "status": "ok", "tid": tid}))
    ctrl_out.write(
        json.dumps(
            {
                "type": "terminal",
                "source": "task",
                "tid": tid,
                "status": "failed",
            }
        )
    )
    ctrl_out.write(json.dumps({"command": "STATUS", "status": "ok", "tid": tid}))

    snapshot = task_cmd.task_terminal_snapshot(tid, context=ctx)

    assert task_cmd.ack_terminal_snapshot(snapshot, context=ctx) is True
    remaining = ctrl_out.peek_many(limit=10)
    assert len(remaining) == 2
    assert all("terminal" not in message for message in remaining)


def test_task_ping_returns_probe_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    calls: list[dict[str, Any]] = []

    def _fake_probe(ctx_arg, *, tid, ctrl_in_name, ctrl_out_name, timeout):
        calls.append(
            {
                "ctx": ctx_arg,
                "tid": tid,
                "ctrl_in_name": ctrl_in_name,
                "ctrl_out_name": ctrl_out_name,
                "timeout": timeout,
            }
        )
        payload = {
            "command": "PING",
            "status": "ok",
            "message": "PONG",
            "tid": tid,
            "request_id": "req-1",
            "task_status": "running",
            "extended": {"depth": 2},
        }
        return ControlProbeResult(
            request_id="req-1",
            matched=MatchedPong(
                payload=payload,
                observed_at=123,
                request_id="req-1",
            ),
        )

    monkeypatch.setattr(task_cmd, "send_keyed_ping_probe", _fake_probe)

    payload = task_cmd.task_ping(tid, timeout=0.25, context=ctx)

    assert payload == {
        "timed_out": False,
        "error": None,
        "observed_at": 123,
        "pong": {
            "command": "PING",
            "status": "ok",
            "message": "PONG",
            "tid": tid,
            "request_id": "req-1",
            "task_status": "running",
            "extended": {"depth": 2},
        },
    }
    assert calls == [
        {
            "ctx": ctx,
            "tid": tid,
            "ctrl_in_name": f"T{tid}.ctrl_in",
            "ctrl_out_name": f"T{tid}.ctrl_out",
            "timeout": 0.25,
        }
    ]


def test_task_ping_reports_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    monkeypatch.setattr(
        task_cmd,
        "send_keyed_ping_probe",
        lambda *_args, **_kwargs: ControlProbeResult(
            request_id="req-timeout",
            timed_out=True,
        ),
    )

    payload = task_cmd.task_ping(tid, timeout=0.0, context=ctx)

    assert payload == {
        "timed_out": True,
        "error": None,
        "observed_at": None,
        "pong": None,
    }


def test_control_convergence_machine_covers_all_transitions() -> None:
    cases: tuple[
        tuple[
            str,
            ControlConvergenceState,
            ControlConvergenceEvidence,
            ControlConvergenceAction,
        ],
        ...,
    ] = (
        (
            "wait for first evidence",
            "command_sent",
            ControlConvergenceEvidence(command=CONTROL_KILL),
            "wait",
        ),
        (
            "kill terminal after command",
            "command_sent",
            ControlConvergenceEvidence(
                command=CONTROL_KILL,
                terminal_status="killed",
            ),
            "accept_terminal",
        ),
        (
            "stop terminal after ack",
            "accepted",
            ControlConvergenceEvidence(
                command="STOP",
                terminal_status="cancelled",
            ),
            "accept_terminal",
        ),
        (
            "kill terminal after runner escalation",
            "escalating_runner",
            ControlConvergenceEvidence(
                command=CONTROL_KILL,
                terminal_status="killed",
                runner_fallback_attempted=True,
            ),
            "accept_terminal",
        ),
        (
            "kill terminal after host escalation",
            "escalating_host",
            ControlConvergenceEvidence(
                command=CONTROL_KILL,
                terminal_status="killed",
                runner_fallback_attempted=True,
                host_fallback_attempted=True,
            ),
            "accept_terminal",
        ),
        (
            "runtime dead after command",
            "command_sent",
            ControlConvergenceEvidence(
                command=CONTROL_KILL,
                runtime_dead_after_control=True,
            ),
            "accept_dead_runtime",
        ),
        (
            "runtime dead after accepted",
            "accepted",
            ControlConvergenceEvidence(
                command=CONTROL_KILL,
                runtime_dead_after_control=True,
            ),
            "accept_dead_runtime",
        ),
        (
            "runtime dead after runner escalation",
            "escalating_runner",
            ControlConvergenceEvidence(
                command=CONTROL_KILL,
                runtime_dead_after_control=True,
                runner_fallback_attempted=True,
            ),
            "accept_dead_runtime",
        ),
        (
            "runtime dead after host escalation",
            "escalating_host",
            ControlConvergenceEvidence(
                command=CONTROL_KILL,
                runtime_dead_after_control=True,
                runner_fallback_attempted=True,
                host_fallback_attempted=True,
            ),
            "accept_dead_runtime",
        ),
        (
            "ack waits",
            "command_sent",
            ControlConvergenceEvidence(command=CONTROL_KILL, ack_seen=True),
            "wait",
        ),
        (
            "accepted ack waits",
            "accepted",
            ControlConvergenceEvidence(command=CONTROL_KILL, ack_seen=True),
            "wait",
        ),
        (
            "command wait expires to runner escalation",
            "accepted",
            ControlConvergenceEvidence(
                command=CONTROL_KILL,
                ack_seen=True,
                observation_budget_expired=True,
            ),
            "escalate_runner",
        ),
        (
            "runner escalation expires to host escalation",
            "escalating_runner",
            ControlConvergenceEvidence(
                command=CONTROL_KILL,
                runner_fallback_attempted=True,
                observation_budget_expired=True,
            ),
            "escalate_host",
        ),
        (
            "host escalation expires unknown",
            "escalating_host",
            ControlConvergenceEvidence(
                command=CONTROL_KILL,
                runner_fallback_attempted=True,
                host_fallback_attempted=True,
                observation_budget_expired=True,
            ),
            "report_unknown",
        ),
        (
            "stop runner escalation expires unknown",
            "escalating_runner",
            ControlConvergenceEvidence(
                command="STOP",
                runner_fallback_attempted=True,
                observation_budget_expired=True,
            ),
            "report_unknown",
        ),
    )
    seen_transitions: set[str] = set()
    seen_states: set[ControlConvergenceState] = set()
    seen_actions: set[ControlConvergenceAction] = set()

    for label, current, evidence, expected_action in cases:
        decision = reduce_control_convergence(current, evidence)
        assert decision.action == expected_action, label
        seen_transitions.add(decision.transition_id)
        seen_states.update((decision.source, decision.target))
        seen_actions.add(decision.action)

    control_convergence_machine.assert_all_states_reachable(("command_sent",))
    control_convergence_machine.assert_transition_ids_covered(seen_transitions)
    control_convergence_machine.assert_states_covered(seen_states)
    control_convergence_machine.assert_actions_covered(seen_actions)


def test_control_convergence_does_not_accept_kill_ack_or_wrong_terminal() -> None:
    ack = reduce_control_convergence(
        "command_sent",
        ControlConvergenceEvidence(command=CONTROL_KILL, ack_seen=True),
    )
    wrong_terminal = reduce_control_convergence(
        "command_sent",
        ControlConvergenceEvidence(
            command=CONTROL_KILL,
            terminal_status="failed",
            observation_budget_expired=True,
        ),
    )

    assert ack.target == "accepted"
    assert ack.action == "wait"
    assert wrong_terminal.target == "escalating_runner"
    assert wrong_terminal.action == "escalate_runner"


def _wait_for_registered_worker_pid(ctx, tid: str, timeout: float = 15.0) -> int | None:
    deadline = time.time() + timeout
    mapping_queue = ctx.queue(WEFT_TID_MAPPINGS_QUEUE, persistent=False)
    while time.time() < deadline:
        for payload, _timestamp in iter_queue_json_entries(mapping_queue):
            if payload.get("full") != tid:
                continue
            runtime_handle = payload.get("runtime_handle")
            if isinstance(runtime_handle, dict):
                try:
                    handle = RunnerHandle.from_dict(runtime_handle)
                except (TypeError, ValueError):
                    continue
                for pid in handle.scoped_host_pids():
                    return pid
        time.sleep(0.05)
    return None


def _wait_for_process_exit(
    pid: int,
    *,
    process: BaseProcess | None = None,
    timeout: float = 5.0,
) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process is not None:
            process.join(timeout=0.05)
            if not process.is_alive():
                return True
        if not pid_is_live(pid):
            return True
        time.sleep(0.05)
    return False


def _launch_running_task(tmp_path) -> tuple[TaskSpec, BaseProcess, int]:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    spec = _make_taskspec(tid)
    process = launch_task_process(
        Consumer,
        ctx.broker_target,
        spec,
        config=ctx.config,
    )
    inbox = ctx.queue(spec.io.inputs["inbox"], persistent=True)
    inbox.write(json.dumps({"kwargs": {"duration": 5.0}}))
    worker_pid = _wait_for_registered_worker_pid(ctx, spec.tid)
    assert worker_pid is not None
    return spec, process, worker_pid


def test_stop_tasks_terminates_active_process_tree(tmp_path) -> None:
    spec, process, worker_pid = _launch_running_task(tmp_path)
    try:
        stopped = task_cmd.stop_tasks([spec.tid], context_path=tmp_path)
        assert stopped == 1
        assert _wait_for_process_exit(process.pid, process=process)
        assert _wait_for_process_exit(worker_pid)
    finally:
        kill_process_tree(process.pid)
        kill_process_tree(worker_pid)


def test_await_control_surface_uses_queue_monitor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    created_monitors: list[_FakeQueueChangeMonitor] = []
    snapshots = iter(
        [
            None,
            task_cmd.status_cmd.TaskSnapshot(
                tid=tid,
                tid_short=tid[-10:],
                name="task-func",
                status="completed",
                event="work_completed",
                activity=None,
                waiting_on=None,
                started_at=None,
                completed_at=time.time_ns(),
                last_timestamp=time.time_ns(),
                duration_seconds=None,
                runner=None,
                runtime_handle=None,
                runtime=None,
                metadata={},
            ),
        ]
    )

    def _fake_monitor(queues, *, config=None):
        monitor = _FakeQueueChangeMonitor(queues, config=config)
        created_monitors.append(monitor)
        return monitor

    monkeypatch.setattr(task_cmd, "QueueChangeMonitor", _fake_monitor)
    monkeypatch.setattr(task_cmd, "mapping_for_tid", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        task_cmd, "load_latest_taskspec_payload", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        task_cmd,
        "task_status",
        lambda *_args, **_kwargs: next(snapshots),
    )

    # Use the production wait budget: this test still builds real queue handles,
    # and PG-backed setup under xdist can exhaust artificial sub-second budgets.
    entry, snapshot = task_cmd._await_control_surface(ctx, tid)

    assert entry is None
    assert snapshot is not None
    assert snapshot.status == "completed"
    assert len(created_monitors) == 1
    assert created_monitors[0].queue_names == [
        "weft.state.tid_mappings",
        "weft.log.tasks",
        f"T{tid}.ctrl_out",
    ]
    assert created_monitors[0].wait_calls


def test_await_control_surface_does_not_promote_kill_ack_to_terminal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    ctrl_out = ctx.queue(f"T{tid}.ctrl_out", persistent=False)
    ctrl_out.write(json.dumps({"command": CONTROL_KILL, "status": "ack", "tid": tid}))

    monkeypatch.setattr(task_cmd, "QueueChangeMonitor", _FakeQueueChangeMonitor)
    monkeypatch.setattr(task_cmd, "mapping_for_tid", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        task_cmd, "load_latest_taskspec_payload", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(task_cmd, "CONTROL_SURFACE_WAIT_INTERVAL", 0.001)
    monkeypatch.setattr(
        task_cmd,
        "task_status",
        lambda *_args, **_kwargs: task_cmd.status_cmd.TaskSnapshot(
            tid=tid,
            tid_short=tid[-10:],
            name="task-func",
            status="running",
            event="task_started",
            activity=None,
            waiting_on=None,
            started_at=time.time_ns(),
            completed_at=None,
            last_timestamp=time.time_ns(),
            duration_seconds=None,
            runner=None,
            runtime_handle=None,
            runtime=None,
            metadata={},
        ),
    )

    _entry, snapshot = task_cmd._await_control_surface(ctx, tid, timeout=0.001)

    assert snapshot is not None
    assert snapshot.status == "running"
    assert snapshot.event == "task_started"


def test_await_control_surface_accepts_terminal_ctrl_out_without_log_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    ctrl_out = ctx.queue(f"T{tid}.ctrl_out", persistent=False)
    ctrl_out.write(
        json.dumps(
            {
                "type": "terminal",
                "source": "task",
                "tid": tid,
                "status": "cancelled",
                "timestamp": time.time_ns(),
            }
        )
    )

    monkeypatch.setattr(task_cmd, "QueueChangeMonitor", _FakeQueueChangeMonitor)
    monkeypatch.setattr(task_cmd, "mapping_for_tid", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        task_cmd,
        "load_latest_taskspec_payload",
        lambda *_args, **_kwargs: {
            "tid": tid,
            "name": "terminal-proof",
            "spec": {"runner": {"name": "host"}},
            "state": {"status": "running"},
            "metadata": {"kind": "test"},
        },
    )
    monkeypatch.setattr(
        task_cmd,
        "task_status",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("terminal ctrl_out should be sufficient")
        ),
    )

    entry, snapshot = task_cmd._await_control_surface(ctx, tid)

    assert entry is None
    assert snapshot is not None
    assert snapshot.status == "cancelled"
    assert snapshot.event == "ctrl_out_terminal"
    assert snapshot.name == "terminal-proof"
    assert snapshot.metadata == {"kind": "test"}


def test_kill_tasks_terminates_active_process_tree(tmp_path) -> None:
    spec, process, worker_pid = _launch_running_task(tmp_path)
    try:
        killed = task_cmd.kill_tasks([spec.tid], context_path=tmp_path)
        assert killed >= 1
        assert _wait_for_process_exit(process.pid, process=process)
        assert _wait_for_process_exit(worker_pid)
    finally:
        kill_process_tree(process.pid)
        kill_process_tree(worker_pid)


def test_stop_tasks_uses_runner_handle_when_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    mapping_queue = ctx.queue("weft.state.tid_mappings", persistent=False)
    ctrl_queue = ctx.queue(f"T{tid}.ctrl_in", persistent=False)
    calls: list[tuple[str, dict[str, Any], float]] = []

    class FakeRunnerPlugin:
        def stop(self, handle, *, timeout: float = 2.0) -> bool:
            calls.append(("stop", handle.to_dict(), timeout))
            return True

    mapping_queue.write(
        json.dumps(
            {
                "short": tid[-6:],
                "full": tid,
                "runner": "fake",
                "runtime_handle": _runtime_handle(
                    "fake",
                    "runtime-123",
                    host_pids=[33333],
                    metadata={"scope": "test"},
                ),
                "name": "task-func",
                "hostname": "test-host",
            }
        )
    )

    monkeypatch.setattr(
        task_cmd, "require_runner_plugin", lambda name: FakeRunnerPlugin()
    )
    monkeypatch.setattr(task_cmd, "_pid_exists", lambda pid: False)
    monkeypatch.setattr(
        task_cmd,
        "terminate_process_tree",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("should not fall back to direct PID stop")
        ),
    )

    stopped = task_cmd.stop_tasks([tid], context_path=root)

    assert stopped == 1
    assert calls == [
        (
            "stop",
            _runtime_handle(
                "fake",
                "runtime-123",
                host_pids=[33333],
                metadata={"scope": "test"},
            ),
            0.2,
        )
    ]
    assert ctrl_queue.read_one() == "STOP"


def test_stop_tasks_prefers_task_process_over_runner_handle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    mapping_queue = ctx.queue("weft.state.tid_mappings", persistent=False)
    ctrl_queue = ctx.queue(f"T{tid}.ctrl_in", persistent=False)
    terminate_calls: list[tuple[int, float, bool]] = []
    plugin_calls: list[tuple[str, dict[str, Any], float]] = []

    class FakeRunnerPlugin:
        def stop(self, handle, *, timeout: float = 2.0) -> bool:
            plugin_calls.append(("stop", handle.to_dict(), timeout))
            return True

    mapping_queue.write(
        json.dumps(
            {
                "short": tid[-6:],
                "full": tid,
                "runner": "fake",
                "runtime_handle": _runtime_handle(
                    "fake",
                    "runtime-123",
                    host_pids=[33333],
                    metadata={"scope": "test"},
                ),
                "name": "task-func",
                "hostname": "test-host",
            }
        )
    )

    monkeypatch.setattr(
        task_cmd, "require_runner_plugin", lambda name: FakeRunnerPlugin()
    )
    monkeypatch.setattr(task_cmd, "_pid_exists", lambda pid: pid == 11111)
    monkeypatch.setattr(
        task_cmd,
        "terminate_process_tree",
        lambda pid, timeout=0.2, kill_after=True: terminate_calls.append(
            (pid, timeout, kill_after)
        ),
    )

    stopped = task_cmd.stop_tasks([tid], context_path=root)

    assert stopped == 1
    assert terminate_calls == []
    assert plugin_calls == [
        (
            "stop",
            _runtime_handle(
                "fake",
                "runtime-123",
                host_pids=[33333],
                metadata={"scope": "test"},
            ),
            0.2,
        )
    ]
    assert ctrl_queue.read_one() == "STOP"


def test_kill_tasks_uses_runner_handle_when_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    mapping_queue = ctx.queue("weft.state.tid_mappings", persistent=False)
    calls: list[tuple[str, dict[str, Any], float]] = []

    class FakeRunnerPlugin:
        def kill(self, handle, *, timeout: float = 2.0) -> bool:
            calls.append(("kill", handle.to_dict(), timeout))
            return True

    mapping_queue.write(
        json.dumps(
            {
                "short": tid[-6:],
                "full": tid,
                "runner": "fake",
                "runtime_handle": _runtime_handle(
                    "fake",
                    "runtime-123",
                    host_pids=[33333],
                    metadata={"scope": "test"},
                ),
                "name": "task-func",
                "hostname": "test-host",
            }
        )
    )

    monkeypatch.setattr(
        task_cmd, "require_runner_plugin", lambda name: FakeRunnerPlugin()
    )
    monkeypatch.setattr(task_cmd, "_pid_exists", lambda pid: False)
    monkeypatch.setattr(
        task_cmd,
        "kill_process_tree",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("should not fall back to direct PID kill")
        ),
    )

    killed = task_cmd.kill_tasks([tid], context_path=root)

    assert killed == 1
    assert calls == [
        (
            "kill",
            _runtime_handle(
                "fake",
                "runtime-123",
                host_pids=[33333],
                metadata={"scope": "test"},
            ),
            0.2,
        )
    ]


def test_kill_tasks_does_not_count_runner_success_while_observed_pid_lives(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    mapping_queue = ctx.queue("weft.state.tid_mappings", persistent=False)
    calls: list[tuple[str, dict[str, Any], float]] = []
    force_calls: list[int] = []
    runtime_handle = _runtime_handle(
        "fake",
        "runtime-123",
        host_pids=[33333],
        metadata={"scope": "test"},
    )
    mapping_payload = {
        "short": tid[-6:],
        "full": tid,
        "runner": "fake",
        "runtime_handle": runtime_handle,
        "name": "task-func",
        "hostname": "test-host",
    }

    class FakeRunnerPlugin:
        def kill(self, handle, *, timeout: float = 2.0) -> bool:
            calls.append(("kill", handle.to_dict(), timeout))
            return True

    def _running_surface(_ctx, _tid, *, timeout=0.0):
        del timeout
        return mapping_payload, task_cmd.status_cmd.TaskSnapshot(
            tid=tid,
            tid_short=tid[-10:],
            name="task-func",
            status="running",
            event="task_started",
            activity=None,
            waiting_on=None,
            started_at=time.time_ns(),
            completed_at=None,
            last_timestamp=time.time_ns(),
            duration_seconds=None,
            runner="fake",
            runtime_handle=runtime_handle,
            runtime=None,
            metadata={},
        )

    mapping_queue.write(json.dumps(mapping_payload))
    monkeypatch.setattr(
        task_cmd, "require_runner_plugin", lambda name: FakeRunnerPlugin()
    )
    monkeypatch.setattr(task_cmd, "_await_control_surface", _running_surface)
    monkeypatch.setattr(task_cmd, "_pid_exists", lambda pid: pid == 33333)
    monkeypatch.setattr(
        task_cmd,
        "kill_process_tree",
        lambda pid, timeout=0.2: force_calls.append(pid) or False,
    )

    killed = task_cmd.kill_tasks([tid], context_path=root)

    assert killed == 0
    assert calls == [("kill", runtime_handle, 0.2)]
    assert force_calls == [33333]


def test_task_stop_stops_pipeline_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    ctrl_queue = ctx.queue(f"P{tid}.ctrl_in", persistent=False)
    _write_logged_pipeline_task(ctx, tid)
    monkeypatch.setattr(
        task_cmd, "_await_control_surface", lambda ctx, tid: (None, None)
    )

    stopped = task_cmd.stop_tasks([tid], context_path=root)

    assert stopped == 1
    assert ctrl_queue.read_one() == "STOP"


def test_task_kill_kills_pipeline_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    ctrl_queue = ctx.queue(f"P{tid}.ctrl_in", persistent=False)
    _write_logged_pipeline_task(ctx, tid)
    monkeypatch.setattr(
        task_cmd, "_await_control_surface", lambda ctx, tid: (None, None)
    )
    monkeypatch.setattr(task_cmd, "_kill_via_fallback", lambda _entry: True)

    killed = task_cmd.kill_tasks([tid], context_path=root)

    assert killed == 1
    assert ctrl_queue.read_one() == "KILL"


def test_stop_tasks_does_not_force_terminal_consumer_for_external_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    mapping_queue = ctx.queue("weft.state.tid_mappings", persistent=False)
    ctrl_queue = ctx.queue(f"T{tid}.ctrl_in", persistent=False)
    mapping_queue.write(
        json.dumps(
            {
                "short": tid[-6:],
                "full": tid,
                "runner": "docker",
                "runtime_handle": _runtime_handle(
                    "docker",
                    "runtime-123",
                    kind="container",
                    authority="runner",
                    observations={"container_id": "runtime-123"},
                    metadata={"image": "python:3.13-alpine"},
                ),
                "name": "task-func",
                "hostname": "test-host",
            }
        )
    )

    monkeypatch.setattr(
        task_cmd,
        "task_status",
        lambda *args, **kwargs: task_cmd.status_cmd.TaskSnapshot(
            tid=tid,
            tid_short=tid[-10:],
            name="docker-task",
            status="cancelled",
            event="control_stop",
            activity=None,
            waiting_on=None,
            started_at=None,
            completed_at=None,
            last_timestamp=time.time_ns(),
            duration_seconds=None,
            runner="docker",
            runtime_handle=_runtime_handle(
                "docker",
                "runtime-123",
                kind="container",
                authority="runner",
                observations={"container_id": "runtime-123"},
                metadata={"image": "python:3.13-alpine"},
            ),
            runtime={
                "runner": "docker",
                "id": "runtime-123",
                "state": "missing",
                "metadata": {"image": "python:3.13-alpine"},
            },
            metadata={},
        ),
    )
    monkeypatch.setattr(
        task_cmd,
        "terminate_process_tree",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("external runners must not force-stop the consumer PID")
        ),
    )

    stopped = task_cmd.stop_tasks([tid], context_path=root)

    assert stopped == 1
    assert ctrl_queue.read_one() == "STOP"


def test_stop_tasks_does_not_force_stop_consumer_without_runner_handle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    mapping_queue = ctx.queue("weft.state.tid_mappings", persistent=False)
    ctrl_queue = ctx.queue(f"T{tid}.ctrl_in", persistent=False)
    mapping_queue.write(
        json.dumps(
            {
                "short": tid[-6:],
                "full": tid,
                "runner": "host",
                "runtime_handle": None,
                "name": "task-func",
                "hostname": "test-host",
            }
        )
    )

    monkeypatch.setattr(
        task_cmd,
        "task_status",
        lambda *args, **kwargs: task_cmd.status_cmd.TaskSnapshot(
            tid=tid,
            tid_short=tid[-10:],
            name="host-task",
            status="running",
            event="work_started",
            activity=None,
            waiting_on=None,
            started_at=None,
            completed_at=None,
            last_timestamp=time.time_ns(),
            duration_seconds=None,
            runner="host",
            runtime_handle=None,
            runtime=None,
            metadata={},
        ),
    )
    monkeypatch.setattr(
        task_cmd,
        "terminate_process_tree",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("graceful stop must not terminate the consumer PID")
        ),
    )

    stopped = task_cmd.stop_tasks([tid], context_path=root)

    assert stopped == 1
    assert ctrl_queue.read_one() == "STOP"


def test_kill_tasks_does_not_force_terminal_consumer_for_external_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    mapping_queue = ctx.queue("weft.state.tid_mappings", persistent=False)
    mapping_queue.write(
        json.dumps(
            {
                "short": tid[-6:],
                "full": tid,
                "runner": "macos-sandbox",
                "runtime_handle": _runtime_handle(
                    "macos-sandbox",
                    "runtime-123",
                    kind="sandboxed-process",
                    host_pids=[33333],
                    observations={"sandbox_profile": "allow-default.sb"},
                    metadata={"profile": "allow-default.sb"},
                ),
                "name": "task-func",
                "hostname": "test-host",
            }
        )
    )

    monkeypatch.setattr(
        task_cmd,
        "task_status",
        lambda *args, **kwargs: task_cmd.status_cmd.TaskSnapshot(
            tid=tid,
            tid_short=tid[-10:],
            name="sandbox-task",
            status="killed",
            event="control_kill",
            activity=None,
            waiting_on=None,
            started_at=None,
            completed_at=None,
            last_timestamp=time.time_ns(),
            duration_seconds=None,
            runner="macos-sandbox",
            runtime_handle=_runtime_handle(
                "macos-sandbox",
                "runtime-123",
                kind="sandboxed-process",
                host_pids=[33333],
                observations={"sandbox_profile": "allow-default.sb"},
                metadata={"profile": "allow-default.sb"},
            ),
            runtime={
                "runner": "macos-sandbox",
                "id": "runtime-123",
                "state": "missing",
                "metadata": {"profile": "allow-default.sb"},
            },
            metadata={},
        ),
    )
    monkeypatch.setattr(
        task_cmd,
        "kill_process_tree",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("external runners must not force-kill the consumer PID")
        ),
    )

    killed = task_cmd.kill_tasks([tid], context_path=root)

    assert killed == 1
