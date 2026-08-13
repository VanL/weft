"""CLI status command tests."""

from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import sys
import time
from typing import Any

import pytest

from tests.conftest import run_cli
from weft._constants import (
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_MANAGER_OUTBOX_QUEUE,
    WEFT_SERVICES_REGISTRY_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
)
from weft.context import build_context
from weft.core.service_convergence import build_manager_service_payload

pytestmark = [pytest.mark.shared]


def _write_log_event(context, payload: dict[str, Any]) -> None:
    queue = context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    queue.write(json.dumps(payload))


def _host_runtime_handle(pid: int) -> dict[str, Any]:
    return {
        "runner": "host",
        "kind": "process",
        "id": str(pid),
        "control": {"authority": "host-pid"},
        "observations": {"host_pids": [pid]},
        "metadata": {},
    }


@pytest.mark.skipif(os.name == "nt", reason="POSIX SIGINT semantics")
def test_task_status_watch_sigint_exits_cleanly(workdir) -> None:
    tid = "1777000000000000888"
    context = build_context(spec_context=workdir)
    started = time.time_ns()
    _write_log_event(
        context,
        {
            "tid": tid,
            "tid_short": tid[-10:],
            "timestamp": started,
            "status": "running",
            "event": "task_started",
            "taskspec": {
                "name": "sigint-task",
                "state": {"status": "running", "started_at": started},
                "io": {
                    "inputs": {"inbox": f"T{tid}.inbox"},
                    "outputs": {"outbox": f"T{tid}.outbox"},
                    "control": {
                        "ctrl_in": f"T{tid}.ctrl_in",
                        "ctrl_out": f"T{tid}.ctrl_out",
                    },
                },
            },
        },
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "weft",
            "task",
            "status",
            tid,
            "--watch",
        ],
        cwd=workdir,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
    )
    try:
        assert process.stdout is not None
        deadline = time.monotonic() + 10
        watch_line = ""
        while time.monotonic() < deadline and "heartbeat" not in watch_line:
            _write_log_event(
                context,
                {
                    "tid": tid,
                    "name": "sigint-task",
                    "status": "running",
                    "event": "heartbeat",
                    "timestamp": time.time_ns(),
                },
            )
            ready, _, _ = select.select([process.stdout], [], [], 0.2)
            if ready:
                watch_line += process.stdout.readline()
        assert "heartbeat" in watch_line, watch_line
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()

    assert process.returncode == 0, (stdout, stderr)
    assert "Traceback" not in stderr
    assert tid in watch_line
    assert "running" in watch_line


def _manager_service_payload(
    context,
    *,
    tid: str,
    status: str = "active",
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


def test_status_reports_no_managers(workdir) -> None:
    rc, out, err = run_cli("status", cwd=workdir)

    assert rc == 0
    assert "Managers: none registered" in out
    assert "Tasks: none" in out
    assert err == ""


def test_status_json_includes_manager_records(workdir) -> None:
    context = build_context(spec_context=workdir)
    registry = context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)

    record = _manager_service_payload(
        context,
        tid="1762000000000000999",
        name="cli-manager",
        runtime_handle=_host_runtime_handle(os.getpid()),
    )

    registry.write(json.dumps(record))

    try:
        rc, out, err = run_cli("status", "--json", cwd=workdir)

        assert rc == 0
        assert err == ""

        payload = json.loads(out)
        managers = payload["managers"]
        assert any(item.get("tid") == record["owner_tid"] for item in managers)
        entry = next(
            item for item in managers if item.get("tid") == record["owner_tid"]
        )
        assert entry["requests"] == WEFT_SPAWN_REQUESTS_QUEUE
        assert entry["runtime_handle"] == record["runtime_handle"]
        assert payload["tasks"] == []
    finally:
        registry.read_many(limit=100)


