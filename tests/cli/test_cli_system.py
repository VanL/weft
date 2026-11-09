"""CLI tests for system maintenance commands."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import psutil
import pytest
from typer.testing import CliRunner

from simplebroker.ext import BrokerError
from tests.conftest import run_cli
from tests.helpers.task_observation import write_task_observation
from tests.helpers.test_backend import prepare_project_root
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import (
    INTERNAL_SERVICE_KEY_HEARTBEAT,
    INTERNAL_SERVICE_KEYS,
    LIVE_SERVICE_STATUSES,
    MANAGER_CHILD_STOP_ESCALATION_SECONDS,
    RETENTION_PRUNE_CLASS_NONTERMINAL_TASK_LOG_SUPERSEDED,
    SERVICE_TYPE_MANAGED,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_SERVICES_REGISTRY_QUEUE,
)
from weft._exceptions import CommandExecutionError, ControlRejected
from weft.cli.app import app
from weft.commands import TaskMonitorSummary
from weft.commands import system as system_commands
from weft.context import WeftContext, build_context
from weft.core.manager_runtime import generate_tid
from weft.core.service_convergence import build_service_owner_payload
from weft.core.task_state import task_state_queue_name
from weft.helpers import iter_queue_json_entries, pid_is_live, process_create_time

pytestmark = [pytest.mark.shared]


def _write_message(context: WeftContext, queue_name: str, body: str) -> None:
    queue = context.queue(queue_name, persistent=True)
    queue.write(body)
    queue.close()


def _write_task_log(context: WeftContext, payload: dict[str, object]) -> None:
    queue = context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    try:
        queue.write(json.dumps(payload))
    finally:
        queue.close()
    write_task_observation(context, payload)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_queue_json(
    context: WeftContext, queue_name: str, payload: dict[str, object]
) -> int:
    queue = context.queue(queue_name, persistent=False)
    try:
        queue.write(json.dumps(payload))
        latest: int | None = None
        for row, message_id in iter_queue_json_entries(queue):
            if row == payload:
                latest = int(message_id)
        assert latest is not None
        return latest
    finally:
        queue.close()


def _read_queue_ids(context: WeftContext, queue_name: str) -> set[int]:
    queue = context.queue(queue_name, persistent=False)
    try:
        return {
            int(message_id) for _payload, message_id in iter_queue_json_entries(queue)
        }
    finally:
        queue.close()


def test_system_builtins_lists_shipped_inventory(workdir: Path) -> None:
    rc, out, err = run_cli(
        "system",
        "builtins",
        cwd=workdir,
    )

    assert rc == 0
    assert err == ""
    assert "task: probe-agents" in out
    assert "Category: agent-runtime" in out
    assert "Description: Probe known delegated provider CLIs" in out
    assert "Target: weft.builtins.agent_probe:probe_agents_task" in out


def test_system_builtins_json_reports_builtin_metadata(workdir: Path) -> None:
    rc, out, err = run_cli(
        "system",
        "builtins",
        "--json",
        cwd=workdir,
    )

    assert rc == 0
    payload = json.loads(out)
    builtin = next(item for item in payload if item["name"] == "probe-agents")
    assert builtin["description"].startswith("Probe known delegated provider CLIs")
    assert builtin["type"] == "task"
    assert builtin["source"] == "builtin"
    assert builtin["category"] == "agent-runtime"
    assert builtin["function_target"] == "weft.builtins.agent_probe:probe_agents_task"
    assert (
        Path(builtin["path"])
        .as_posix()
        .endswith("weft/builtins/tasks/probe-agents.json")
    )
    dockerized_agent = next(
        item for item in payload if item["name"] == "dockerized-agent"
    )
    assert dockerized_agent["supported_platforms"] == ["linux", "darwin"]
    prepare_agent_images = next(
        item for item in payload if item["name"] == "prepare-agent-images"
    )
    assert prepare_agent_images["supported_platforms"] == ["linux", "darwin"]
    assert err == ""


def test_system_builtins_ignores_local_project_shadow(workdir: Path) -> None:
    source_path = workdir / "local_probe_agents.json"
    _write_json(
        source_path,
        {
            "name": "local-probe-agents",
            "description": "Local shadow for testing",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:simulate_work",
            },
            "metadata": {"shadow": True},
        },
    )

    rc, _out, err = run_cli(
        "spec",
        "create",
        "probe-agents",
        "--type",
        "task",
        "--file",
        source_path,
        "--context",
        workdir,
        cwd=workdir,
    )
    assert rc == 0
    assert err == ""

    rc, out, err = run_cli("system", "builtins", "--json", cwd=workdir)
    assert rc == 0
    assert err == ""
    payload = json.loads(out)
    builtin = next(item for item in payload if item["name"] == "probe-agents")
    assert builtin["source"] == "builtin"
    assert builtin["function_target"] == "weft.builtins.agent_probe:probe_agents_task"
    assert "local-probe-agents" not in {item["name"] for item in payload}


def test_system_prune_rejects_retired_tid_state_group(workdir: Path) -> None:
    rc, out, err = run_cli(
        "system",
        "prune",
        "--family",
        "runtime-state",
        "--queue",
        "tid-mappings",
        "--context",
        workdir,
        cwd=workdir,
    )

    assert rc == 2
    assert out == ""
    assert "tid-mappings" in err
    assert "allowed" in err


def test_system_prune_apply_requires_explicit_family_before_mutation(
    workdir: Path,
) -> None:
    context = build_context(spec_context=workdir)
    old_id = _write_queue_json(
        context,
        task_state_queue_name("1770000000000000502"),
        {"short": "111", "full": "1770000000000000502"},
    )
    new_id = _write_queue_json(
        context,
        task_state_queue_name("1770000000000000502"),
        {"short": "222", "full": "1770000000000000502"},
    )

    rc, _out, err = run_cli(
        "system",
        "prune",
        "--apply",
        "--json",
        "--min-age",
        "0",
        "--queue",
        "tid-mappings",
        "--context",
        workdir,
        cwd=workdir,
    )

    assert rc == 2
    assert "Missing option '--family'" in err
    assert _read_queue_ids(context, task_state_queue_name("1770000000000000502")) >= {
        old_id,
        new_id,
    }


def test_system_prune_rejects_invalid_options(workdir: Path) -> None:
    rc, _out, err = run_cli(
        "system",
        "prune",
        "--family",
        "runtime-state",
        "--queue",
        "nonsense",
        "--context",
        workdir,
        cwd=workdir,
    )

    assert rc == 2
    assert "unknown runtime-state queue filter" in err

    rc, _out, err = run_cli(
        "system",
        "prune",
        "--family",
        "runtime-state",
        "--keep-recent-per-key",
        "0",
        "--context",
        workdir,
        cwd=workdir,
    )

    assert rc == 2
    assert "--keep-recent-per-key must be >= 1" in err


def test_task_monitor_stdout_json_conflict_is_usage_error(workdir: Path) -> None:
    rc, _out, err = run_cli(
        "system",
        "task-monitor",
        "--sink",
        "stdout",
        "--json",
        "--once",
        "--context",
        workdir,
        cwd=workdir,
    )

    assert rc == 2
    assert "--json cannot be combined with --sink stdout" in err
    assert "Traceback" not in err


@pytest.mark.parametrize("verb", ["stop", "kill"])
def test_single_task_control_unavailable_metadata_fails_without_traceback(
    workdir: Path,
    verb: str,
) -> None:
    rc, _out, err = run_cli(
        "task",
        verb,
        "1777000000000000999",
        "--context",
        workdir,
        cwd=workdir,
    )

    assert rc == 1
    assert "observation metadata unavailable" in err
    assert "Traceback" not in err

    rc, _out, err = run_cli(
        "system",
        "builtins",
        "--json",
        cwd=workdir,
    )

    assert rc == 0


def test_task_stop_terminal_task_is_rejected_without_control_queue_residue(
    workdir: Path,
) -> None:
    context = build_context(spec_context=workdir)
    tid = "1777000000000000998"
    _write_task_log(
        context,
        {
            "event": "work_completed",
            "type": "terminal",
            "source": "task",
            "status": "completed",
            "tid": tid,
            "taskspec": {
                "tid": tid,
                "name": "already-completed",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                    "runner": {"name": "host", "options": {}},
                },
                "io": {
                    "outputs": {"outbox": f"T{tid}.outbox"},
                    "control": {
                        "ctrl_in": f"T{tid}.ctrl_in",
                        "ctrl_out": f"T{tid}.ctrl_out",
                    },
                },
                "state": {"status": "completed"},
            },
        },
    )

    rc, out, err = run_cli(
        "task",
        "stop",
        tid,
        "--context",
        workdir,
        cwd=workdir,
    )

    assert rc == 1
    assert out == ""
    assert f"Task {tid} already completed" in err
    assert "Traceback" not in err
    with context.broker() as broker:
        queue_names = {item.queue for item in broker.list_queue_stats()}
    assert f"T{tid}.ctrl_in" not in queue_names


def test_system_prune_retention_dry_run_json(workdir: Path) -> None:
    context = build_context(spec_context=workdir)
    _write_queue_json(
        context,
        WEFT_GLOBAL_LOG_QUEUE,
        {"tid": "1770000000000000600", "status": "created"},
    )
    _write_queue_json(
        context,
        WEFT_GLOBAL_LOG_QUEUE,
        {
            "tid": "1770000000000000600",
            "status": "completed",
            "type": "terminal",
            "source": "task",
        },
    )

    rc, out, err = run_cli(
        "system",
        "prune",
        "--family",
        "task-log",
        "--dry-run",
        "--json",
        "--min-age",
        "0",
        "--context",
        workdir,
        cwd=workdir,
    )

    assert rc == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["family"] == "task-log"
    assert payload["dry_run"] is True
    assert payload["candidate_class_counts"] == {
        RETENTION_PRUNE_CLASS_NONTERMINAL_TASK_LOG_SUPERSEDED: 1
    }


def test_system_prune_all_plain_output_labels_each_summary(workdir: Path) -> None:
    rc, out, err = run_cli(
        "system",
        "prune",
        "--family",
        "all",
        "--context",
        workdir,
        cwd=workdir,
    )

    assert rc == 0
    assert err == ""
    assert out.startswith("Runtime state:\n")
    assert "\nRetention:\n" in out


def test_system_prune_retention_apply_requires_archive_unless_force(
    workdir: Path,
) -> None:
    context = build_context(spec_context=workdir)
    _write_queue_json(
        context,
        WEFT_GLOBAL_LOG_QUEUE,
        {"tid": "1770000000000000601", "status": "created"},
    )
    _write_queue_json(
        context,
        WEFT_GLOBAL_LOG_QUEUE,
        {
            "tid": "1770000000000000601",
            "status": "completed",
            "type": "terminal",
            "source": "task",
        },
    )

    rc, _out, err = run_cli(
        "system",
        "prune",
        "--family",
        "task-log",
        "--apply",
        "--json",
        "--min-age",
        "0",
        "--context",
        workdir,
        cwd=workdir,
    )

    assert rc == 2
    assert "--archive is required" in err

    rc, out, err = run_cli(
        "system",
        "prune",
        "--family",
        "task-log",
        "--apply",
        "--force",
        "--json",
        "--min-age",
        "0",
        "--context",
        workdir,
        cwd=workdir,
    )

    assert rc == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["force"] is True
    assert payload["deleted"] >= 1
    assert payload["archived"] >= 1
    assert list(
        (build_context(spec_context=workdir).logs_dir / "retention-prune").glob(
            "*.jsonl"
        )
    )


def test_system_prune_rejects_invalid_retention_options(workdir: Path) -> None:
    rc, _out, err = run_cli(
        "system",
        "prune",
        "--family",
        "nonsense",
        "--context",
        workdir,
        cwd=workdir,
    )

    assert rc == 2
    assert "unknown prune family" in err

    rc, _out, err = run_cli(
        "system",
        "prune",
        "--family",
        "task-local",
        "--force",
        "--context",
        workdir,
        cwd=workdir,
    )

    assert rc == 2
    assert "--force requires --apply" in err

    rc, _out, err = run_cli(
        "system",
        "prune",
        "--family",
        "runtime-state",
        "--apply",
        "--force",
        "--context",
        workdir,
        cwd=workdir,
    )

    assert rc == 2
    assert "--force is only supported for retention prune families" in err


SYSTEM_STOP_TEST_WAIT_SECONDS = 60.0
"""Bound for services to appear and for stopped processes to be reaped."""

SYSTEM_STOP_REJECTION_TIMEOUT = MANAGER_CHILD_STOP_ESCALATION_SECONDS + 1.0
"""A stop window long enough for both escalation rungs to come due."""


def _wait_for(predicate: Callable[[], object], *, message: str) -> object:
    deadline = time.monotonic() + SYSTEM_STOP_TEST_WAIT_SECONDS
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        # Process exit and registry rows written by other processes have no
        # event this test can block on; poll on a short bounded interval.
        time.sleep(0.1)
    raise AssertionError(f"Timed out waiting for {message}")


def _wait_for_exit(pid: int) -> None:
    _wait_for(lambda: not pid_is_live(pid), message=f"PID {pid} exit")


def _live_service_pids(context: WeftContext) -> dict[str, int]:
    """Map each internal service key to the PID of its latest live registry row."""

    queue = context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
    try:
        latest: dict[str, tuple[int, dict[str, object]]] = {}
        for payload, timestamp in iter_queue_json_entries(queue):
            key = payload.get("service_key")
            if key in INTERNAL_SERVICE_KEYS and (
                key not in latest or latest[key][0] < timestamp
            ):
                latest[str(key)] = (timestamp, payload)
    finally:
        queue.close()
    pids: dict[str, int] = {}
    for key, (_timestamp, payload) in latest.items():
        handle = payload.get("runtime_handle")
        observations = handle.get("observations") if isinstance(handle, dict) else None
        host_pids = (
            observations.get("host_pids") if isinstance(observations, dict) else None
        )
        if (
            payload.get("status") in LIVE_SERVICE_STATUSES
            and isinstance(host_pids, list)
            and host_pids
            and pid_is_live(host_pids[0])
        ):
            pids[key] = int(host_pids[0])
    return pids


def _start_manager(harness: WeftTestHarness, **env: str) -> int:
    """Start a Manager through the CLI and return its PID."""

    rc, out, err = run_cli(
        "manager",
        "start",
        "--context",
        harness.root,
        cwd=harness.root,
        env={**os.environ, **env},
        harness=harness,
    )
    assert rc == 0, (rc, out, err)
    records = harness._list_active_manager_records()
    assert len(records) == 1
    return harness._mapping_host_pids(records[0])[0]


@pytest.fixture
def sleeper() -> Iterator[subprocess.Popen[bytes]]:
    """A live host process that never reads a control queue."""

    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        yield process
    finally:
        process.kill()
        process.wait(timeout=10)


def _write_fake_service_record(
    context: WeftContext,
    process: subprocess.Popen[bytes],
    *,
    ctrl_in: bool,
    hostname: str | None = None,
    create_time_offset: float | None = 0.0,
) -> str:
    """Publish a live heartbeat registry row and snapshot for ``process``.

    ``hostname`` defaults to this host; ``create_time_offset`` of ``None``
    omits the recorded creation time, a non-zero offset records a wrong one.
    """

    tid = generate_tid(context)
    pid = process.pid
    observed = process_create_time(pid)
    assert observed is not None
    process_entry: dict[str, object] = {"pid": pid}
    if create_time_offset is not None:
        process_entry["create_time"] = observed + create_time_offset
    payload = build_service_owner_payload(
        service_key=INTERNAL_SERVICE_KEY_HEARTBEAT,
        service_type=SERVICE_TYPE_MANAGED,
        owner_tid=tid,
        status="active",
        name="heartbeat-service",
        queues={"ctrl_in": f"T{tid}.ctrl_in"} if ctrl_in else {},
        runtime_handle={
            "runner": "host",
            "kind": "process",
            "id": str(pid),
            "control": {"authority": "host-pid"},
            "observations": {
                "host_pids": [pid],
                "host_processes": [process_entry],
            },
            "metadata": {},
        },
    )
    _write_message(context, WEFT_SERVICES_REGISTRY_QUEUE, json.dumps(payload))
    snapshot = {
        "full": tid,
        "short": tid[-10:],
        "hostname": hostname or socket.gethostname(),
        "terminal": False,
    }
    _write_message(context, task_state_queue_name(tid), json.dumps(snapshot))
    return tid


def test_system_stop_stops_manager_then_every_internal_service(
    weft_harness: WeftTestHarness,
) -> None:
    """`weft system stop` leaves no Manager or service process and exits 0.

    Spec: docs/specifications/03-Manager_Architecture.md [MA-3] (system stop).
    """

    manager_pid = _start_manager(
        weft_harness,
        WEFT_TASK_MONITOR_ENABLED="1",
        WEFT_LIVENESS_MONITOR_ENABLED="1",
    )
    services = _wait_for(
        lambda: (
            pids if len(pids := _live_service_pids(weft_harness.context)) == 3 else None
        ),
        message="three live internal services",
    )
    assert isinstance(services, dict)

    rc, out, err = run_cli(
        "system", "stop", "--context", weft_harness.root, cwd=weft_harness.root
    )

    assert rc == 0, (rc, out, err)
    assert "Stopped manager" in out
    for pid in (manager_pid, *services.values()):
        _wait_for_exit(pid)


def test_system_stop_without_manager_stops_live_services(
    weft_harness: WeftTestHarness,
) -> None:
    """With no Manager, `weft system stop` still stops live services, exit 0.

    Spec: docs/specifications/03-Manager_Architecture.md [MA-3] (system stop).
    """

    manager_pid = _start_manager(weft_harness)
    heartbeat_pid = _wait_for(
        lambda: _live_service_pids(weft_harness.context).get(
            INTERNAL_SERVICE_KEY_HEARTBEAT
        ),
        message="a live heartbeat service",
    )
    assert isinstance(heartbeat_pid, int)
    psutil.Process(manager_pid).kill()
    _wait_for_exit(manager_pid)
    assert pid_is_live(heartbeat_pid)

    rc, out, err = run_cli(
        "system", "stop", "--context", weft_harness.root, cwd=weft_harness.root
    )

    assert rc == 0, (rc, out, err)
    assert "Stopped manager" not in out
    assert f"Stopped service {INTERNAL_SERVICE_KEY_HEARTBEAT}" in out
    _wait_for_exit(heartbeat_pid)


def test_system_stop_reports_unreachable_service_without_signalling_it(
    workdir: Path, sleeper: subprocess.Popen[bytes]
) -> None:
    """A live record with no control route is reported, never guessed.

    Spec: docs/specifications/03-Manager_Architecture.md [MA-3] (system stop).
    """

    context = build_context(spec_context=workdir)
    tid = _write_fake_service_record(context, sleeper, ctrl_in=False)

    rc, out, err = run_cli("system", "stop", "--context", workdir, cwd=workdir)

    assert rc == 0, (rc, out, err)
    assert f"Unreachable service {INTERNAL_SERVICE_KEY_HEARTBEAT} {tid}" in err
    assert sleeper.poll() is None


def test_system_stop_force_escalates_a_service_that_ignores_stop(
    workdir: Path, sleeper: subprocess.Popen[bytes]
) -> None:
    """A same-host host-pid service that ignores STOP is escalated and stops.

    Spec: docs/specifications/03-Manager_Architecture.md [MA-3] (system stop).
    """

    context = build_context(spec_context=workdir)
    tid = _write_fake_service_record(context, sleeper, ctrl_in=True)

    rc, out, err = run_cli(
        "system", "stop", "--force", "--context", workdir, cwd=workdir
    )

    assert rc == 0, (rc, out, err)
    assert f"Stopped service {INTERNAL_SERVICE_KEY_HEARTBEAT} {tid} (escalated)" in out
    assert sleeper.wait(timeout=SYSTEM_STOP_TEST_WAIT_SECONDS) is not None


def _settle_fake_service(context: WeftContext, tid: str) -> None:
    """Publish terminal proof so harness cleanup does not wait on the record."""

    snapshot = {"full": tid, "short": tid[-10:], "terminal": True}
    _write_message(context, task_state_queue_name(tid), json.dumps(snapshot))


def _failing_registry_reads(
    monkeypatch: pytest.MonkeyPatch, *, succeed_first: int
) -> list[str]:
    """Make registry reads after the first ``succeed_first`` fail to open.

    The real reader runs against a broker whose generator cannot open, so the
    reader's own ``strict`` mode decides whether the failure is raised.
    """

    calls: list[str] = []
    real = system_commands.iter_broker_queue_json_entries

    class _UnreadableBroker:
        def peek_generator(self, *_args: object, **_kwargs: object) -> None:
            raise BrokerError("injected registry read failure")

    def reader(
        broker: object, queue_name: str, **kwargs: Any
    ) -> Iterator[tuple[dict[str, Any], int]]:
        calls.append(queue_name)
        if len(calls) > succeed_first:
            broker = _UnreadableBroker()
        return real(broker, queue_name, **kwargs)

    monkeypatch.setattr(system_commands, "iter_broker_queue_json_entries", reader)
    return calls


def test_system_stop_registry_read_failure_at_collection_is_an_error(
    workdir: Path,
    sleeper: subprocess.Popen[bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A registry that cannot be read is never "nothing to stop" [MA-3]."""

    context = build_context(spec_context=workdir)
    _write_fake_service_record(context, sleeper, ctrl_in=True)
    calls = _failing_registry_reads(monkeypatch, succeed_first=0)

    with pytest.raises(CommandExecutionError, match="injected registry"):
        system_commands.stop_system(context, timeout=5.0)

    assert calls
    assert sleeper.poll() is None


