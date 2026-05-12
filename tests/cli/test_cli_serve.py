"""CLI coverage for `weft manager serve`."""

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
    INTERNAL_SERVICE_KEY_HEARTBEAT,
    INTERNAL_SERVICE_KEY_METADATA_KEY,
    INTERNAL_SERVICE_KEY_TASK_MONITOR,
    MANAGER_SERVE_LOG_SCHEMA,
    TERMINAL_TASK_STATUSES,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_SERVICES_REGISTRY_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
)
from weft.context import build_context
from weft.core.control_probe import send_keyed_ping_probe
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

    command_args = tuple(map(str, args))
    foreground_serve = command_args[:2] == ("manager", "serve")
    if active_test_backend(env_vars) == "postgres" and not foreground_serve:
        cmd = ["uv", "run", "--active", "python", "-m", "weft.cli", *command_args]
    else:
        cmd = [sys.executable, "-m", "weft.cli", *command_args]

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
    queue = context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
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
        authority = _runtime_authority_from_record(record)
        if authority == "external-supervisor":
            records.append(record)
            continue
        pid = _host_pid_from_record(record)
        if isinstance(pid, int) and pid_is_live(pid):
            records.append(record)
    records.sort(key=lambda item: (int(item["tid"]), int(item["timestamp"])))
    return records


def _runtime_authority_from_record(record: dict[str, Any]) -> str | None:
    handle = record.get("runtime_handle")
    if not isinstance(handle, dict):
        return None
    control = handle.get("control")
    if not isinstance(control, dict):
        return None
    authority = control.get("authority")
    return authority if isinstance(authority, str) else None


def _host_pid_from_record(record: dict[str, Any]) -> int | None:
    handle = record.get("runtime_handle")
    if not isinstance(handle, dict):
        return None
    observations = handle.get("observations")
    if not isinstance(observations, dict):
        return None
    host_pids = observations.get("host_pids")
    if not isinstance(host_pids, list):
        return None
    return next((pid for pid in host_pids if isinstance(pid, int) and pid > 0), None)


