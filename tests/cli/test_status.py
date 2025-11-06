"""CLI status command tests."""

from __future__ import annotations

import json
import time
from typing import Any

from simplebroker import Queue
from tests.conftest import run_cli
from weft._constants import (
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    WEFT_WORKERS_REGISTRY_QUEUE,
)
from weft.context import build_context


def _write_log_event(context, payload: dict[str, Any]) -> None:
    queue = Queue(
        WEFT_GLOBAL_LOG_QUEUE,
        db_path=str(context.database_path),
        persistent=False,
        config=context.broker_config,
    )
    queue.write(json.dumps(payload))


def test_status_reports_no_managers(workdir) -> None:
    rc, out, err = run_cli("status", cwd=workdir)

    assert rc == 0
    assert "Managers: none registered" in out
    assert "Tasks: none" in out
    assert err == ""


def test_status_json_includes_manager_records(workdir) -> None:
    context = build_context(spec_context=workdir)
    registry = Queue(
        WEFT_WORKERS_REGISTRY_QUEUE,
        db_path=str(context.database_path),
        persistent=False,
        config=context.broker_config,
    )

    record = {
        "tid": "1762000000000000999",
        "name": "cli-manager",
        "status": "active",
        "pid": 12345,
        "role": "manager",
        "requests": WEFT_SPAWN_REQUESTS_QUEUE,
        "timestamp": 1762000000000001999,
    }

    registry.write(json.dumps(record))

    try:
        rc, out, err = run_cli("status", "--json", cwd=workdir)

        assert rc == 0
        assert err == ""

        payload = json.loads(out)
        managers = payload["managers"]
        assert any(item.get("tid") == record["tid"] for item in managers)
        entry = next(item for item in managers if item.get("tid") == record["tid"])
        assert entry["requests"] == WEFT_SPAWN_REQUESTS_QUEUE
        assert entry["pid"] == record["pid"]
        assert payload["tasks"] == []
    finally:
        registry.read_many(limit=100)


def test_status_filters_stopped_managers_by_default(workdir) -> None:
    context = build_context(spec_context=workdir)
    registry = Queue(
        WEFT_WORKERS_REGISTRY_QUEUE,
        db_path=str(context.database_path),
        persistent=False,
        config=context.broker_config,
    )

    stopped_record = {
        "tid": "1762000000000000123",
        "name": "old-manager",
        "status": "stopped",
        "pid": 11111,
        "role": "manager",
        "requests": WEFT_SPAWN_REQUESTS_QUEUE,
        "timestamp": 1762000000000002222,
    }

    registry.write(json.dumps(stopped_record))

    try:
        rc, out, err = run_cli("status", cwd=workdir)

        assert rc == 0
        assert stopped_record["tid"] not in out

        rc, out, err = run_cli("status", "--all", cwd=workdir)

        assert rc == 0
        assert stopped_record["tid"] in out
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


def test_status_tid_filter_not_found(workdir) -> None:
    rc, out, err = run_cli("status", "nonexistent", cwd=workdir)

    assert rc == 2
    assert out == ""
    assert err.strip().startswith("weft: task nonexistent not found")