def test_status_json_excludes_wrong_service_key_manager_records(workdir) -> None:
    context = build_context(spec_context=workdir)
    registry = context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)

    record = _manager_service_payload(
        context,
        tid="1762000000000000888",
        name="custom-manager",
        runtime_handle=_host_runtime_handle(os.getpid()),
        requests="custom.manager.requests",
    )
    record["service_key"] = "manager:custom.manager.requests:test"

    registry.write(json.dumps(record))

    try:
        rc, out, err = run_cli("status", "--json", cwd=workdir)

        assert rc == 0
        assert err == ""

        payload = json.loads(out)
        managers = payload["managers"]
        assert not any(item.get("tid") == record["owner_tid"] for item in managers)
    finally:
        registry.read_many(limit=100)


def test_status_filters_stopped_managers_by_default(workdir) -> None:
    context = build_context(spec_context=workdir)
    registry = context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)

    stopped_record = _manager_service_payload(
        context,
        tid="1762000000000000123",
        name="old-manager",
        status="stopped",
    )

    registry.write(json.dumps(stopped_record))

    try:
        rc, out, _err = run_cli("status", cwd=workdir)

        assert rc == 0
        assert stopped_record["owner_tid"] not in out

        rc, out, _err = run_cli("status", "--all", cwd=workdir)

        assert rc == 0
        assert stopped_record["owner_tid"] in out
    finally:
        registry.read_many(limit=100)


def test_status_reports_running_task_json(workdir) -> None:
    context = build_context(spec_context=workdir)
    tid = "1844674407370955161"
    started = time.time_ns()
    taskspec = {
        "name": "sample-task",
        "state": {
            "status": "running",
            "started_at": started,
            "completed_at": None,
        },
        "io": {
            "inputs": {"inbox": f"T{tid}.inbox"},
            "outputs": {"outbox": f"T{tid}.outbox"},
            "control": {
                "ctrl_in": f"T{tid}.ctrl_in",
                "ctrl_out": f"T{tid}.ctrl_out",
            },
        },
        "metadata": {"owner": "tests"},
    }
    payload = {
        "event": "work_started",
        "status": "running",
        "tid": tid,
        "tid_short": tid[-10:],
        "timestamp": started,
        "taskspec": taskspec,
    }
    _write_log_event(context, payload)

    rc, out, err = run_cli("status", "--json", "--all", cwd=workdir)

    assert rc == 0
    assert err == ""

    data = json.loads(out)
    tasks = data["tasks"]
    assert len(tasks) == 1
    entry = tasks[0]
    assert entry["tid"] == tid
    assert entry["status"] == "running"
    assert entry["metadata"]["owner"] == "tests"


def test_status_json_reports_dead_host_running_task_as_stale_liveness(workdir) -> None:
    context = build_context(spec_context=workdir)
    tid = "1844674407370955169"
    started = time.time_ns()
    taskspec = {
        "name": "dead-host-task",
        "state": {
            "status": "running",
            "started_at": started,
            "completed_at": None,
        },
        "io": {
            "inputs": {"inbox": f"T{tid}.inbox"},
            "outputs": {"outbox": f"T{tid}.outbox"},
            "control": {
                "ctrl_in": f"T{tid}.ctrl_in",
                "ctrl_out": f"T{tid}.ctrl_out",
            },
        },
        "metadata": {"owner": "tests"},
    }
    _write_log_event(
        context,
        {
            "event": "task_started",
            "status": "running",
            "tid": tid,
            "tid_short": tid[-10:],
            "timestamp": started,
            "taskspec": taskspec,
        },
    )
    mapping_queue = context.queue("weft.state.tid_mappings", persistent=False)
    mapping_queue.write(
        json.dumps(
            {
                "short": tid[-10:],
                "full": tid,
                "runner": "host",
                "runtime_handle": _host_runtime_handle(999_999_996),
            }
        )
    )

    rc, out, err = run_cli("status", "--json", cwd=workdir)

    assert rc == 0
    assert err == ""
    data = json.loads(out)
    assert len(data["tasks"]) == 1
    assert data["tasks"][0]["tid"] == tid
    assert data["tasks"][0]["status"] == "running"
    assert data["tasks"][0]["reconciliation"]["classification"] == "stale_liveness"
    assert data["tasks"][0]["reconciliation"]["reason"] == "host_process_not_live"


