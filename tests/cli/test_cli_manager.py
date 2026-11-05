"""CLI coverage for `weft manager` subcommands."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from contextlib import ExitStack, closing
from pathlib import Path
from typing import Any, Literal

import pytest

from simplebroker.ext import BrokerError
from tests.conftest import run_cli
from tests.helpers.test_backend import prepare_project_root
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import (
    INTERNAL_SERVICE_KEY_HEARTBEAT,
    INTERNAL_SERVICE_KEYS,
    MANAGER_ADMISSION_RECHECK_SECONDS,
    MANAGER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS,
    TERMINAL_ENVELOPE_TYPE,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_MANAGER_OUTBOX_QUEUE,
    WEFT_SERVICES_REGISTRY_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
)
from weft.context import WeftContext, build_context
from weft.core.launcher import launch_task_process
from weft.core.manager import Manager
from weft.core.manager_runtime import build_manager_spec, generate_tid
from weft.core.queue_wait import QueueChangeMonitor
from weft.core.service_convergence import build_manager_service_payload
from weft.core.task_state import read_task_state_snapshot, task_state_queue_name
from weft.core.taskspec import TaskSpec
from weft.helpers import (
    encode_tid,
    iter_queue_json_entries,
    pid_is_live,
    process_create_time,
)

pytestmark = [pytest.mark.shared]


def _host_runtime_handle(pid: int, create_time: float | None = None) -> dict[str, Any]:
    return {
        "runner": "host",
        "kind": "process",
        "id": str(pid),
        "control": {"authority": "host-pid"},
        "observations": {
            "host_pids": [pid],
            **(
                {"host_processes": [{"pid": pid, "create_time": create_time}]}
                if create_time is not None
                else {}
            ),
        },
        "metadata": {},
    }


def _host_pid_from_handle(payload: dict[str, Any]) -> int | None:
    handle = payload.get("runtime_handle")
    if not isinstance(handle, dict):
        return None
    observations = handle.get("observations")
    if not isinstance(observations, dict):
        return None
    host_pids = observations.get("host_pids")
    if not isinstance(host_pids, list):
        return None
    return next((pid for pid in host_pids if isinstance(pid, int) and pid > 0), None)


def _manager_service_payload(
    context: WeftContext,
    *,
    tid: str,
    status: Literal["active", "draining", "stopped", "superseded"] = "active",
    name: str = "manager",
    runtime_handle: dict[str, Any] | None = None,
    requests: str = WEFT_SPAWN_REQUESTS_QUEUE,
) -> dict[str, Any]:
    return build_manager_service_payload(
        context=context,
        tid=tid,
        name=name,
        status=status,
        queues={
            "requests": requests,
            "ctrl_in": f"T{tid}.ctrl_in",
            "ctrl_out": f"T{tid}.ctrl_out",
            "outbox": WEFT_MANAGER_OUTBOX_QUEUE,
        },
        runtime_handle=runtime_handle or {},
    )


def _parse_manager_start_output(output: str) -> tuple[str, bool]:
    match = re.search(r"Started manager (\d+)", output)
    if match is not None:
        return match.group(1), True
    match = re.search(r"Manager (\d+) already running", output)
    if match is None:
        raise AssertionError(f"Unable to parse manager start output: {output!r}")
    return match.group(1), False


def _task_log_payloads(context: WeftContext, tid: str) -> list[dict[str, Any]]:
    queue = context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    try:
        payloads: list[dict[str, Any]] = []
        for payload, _timestamp in iter_queue_json_entries(queue):
            if payload.get("tid") == tid:
                payloads.append(payload)
        return payloads
    finally:
        queue.close()


def test_manager_start_help_includes_replace(workdir: Path) -> None:
    rc, out, err = run_cli(
        "manager",
        "start",
        "--help",
        cwd=workdir,
    )

    assert rc == 0
    assert err == ""
    assert "--replace" in out


def test_manager_start_and_status(weft_harness: WeftTestHarness) -> None:
    context_root = weft_harness.root

    rc, out, err = run_cli(
        "manager",
        "start",
        "--context",
        context_root,
        cwd=context_root,
        harness=weft_harness,
    )
    assert rc == 0, (rc, out, err)
    assert "manager" in out.lower()
    assert err == ""

    rc, out, err = run_cli(
        "manager",
        "list",
        "--json",
        "--context",
        context_root,
        cwd=context_root,
        harness=weft_harness,
    )
    assert rc == 0, (rc, out, err)
    assert err == ""
    records = json.loads(out or "[]")
    active = [record for record in records if record.get("status") == "active"]
    assert active
    tid = active[0]["tid"]

    rc, out, err = run_cli(
        "manager",
        "start",
        "--context",
        context_root,
        cwd=context_root,
        harness=weft_harness,
    )
    assert rc == 0, (rc, out, err)
    assert "already running" in out.lower()
    assert err == ""

    rc, out, err = run_cli(
        "manager",
        "status",
        tid,
        "--context",
        context_root,
        "--json",
        cwd=context_root,
        harness=weft_harness,
    )
    assert rc == 0, (rc, out, err)
    assert err == ""
    detail = json.loads(out)
    assert detail.get("tid") == tid
    assert detail.get("status") == "active"

    rc, out, err = run_cli(
        "manager",
        "status",
        encode_tid(tid),
        "--context",
        context_root,
        "--json",
        cwd=context_root,
        harness=weft_harness,
    )
    assert rc == 0, (rc, out, err)
    detail = json.loads(out)
    assert detail.get("tid") == tid
    assert detail.get("status") == "active"

    rc, out, err = run_cli(
        "manager",
        "stop",
        "--context",
        context_root,
        cwd=context_root,
        harness=weft_harness,
    )
    assert rc == 0, (rc, out, err)
    assert err == ""

    rc, out, err = run_cli(
        "manager",
        "status",
        tid,
        "--context",
        context_root,
        "--json",
        cwd=context_root,
        harness=weft_harness,
    )
    assert rc == 0, (rc, out, err)
    assert err == ""
    detail = json.loads(out)
    assert detail.get("status") == "stopped"


_TERM_IGNORING_TASK_SCRIPT = """
from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

