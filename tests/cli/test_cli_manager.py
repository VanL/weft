"""CLI coverage for `weft manager` subcommands."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Literal

import pytest

from tests.conftest import run_cli
from tests.helpers.test_backend import prepare_project_root
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import (
    INTERNAL_SERVICE_KEYS,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_MANAGER_OUTBOX_QUEUE,
    WEFT_SERVICES_REGISTRY_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
)
from weft.context import WeftContext, build_context
from weft.core.launcher import launch_task_process
from weft.core.manager import Manager
from weft.core.manager_runtime import build_manager_spec, generate_tid
from weft.core.service_convergence import build_manager_service_payload
from weft.core.task_state import read_task_state_snapshot, task_state_queue_name
from weft.core.taskspec import TaskSpec
from weft.helpers import iter_queue_json_entries, process_create_time

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


def _internal_service_tids(context: WeftContext) -> set[str]:
    """Name the live internal services from the service registry rows."""

    return {
        payload["owner_tid"]
        for payload, _timestamp in _queue_rows(context, WEFT_SERVICES_REGISTRY_QUEUE)
        if payload.get("service_key") in INTERNAL_SERVICE_KEYS
        and isinstance(payload.get("owner_tid"), str)
    }


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


def test_manager_stop_orders_user_work_before_internal_services(
    weft_harness: WeftTestHarness, tmp_path: Path
) -> None:
    """``weft manager stop`` drains user work, then services, then itself.

    Spec: docs/specifications/03-Manager_Architecture.md [MA-3]. The user
    task ignores SIGTERM and is ready (its handler installed) before STOP,
    so a simultaneous broadcast could not produce this order by chance.
    Order is read from task-owned terminal snapshots (broker message IDs):
    TaskMonitor collates and retires internal-service rows from
    ``weft.log.tasks``, so the raw log is not a stable oracle for services.
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
    services: set[str] = set()
    while time.monotonic() < deadline:
        services = _internal_service_tids(context)
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
    drained = [
        timestamp
        for payload, timestamp in _queue_rows(context, WEFT_GLOBAL_LOG_QUEUE)
        if payload.get("tid") == manager_tid
        and payload.get("event") == "manager_stop_drained"
    ]
    assert drained
    for service_tid in services:
        service_terminal = _first_terminal_snapshot(context, service_tid)
        assert service_terminal is not None, f"service {service_tid} not terminal"
        assert service_terminal[1]["projection"]["event"] == "control_stop"
        assert user_terminal[0] < service_terminal[0] < drained[0]
        assert service_terminal[0] < manager_terminal[0]


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
    snapshot = _wait_until(
        lambda: _running_snapshot(context, task_tid), message="task running"
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
    _wait_until(
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
    assert control_stop is not None, "task never handled a delivered STOP"
    assert drained is not None
    assert control_stop < drained
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
        snapshots[label] = _wait_until(
            lambda tid=task_tid: _running_snapshot(context, tid),
            message=f"task {label} running",
        )
        _wait_until(
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
    terminal_a = _first_log_id(context, task_a, {"control_stop"})
    terminal_b = _first_log_id(context, task_b, {"control_stop", "task_signal_stop"})
    drained_b = _first_log_id(context, manager_b, {"manager_stop_drained"})
    assert terminal_a is not None and terminal_b is not None
    assert drained_b is not None
    assert terminal_a < drained_b and terminal_b < drained_b
    _wait_until(
        lambda: (
            not any(_pid_running(pid) for pid in _snapshot_pids(snapshots["a"]))
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