def test_task_status_process_json_reports_dead_pid_stale_liveness(workdir) -> None:
    context = build_context(spec_context=workdir)
    tid = "1844674407370955170"
    started = time.time_ns()
    taskspec = {
        "name": "dead-host-task",
        "state": {
            "status": "running",
            "started_at": started,
            "completed_at": None,
        },
        "io": {
            "inputs": {"inbox": f"T{tid}.inbox"},
            "outputs": {"outbox": f"T{tid}.outbox"},
            "control": {
                "ctrl_in": f"T{tid}.ctrl_in",
                "ctrl_out": f"T{tid}.ctrl_out",
            },
        },
        "metadata": {"owner": "tests"},
    }
    _write_log_event(
        context,
        {
            "event": "task_started",
            "status": "running",
            "tid": tid,
            "tid_short": tid[-10:],
            "timestamp": started,
            "taskspec": taskspec,
        },
    )
    mapping_queue = context.queue("weft.state.tid_mappings", persistent=False)
    mapping_queue.write(
        json.dumps(
            {
                "short": tid[-10:],
                "full": tid,
                "runner": "host",
                "runtime_handle": {
                    "runner": "host",
                    "kind": "process",
                    "id": "999999995",
                    "control": {"authority": "host-pid"},
                    "observations": {"host_pids": [999_999_995, 999_999_994]},
                    "metadata": {},
                },
            }
        )
    )

    rc, out, err = run_cli("task", "status", tid, "--process", "--json", cwd=workdir)

    assert rc == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["tid"] == tid
    assert payload["status"] == "running"
    assert payload["reconciliation"]["classification"] == "stale_liveness"
    assert payload["reconciliation"]["reason"] == "host_process_not_live"
    assert payload["host_pids"] == [999_999_994, 999_999_995]
    assert payload["managed_pids"] == [999_999_994, 999_999_995]
    assert payload["live_managed_pids"] == []


def test_task_status_process_plain_preserves_activity_waiting_and_live_pids(
    workdir,
) -> None:
    context = build_context(spec_context=workdir)
    tid = "1844674407370955171"
    started = time.time_ns()
    taskspec = {
        "name": "live-host-task",
        "state": {"status": "running", "started_at": started},
        "io": {
            "inputs": {"inbox": f"T{tid}.inbox"},
            "outputs": {"outbox": f"T{tid}.outbox"},
            "control": {
                "ctrl_in": f"T{tid}.ctrl_in",
                "ctrl_out": f"T{tid}.ctrl_out",
            },
        },
        "metadata": {"owner": "tests"},
    }
    _write_log_event(
        context,
        {
            "event": "work_started",
            "status": "running",
            "activity": "waiting",
            "waiting_on": "upstream-result",
            "tid": tid,
            "tid_short": tid[-10:],
            "timestamp": started,
            "taskspec": taskspec,
        },
    )
    mapping_queue = context.queue("weft.state.tid_mappings", persistent=False)
    mapping_queue.write(
        json.dumps(
            {
                "short": tid[-10:],
                "full": tid,
                "runner": "host",
                "runtime_handle": _host_runtime_handle(os.getpid()),
            }
        )
    )

    rc, out, err = run_cli("task", "status", tid, "--process", cwd=workdir)

    assert rc == 0
    assert out.splitlines() == [
        f"{tid} running host live-host-task (work_started)",
        "activity: waiting",
        "waiting_on: upstream-result",
        f"host_pids: [{os.getpid()}] live_host_pids: [{os.getpid()}]",
    ]
    assert err == ""


def test_task_status_not_found(workdir) -> None:
    rc, out, err = run_cli("task", "status", "nonexistent", cwd=workdir)

    assert rc == 2
    assert out == ""
    assert "Task nonexistent not found" in err


def test_task_status_rejects_removed_live_probe_option(workdir) -> None:
    rc, out, err = run_cli(
        "task",
        "status",
        "1778084345905438799",
        "--probe" + "-live",
        cwd=workdir,
    )

    assert rc != 0
    assert out == ""
    assert "probe-live" in err
