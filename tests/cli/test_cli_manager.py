"""CLI coverage for `weft manager` subcommands."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from typing import Any

import pytest

from tests.conftest import run_cli
from tests.helpers.test_backend import prepare_project_root
from weft._constants import (
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_MANAGERS_REGISTRY_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
)
from weft.context import build_context
from weft.helpers import iter_queue_json_entries

pytestmark = [pytest.mark.shared]


def _host_runtime_handle(pid: int) -> dict[str, Any]:
    return {
        "runner": "host",
        "kind": "process",
        "id": str(pid),
        "control": {"authority": "host-pid"},
        "observations": {"host_pids": [pid]},
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


def _parse_started_manager(output: str) -> str:
    match = re.search(r"Started manager (\d+)", output)
    if match is None:
        raise AssertionError(f"Unable to parse manager start output: {output!r}")
    return match.group(1)


def _task_log_payloads(context, tid: str) -> list[dict[str, Any]]:
    queue = context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    try:
        payloads: list[dict[str, Any]] = []
        for payload, _timestamp in iter_queue_json_entries(queue):
            if payload.get("tid") == tid:
                payloads.append(payload)
        return payloads
    finally:
        queue.close()


def test_manager_start_and_status(workdir):
    context_root = prepare_project_root(workdir / "manager-project")
    build_context(spec_context=context_root)

    rc, out, err = run_cli(
        "manager",
        "start",
        "--context",
        context_root,
        cwd=workdir,
    )
    assert rc == 0
    assert "manager" in out.lower()
    assert err == ""

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
    records = json.loads(out or "[]")
    active = [record for record in records if record.get("status") == "active"]
    assert active
    tid = active[0]["tid"]

    rc, out, err = run_cli(
        "manager",
        "start",
        "--context",
        context_root,
        cwd=workdir,
    )
    assert rc == 0
    assert "already running" in out.lower()
    assert err == ""

    rc, out, err = run_cli(
        "manager",
        "status",
        tid,
        "--context",
        context_root,
        "--json",
        cwd=workdir,
    )
    assert rc == 0
    assert err == ""
    detail = json.loads(out)
    assert detail.get("tid") == tid
    assert detail.get("status") == "active"

    rc, out, err = run_cli(
        "manager",
        "stop",
        tid,
        "--context",
        context_root,
        cwd=workdir,
    )
    assert rc == 0
    assert err == ""

    rc, out, err = run_cli(
        "manager",
        "status",
        tid,
        "--context",
        context_root,
        "--json",
        cwd=workdir,
    )
    assert rc == 0
    assert err == ""
    detail = json.loads(out)
    assert detail.get("status") == "stopped"


@pytest.mark.skipif(os.name == "nt", reason="POSIX only")
def test_manager_start_detaches_manager_process_group_from_cli_caller(workdir):
    context_root = prepare_project_root(workdir / "detached-manager")
    tid: str | None = None

    try:
        rc, out, err = run_cli(
            "manager",
            "start",
            "--context",
            context_root,
            cwd=workdir,
        )
        assert rc == 0
        assert err == ""
        tid = _parse_started_manager(out)

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
                cwd=workdir,
            )
            assert rc == 0
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
            cwd=workdir,
        )
        assert rc == 0
        assert err == ""
        detail = json.loads(out)
        assert detail.get("status") == "active"
        assert _host_pid_from_handle(detail) == pid
    finally:
        if tid is not None:
            run_cli(
                "manager",
                "stop",
                tid,
                "--context",
                context_root,
                cwd=workdir,
            )


def test_manager_stop_missing_tid(workdir):
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


def test_manager_list_empty(workdir):
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
def test_manager_list_and_status_agree_on_stale_active_manager(workdir):
    context_root = prepare_project_root(workdir / "stale-manager")
    context = build_context(spec_context=context_root)
    tid = "1761000000000000007"

    process = subprocess.Popen([sys.executable, "-c", "import os; os._exit(0)"])
    try:
        process.wait(timeout=2.0)
        registry_queue = context.queue(WEFT_MANAGERS_REGISTRY_QUEUE, persistent=False)
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
def test_manager_start_replaces_stale_active_manager(workdir):
    context_root = prepare_project_root(workdir / "stale-manager-start")
    context = build_context(spec_context=context_root)
    stale_tid = "1761000000000000008"
    started_tid: str | None = None

    process = subprocess.Popen([sys.executable, "-c", "import os; os._exit(0)"])
    try:
        process.wait(timeout=2.0)
        registry_queue = context.queue(WEFT_MANAGERS_REGISTRY_QUEUE, persistent=False)
        registry_queue.write(
            json.dumps(
                {
                    "tid": stale_tid,
                    "status": "active",
                    "name": "stale-manager",
                    "runtime_handle": _host_runtime_handle(process.pid),
                    "role": "manager",
                    "requests": WEFT_SPAWN_REQUESTS_QUEUE,
                    "ctrl_in": f"T{stale_tid}.ctrl_in",
                    "ctrl_out": f"T{stale_tid}.ctrl_out",
                    "outbox": "weft.manager.outbox",
                }
            )
        )
    finally:
        process.wait()

    try:
        rc, out, err = run_cli(
            "manager",
            "start",
            "--context",
            context_root,
            cwd=workdir,
        )
        assert rc == 0
        assert err == ""

        started_tid = _parse_started_manager(out)
        assert started_tid != stale_tid

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
        active = [
            record for record in manager_records if record.get("status") == "active"
        ]
        assert {record["tid"] for record in active} == {started_tid}

        registry_reader = context.queue(WEFT_MANAGERS_REGISTRY_QUEUE, persistent=False)
        try:
            payloads = [
                json.loads(item)
                for item, _timestamp in registry_reader.peek_many(
                    limit=100, with_timestamps=True
                )
            ]
        finally:
            registry_reader.close()

        assert all(_host_pid_from_handle(record) != process.pid for record in payloads)
    finally:
        if started_tid is not None:
            run_cli(
                "manager",
                "stop",
                started_tid,
                "--context",
                context_root,
                cwd=workdir,
            )


def test_manager_status_missing(workdir):
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


def test_manager_force_stop_missing_pid_record(workdir):
    context_root = prepare_project_root(workdir / "force-manager")
    context = build_context(spec_context=context_root)
    tid = "1761000000000000001"

    mapping_queue = context.queue(WEFT_TID_MAPPINGS_QUEUE, persistent=False)
    mapping_queue.write(
        json.dumps({"full": tid, "runtime_handle": _host_runtime_handle(999_999)})
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
    assert rc == 0
    assert out == ""
    assert err == ""
