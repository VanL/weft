"""CLI coverage for `weft manager serve`."""

from __future__ import annotations

import json
import os
import queue as thread_queue
import selectors
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TextIO

import psutil
import pytest

from tests.conftest import REPO_ROOT, run_cli
from tests.helpers.test_backend import active_test_backend, prepare_cli_root
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import (
    INTERNAL_SERVICE_KEY_HEARTBEAT,
    INTERNAL_SERVICE_KEY_LIVENESS_MONITOR,
    INTERNAL_SERVICE_KEY_TASK_MONITOR,
    INTERNAL_SERVICE_KEYS,
    MANAGER_SERVE_LOG_SCHEMA,
    SERVICE_STATUS_TERMINAL,
    TERMINAL_TASK_STATUSES,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_SERVICES_REGISTRY_QUEUE,
)
from weft.context import WeftContext, build_context
from weft.core.control_probe import send_keyed_ping_probe
from weft.core.service_convergence import (
    manager_service_key,
    project_manager_service_record,
)
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
        cmd = ["uv", "run", "--active", "python", "-m", "weft", *command_args]
    else:
        cmd = [sys.executable, "-m", "weft", *command_args]

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


def _manager_records(context: WeftContext) -> list[dict[str, Any]]:
    queue = context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
    try:
        snapshot: dict[str, dict[str, Any]] = {}
        for payload, ts in iter_queue_json_entries(queue):
            record = project_manager_service_record(
                payload,
                timestamp=ts,
                service_key=manager_service_key(context),
            )
            if record is None:
                continue
            tid = record["tid"]
            existing = snapshot.get(tid)
            if existing is None or int(existing["timestamp"]) < ts:
                snapshot[tid] = record
    finally:
        queue.close()
    return list(snapshot.values())


def _active_canonical_manager_records(context: WeftContext) -> list[dict[str, Any]]:
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
    context: WeftContext,
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


def _published_manager_idle_timeouts(context: WeftContext, tid: str) -> set[float]:
    """Return the idle timeouts in a Manager's own published TaskSpec events."""

    log_queue = context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    try:
        rows = list(iter_queue_json_entries(log_queue))
    finally:
        log_queue.close()
    timeouts = {
        row["taskspec"]["metadata"]["idle_timeout"]
        for row, _timestamp in rows
        if row.get("tid") == tid and isinstance(row.get("taskspec"), dict)
    }
    assert timeouts, f"no published TaskSpec for manager {tid}"
    return timeouts


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


def _service_records(context: WeftContext, *, service_key: str) -> list[dict[str, Any]]:
    queue = context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
    try:
        latest_by_tid: dict[str, dict[str, Any]] = {}
        for payload, timestamp in iter_queue_json_entries(queue):
            if payload.get("service_key") != service_key:
                continue
            tid = payload.get("owner_tid") or payload.get("tid")
            if not isinstance(tid, str) or not tid:
                continue
            queues = payload.get("queues")
            if not isinstance(queues, dict):
                queues = {}
            ctrl_in = payload.get("ctrl_in") or queues.get("ctrl_in")
            ctrl_out = payload.get("ctrl_out") or queues.get("ctrl_out")
            record = {
                "tid": tid,
                "timestamp": timestamp,
                "status": payload.get("status"),
                "ctrl_in": ctrl_in if isinstance(ctrl_in, str) else None,
                "ctrl_out": ctrl_out if isinstance(ctrl_out, str) else None,
                "runtime_handle": payload.get("runtime_handle"),
            }
            pid = _host_pid_from_record(record)
            record["pid"] = pid if isinstance(pid, int) else None
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


def _live_service_records(
    context: WeftContext, *, service_key: str
) -> list[dict[str, Any]]:
    live_records: list[dict[str, Any]] = []
    for record in _service_records(context, service_key=service_key):
        status = record.get("status")
        if isinstance(status, str) and status in {
            *TERMINAL_TASK_STATUSES,
            SERVICE_STATUS_TERMINAL,
        }:
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
            timeout=1.0,
        )
        pid = record.get("pid")
        if probe.matched is not None or (isinstance(pid, int) and pid_is_live(pid)):
            live_records.append(record)
    return live_records