if hasattr(signal, "SIGTERM"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path(sys.argv[1]).write_text(str(os.getpid()), encoding="utf-8")
time.sleep(120)
"""


def _queue_rows(context: WeftContext, name: str) -> list[tuple[dict[str, Any], int]]:
    """Peek every JSON row of one queue, including claimed rows."""

    queue = context.queue(name, persistent=False)
    try:
        rows = queue.peek_many(
            limit=100_000, with_timestamps=True, include_claimed=True
        )
    finally:
        queue.close()
    decoded: list[tuple[dict[str, Any], int]] = []
    for body, timestamp in rows:
        payload = json.loads(body)
        if isinstance(payload, dict):
            decoded.append((payload, int(timestamp)))
    return decoded


def _live_internal_service_pids(context: WeftContext) -> dict[str, int]:
    """Map each internal service TID whose registered host PID is live."""

    pids: dict[str, int] = {}
    for payload, _timestamp in _queue_rows(context, WEFT_SERVICES_REGISTRY_QUEUE):
        tid = payload.get("owner_tid")
        pid = _host_pid_from_handle(payload)
        if (
            payload.get("service_key") in INTERNAL_SERVICE_KEYS
            and isinstance(tid, str)
            and pid is not None
            and pid_is_live(pid)
        ):
            pids[tid] = pid
    return pids


def _live_heartbeat_owner(context: WeftContext) -> tuple[str, int] | None:
    """Return the live heartbeat service's TID and host PID, if any."""

    for payload, _timestamp in _queue_rows(context, WEFT_SERVICES_REGISTRY_QUEUE):
        tid = payload.get("owner_tid")
        pid = _host_pid_from_handle(payload)
        if (
            payload.get("service_key") == INTERNAL_SERVICE_KEY_HEARTBEAT
            and payload.get("status") == "active"
            and isinstance(tid, str)
            and pid is not None
            and pid_is_live(pid)
        ):
            return tid, pid
    return None


def _manager_host_pid(context: WeftContext, manager_tid: str) -> int | None:
    """Return the host PID the Manager's registry row names, if any."""

    for payload, _timestamp in _queue_rows(context, WEFT_SERVICES_REGISTRY_QUEUE):
        if payload.get("role") == "manager" and manager_tid in {
            payload.get("owner_tid"),
            payload.get("tid"),
        }:
            pid = _host_pid_from_handle(payload)
            if pid is not None:
                return pid
    return None


def _first_terminal_snapshot(
    context: WeftContext, tid: str
) -> tuple[int, dict[str, Any]] | None:
    """Return the task's first task-owned terminal snapshot row."""

    return next(
        (
            (timestamp, payload)
            for payload, timestamp in _queue_rows(context, task_state_queue_name(tid))
            if payload.get("terminal") is True
        ),
        None,
    )


def test_manager_stop_leaves_internal_services_live(
    weft_harness: WeftTestHarness, tmp_path: Path
) -> None:
    """``weft manager stop`` drains user work, then itself; services run on.

    Spec: docs/specifications/03-Manager_Architecture.md [MA-1] item 7,
    [MA-3]; docs/program-theory.md [WT-2a.13] (owner, 2026-11-04). The user
    task ignores SIGTERM and is ready (its handler installed) before STOP.
    Order is read from task-owned terminal snapshots (broker message IDs).
    The services stay live with no terminal snapshot; the harness stops
    them at cleanup. Named mutation: keeping the services phase of the
    drain gives each service a ``control_stop`` terminal snapshot.
    """

    context_root = weft_harness.root
    context = weft_harness.context
    script = tmp_path / "term_ignoring_task.py"
    script.write_text(_TERM_IGNORING_TASK_SCRIPT.lstrip(), encoding="utf-8")
    marker = tmp_path / "term-ignoring-task.pid"

    rc, out, err = run_cli(
        "manager",
        "start",
        "--context",
        context_root,
        cwd=context_root,
        env={**os.environ, "WEFT_TASK_MONITOR_ENABLED": "1"},
        harness=weft_harness,
    )
    assert rc == 0, (rc, out, err)
    manager_tid, _started = _parse_manager_start_output(out)
    weft_harness.register_manager_tid(manager_tid)

    rc, out, err = run_cli(
        "run",
        "--no-wait",
        "--context",
        context_root,
        sys.executable,
        str(script),
        str(marker),
        cwd=context_root,
        harness=weft_harness,
    )
    assert rc == 0, (rc, out, err)
    user_tid = out.strip()
    weft_harness.register_tid(user_tid)

    deadline = time.monotonic() + 60.0
    services: dict[str, int] = {}
    while time.monotonic() < deadline:
        services = _live_internal_service_pids(context)
        if marker.exists() and marker.read_text(encoding="utf-8").strip() and services:
            break
        time.sleep(0.1)
    assert marker.exists(), "user task never became ready"
    assert services, "no internal service appeared before STOP"

    rc, out, err = run_cli(
        "manager",
        "stop",
        "--context",
        context_root,
        cwd=context_root,
        harness=weft_harness,
    )
    assert rc == 0, (rc, out, err)

    user_terminal = _first_terminal_snapshot(context, user_tid)
    manager_terminal = _first_terminal_snapshot(context, manager_tid)
    assert user_terminal is not None
    assert manager_terminal is not None
    assert user_terminal[0] < manager_terminal[0]
    drained = [
        timestamp
        for payload, timestamp in _queue_rows(context, WEFT_GLOBAL_LOG_QUEUE)
        if payload.get("tid") == manager_tid
        and payload.get("event") == "manager_stop_drained"
    ]
    assert drained
    assert user_terminal[0] < drained[0]
    for service_tid, pid in services.items():
        assert _first_terminal_snapshot(context, service_tid) is None, (
            f"service {service_tid} stopped with the Manager"
        )
        assert pid_is_live(pid), f"service {service_tid} not live after stop"


def test_manager_stop_exits_within_drain_window_leaving_heartbeat_live(
    weft_harness: WeftTestHarness,
) -> None:
    """The Manager exits on ``weft manager stop`` while its heartbeat lives.

    Spec: docs/specifications/03-Manager_Architecture.md [MA-1] item 7,
    [MA-3]. Plan 2026-11-04-context-level-service-singletons Task 3 (f) and
    section 6b: STOP arrives through the Manager's queue, so the Manager
    exits through ordinary interpreter shutdown, which on Windows joins
    every non-daemon child ``multiprocessing`` still tracks. Runs on every
    platform. Named mutation: leaving the detached service in the
    ``multiprocessing`` children set makes a Windows Manager wait on the
    heartbeat, so the stop overruns the drain window; keeping the services
    phase stops the heartbeat.
    """

    context_root = weft_harness.root
    context = weft_harness.context
    rc, out, err = run_cli(
        "manager",
        "start",
        "--context",
        context_root,
        cwd=context_root,
        harness=weft_harness,
    )
    assert rc == 0, (rc, out, err)
    manager_tid, _started = _parse_manager_start_output(out)
    weft_harness.register_manager_tid(manager_tid)

    heartbeat_tid, heartbeat_pid = _wait_until(
        lambda: _live_heartbeat_owner(context),
        timeout=60.0,
        message="a live heartbeat service",
    )
    manager_pid = _wait_until(
        lambda: _manager_host_pid(context, manager_tid),
        timeout=60.0,
        message="the manager host PID",
    )

    started = time.monotonic()
    rc, out, err = run_cli(
        "manager",
        "stop",
        manager_tid,
        "--context",
        context_root,
        cwd=context_root,
        harness=weft_harness,
    )
    elapsed = time.monotonic() - started
    assert rc == 0, (rc, out, err)
    remaining = MANAGER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS - elapsed
    assert remaining > 0, f"manager stop took {elapsed:.1f}s"
    _wait_until(
        lambda: not pid_is_live(manager_pid),
        timeout=remaining,
        message="the manager process to exit within the drain window",
    )
    assert pid_is_live(heartbeat_pid), "heartbeat stopped with the Manager"
    assert _first_terminal_snapshot(context, heartbeat_tid) is None


_LONG_WORK_SPEC: dict[str, Any] = {
    "type": "function",
    "function_target": "tests.tasks.sample_targets:simulate_work",
    "keyword_args": {"duration": 120.0},
}


def _wait_until(
    predicate: Any, *, timeout: float = 60.0, message: str = "condition"
) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError(f"Timed out waiting for {message}")


def _wait_on_queues(
    context: WeftContext,
    queue_names: list[str],
    ready: Any,
    *,
    timeout: float = 60.0,
    message: str = "condition",
) -> Any:
    """Re-check ``ready`` on activity in the named queues, not on a timer."""

    with ExitStack() as stack:
        queues = [
            stack.enter_context(context.queue(name, persistent=True))
            for name in queue_names
        ]
        monitor = stack.enter_context(
            closing(QueueChangeMonitor(queues, config=context.config))
        )
        deadline = time.monotonic() + timeout
        while True:
            value = ready()
            if value:
                return value
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"Timed out waiting for {message}")
            monitor.wait(remaining)