def test_system_stop_registry_read_failure_at_settlement_is_an_error(
    workdir: Path,
    sleeper: subprocess.Popen[bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed settlement read never confirms a pending service stopped [MA-3]."""

    context = build_context(spec_context=workdir)
    tid = _write_fake_service_record(
        context, sleeper, ctrl_in=True, create_time_offset=None
    )
    calls = _failing_registry_reads(monkeypatch, succeed_first=1)

    with pytest.raises(CommandExecutionError, match="injected registry"):
        system_commands.stop_system(context, timeout=5.0)

    assert len(calls) >= 2
    assert sleeper.poll() is None
    _settle_fake_service(context, tid)


def test_system_stop_rejects_a_service_that_does_not_settle(
    workdir: Path, sleeper: subprocess.Popen[bytes]
) -> None:
    """A reachable service that ignores STOP past the timeout is rejected.

    Its recorded identity has no creation time, so it is unverifiable and
    neither escalation rung may signal it ([MA-3]; LIVENESS.R5).
    """

    context = build_context(spec_context=workdir)
    tid = _write_fake_service_record(
        context, sleeper, ctrl_in=True, create_time_offset=None
    )

    with pytest.raises(ControlRejected, match=tid):
        system_commands.stop_system(context, timeout=SYSTEM_STOP_REJECTION_TIMEOUT)

    assert sleeper.poll() is None
    _settle_fake_service(context, tid)


def test_system_stop_never_signals_a_foreign_host_service(
    workdir: Path, sleeper: subprocess.Popen[bytes]
) -> None:
    """A snapshot naming another host disables both escalation rungs [MA-3]."""

    context = build_context(spec_context=workdir)
    tid = _write_fake_service_record(
        context, sleeper, ctrl_in=True, hostname="weft-foreign-host.invalid"
    )

    with pytest.raises(ControlRejected, match=tid):
        system_commands.stop_system(context, timeout=SYSTEM_STOP_REJECTION_TIMEOUT)

    assert sleeper.poll() is None
    _settle_fake_service(context, tid)


def test_system_stop_never_signals_a_reused_pid(
    workdir: Path, sleeper: subprocess.Popen[bytes]
) -> None:
    """A recorded identity whose creation time differs names an exited service.

    The PID now belongs to another process: the record is settled, not a
    target, and that process is never signalled (LIVENESS.R5).
    """

    context = build_context(spec_context=workdir)
    _write_fake_service_record(context, sleeper, ctrl_in=True, create_time_offset=5.0)

    result = system_commands.stop_system(context, timeout=SYSTEM_STOP_REJECTION_TIMEOUT)

    assert (result.stopped, result.escalated, result.unreachable) == ((), (), ())
    assert sleeper.poll() is None


def test_system_dump_exports_messages(workdir: Path) -> None:
    context = build_context(spec_context=workdir)
    _write_message(context, "dump.test", "payload")

    output_path = workdir / "weft_export.jsonl"

    rc, out, err = run_cli(
        "system",
        "dump",
        "--output",
        output_path,
        "--context",
        workdir,
        cwd=workdir,
    )

    assert rc == 0
    assert err == ""
    assert "Exported" in out
    assert output_path.exists()

    lines = output_path.read_text(encoding="utf-8").splitlines()
    assert any('"queue": "dump.test"' in line for line in lines)


def test_system_dump_excludes_runtime_queues(workdir: Path) -> None:
    context = build_context(spec_context=workdir)
    _write_message(context, "persist.test", "keep")
    _write_message(context, "weft.state.test_runtime", "skip")

    output_path = workdir / "weft_export_runtime.jsonl"

    rc, out, err = run_cli(
        "system",
        "dump",
        "--output",
        output_path,
        "--context",
        workdir,
        cwd=workdir,
    )

    assert rc == 0
    assert err == ""
    assert "Exported" in out

    content = output_path.read_text(encoding="utf-8")
    assert "persist.test" in content
    assert "weft.state.test_runtime" not in content


def test_system_load_imports_dump(workdir: Path) -> None:
    source_dir = workdir / "source"
    prepare_project_root(source_dir)
    source_context = build_context(spec_context=source_dir)
    _write_message(source_context, "import.test", "hello")

    export_path = workdir / "export.jsonl"
    rc, out, err = run_cli(
        "system",
        "dump",
        "--output",
        export_path,
        "--context",
        source_dir,
        cwd=source_dir,
    )

    assert rc == 0
    assert err == ""
    assert export_path.exists()

    target_dir = workdir / "target"
    prepare_project_root(target_dir)

    rc, out, err = run_cli(
        "system",
        "load",
        "--input",
        export_path,
        "--dry-run",
        "--context",
        target_dir,
        cwd=target_dir,
    )

    assert rc == 0
    assert err == ""
    assert "Import Preview" in out

    rc, out, err = run_cli(
        "system",
        "load",
        "--input",
        export_path,
        "--context",
        target_dir,
        cwd=target_dir,
    )

    assert rc == 0
    assert err == ""
    assert "Import completed" in out

    target_context = build_context(spec_context=target_dir)
    queue = target_context.queue("import.test", persistent=True)
    try:
        assert queue.peek_one() == "hello"
    finally:
        queue.close()


def test_system_task_monitor_stdout_jsonl(workdir: Path) -> None:
    context = build_context(spec_context=workdir)
    tid = "1778084345905438730"
    _write_task_log(
        context,
        {
            "event": "work_completed",
            "type": "terminal",
            "source": "task",
            "status": "completed",
            "tid": tid,
            "taskspec": {
                "tid": tid,
                "name": "cli-task-monitor",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                    "runner": {"name": "host", "options": {}},
                },
                "io": {
                    "outputs": {"outbox": f"T{tid}.outbox"},
                    "control": {
                        "ctrl_in": f"T{tid}.ctrl_in",
                        "ctrl_out": f"T{tid}.ctrl_out",
                    },
                },
                "state": {"status": "completed"},
            },
        },
    )

    rc, out, err = run_cli(
        "system",
        "task-monitor",
        "--once",
        "--sink",
        "stdout",
        "--no-checkpoint",
        "--since",
        "0",
        "--context",
        workdir,
        cwd=workdir,
    )

    assert rc == 0
    assert err == ""
    records = [json.loads(line) for line in out.splitlines() if line.strip()]
    assert any(record["record_type"] == "monitor_run_started" for record in records)
    assert any(
        record["record_type"] == "task_summary"
        and record["classification"] == "terminal_log"
        for record in records
    )


def test_system_task_monitor_rejects_stdout_json(workdir: Path) -> None:
    rc, out, err = run_cli(
        "system",
        "task-monitor",
        "--sink",
        "stdout",
        "--json",
        "--context",
        workdir,
        cwd=workdir,
    )

    assert rc == 2
    assert out == ""
    assert "cannot be combined" in err


def test_system_load_alias_conflict_uses_exit_3(workdir: Path) -> None:
    context = build_context(spec_context=workdir)
    with context.broker() as broker:
        broker.add_alias("existing_alias", "old.target")
    dump_path = workdir / "alias-conflict.jsonl"
    dump_path.write_text(
        "\n".join(
            (
                json.dumps(
                    {
                        "type": "header",
                        "format": "simplebroker-dump",
                        "version": 1,
                        "backend": "test",
                        "last_ts": 0,
                    }
                ),
                json.dumps(
                    {
                        "type": "alias",
                        "alias": "existing_alias",
                        "target": "new.target",
                    }
                ),
                "",
            )
        ),
        encoding="utf-8",
    )

    rc, out, err = run_cli(
        "system",
        "load",
        "--input",
        dump_path,
        "--context",
        workdir,
        cwd=workdir,
    )

    assert rc == 3
    assert out == ""
    assert "alias conflicts" in err


def test_system_task_monitor_follow_closes_stream_on_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class InterruptingStream:
        closed = False
        yielded = False

        def __iter__(self) -> InterruptingStream:
            return self

        def __next__(self) -> TaskMonitorSummary:
            if not self.yielded:
                self.yielded = True
                return TaskMonitorSummary(record={"tid": "1777000000000000001"})
            raise KeyboardInterrupt

        def close(self) -> None:
            self.closed = True

    stream = InterruptingStream()
    monkeypatch.setattr(
        "weft.cli.app.commands.cmd_system_task_monitor",
        lambda **_kwargs: stream,
    )

    result = CliRunner().invoke(app, ["system", "task-monitor", "--follow"])

    assert result.exit_code == 0
    assert stream.closed is True
    assert "1777000000000000001" in result.stdout
    assert "Traceback" not in result.stderr


def test_system_task_monitor_disk_json_summary(workdir: Path) -> None:
    context = build_context(spec_context=workdir)
    tid = "1778084345905438731"
    _write_task_log(
        context,
        {
            "event": "work_completed",
            "type": "terminal",
            "source": "task",
            "status": "completed",
            "tid": tid,
            "taskspec": {
                "tid": tid,
                "name": "cli-task-monitor-disk",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                    "runner": {"name": "host", "options": {}},
                },
                "io": {
                    "outputs": {"outbox": f"T{tid}.outbox"},
                    "control": {
                        "ctrl_in": f"T{tid}.ctrl_in",
                        "ctrl_out": f"T{tid}.ctrl_out",
                    },
                },
                "state": {"status": "completed"},
            },
        },
    )
    log_dir = workdir / "logs"
    checkpoint = workdir / "checkpoint.json"

    rc, out, err = run_cli(
        "system",
        "task-monitor",
        "--once",
        "--sink",
        "disk",
        "--log-dir",
        log_dir,
        "--checkpoint",
        checkpoint,
        "--json",
        "--since",
        "0",
        "--context",
        workdir,
        cwd=workdir,
    )

    assert rc == 0
    assert err == ""
    summary = json.loads(out)
    assert summary["summaries_emitted"] >= 1
    assert checkpoint.exists()
    assert list(log_dir.glob("*.jsonl"))


def test_system_task_monitor_uses_default_logs_dir(workdir: Path) -> None:
    context = build_context(spec_context=workdir)
    tid = "1778084345905438732"
    _write_task_log(
        context,
        {
            "event": "work_completed",
            "type": "terminal",
            "source": "task",
            "status": "completed",
            "tid": tid,
            "taskspec": {
                "tid": tid,
                "name": "cli-task-monitor-default-log-dir",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                    "runner": {"name": "host", "options": {}},
                },
                "state": {"status": "completed"},
            },
        },
    )

    rc, out, err = run_cli(
        "system",
        "task-monitor",
        "--once",
        "--sink",
        "disk",
        "--json",
        "--no-checkpoint",
        "--since",
        "0",
        "--context",
        workdir,
        cwd=workdir,
    )

    assert rc == 0
    assert err == ""
    summary = json.loads(out)
    expected_dir = (workdir / ".weft" / "logs" / "task-monitor").resolve()
    assert Path(summary["log_path"]).parent == expected_dir
    assert list(expected_dir.glob("*.jsonl"))


def test_system_task_monitor_rejects_removed_archive_dir_option(workdir: Path) -> None:
    rc, out, err = run_cli(
        "system",
        "task-monitor",
        "--sink",
        "disk",
        "--archive" + "-dir",
        workdir / "archive",
        "--context",
        workdir,
        cwd=workdir,
    )

    assert rc != 0
    assert out == ""
    assert "archive-dir" in err
