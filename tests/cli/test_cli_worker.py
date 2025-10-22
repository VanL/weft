"""CLI coverage for `weft worker` subcommands."""

from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import run_cli
from weft._constants import WEFT_TID_MAPPINGS_QUEUE
from weft.context import build_context


def _write_worker_spec(
    workdir: Path, *, auto_stop: bool = True
) -> tuple[Path, str, Path]:
    tid = "1761000000000000000"
    context_root = workdir / "worker-project"
    context = build_context(spec_context=context_root)

    spec = {
        "tid": tid,
        "name": "worker",
        "version": "1.0",
        "spec": {
            "type": "function",
            "function_target": "weft.core.manager:Manager",
            "weft_context": str(context_root),
            "reserved_policy_on_stop": "keep",
            "reserved_policy_on_error": "keep",
        },
        "io": {
            "inputs": {"inbox": f"worker.{tid}.inbox"},
            "outputs": {"outbox": f"T{tid}.outbox"},
            "control": {
                "ctrl_in": f"worker.{tid}.ctrl_in",
                "ctrl_out": f"worker.{tid}.ctrl_out",
            },
        },
        "state": {"status": "created"},
        "metadata": {"capabilities": ["tests.tasks.sample_targets:large_output"]},
    }

    spec_path = workdir / "worker.json"
    spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")

    if auto_stop:
        ctrl_queue = context.queue(f"worker.{tid}.ctrl_in", persistent=False)
        ctrl_queue.write("STOP")

    return spec_path, tid, context_root


def test_worker_start_and_status(workdir):
    spec_path, tid, context_root = _write_worker_spec(workdir, auto_stop=True)

    rc, out, err = run_cli(
        "worker",
        "start",
        spec_path,
        "--foreground",
        cwd=workdir,
    )
    assert rc == 0
    assert out == ""
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
    assert any(
        record.get("tid") == tid and record.get("status") == "stopped"
        for record in records
    )

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
    assert detail.get("status") == "stopped"


def test_worker_stop_missing_tid(workdir):
    context_root = workdir / "missing-worker"
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
    context_root = workdir / "empty-worker"
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


def test_worker_status_missing(workdir):
    context_root = workdir / "status-worker"
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
    context_root = workdir / "force-worker"
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