def _first_terminal_log_id(context: WeftContext, tid: str) -> int | None:
    """Return the task's terminal envelope message ID in ``weft.log.tasks``."""

    return next(
        (
            timestamp
            for payload, timestamp in _queue_rows(context, WEFT_GLOBAL_LOG_QUEUE)
            if payload.get("tid") == tid
            and payload.get("type") == TERMINAL_ENVELOPE_TYPE
        ),
        None,
    )


def _running_snapshot(context: WeftContext, tid: str) -> dict[str, Any] | None:
    snapshot = read_task_state_snapshot(context, tid)
    if snapshot is None:
        return None
    payload = snapshot[1]
    projection = payload.get("projection")
    if isinstance(projection, dict) and projection.get("status") == "running":
        return payload
    return None


def _checkpoint_residents(context: WeftContext, manager_tid: str) -> set[str]:
    snapshot = read_task_state_snapshot(context, manager_tid)
    if snapshot is None:
        return set()
    residents = snapshot[1].get("scheduler_frontiers", {}).get("residents", {})
    return set(residents) if isinstance(residents, dict) else set()


def _snapshot_pids(payload: dict[str, Any]) -> set[int]:
    return {
        entry["pid"]
        for entry in payload.get("host_processes", [])
        if isinstance(entry, dict) and isinstance(entry.get("pid"), int)
    }