def _wait_for_single_live_service(
    context: WeftContext,
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


_PIPE_SELECT_SECONDS = 0.05
"""Select slice while the serve process runs; its exit is not selectable."""


def _stop_process(
    process: subprocess.Popen[str], *, timeout: float = 10.0
) -> tuple[str, str]:
    """Wait for the process to exit, escalating, and return what it wrote.

    On POSIX, reading ends at the process's exit, not at pipe EOF: the
    internal services a serve process launched outlive it ([MA-1] item 7)
    and keep ``multiprocessing``'s resource tracker alive, and the tracker
    holds the inherited stdout/stderr write ends until they exit. Windows
    passes no such handles, so ``communicate`` reaches EOF at exit there.
    """

    if os.name == "nt":
        try:
            return process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                return process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                return process.communicate(timeout=timeout)
    return _read_until_exit(process, timeout=timeout)


def _read_until_exit(
    process: subprocess.Popen[str], *, timeout: float
) -> tuple[str, str]:
    """Read both pipes while the process runs, then drain them once it exits."""

    assert process.stdout is not None and process.stderr is not None
    streams = (process.stdout, process.stderr)
    fds = tuple(stream.fileno() for stream in streams)
    chunks: dict[int, list[bytes]] = {fd: [] for fd in fds}
    escalations = iter((process.terminate, process.kill))
    deadline = time.monotonic() + timeout
    with selectors.DefaultSelector() as selector:
        for fd in chunks:
            os.set_blocking(fd, False)
            selector.register(fd, selectors.EVENT_READ)
        while True:
            exited = process.poll() is not None
            for key, _events in selector.select(
                timeout=0.0 if exited else _PIPE_SELECT_SECONDS
            ):
                if _read_available(key.fd, chunks[key.fd]):
                    selector.unregister(key.fd)
            if exited:
                break
            if time.monotonic() >= deadline:
                escalate = next(escalations, None)
                if escalate is None:
                    raise subprocess.TimeoutExpired(process.args, timeout)
                escalate()
                deadline = time.monotonic() + timeout
    for stream in streams:
        stream.close()
    stdout, stderr = (
        b"".join(chunks[fd]).decode("utf-8", errors="replace") for fd in fds
    )
    return stdout, stderr


def _read_available(fd: int, chunks: list[bytes]) -> bool:
    """Append everything readable now from a non-blocking pipe; True at EOF."""

    while True:
        try:
            data = os.read(fd, 65536)
        except BlockingIOError:
            return False
        if not data:
            return True
        chunks.append(data)


def _is_multiprocessing_resource_tracker(child: psutil.Process) -> bool:
    try:
        return any("multiprocessing.resource_tracker" in arg for arg in child.cmdline())
    except psutil.Error:
        return False


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
    workdir: Path, weft_harness: WeftTestHarness
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
    workdir: Path,
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
    assert "--replace" in out


def test_serve_level_info_emits_process_operational_log(
    workdir: Path,
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
    workdir: Path,
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
    workdir: Path,
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
            # Production defaults: both monitor services on. The
            # LivenessMonitor restarts on the TaskMonitor backoff.
            "WEFT_TASK_MONITOR_ENABLED": "1",
            "WEFT_LIVENESS_MONITOR_ENABLED": "1",
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
        liveness_monitor = _wait_for_single_live_service(
            context,
            service_key=INTERNAL_SERVICE_KEY_LIVENESS_MONITOR,
            process=process,
        )
        assert (
            len({heartbeat["tid"], task_monitor["tid"], liveness_monitor["tid"]}) == 3
        )

        service_timeout = 45.0
        for service_key, current in (
            (INTERNAL_SERVICE_KEY_TASK_MONITOR, task_monitor),
            (INTERNAL_SERVICE_KEY_LIVENESS_MONITOR, liveness_monitor),
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
    workdir: Path, weft_harness: WeftTestHarness
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


def test_serve_forces_no_idle_timeout(
    workdir: Path, weft_harness: WeftTestHarness
) -> None:
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

        # Serve overrides the configured 0.05s lifetime with no idle timeout:
        # the serve Manager's own published TaskSpec carries idle_timeout 0.0,
        # and the Manager's idle path returns at once for a value <= 0.
        assert _published_manager_idle_timeouts(context, record["tid"]) == {0.0}
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
    workdir: Path, weft_harness: WeftTestHarness
) -> None:
    """SIGTERM drains user work, then the Manager; its services run on.

    Spec: docs/specifications/03-Manager_Architecture.md [MA-1] item 7,
    [MA-3]. SIGTERM coverage of plan
    2026-11-04-context-level-service-singletons Task 3 (f); the CLI
    ``weft manager stop`` variant runs on every platform in
    ``tests/cli/test_cli_manager.py``. The harness stops the services at
    cleanup.
    """

    context_root = workdir
    context = build_context(spec_context=context_root)

    process = _popen_cli("manager", "serve", "--context", context_root, cwd=workdir)
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

        # The drain contract covers task processes: the Consumer and any
        # worker it registered. The spawn context's resource tracker is also a
        # descendant, but CPython exits it on pipe EOF, which arrives only
        # after the manager and every spawned child have closed the inherited
        # write end, so it is never ordered before the manager's own exit.
        # Internal services are context-level singletons the drain leaves
        # running, so their process trees are not user work.
        service_pids = {
            pid
            for service_key in INTERNAL_SERVICE_KEYS
            for service in _service_records(context, service_key=service_key)
            if isinstance(pid := service.get("pid"), int) and pid_is_live(pid)
        }
        service_trees = set(service_pids)
        for pid in service_pids:
            try:
                service_trees.update(
                    child.pid for child in psutil.Process(pid).children(recursive=True)
                )
            except psutil.Error:
                continue
        user_pids = [
            child.pid
            for child in psutil.Process(process.pid).children(recursive=True)
            if not _is_multiprocessing_resource_tracker(child)
            and child.pid not in service_trees
        ]
        assert user_pids
        assert isinstance(heartbeat["pid"], int)
        assert heartbeat["pid"] in service_pids
        process.terminate()
        stdout, stderr = _stop_process(process)
        assert process.returncode == 0
        assert stdout == ""
        assert stderr == "" or "STOP command received" in stderr

        # [MA-3] permits SIGTERM escalation while the manager drains children.
        # Either STOP delivery path must cancel the child before manager exit.
        weft_harness.wait_for_terminal_state(
            tid, timeout=10.0, expected_status="cancelled"
        )
        assert not [pid for pid in user_pids if pid_is_live(pid)]
        assert all(pid_is_live(pid) for pid in service_pids), (
            "an internal service stopped with the Manager"
        )
    finally:
        if process.poll() is None:
            process.terminate()
            _stop_process(process)


_SINGLETON_SERVICE_KEYS = (
    INTERNAL_SERVICE_KEY_HEARTBEAT,
    INTERNAL_SERVICE_KEY_TASK_MONITOR,
    INTERNAL_SERVICE_KEY_LIVENESS_MONITOR,
)


def _stream_lines(stream: TextIO, lines: thread_queue.Queue[str | None]) -> None:
    """Copy one text stream into a queue line by line; ``None`` marks EOF."""

    for line in stream:
        lines.put(line)
    lines.put(None)


def _wait_for_kept_live_decisions(
    lines: thread_queue.Queue[str | None],
    *,
    expected: Mapping[str, str],
    timeout: float,
) -> list[dict[str, Any]]:
    """Read serve-log records until each service key was kept live once.

    ``expected`` maps a service key to the live owner TID the Manager must
    adopt. Returns every ``managed_service_decision`` record read.
    """

    pending = dict(expected)
    decisions: list[dict[str, Any]] = []
    deadline = time.monotonic() + timeout
    while pending:
        remaining = deadline - time.monotonic()
        assert remaining > 0, f"no keep_live decision for {sorted(pending)}"
        line = lines.get(timeout=remaining)
        assert line is not None, f"serve log ended before {sorted(pending)}"
        for event in _operational_log_events(line):
            if event.get("event") != "managed_service_decision":
                continue
            decisions.append(event)
            key = event.get("service_key")
            if (
                event.get("action") == "keep_live"
                and isinstance(key, str)
                and event.get("canonical_live_tid") == pending.get(key)
            ):
                pending.pop(key)
    return decisions


def _stop_services_and_wait(
    owners: Mapping[str, dict[str, Any]],
    *,
    workdir: Path,
    harness: WeftTestHarness,
    timeout: float = 30.0,
) -> None:
    """STOP each service owner through the CLI and wait for its process exit."""

    for owner in owners.values():
        rc, out, err = run_cli(
            "task",
            "stop",
            str(owner["tid"]),
            "--context",
            workdir,
            cwd=workdir,
            harness=harness,
        )
        assert rc == 0, (rc, out, err)
    for key, owner in owners.items():
        pid = owner["pid"]
        deadline = time.monotonic() + timeout
        while pid_is_live(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not pid_is_live(pid), f"service {key} did not exit on STOP"


def test_serve_adopts_live_services_and_launches_after_they_exit(
    workdir: Path,
    weft_harness: WeftTestHarness,
) -> None:
    """A starting Manager ensures services: it adopts live ones, launches gone ones.

    Spec: docs/specifications/03-Manager_Architecture.md [MA-1] item 7;
    docs/program-theory.md [ALT-WT-023] (owner, 2026-11-04). Plan
    2026-11-04-context-level-service-singletons Task 4. Manager A launches
    the services and stops; they run on. Manager B, started while they are
    live, keeps each one live through the existing evidence path and
    launches nothing. Once the services have exited (STOP stands in for
    their idle exit: the Manager sees the same terminal evidence), Manager C
    launches a fresh one for each key.
    """

    context_root = workdir
    context = build_context(spec_context=context_root)
    env = {
        "WEFT_TASK_MONITOR_ENABLED": "1",
        "WEFT_LIVENESS_MONITOR_ENABLED": "1",
        "WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS": "0.1",
    }

    def serve(*extra: str) -> subprocess.Popen[str]:
        process = _popen_cli(
            "manager", "serve", *extra, "--context", context_root, cwd=workdir, env=env
        )
        weft_harness.register_pid(process.pid, kind="owner")
        return process

    def stop_manager(tid: str) -> None:
        rc, out, err = run_cli(
            "manager",
            "stop",
            tid,
            "--context",
            context_root,
            cwd=workdir,
            harness=weft_harness,
        )
        assert rc == 0, (rc, out, err)

    processes: list[subprocess.Popen[str]] = []
    try:
        # Manager A launches the services, then stops; they run on.
        process_a = serve()
        processes.append(process_a)
        manager_a = _wait_for_active_canonical_manager(context, process=process_a)
        weft_harness.register_manager_tid(manager_a["tid"])
        owners = {
            key: _wait_for_single_live_service(
                context, service_key=key, process=process_a
            )
            for key in _SINGLETON_SERVICE_KEYS
        }
        for owner in owners.values():
            weft_harness.register_tid(str(owner["tid"]))
        stop_manager(manager_a["tid"])
        _stop_process(process_a)
        assert process_a.returncode == 0
        for key, owner in owners.items():
            assert isinstance(owner["pid"], int) and pid_is_live(owner["pid"]), key

        # Manager B adopts the live services and launches nothing.
        process_b = serve("--level", "debug")
        processes.append(process_b)
        assert process_b.stderr is not None
        lines: thread_queue.Queue[str | None] = thread_queue.Queue()
        reader = threading.Thread(
            target=_stream_lines, args=(process_b.stderr, lines), daemon=True
        )
        reader.start()
        manager_b = _wait_for_active_canonical_manager(context, process=process_b)
        weft_harness.register_manager_tid(manager_b["tid"])
        decisions = _wait_for_kept_live_decisions(
            lines,
            expected={key: str(owner["tid"]) for key, owner in owners.items()},
            timeout=60.0,
        )
        assert not [event for event in decisions if event.get("action") == "start_now"]
        stop_manager(manager_b["tid"])
        process_b.wait(timeout=30.0)
        assert process_b.returncode == 0
        reader.join(timeout=30.0)
        for key, owner in owners.items():
            live = _live_service_records(context, service_key=key)
            assert [record["tid"] for record in live] == [owner["tid"]], key

        # The services exit; Manager C launches a fresh one for each key.
        _stop_services_and_wait(owners, workdir=workdir, harness=weft_harness)
        process_c = serve()
        processes.append(process_c)
        manager_c = _wait_for_active_canonical_manager(context, process=process_c)
        weft_harness.register_manager_tid(manager_c["tid"])
        for key, owner in owners.items():
            fresh = _wait_for_single_live_service(
                context,
                service_key=key,
                process=process_c,
                previous_tids={str(owner["tid"])},
                timeout=45.0,
            )
            weft_harness.register_tid(str(fresh["tid"]))
        stop_manager(manager_c["tid"])
        _stop_process(process_c)
        assert process_c.returncode == 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=30.0)