def _wait_for_active_canonical_manager(
    context,
    *,
    process: subprocess.Popen[str] | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    wait_timeout = timeout
    if wait_timeout is None:
        wait_timeout = 20.0 if os.name == "nt" else 10.0
    deadline = time.time() + wait_timeout
    while time.time() < deadline:
        records = _active_canonical_manager_records(context)
        if records:
            return records[0]
        if process is not None and process.poll() is not None:
            stdout, stderr = _stop_process(process, timeout=1.0)
            raise AssertionError(
                "Serve process exited before a canonical manager became active: "
                f"returncode={process.returncode}, stdout={stdout!r}, stderr={stderr!r}"
            )
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


def _service_records(context, *, service_key: str) -> list[dict[str, Any]]:
    queue = context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    try:
        latest_by_tid: dict[str, dict[str, Any]] = {}
        for payload, timestamp in iter_queue_json_entries(queue):
            if payload.get("event") == "task_spawned":
                tid = payload.get("child_tid")
                taskspec = payload.get("child_taskspec")
            else:
                tid = payload.get("tid")
                taskspec = payload.get("taskspec")
            if not isinstance(tid, str) or not tid:
                continue
            if not isinstance(taskspec, dict):
                continue
            metadata = taskspec.get("metadata")
            if not isinstance(metadata, dict):
                continue
            if metadata.get(INTERNAL_SERVICE_KEY_METADATA_KEY) != service_key:
                continue
            state = taskspec.get("state")
            pid = state.get("pid") if isinstance(state, dict) else None
            io_section = taskspec.get("io")
            control = io_section.get("control") if isinstance(io_section, dict) else {}
            ctrl_in = control.get("ctrl_in") if isinstance(control, dict) else None
            ctrl_out = control.get("ctrl_out") if isinstance(control, dict) else None
            record = {
                "tid": tid,
                "timestamp": timestamp,
                "event": payload.get("event"),
                "status": payload.get("status"),
                "pid": pid if isinstance(pid, int) else None,
                "ctrl_in": ctrl_in if isinstance(ctrl_in, str) else None,
                "ctrl_out": ctrl_out if isinstance(ctrl_out, str) else None,
            }
            existing = latest_by_tid.get(tid)
            if existing is not None and record["pid"] is None:
                record["pid"] = existing.get("pid")
            if existing is not None and record["ctrl_in"] is None:
                record["ctrl_in"] = existing.get("ctrl_in")
            if existing is not None and record["ctrl_out"] is None:
                record["ctrl_out"] = existing.get("ctrl_out")
            if existing is None or int(existing["timestamp"]) <= timestamp:
                latest_by_tid[tid] = record
    finally:
        queue.close()
    return sorted(
        latest_by_tid.values(),
        key=lambda record: (str(record["tid"]), int(record["timestamp"])),
    )


def _live_service_records(context, *, service_key: str) -> list[dict[str, Any]]:
    live_records: list[dict[str, Any]] = []
    for record in _service_records(context, service_key=service_key):
        status = record.get("status")
        if isinstance(status, str) and status in TERMINAL_TASK_STATUSES:
            continue
        tid = record.get("tid")
        ctrl_in = record.get("ctrl_in")
        ctrl_out = record.get("ctrl_out")
        if not isinstance(tid, str):
            continue
        if not isinstance(ctrl_in, str) or not isinstance(ctrl_out, str):
            continue
        probe = send_keyed_ping_probe(
            context,
            tid=tid,
            ctrl_in_name=ctrl_in,
            ctrl_out_name=ctrl_out,
            timeout=1.0,
        )
        pid = record.get("pid")
        if probe.matched is not None or (isinstance(pid, int) and pid_is_live(pid)):
            live_records.append(record)
    return live_records


def _wait_for_single_live_service(
    context,
    *,
    service_key: str,
    process: subprocess.Popen[str],
    previous_tids: set[str] | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    deadline = time.time() + timeout
    ignored_tids = previous_tids or set()
    last_records: list[dict[str, Any]] = []
    while time.time() < deadline:
        if process.poll() is not None:
            stdout, stderr = _stop_process(process, timeout=1.0)
            raise AssertionError(
                "Serve process exited while waiting for service "
                f"{service_key}: returncode={process.returncode}, "
                f"stdout={stdout!r}, stderr={stderr!r}"
            )
        live_records = _live_service_records(context, service_key=service_key)
        last_records = live_records
        replacement_records = [
            record for record in live_records if record["tid"] not in ignored_tids
        ]
        if len(live_records) == 1 and replacement_records:
            return replacement_records[0]
        time.sleep(0.05)

    all_records = _service_records(context, service_key=service_key)
    raise AssertionError(
        "Timed out waiting for exactly one live service "
        f"{service_key}; live={last_records!r}; all={all_records!r}"
    )


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


def _operational_log_events(output: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in output.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("schema") == MANAGER_SERVE_LOG_SCHEMA:
            events.append(payload)
    return events


def test_serve_runs_in_foreground_and_reuses_single_manager(
    workdir, weft_harness: WeftTestHarness
) -> None:
    context_root = workdir
    context = build_context(spec_context=context_root)

    process = _popen_cli("manager", "serve", "--context", context_root, cwd=workdir)
    weft_harness.register_pid(process.pid, kind="owner")

    try:
        record = _wait_for_active_canonical_manager(context, process=process)
        manager_tid = record["tid"]
        weft_harness.register_manager_tid(manager_tid)
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
        weft_harness.wait_for_completion(out)

        active_records = _active_canonical_manager_records(context)
        assert [entry["tid"] for entry in active_records] == [manager_tid]

        rc, out, err = run_cli(
            "manager",
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


def test_serve_help_includes_operational_log_options(
    workdir,
    weft_harness: WeftTestHarness,
) -> None:
    rc, out, err = run_cli(
        "manager",
        "serve",
        "--help",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    assert err == ""
    assert "--level" in out
    assert "--log-interval" in out


def test_serve_level_info_emits_process_operational_log(
    workdir,
    weft_harness: WeftTestHarness,
) -> None:
    context_root = workdir
    context = build_context(spec_context=context_root)

    process = _popen_cli(
        "manager",
        "serve",
        "--level",
        "info",
        "--log-interval",
        "0.1",
        "--context",
        context_root,
        cwd=workdir,
    )
    weft_harness.register_pid(process.pid, kind="owner")

    try:
        record = _wait_for_active_canonical_manager(context, process=process)
        weft_harness.register_manager_tid(record["tid"])

        rc, out, err = run_cli(
            "manager",
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
        events = _operational_log_events(stderr)
        assert events
        first = events[0]
        assert first["schema"] == MANAGER_SERVE_LOG_SCHEMA
        assert first["schema_version"] == 1
        assert first["manager_tid"] == record["tid"]
        assert first["configured_level"] == "info"
        assert first["event"] in {
            "manager_loop_summary",
            "manager_registry_snapshot",
            "managed_service_decision",
            "task_monitor_config",
            "task_monitor_cycle",
        }

        log_queue = context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
        try:
            task_log_rows = list(iter_queue_json_entries(log_queue))
        finally:
            log_queue.close()
        assert not any(
            MANAGER_SERVE_LOG_SCHEMA in json.dumps(row) for row, _ts in task_log_rows
        )
    finally:
        if process.poll() is None:
            process.terminate()
            _stop_process(process)


def test_serve_level_off_suppresses_env_operational_log(
    workdir,
    weft_harness: WeftTestHarness,
) -> None:
    context_root = workdir
    context = build_context(spec_context=context_root)

    process = _popen_cli(
        "manager",
        "serve",
        "--level",
        "off",
        "--context",
        context_root,
        cwd=workdir,
        env={"WEFT_MANAGER_SERVE_LOG_LEVEL": "debug"},
    )
    weft_harness.register_pid(process.pid, kind="owner")

    try:
        record = _wait_for_active_canonical_manager(context, process=process)
        weft_harness.register_manager_tid(record["tid"])

        rc, out, err = run_cli(
            "manager",
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
        assert _operational_log_events(stderr) == []
        assert stderr == ""
    finally:
        if process.poll() is None:
            process.terminate()
            _stop_process(process)


def test_serve_restarts_singleton_services_without_duplicates(
    workdir,
    weft_harness: WeftTestHarness,
) -> None:
    context_root = workdir
    context = build_context(spec_context=context_root)

    process = _popen_cli(
        "manager",
        "serve",
        "--context",
        context_root,
        cwd=workdir,
        env={
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS": "0.1",
        },
    )
    weft_harness.register_pid(process.pid, kind="owner")

    try:
        record = _wait_for_active_canonical_manager(context, process=process)
        manager_tid = record["tid"]
        weft_harness.register_manager_tid(manager_tid)

        heartbeat = _wait_for_single_live_service(
            context,
            service_key=INTERNAL_SERVICE_KEY_HEARTBEAT,
            process=process,
        )
        task_monitor = _wait_for_single_live_service(
            context,
            service_key=INTERNAL_SERVICE_KEY_TASK_MONITOR,
            process=process,
        )
        assert heartbeat["tid"] != task_monitor["tid"]

        service_timeout = 45.0
        for service_key, current in (
            (INTERNAL_SERVICE_KEY_TASK_MONITOR, task_monitor),
            (INTERNAL_SERVICE_KEY_HEARTBEAT, heartbeat),
        ):
            old_tid = str(current["tid"])
            rc, out, err = run_cli(
                "task",
                "kill",
                old_tid,
                "--context",
                context_root,
                cwd=workdir,
                harness=weft_harness,
                timeout=service_timeout,
            )
            assert rc == 0
            assert out.startswith("Killed ")
            assert err == ""

            replacement = _wait_for_single_live_service(
                context,
                service_key=service_key,
                process=process,
                previous_tids={old_tid},
                timeout=service_timeout,
            )
            assert replacement["tid"] != old_tid
            weft_harness.register_tid(str(replacement["tid"]))

        rc, out, err = run_cli(
            "manager",
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

    process = _popen_cli("manager", "serve", "--context", context_root, cwd=workdir)
    weft_harness.register_pid(process.pid, kind="owner")

    try:
        record = _wait_for_active_canonical_manager(context, process=process)
        manager_tid = record["tid"]
        weft_harness.register_manager_tid(manager_tid)

        rc, out, err = run_cli(
            "manager",
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
                "manager",
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
        "manager",
        "serve",
        "--context",
        context_root,
        cwd=workdir,
        env={"WEFT_MANAGER_LIFETIME_TIMEOUT": "0.05"},
    )
    weft_harness.register_pid(process.pid, kind="owner")

    try:
        record = _wait_for_active_canonical_manager(context, process=process)
        weft_harness.register_manager_tid(record["tid"])

        # 4× the configured WEFT_MANAGER_LIFETIME_TIMEOUT (0.05s). Proves a
        # negative — the manager does not exit while it has active work.
        # The duration is the test invariant; do not shorten it.
        time.sleep(0.2)
        assert process.poll() is None

        rc, out, err = run_cli(
            "manager",
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

    process = _popen_cli("manager", "serve", "--context", context_root, cwd=workdir)
    weft_harness.register_pid(process.pid, kind="owner")

    try:
        record = _wait_for_active_canonical_manager(context, process=process)
        manager_tid = record["tid"]
        weft_harness.register_manager_tid(manager_tid)

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