def _pid_running(pid: int) -> bool:
    psutil = pytest.importorskip("psutil")
    try:
        process = psutil.Process(pid)
        return bool(process.is_running() and process.status() != psutil.STATUS_ZOMBIE)
    except psutil.Error:
        return False


def _first_log_id(context: WeftContext, tid: str, events: set[str]) -> int | None:
    return next(
        (
            timestamp
            for payload, timestamp in _queue_rows(context, WEFT_GLOBAL_LOG_QUEUE)
            if payload.get("tid") == tid and payload.get("event") in events
        ),
        None,
    )


def _manager_pid(weft_harness: WeftTestHarness, manager_tid: str) -> int:
    def lookup() -> int | None:
        rc, out, err = run_cli(
            "manager",
            "status",
            manager_tid,
            "--json",
            "--context",
            weft_harness.root,
            cwd=weft_harness.root,
            harness=weft_harness,
        )
        assert rc == 0, (rc, out, err)
        return _host_pid_from_handle(json.loads(out))

    return int(_wait_until(lookup, message=f"Manager {manager_tid} pid"))


def test_manager_stop_reaches_earlier_manager_detached_task(
    weft_harness: WeftTestHarness,
) -> None:
    """A system STOP reaches a live task an earlier, dead Manager launched.

    Spec: docs/specifications/03-Manager_Architecture.md [MA-3] (Reading B).
    The task's own ``control_stop`` is the delivery oracle: the Manager's
    host SIGTERM rescue would publish ``task_signal_stop`` instead, and a
    ``T*.ctrl_in`` pattern broadcast reaches no queue for a task that was
    never controlled (its ``ctrl_in`` is absent before the STOP).
    """

    psutil = pytest.importorskip("psutil")
    context_root = weft_harness.root
    context = weft_harness.context

    rc, out, err = run_cli(
        "manager",
        "start",
        "--context",
        context_root,
        cwd=context_root,
        harness=weft_harness,
    )
    assert rc == 0, (rc, out, err)
    manager_a, _started = _parse_manager_start_output(out)
    weft_harness.register_manager_tid(manager_a)

    rc, out, err = run_cli(
        "run",
        "--no-wait",
        "--context",
        context_root,
        sys.executable,
        "-c",
        "import time; time.sleep(120)",
        cwd=context_root,
        harness=weft_harness,
    )
    assert rc == 0, (rc, out, err)
    task_tid = out.strip()
    weft_harness.register_tid(task_tid)
    snapshot = _wait_on_queues(
        context,
        [task_state_queue_name(task_tid)],
        lambda: _running_snapshot(context, task_tid),
        message="task running",
    )
    task_pids = _snapshot_pids(snapshot)
    assert task_pids

    psutil.Process(_manager_pid(weft_harness, manager_a)).kill()

    rc, out, err = run_cli(
        "manager",
        "start",
        "--context",
        context_root,
        cwd=context_root,
        harness=weft_harness,
    )
    assert rc == 0, (rc, out, err)
    manager_b, started = _parse_manager_start_output(out)
    assert started and manager_b != manager_a
    weft_harness.register_manager_tid(manager_b)
    _wait_on_queues(
        context,
        [task_state_queue_name(manager_b)],
        lambda: task_tid in _checkpoint_residents(context, manager_b),
        message="Manager B adopting the detached task",
    )
    assert _running_snapshot(context, task_tid) is not None
    ctrl_in = f"T{task_tid}.ctrl_in"
    with context.broker() as broker:
        assert ctrl_in not in set(broker.list_queues())

    rc, out, err = run_cli(
        "manager",
        "stop",
        manager_b,
        "--context",
        context_root,
        cwd=context_root,
        harness=weft_harness,
    )
    assert rc == 0, (rc, out, err)

    weft_harness.wait_for_terminal_state(task_tid, expected_status="cancelled")
    control_stop = _first_log_id(context, task_tid, {"control_stop"})
    drained = _first_log_id(context, manager_b, {"manager_stop_drained"})
    terminal = _first_terminal_log_id(context, task_tid)
    assert control_stop is not None, "task never handled a delivered STOP"
    assert drained is not None
    assert terminal is not None and terminal < drained
    _wait_until(
        lambda: not any(_pid_running(pid) for pid in task_pids),
        timeout=30.0,
        message="task process exit",
    )


