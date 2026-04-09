"""CLI coverage for `weft worker` subcommands."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from tests.conftest import run_cli
from tests.helpers.test_backend import prepare_project_root
from weft._constants import (
    WEFT_SPAWN_REQUESTS_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
    WEFT_WORKERS_REGISTRY_QUEUE,
)
from weft.context import build_context

pytestmark = [pytest.mark.shared]


def test_worker_start_and_status(workdir):
    context_root = prepare_project_root(workdir / "worker-project")
    build_context(spec_context=context_root)

    rc, out, err = run_cli(
        "worker",
        "start",
        "--context",
        context_root,
        cwd=workdir,
    )
    assert rc == 0
    assert "manager" in out.lower()
    assert err == ""

    rc, out, err = run_cli(
        "worker",
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
        "worker",
        "start",
        "--context",
        context_root,
        cwd=workdir,
    )
    assert rc == 0
    assert "already running" in out.lower()
    assert err == ""

    rc, out, err = run_cli(
        "worker",
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
        "worker",
        "stop",
        tid,
        "--context",
        context_root,
        cwd=workdir,
    )
    assert rc == 0
    assert err == ""

    rc, out, err = run_cli(
        "worker",
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


def test_worker_stop_missing_tid(workdir):
    context_root = prepare_project_root(workdir / "missing-worker")
    build_context(spec_context=context_root)

    rc, out, err = run_cli(
        "worker",
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


def test_worker_list_empty(workdir):
    context_root = prepare_project_root(workdir / "empty-worker")
    build_context(spec_context=context_root)

    rc, out, err = run_cli(
        "worker",
        "list",
        "--context",
        context_root,
        cwd=workdir,
    )
    assert rc == 0
    assert "No registered workers" in out
    assert err == ""


@pytest.mark.skipif(os.name == "nt", reason="POSIX only")
def test_worker_list_and_status_agree_on_stale_active_manager(workdir):
    context_root = prepare_project_root(workdir / "stale-worker")
    context = build_context(spec_context=context_root)
    tid = "1761000000000000007"

    process = subprocess.Popen([sys.executable, "-c", "import os; os._exit(0)"])
    try:
        process.wait(timeout=2.0)
        registry_queue = context.queue(WEFT_WORKERS_REGISTRY_QUEUE, persistent=False)
        registry_queue.write(
            json.dumps(
                {
                    "tid": tid,
                    "status": "active",
                    "name": "stale-manager",
                    "pid": process.pid,
                    "role": "manager",
                    "requests": WEFT_SPAWN_REQUESTS_QUEUE,
                }
            )
        )
    finally:
        process.wait()

    rc, out, err = run_cli(
        "worker",
        "list",
        "--json",
        "--context",
        context_root,
        cwd=workdir,
    )
    assert rc == 0
    assert err == ""
    worker_records = json.loads(out or "[]")
    assert tid not in {record["tid"] for record in worker_records}

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


def test_worker_status_missing(workdir):
    context_root = prepare_project_root(workdir / "status-worker")
    build_context(spec_context=context_root)

    rc, out, err = run_cli(
        "worker",
        "status",
        "999",
        "--context",
        context_root,
        cwd=workdir,
    )
    assert rc == 1
    combined = f"{out}\n{err}"
    assert "not found" in combined.lower()


def test_worker_force_stop_missing_pid_record(workdir):
    context_root = prepare_project_root(workdir / "force-worker")
    context = build_context(spec_context=context_root)
    tid = "1761000000000000001"

    mapping_queue = context.queue(WEFT_TID_MAPPINGS_QUEUE, persistent=False)
    mapping_queue.write(json.dumps({"full": tid, "pid": 999_999}))

    rc, out, err = run_cli(
        "worker",
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
