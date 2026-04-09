"""CLI coverage for `weft serve`."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import REPO_ROOT, run_cli
from tests.helpers.test_backend import active_test_backend, prepare_cli_root
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import (
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    WEFT_WORKERS_REGISTRY_QUEUE,
)
from weft.context import build_context
from weft.helpers import (
    is_canonical_manager_record,
    iter_queue_json_entries,
    pid_is_live,
)

pytestmark = [pytest.mark.shared]


def _popen_cli(
    *args: object,
    cwd: Path,
    env: Mapping[str, str] | None = None,
) -> subprocess.Popen[str]:
    env_vars = os.environ.copy()
    if env is not None:
        env_vars.update(env)
    env_vars.pop("__PYVENV_LAUNCHER__", None)
    existing_path = env_vars.get("PYTHONPATH", "")
    path_parts = [str(REPO_ROOT)]
    if existing_path:
        path_parts.append(existing_path)
    env_vars["PYTHONPATH"] = os.pathsep.join(path_parts)
    env_vars["PYTHONIOENCODING"] = "utf-8"

    prepare_cli_root(args, cwd=cwd, env=env_vars)

    if active_test_backend(env_vars) == "postgres":
        cmd = ["uv", "run", "--active", "python", "-m", "weft.cli", *map(str, args)]
    else:
        cmd = [sys.executable, "-m", "weft.cli", *map(str, args)]

    return subprocess.Popen(
        cmd,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env_vars,
    )


def _manager_records(context) -> list[dict[str, Any]]:
    queue = context.queue(WEFT_WORKERS_REGISTRY_QUEUE, persistent=False)
    try:
        snapshot: dict[str, dict[str, Any]] = {}
        for data, ts in iter_queue_json_entries(queue):
            tid = data.get("tid")
            if not isinstance(tid, str) or not tid:
                continue
            record = dict(data)
            record["timestamp"] = ts
            record.setdefault("requests", WEFT_SPAWN_REQUESTS_QUEUE)
            existing = snapshot.get(tid)
            if existing is None or int(existing["timestamp"]) < ts:
                snapshot[tid] = record
    finally:
        queue.close()
    return list(snapshot.values())


def _active_canonical_manager_records(context) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for record in _manager_records(context):
        if not is_canonical_manager_record(record):
            continue
        if record.get("status") != "active":
            continue
        pid = record.get("pid")
        if isinstance(pid, int) and pid_is_live(pid):
            records.append(record)
    records.sort(key=lambda item: (int(item["tid"]), int(item["timestamp"])))
    return records


def _wait_for_active_canonical_manager(
    context, *, timeout: float = 10.0
) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        records = _active_canonical_manager_records(context)
        if records:
            return records[0]
        time.sleep(0.05)
    raise AssertionError("Timed out waiting for an active canonical manager")


def _wait_for_started_task_tid(
    harness: WeftTestHarness,
    *,
    task_name: str,
    timeout: float = 10.0,
) -> str:
    queue = harness.context.queue(
        WEFT_GLOBAL_LOG_QUEUE,
        persistent=False,
    )
    try:
        deadline = time.time() + timeout
        while time.time() < deadline:
            for payload, _ts in queue.peek_many(limit=200, with_timestamps=True) or []:
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if data.get("event") != "work_started":
                    continue
                taskspec = data.get("taskspec") or {}
                if taskspec.get("name") != task_name:
                    continue
                tid = data.get("tid")
                if isinstance(tid, str) and tid:
                    return tid
            time.sleep(0.05)
    finally:
        queue.close()

    raise AssertionError(f"Timed out waiting for started task {task_name!r}")


def _stop_process(
    process: subprocess.Popen[str], *, timeout: float = 10.0
) -> tuple[str, str]:
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate(timeout=timeout)
    return stdout, stderr


def test_serve_runs_in_foreground_and_reuses_single_manager(
    workdir, weft_harness: WeftTestHarness
) -> None:
    context_root = workdir
    context = build_context(spec_context=context_root)

    process = _popen_cli("serve", "--context", context_root, cwd=workdir)
    weft_harness.register_pid(process.pid, kind="owner")

    try:
        record = _wait_for_active_canonical_manager(context)
        manager_tid = record["tid"]
        weft_harness.register_worker_tid(manager_tid)
        assert process.poll() is None

        rc, out, err = run_cli(
            "run",
            "--function",
            "tests.tasks.sample_targets:simulate_work",
            "--kw",
            "duration=0.1",
            "--no-wait",
            "--context",
            context_root,
            cwd=workdir,
            harness=weft_harness,
        )
        assert rc == 0
        assert err == ""
        assert len(out) == 19 and out.isdigit()
        weft_harness.wait_for_completion(out, timeout=10.0)

        active_records = _active_canonical_manager_records(context)
        assert [entry["tid"] for entry in active_records] == [manager_tid]

        rc, out, err = run_cli(
            "worker",
            "stop",
            manager_tid,
            "--context",
            context_root,
            cwd=workdir,
            harness=weft_harness,
        )
        assert rc == 0
        assert out == ""
        assert err == ""

        stdout, stderr = _stop_process(process)
        assert process.returncode == 0
        assert stdout == ""
        assert stderr == ""
    finally:
        if process.poll() is None:
            process.terminate()
            _stop_process(process)


def test_serve_rejects_duplicate_canonical_manager(
    workdir, weft_harness: WeftTestHarness
) -> None:
    context_root = workdir
    context = build_context(spec_context=context_root)
    record: dict[str, Any] | None = None

    process = _popen_cli("serve", "--context", context_root, cwd=workdir)
    weft_harness.register_pid(process.pid, kind="owner")

    try:
        record = _wait_for_active_canonical_manager(context)
        manager_tid = record["tid"]
        weft_harness.register_worker_tid(manager_tid)

        rc, out, err = run_cli(
            "serve",
            "--context",
            context_root,
            cwd=workdir,
            harness=weft_harness,
        )
        assert rc == 1
        assert "already running" in f"{out}\n{err}".lower()
    finally:
        if process.poll() is None and record is not None:
            run_cli(
                "worker",
                "stop",
                record["tid"],
                "--context",
                context_root,
                cwd=workdir,
                harness=weft_harness,
            )
            _stop_process(process)


def test_serve_forces_no_idle_timeout(workdir, weft_harness: WeftTestHarness) -> None:
    context_root = workdir
    context = build_context(spec_context=context_root)

    process = _popen_cli(
        "serve",
        "--context",
        context_root,
        cwd=workdir,
        env={"WEFT_MANAGER_LIFETIME_TIMEOUT": "0.05"},
    )
    weft_harness.register_pid(process.pid, kind="owner")

    try:
        record = _wait_for_active_canonical_manager(context)
        weft_harness.register_worker_tid(record["tid"])

        time.sleep(0.2)
        assert process.poll() is None

        rc, out, err = run_cli(
            "worker",
            "stop",
            record["tid"],
            "--context",
            context_root,
            cwd=workdir,
            harness=weft_harness,
        )
        assert rc == 0
        assert out == ""
        assert err == ""

        stdout, stderr = _stop_process(process)
        assert process.returncode == 0
        assert stdout == ""
        assert stderr == ""
    finally:
        if process.poll() is None:
            process.terminate()
            _stop_process(process)


@pytest.mark.skipif(os.name == "nt", reason="POSIX only")
def test_serve_sigterm_drains_children_cleanly(
    workdir, weft_harness: WeftTestHarness
) -> None:
    context_root = workdir
    context = build_context(spec_context=context_root)

    process = _popen_cli("serve", "--context", context_root, cwd=workdir)
    weft_harness.register_pid(process.pid, kind="owner")

    try:
        record = _wait_for_active_canonical_manager(context)
        manager_tid = record["tid"]
        weft_harness.register_worker_tid(manager_tid)

        rc, out, err = run_cli(
            "run",
            "--function",
            "tests.tasks.sample_targets:simulate_work",
            "--kw",
            "duration=5",
            "--no-wait",
            "--context",
            context_root,
            cwd=workdir,
            harness=weft_harness,
        )
        assert rc == 0
        assert err == ""
        tid = out.strip()
        assert tid
        started_tid = _wait_for_started_task_tid(
            weft_harness,
            task_name="simulate_work",
        )
        assert started_tid == tid

        process.terminate()
        stdout, stderr = _stop_process(process)
        assert process.returncode == 0
        assert stdout == ""
        assert stderr == "" or "STOP command received" in stderr

        with pytest.raises(RuntimeError, match=rf"Task {tid} reported control_stop"):
            weft_harness.wait_for_completion(tid, timeout=10.0)
    finally:
        if process.poll() is None:
            process.terminate()
            _stop_process(process)