def _non_canonical_manager_spec(context: WeftContext, tid: str, inbox: str) -> TaskSpec:
    payload = build_manager_spec(context, tid, idle_timeout_override=0.0).model_dump(
        mode="json"
    )
    payload["io"]["inputs"]["inbox"] = inbox
    return TaskSpec.model_validate(payload, context={"auto_expand": False})


def test_manager_stop_reaches_task_under_another_live_manager(
    weft_harness: WeftTestHarness,
) -> None:
    """A STOP-drain member another live Manager covers is stopped, not adopted.

    Spec: docs/specifications/03-Manager_Architecture.md [MA-3] (Reading B,
    sub-question (a)). Two non-canonical Managers share one context; neither
    yields leadership or runs services. Stopping B reaches A's task through
    its resolved route; A keeps custody, reaps it, and stays alive.
    """

    psutil = pytest.importorskip("psutil")
    context = weft_harness.context
    suffix = generate_tid(context)
    managers: dict[str, tuple[str, str]] = {}
    processes: dict[str, Any] = {}
    for label in ("a", "b"):
        inbox = f"test.stop-cover.{suffix}.{label}.requests"
        with context.queue(inbox, persistent=True) as queue:
            queue.write(json.dumps({"name": f"cover-{label}", "spec": _LONG_WORK_SPEC}))
            peeked = queue.peek_one(with_timestamps=True)
        assert isinstance(peeked, tuple)
        task_tid = str(peeked[1])
        weft_harness.register_tid(task_tid)
        manager_tid = generate_tid(context)
        process = launch_task_process(
            Manager,
            context.broker_target,
            _non_canonical_manager_spec(context, manager_tid, inbox),
            config=context.config,
        )
        assert process.pid is not None
        weft_harness.register_manager_tid(manager_tid)
        weft_harness.register_pid(process.pid, manager_tid)
        managers[label] = (manager_tid, task_tid)
        processes[label] = process

    snapshots: dict[str, dict[str, Any]] = {}
    for label, (manager_tid, task_tid) in managers.items():
        snapshots[label] = _wait_on_queues(
            context,
            [task_state_queue_name(task_tid)],
            lambda tid=task_tid: _running_snapshot(context, tid),
            message=f"task {label} running",
        )
        _wait_on_queues(
            context,
            [task_state_queue_name(manager_tid)],
            lambda m=manager_tid, tid=task_tid: (
                tid in _checkpoint_residents(context, m)
            ),
            message=f"Manager {label} checkpoint listing its task",
        )
    manager_a, task_a = managers["a"]
    manager_b, task_b = managers["b"]

    rc, out, err = run_cli(
        "manager",
        "stop",
        manager_b,
        "--context",
        weft_harness.root,
        cwd=weft_harness.root,
        harness=weft_harness,
    )
    assert rc == 0, (rc, out, err)

    weft_harness.wait_for_terminal_state(task_a, expected_status="cancelled")
    weft_harness.wait_for_terminal_state(task_b, expected_status="cancelled")
    assert _first_log_id(context, task_a, {"control_stop"}) is not None
    terminal_a = _first_terminal_log_id(context, task_a)
    terminal_b = _first_terminal_log_id(context, task_b)
    drained_b = _first_log_id(context, manager_b, {"manager_stop_drained"})
    assert terminal_a is not None and terminal_b is not None
    assert drained_b is not None
    assert terminal_a < drained_b and terminal_b < drained_b
    # PID absence, not "not running": A must have joined (reaped) its child.
    _wait_until(
        lambda: (
            not any(psutil.pid_exists(pid) for pid in _snapshot_pids(snapshots["a"]))
            and task_a not in _checkpoint_residents(context, manager_a)
        ),
        timeout=30.0,
        message="Manager A reaping its stopped task",
    )
    assert processes["a"].is_alive()
    rc, out, err = run_cli(
        "manager",
        "status",
        manager_a,
        "--json",
        "--context",
        weft_harness.root,
        cwd=weft_harness.root,
        harness=weft_harness,
    )
    assert rc == 0, (rc, out, err)
    assert json.loads(out).get("status") == "active"

    rc, out, err = run_cli(
        "manager",
        "stop",
        manager_a,
        "--context",
        weft_harness.root,
        cwd=weft_harness.root,
        harness=weft_harness,
    )
    assert rc == 0, (rc, out, err)
    processes["a"].join(timeout=30.0)
    processes["b"].join(timeout=30.0)


def _start_covering_manager(
    weft_harness: WeftTestHarness, label: str, suffix: str
) -> tuple[str, str, Any]:
    """Start one non-canonical Manager over its own inbox holding one task."""

    context = weft_harness.context
    inbox = f"test.stop-cover-dead.{suffix}.{label}.requests"
    with context.queue(inbox, persistent=True) as queue:
        queue.write(json.dumps({"name": f"cover-{label}", "spec": _LONG_WORK_SPEC}))
        peeked = queue.peek_one(with_timestamps=True)
    assert isinstance(peeked, tuple)
    task_tid = str(peeked[1])
    weft_harness.register_tid(task_tid)
    manager_tid = generate_tid(context)
    process = launch_task_process(
        Manager,
        context.broker_target,
        _non_canonical_manager_spec(context, manager_tid, inbox),
        config=context.config,
    )
    assert process.pid is not None
    weft_harness.register_manager_tid(manager_tid)
    weft_harness.register_pid(process.pid, manager_tid)
    return manager_tid, task_tid, process


def test_manager_stop_reaches_task_of_peer_that_died_after_bootstrap(
    weft_harness: WeftTestHarness,
) -> None:
    """A system STOP reaches a live task whose Manager died after B started.

    Spec: docs/specifications/03-Manager_Architecture.md [MA-3]. B bootstraps
    while A is alive and lists its task, so B marks the task covered and does
    not adopt it. A then dies and its task keeps running. B's STOP must reach
    that task through the task's own snapshot, not through A's checkpoint or
    B's residents. The task's own ``control_stop`` is the delivery oracle (a
    host SIGTERM rescue would publish ``task_signal_stop`` instead).
    """

    psutil = pytest.importorskip("psutil")
    context = weft_harness.context
    suffix = generate_tid(context)
    manager_a, task_a, process_a = _start_covering_manager(weft_harness, "a", suffix)
    snapshot_a = _wait_on_queues(
        context,
        [task_state_queue_name(task_a)],
        lambda: _running_snapshot(context, task_a),
        message="task a running",
    )
    _wait_on_queues(
        context,
        [task_state_queue_name(manager_a)],
        lambda: task_a in _checkpoint_residents(context, manager_a),
        message="Manager A checkpoint listing its task",
    )
    for pid in _snapshot_pids(snapshot_a):
        weft_harness.register_pid(pid, task_a)
    manager_b, task_b, _process_b = _start_covering_manager(weft_harness, "b", suffix)
    _wait_on_queues(
        context,
        [task_state_queue_name(task_b)],
        lambda: _running_snapshot(context, task_b),
        message="task b running",
    )
    _wait_on_queues(
        context,
        [task_state_queue_name(manager_b)],
        lambda: task_b in _checkpoint_residents(context, manager_b),
        message="Manager B checkpoint listing its task",
    )
    # B bootstrapped with A alive: A's task is covered, not adopted.
    assert task_a not in _checkpoint_residents(context, manager_b)

    psutil.Process(process_a.pid).kill()
    process_a.join(timeout=30.0)
    assert not process_a.is_alive()
    # B's live verdict about A from a probe before A died is reused for
    # MANAGER_ADMISSION_RECHECK_SECONDS; past it, A is dead to B and nothing
    # but the task's own snapshot can name the task.
    time.sleep(2 * MANAGER_ADMISSION_RECHECK_SECONDS)
    assert any(psutil.pid_exists(pid) for pid in _snapshot_pids(snapshot_a))
    assert _first_log_id(context, task_a, {"control_stop"}) is None

    rc, out, err = run_cli(
        "manager",
        "stop",
        manager_b,
        "--context",
        weft_harness.root,
        cwd=weft_harness.root,
        harness=weft_harness,
    )
    assert rc == 0, (rc, out, err)
    weft_harness.wait_for_terminal_state(task_b, expected_status="cancelled")
    assert _first_log_id(context, manager_b, {"manager_stop_drained"}) is not None
    try:
        _wait_until(
            lambda: _first_log_id(context, task_a, {"control_stop"}) is not None,
            timeout=20.0,
            message="B's STOP reaching the dead Manager's task",
        )
    except AssertionError as failure:
        # The yielded harness fixture otherwise resumes without this exception,
        # so preserve the live evidence before its normal teardown removes it.
        weft_harness._attach_failure_diagnostics(failure)
        try:
            facts: dict[str, Any] = {
                "task_a_latest": read_task_state_snapshot(context, task_a),
                "task_b_latest": read_task_state_snapshot(context, task_b),
                "manager_b_latest": read_task_state_snapshot(context, manager_b),
                "task_a_first_terminal": _first_terminal_log_id(context, task_a),
                "manager_b_drained": _first_log_id(
                    context, manager_b, {"manager_stop_drained"}
                ),
                "control_queues": {},
            }
            for tid in (task_a, task_b, manager_b):
                name = f"T{tid}.ctrl_in"
                with context.queue(name, persistent=True) as queue:
                    facts["control_queues"][name] = queue.peek_many(
                        limit=10, with_timestamps=True, include_claimed=True
                    )
            failure.add_note(
                "STOP delivery evidence: " + json.dumps(facts, sort_keys=True)
            )
        except (
            BrokerError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as diagnostic_error:
            failure.add_note(
                f"STOP delivery diagnostics unavailable: {diagnostic_error}"
            )
        raise
    weft_harness.wait_for_terminal_state(task_a, expected_status="cancelled")


@pytest.mark.skipif(os.name == "nt", reason="POSIX only")
def test_manager_start_detaches_manager_process_group_from_cli_caller(
    weft_harness: WeftTestHarness,
) -> None:
    context_root = weft_harness.root
    tid: str | None = None
    started_by_test = False

    try:
        rc, out, err = run_cli(
            "manager",
            "start",
            "--context",
            context_root,
            cwd=context_root,
            harness=weft_harness,
        )
        assert rc == 0, (rc, out, err)
        assert err == ""
        tid, started_by_test = _parse_manager_start_output(out)

        pid: int | None = None
        deadline = time.time() + 10.0
        while time.time() < deadline and pid is None:
            rc, out, err = run_cli(
                "manager",
                "status",
                tid,
                "--json",
                "--context",
                context_root,
                cwd=context_root,
                harness=weft_harness,
            )
            assert rc == 0, (rc, out, err)
            assert err == ""
            pid = _host_pid_from_handle(json.loads(out))
            if pid is None:
                time.sleep(0.05)

        assert isinstance(pid, int)
        assert os.getpgid(pid) == pid

        rc, out, err = run_cli(
            "manager",
            "status",
            tid,
            "--json",
            "--context",
            context_root,
            cwd=context_root,
            harness=weft_harness,
        )
        assert rc == 0, (rc, out, err)
        assert err == ""
        detail = json.loads(out)
        assert detail.get("status") == "active"
        assert _host_pid_from_handle(detail) == pid
    finally:
        if tid is not None and started_by_test:
            run_cli(
                "manager",
                "stop",
                tid,
                "--context",
                context_root,
                cwd=context_root,
                harness=weft_harness,
            )


def test_manager_stop_missing_tid(workdir: Path) -> None:
    context_root = prepare_project_root(workdir / "missing-manager")
    build_context(spec_context=context_root)

    rc, out, err = run_cli(
        "manager",
        "stop",
        "999",
        "--timeout",
        "0.1",
        "--context",
        context_root,
        cwd=workdir,
    )
    assert rc == 1
    combined = f"{out}\n{err}".lower()
    assert "did not stop" in combined or "not found" in combined


def test_manager_stop_without_tid_noops_when_no_manager(workdir: Path) -> None:
    context_root = prepare_project_root(workdir / "stop-active-empty")
    build_context(spec_context=context_root)

    rc, out, err = run_cli(
        "manager",
        "stop",
        "--context",
        context_root,
        cwd=workdir,
    )

    assert rc == 0
    assert out == ""
    assert err == ""


def test_manager_list_empty(workdir: Path) -> None:
    context_root = prepare_project_root(workdir / "empty-manager")
    build_context(spec_context=context_root)

    rc, out, err = run_cli(
        "manager",
        "list",
        "--context",
        context_root,
        cwd=workdir,
    )
    assert rc == 0
    assert "No registered managers" in out
    assert err == ""


@pytest.mark.skipif(os.name == "nt", reason="POSIX only")
def test_manager_list_and_status_agree_on_stale_active_manager(workdir: Path) -> None:
    context_root = prepare_project_root(workdir / "stale-manager")
    context = build_context(spec_context=context_root)
    tid = "1761000000000000007"

    process = subprocess.Popen([sys.executable, "-c", "import os; os._exit(0)"])
    try:
        process.wait(timeout=2.0)
        registry_queue = context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
        registry_queue.write(
            json.dumps(
                {
                    "tid": tid,
                    "status": "active",
                    "name": "stale-manager",
                    "runtime_handle": _host_runtime_handle(process.pid),
                    "role": "manager",
                    "requests": WEFT_SPAWN_REQUESTS_QUEUE,
                }
            )
        )
    finally:
        process.wait()

    rc, out, err = run_cli(
        "manager",
        "list",
        "--json",
        "--context",
        context_root,
        cwd=workdir,
    )
    assert rc == 0
    assert err == ""
    manager_records = json.loads(out or "[]")
    assert tid not in {record["tid"] for record in manager_records}

    rc, out, err = run_cli(
        "status",
        "--json",
        "--context",
        context_root,
        cwd=workdir,
    )
    assert rc == 0
    assert err == ""
    status_payload = json.loads(out or "{}")
    assert tid not in {record["tid"] for record in status_payload["managers"]}


@pytest.mark.skipif(os.name == "nt", reason="POSIX only")
def test_manager_list_diagnostic_shows_stale_active_manager(workdir: Path) -> None:
    context_root = prepare_project_root(workdir / "diagnostic-stale-manager")
    context = build_context(spec_context=context_root)
    tid = "1761000000000000021"

    process = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
    )
    try:
        create_time = process_create_time(process.pid)
        assert create_time is not None
        process.communicate(timeout=2.0)
        registry_queue = context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
        try:
            registry_queue.write(
                json.dumps(
                    _manager_service_payload(
                        context,
                        tid=tid,
                        name="stale-manager",
                        runtime_handle=_host_runtime_handle(process.pid, create_time),
                    )
                )
            )
        finally:
            registry_queue.close()
    finally:
        process.wait()

    rc, out, err = run_cli(
        "manager",
        "list",
        "--diagnostic",
        "--json",
        "--context",
        context_root,
        cwd=workdir,
    )

    assert rc == 0
    assert err == ""
    records = json.loads(out or "[]")
    record = next(record for record in records if record["tid"] == tid)
    assert record["liveness"] == "stale"
    assert record["proof_source"] == "host-pid"
    assert record["canonical"] is False


@pytest.mark.skipif(os.name == "nt", reason="POSIX only")
def test_manager_start_skips_stale_manager_and_preserves_history(
    weft_harness: WeftTestHarness,
) -> None:
    context_root = weft_harness.root
    context = weft_harness.context
    stale_tid = "1761000000000000008"
    started_tid: str | None = None

    process = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
    )
    try:
        create_time = process_create_time(process.pid)
        assert create_time is not None
        process.communicate(timeout=2.0)
        registry_queue = context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
        try:
            registry_queue.write(
                json.dumps(
                    _manager_service_payload(
                        context,
                        tid=stale_tid,
                        name="stale-manager",
                        runtime_handle=_host_runtime_handle(process.pid, create_time),
                    )
                )
            )
        finally:
            registry_queue.close()
    finally:
        process.wait()

    try:
        rc, out, err = run_cli(
            "manager",
            "start",
            "--context",
            context_root,
            cwd=context_root,
            harness=weft_harness,
        )
        assert rc == 0, (rc, out, err)
        assert err == ""

        started_tid, _started_by_test = _parse_manager_start_output(out)
        assert started_tid != stale_tid

        rc, out, err = run_cli(
            "manager",
            "list",
            "--json",
            "--context",
            context_root,
            cwd=context_root,
            harness=weft_harness,
        )
        assert rc == 0, (rc, out, err)
        assert err == ""
        manager_records = json.loads(out or "[]")
        active = [
            record for record in manager_records if record.get("status") == "active"
        ]
        assert {record["tid"] for record in active} == {started_tid}

        registry_reader = context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
        try:
            payloads = [
                json.loads(item)
                for item, _timestamp in registry_reader.peek_generator(
                    with_timestamps=True
                )
            ]
        finally:
            registry_reader.close()

        assert any(
            record.get("owner_tid") == stale_tid
            and _host_pid_from_handle(record) == process.pid
            for record in payloads
        )  # Registry readers filter stale rows; they do not delete foreign history.
    finally:
        if started_tid is not None:
            run_cli(
                "manager",
                "stop",
                started_tid,
                "--context",
                context_root,
                cwd=context_root,
                harness=weft_harness,
            )


def test_manager_status_missing(workdir: Path) -> None:
    context_root = prepare_project_root(workdir / "status-manager")
    build_context(spec_context=context_root)

    rc, out, err = run_cli(
        "manager",
        "status",
        "999",
        "--context",
        context_root,
        cwd=workdir,
    )
    assert rc == 1
    combined = f"{out}\n{err}"
    assert "not found" in combined.lower()


def test_manager_force_stop_without_identity_remains_unconfirmed(workdir: Path) -> None:
    context_root = prepare_project_root(workdir / "force-manager")
    context = build_context(spec_context=context_root)
    tid = "1761000000000000001"

    mapping_queue = context.queue(task_state_queue_name(tid), persistent=False)
    mapping_queue.write(
        json.dumps(
            {
                "full": tid,
                "short": tid[-10:],
                "runtime_handle": _host_runtime_handle(999_999),
            }
        )
    )

    rc, out, err = run_cli(
        "manager",
        "stop",
        tid,
        "--timeout",
        "0",
        "--force",
        "--context",
        context_root,
        cwd=workdir,
    )
    assert rc == 1
    assert out == ""
    assert err.strip() == (
        f"Manager {tid} stop is unconfirmed; no host PID is available for --force"
    )
    assert mapping_queue.peek_one() is not None
    mapping_queue.close()
