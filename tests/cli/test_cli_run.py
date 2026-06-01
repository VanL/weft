"""End-to-end tests for `weft run`."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import run_cli
from tests.fixtures.provider_cli_fixture import (
    PROVIDER_FIXTURE_NAMES,
    write_provider_cli_wrapper,
)
from tests.helpers.test_backend import active_test_backend
from tests.helpers.weft_harness import WeftTestHarness
from tests.taskspec import fixtures as taskspec_fixtures
from weft._constants import (
    TERMINAL_TASK_STATUSES,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_MANAGER_OUTBOX_QUEUE,
    WEFT_SERVICES_REGISTRY_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
)
from weft.commands import manager as manager_cmd
from weft.commands import tasks as task_cmd
from weft.context import WeftContext, build_context
from weft.core.endpoints import resolve_endpoint
from weft.core.service_convergence import build_manager_service_payload
from weft.helpers import pid_is_live

PROCESS_SCRIPT = Path(__file__).resolve().parents[1] / "tasks" / "process_target.py"
INTERACTIVE_SCRIPT = (
    Path(__file__).resolve().parents[1] / "tasks" / "interactive_echo.py"
)
PARALLEL_MANAGER_REUSE_CONSTRAINED_TASK_TIMEOUT = 120.0
"""Timeout for Windows/macOS parallel manager reuse tests under CI load.

These tests intentionally combine subprocess bootstrap, SQLite coordination,
and xdist-level parallelism. Windows 3.14 CI has shown that a child can reach
``work_started`` and then wait far longer than the generic 30s constrained
timeout before committing its outbox and terminal event.
"""


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_queue_message(
    context: WeftContext,
    name: str,
    payload: str,
    *,
    persistent: bool = True,
) -> None:
    queue = context.queue(name, persistent=persistent)
    try:
        queue.write(payload)
    finally:
        queue.close()


def _host_runtime_handle(pid: int) -> dict[str, Any]:
    return {
        "runner": "host",
        "kind": "process",
        "id": str(pid),
        "control": {"authority": "host-pid"},
        "observations": {"host_pids": [pid]},
        "metadata": {},
    }


def _manager_service_payload(
    context: WeftContext,
    *,
    tid: str,
    name: str = "manager",
    runtime_handle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return build_manager_service_payload(
        context=context,
        tid=tid,
        name=name,
        status="active",
        queues={
            "requests": WEFT_SPAWN_REQUESTS_QUEUE,
            "ctrl_in": f"T{tid}.ctrl_in",
            "ctrl_out": f"T{tid}.ctrl_out",
            "outbox": WEFT_MANAGER_OUTBOX_QUEUE,
        },
        runtime_handle=runtime_handle or {},
    )


def _wait_for_endpoint_claim(
    context: WeftContext,
    *,
    name: str,
    tid: str,
    timeout: float = 30.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_resolved: dict[str, Any] | None = None
    last_snapshot: Any | None = None
    while time.monotonic() < deadline:
        resolved = resolve_endpoint(context, name)
        if resolved is not None:
            last_resolved = resolved.to_dict()
            if resolved.record.tid == tid:
                return last_resolved

        snapshot = task_cmd.task_status(tid, context_path=context.root)
        if snapshot is not None:
            last_snapshot = snapshot
            if snapshot.status in TERMINAL_TASK_STATUSES:
                raise AssertionError(
                    "Endpoint owner reached a terminal state before claiming "
                    f"{name!r}: snapshot={snapshot!r}; resolved={last_resolved!r}"
                )
        time.sleep(0.05)

    raise AssertionError(
        f"Timed out waiting for endpoint {name!r} to resolve to {tid}; "
        f"last_resolved={last_resolved!r}; last_snapshot={last_snapshot!r}"
    )


def _wait_for_endpoint_release(
    context: WeftContext,
    *,
    name: str,
    tid: str,
    timeout: float = 30.0,
) -> None:
    deadline = time.monotonic() + timeout
    last_resolved: dict[str, Any] | None = None
    last_snapshot: Any | None = None
    while time.monotonic() < deadline:
        resolved = resolve_endpoint(context, name)
        if resolved is None:
            return
        last_resolved = resolved.to_dict()
        last_snapshot = task_cmd.task_status(tid, context_path=context.root)
        time.sleep(0.05)

    raise AssertionError(
        f"Timed out waiting for endpoint {name!r} to release; "
        f"last_resolved={last_resolved!r}; last_snapshot={last_snapshot!r}"
    )


def _wait_for_task_waiting_on_input(
    context: WeftContext,
    *,
    tid: str,
    queue_name: str,
    timeout: float = 30.0,
) -> None:
    deadline = time.monotonic() + timeout
    last_snapshot: Any | None = None
    while time.monotonic() < deadline:
        snapshot = task_cmd.task_status(tid, context_path=context.root)
        if snapshot is not None:
            last_snapshot = snapshot
            if snapshot.status == "running" or (
                snapshot.activity == "waiting" and snapshot.waiting_on == queue_name
            ):
                return
            if snapshot.status in TERMINAL_TASK_STATUSES:
                raise AssertionError(
                    "Task reached a terminal state before becoming ready for "
                    f"input: tid={tid}; queue={queue_name!r}; snapshot={snapshot!r}"
                )
        time.sleep(0.05)

    raise AssertionError(
        f"Timed out waiting for task {tid} to wait on {queue_name!r}; "
        f"last_snapshot={last_snapshot!r}"
    )


def _create_stored_task_spec(
    workdir: Path,
    *,
    name: str,
    payload: dict[str, object],
    harness: WeftTestHarness,
) -> None:
    spec_path = workdir / f"{name}.json"
    _write_json(spec_path, payload)
    rc, out, err = run_cli(
        "spec",
        "create",
        name,
        "--type",
        "task",
        "--file",
        spec_path,
        "--context",
        workdir,
        cwd=workdir,
        harness=harness,
    )
    assert rc == 0, (out, err)
    assert err == ""


def _latest_completed_record(harness, limit: int = 512) -> tuple[str, dict] | None:
    queue = harness.context.queue(
        WEFT_GLOBAL_LOG_QUEUE,
        persistent=False,
    )
    try:
        records = queue.peek_many(limit=limit, with_timestamps=True) or []
    finally:
        queue.close()
    for payload, _ in reversed(records):
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        tid = data.get("tid")
        if isinstance(tid, str) and data.get("event") == "work_completed":
            return tid, data
    return None


def _assert_sqlite_integrity(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    finally:
        connection.close()
    assert result == ("ok",)


def _parallel_manager_list_snapshot(root: Path) -> Any:
    exit_code, payload = manager_cmd.list_command(
        json_output=True,
        include_stopped=True,
        context_path=root,
    )
    if payload is None:
        return {"exit_code": exit_code, "payload": None}
    try:
        return {
            "exit_code": exit_code,
            "payload": json.loads(payload),
        }
    except json.JSONDecodeError:
        return {
            "exit_code": exit_code,
            "payload": payload,
        }


def _raise_parallel_manager_reuse_failure(
    *,
    phase: str,
    root: Path,
    env: dict[str, str],
    harness: WeftTestHarness,
    max_workers: int,
    submit_timeout: float,
    status_timeout: float,
    submit_results: list[tuple[int, str, str]] | None = None,
    status_observations: list[dict[str, Any]] | None = None,
    status_payload: dict[str, object] | None = None,
    detail: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "phase": phase,
        "root": str(root),
        "max_workers": max_workers,
        "submit_timeout": submit_timeout,
        "status_timeout": status_timeout,
        "reuse_enabled": env.get("WEFT_MANAGER_REUSE_ENABLED"),
        "broker_test_backend": env.get(
            "BROKER_TEST_BACKEND", os.environ.get("BROKER_TEST_BACKEND", "sqlite")
        ),
        "submit_results": submit_results,
        "status_observations": status_observations,
        "status_payload": status_payload,
        "manager_list_snapshot": _parallel_manager_list_snapshot(root),
        "harness": harness.dump_debug_state(),
    }
    if detail is not None:
        payload["detail"] = detail
    raise AssertionError(json.dumps(payload, indent=2, ensure_ascii=False))


def _run_parallel_manager_reuse_cycle(
    *,
    root: Path,
    env: dict[str, str],
    harness: WeftTestHarness,
    max_workers: int,
    submit_timeout: float,
    status_timeout: float,
    task_completion_timeout: float,
) -> dict[str, object]:
    def _submit(
        current_root: Path = root,
        current_env: dict[str, str] = env,
        current_timeout: float = submit_timeout,
    ) -> tuple[int, str, str]:
        return run_cli(
            "run",
            "--no-wait",
            "--function",
            "tests.tasks.sample_targets:simulate_work",
            "--kw",
            "duration=0.2",
            "--kw",
            'result="ok"',
            cwd=current_root,
            env=current_env,
            harness=harness,
            prepare_root=False,
            timeout=current_timeout,
        )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(lambda _index: _submit(), range(4)))

    if not all(rc == 0 for rc, _out, _err in results):
        _raise_parallel_manager_reuse_failure(
            phase="submit_nonzero",
            root=root,
            env=env,
            harness=harness,
            max_workers=max_workers,
            submit_timeout=submit_timeout,
            status_timeout=status_timeout,
            submit_results=results,
        )
    if not all(err == "" for _rc, _out, err in results):
        _raise_parallel_manager_reuse_failure(
            phase="submit_stderr",
            root=root,
            env=env,
            harness=harness,
            max_workers=max_workers,
            submit_timeout=submit_timeout,
            status_timeout=status_timeout,
            submit_results=results,
        )
    if not all(len(out) == 19 and out.isdigit() for _rc, out, _err in results):
        _raise_parallel_manager_reuse_failure(
            phase="submit_bad_tid",
            root=root,
            env=env,
            harness=harness,
            max_workers=max_workers,
            submit_timeout=submit_timeout,
            status_timeout=status_timeout,
            submit_results=results,
        )

    # Superseded managers are allowed to stay registered while draining any
    # children they already launched. Manager convergence is only a stable
    # invariant after the submitted no-wait work has reached terminal state.
    for _rc, out, _err in results:
        tid = out.strip()
        try:
            harness.wait_for_terminal_state(tid, timeout=task_completion_timeout)
        except TimeoutError as exc:
            _raise_parallel_manager_reuse_failure(
                phase="task_completion_timeout",
                root=root,
                env=env,
                harness=harness,
                max_workers=max_workers,
                submit_timeout=submit_timeout,
                status_timeout=status_timeout,
                submit_results=results,
                detail=f"tid={tid}: {exc}",
            )
        except RuntimeError as exc:
            _raise_parallel_manager_reuse_failure(
                phase="task_unexpected_terminal_state",
                root=root,
                env=env,
                harness=harness,
                max_workers=max_workers,
                submit_timeout=submit_timeout,
                status_timeout=status_timeout,
                submit_results=results,
                detail=f"tid={tid}: {exc}",
            )

    deadline = time.time() + status_timeout
    payload: dict[str, object] | None = None
    status_observations: list[dict[str, Any]] = []
    while time.time() < deadline:
        rc, out, err = run_cli(
            "status",
            "--json",
            cwd=root,
            env=env,
            harness=harness,
        )
        if rc != 0:
            status_observations.append(
                {
                    "attempt": len(status_observations) + 1,
                    "rc": rc,
                    "stderr": err,
                }
            )
            _raise_parallel_manager_reuse_failure(
                phase="status_nonzero",
                root=root,
                env=env,
                harness=harness,
                max_workers=max_workers,
                submit_timeout=submit_timeout,
                status_timeout=status_timeout,
                submit_results=results,
                status_observations=status_observations,
                detail=err,
            )
        try:
            payload = json.loads(out)
        except json.JSONDecodeError:
            status_observations.append(
                {
                    "attempt": len(status_observations) + 1,
                    "rc": rc,
                    "stderr": err,
                    "stdout": out,
                }
            )
            _raise_parallel_manager_reuse_failure(
                phase="status_invalid_json",
                root=root,
                env=env,
                harness=harness,
                max_workers=max_workers,
                submit_timeout=submit_timeout,
                status_timeout=status_timeout,
                submit_results=results,
                status_observations=status_observations,
            )
        managers = payload.get("managers")
        observation: dict[str, Any] = {
            "attempt": len(status_observations) + 1,
            "rc": rc,
            "stderr": err,
            "manager_count": len(managers) if isinstance(managers, list) else None,
        }
        if isinstance(managers, list):
            observation["manager_tids"] = [
                record.get("tid") for record in managers if isinstance(record, dict)
            ]
        else:
            observation["payload"] = payload
        status_observations.append(observation)
        if isinstance(managers, list) and len(managers) == 1:
            break
        time.sleep(0.1)

    if payload is None:
        _raise_parallel_manager_reuse_failure(
            phase="status_no_payload",
            root=root,
            env=env,
            harness=harness,
            max_workers=max_workers,
            submit_timeout=submit_timeout,
            status_timeout=status_timeout,
            submit_results=results,
            status_observations=status_observations,
        )
    managers = payload.get("managers")
    if not isinstance(managers, list):
        _raise_parallel_manager_reuse_failure(
            phase="manager_payload_not_list",
            root=root,
            env=env,
            harness=harness,
            max_workers=max_workers,
            submit_timeout=submit_timeout,
            status_timeout=status_timeout,
            submit_results=results,
            status_observations=status_observations,
            status_payload=payload,
        )
    if len(managers) != 1:
        _raise_parallel_manager_reuse_failure(
            phase="manager_convergence_timeout",
            root=root,
            env=env,
            harness=harness,
            max_workers=max_workers,
            submit_timeout=submit_timeout,
            status_timeout=status_timeout,
            submit_results=results,
            status_observations=status_observations,
            status_payload=payload,
        )
    return payload


def _wait_for_started_task_tid(
    harness,
    *,
    task_name: str,
    timeout: float = 30.0,
) -> str:
    queue = harness.context.queue(
        WEFT_GLOBAL_LOG_QUEUE,
        persistent=False,
    )
    try:
        deadline = time.time() + timeout
        while time.time() < deadline:
            events = queue.peek_many(limit=200, with_timestamps=False) or []
            for raw in reversed(events):
                payload = raw[0] if isinstance(raw, tuple) else raw
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                event = data.get("event")
                if event == "work_started":
                    taskspec = data.get("taskspec") or {}
                    tid = data.get("tid")
                elif event == "task_spawned":
                    taskspec = data.get("child_taskspec") or {}
                    tid = data.get("child_tid")
                else:
                    continue
                if not isinstance(taskspec, dict) or taskspec.get("name") != task_name:
                    continue
                if isinstance(tid, str) and tid:
                    return tid
            time.sleep(0.05)
    finally:
        queue.close()

    raise AssertionError(f"Timed out waiting for started task {task_name!r}")


def _wait_for_task_process_exit(
    harness,
    *,
    tid: str,
    timeout: float = 10.0,
) -> None:
    deadline = time.time() + timeout
    last_mapping: dict[str, object] | None = None
    while time.time() < deadline:
        mapping = task_cmd.mapping_for_tid(harness.context, tid) or {}
        last_mapping = mapping
        candidate_pids: list[int] = []
        for key in ("task_pid", "pid"):
            value = mapping.get(key)
            if isinstance(value, int):
                candidate_pids.append(value)
        managed = mapping.get("managed_pids")
        if isinstance(managed, list):
            candidate_pids.extend(value for value in managed if isinstance(value, int))
        if not any(pid_is_live(pid) for pid in set(candidate_pids)):
            return
        time.sleep(0.05)

    raise AssertionError(
        f"Timed out waiting for task {tid} processes to exit; "
        f"last mapping={last_mapping!r}"
    )


def test_cli_run_function_inline(workdir, weft_harness) -> None:
    rc, out, err = run_cli(
        "run",
        "--function",
        "tests.tasks.sample_targets:echo_payload",
        "--arg",
        "hello",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    assert out == "hello"
    assert err == ""


def test_cli_run_command_inline(workdir, weft_harness) -> None:
    rc, out, err = run_cli(
        "run",
        "--",
        sys.executable,
        str(PROCESS_SCRIPT),
        "--result",
        "cmd-output",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    assert "cmd-output" in out
    assert err == ""


def test_cli_run_help_hides_monitor_and_documents_named_or_path_spec(
    workdir, weft_harness
) -> None:
    rc, out, err = run_cli(
        "run",
        "--help",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    assert err == ""
    assert "--spec NAME|PATH" in out
    assert "--pipeline NAME|PATH" in out
    assert "Execute a task spec by stored name or JSON" in out
    assert "Execute a pipeline by stored name or JSON" in out
    assert "--name TEXT" in out
    assert "claims the named runtime endpoint" in out
    assert "--monitor" not in out


def test_cli_run_spec_help_is_spec_aware_for_builtin_dockerized_agent(
    workdir, weft_harness
) -> None:
    rc, out, err = run_cli(
        "run",
        "--spec",
        "dockerized-agent",
        "--help",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    assert err == ""
    assert "--spec NAME|PATH" in out
    assert "Spec Help: dockerized-agent" in out
    assert "Parameterization Options:" in out
    assert "--provider TEXT" in out
    assert "choices: claude_code, codex, gemini, opencode, qwen" in out
    assert "--model TEXT" in out
    assert "Run Input Options:" in out
    assert "--prompt TEXT" in out
    assert "--document PATH" in out
    assert "Stdin: Document text supplied through stdin [optional]" in out


def test_cli_run_rejects_unknown_long_option_without_spec(
    workdir, weft_harness
) -> None:
    rc, out, err = run_cli(
        "run",
        "--bogus",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc != 0
    assert out == ""
    assert "Unknown option '--bogus'" in err


def test_cli_run_spec_name_resolves_stored_task_spec(workdir, weft_harness) -> None:
    _create_stored_task_spec(
        workdir,
        name="stored-echo",
        payload={
            "name": "stored-echo",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:simulate_work",
                "keyword_args": {"result": "stored-spec-result"},
            },
            "metadata": {},
        },
        harness=weft_harness,
    )

    rc, out, err = run_cli(
        "run",
        "--spec",
        "stored-echo",
        "--context",
        workdir,
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    assert out == "stored-spec-result"
    assert err == ""


def test_cli_run_spec_name_resolves_builtin_probe_helper_and_writes_agent_settings(
    workdir: Path,
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_wrappers = [
        (provider_name, write_provider_cli_wrapper(workdir, provider_name))
        for provider_name in PROVIDER_FIXTURE_NAMES
    ]
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join([str(workdir), env.get("PATH", "")])
    monkeypatch.setenv("PATH", env["PATH"])
    weft_harness.ensure_foreground_manager()

    for _provider_name, wrapper in provider_wrappers:
        resolved = shutil.which(wrapper.name, path=env["PATH"])
        assert resolved is not None
        assert Path(resolved).resolve() == wrapper.resolve()

    rc, out, err = run_cli(
        "run",
        "--spec",
        "probe-agents",
        "--context",
        workdir,
        cwd=workdir,
        env=env,
        harness=weft_harness,
        timeout=180.0,
    )

    assert rc == 0
    payload = json.loads(out)
    assert payload["summary"]["available"] == len(PROVIDER_FIXTURE_NAMES)
    assert payload["summary"]["not_found"] == 0
    assert payload["summary"]["probe_failed"] == 0
    assert payload["summary"]["settings_created"] == len(PROVIDER_FIXTURE_NAMES)
    assert err == ""

    settings_path = workdir / ".weft" / "agents.json"
    settings_payload = json.loads(settings_path.read_text(encoding="utf-8"))
    providers = settings_payload["provider_cli"]["providers"]
    for provider_name, wrapper in provider_wrappers:
        configured_executable = providers[provider_name]["executable"]
        assert Path(configured_executable).resolve() == wrapper.resolve()


def test_cli_run_spec_name_prefers_local_shadow_over_builtin(
    workdir: Path,
    weft_harness: WeftTestHarness,
) -> None:
    _create_stored_task_spec(
        workdir,
        name="probe-agents",
        payload={
            "name": "shadow-probe-agents",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:simulate_work",
                "keyword_args": {"result": "local-shadow-result"},
            },
            "metadata": {"shadow": True},
        },
        harness=weft_harness,
    )

    rc, out, err = run_cli(
        "run",
        "--spec",
        "probe-agents",
        "--context",
        workdir,
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    assert out == "local-shadow-result"
    assert err == ""


def test_cli_run_reads_stdin(workdir, weft_harness) -> None:
    script = workdir / "stdin_script.py"
    script.write_text(
        "import sys\ndata = sys.stdin.read()\nprint(data.upper(), end='')\n",
        encoding="utf-8",
    )

    rc, out, err = run_cli(
        "run",
        "--",
        sys.executable,
        str(script),
        cwd=workdir,
        harness=weft_harness,
        stdin="piped data",
    )

    assert rc == 0
    assert out == "PIPED DATA"
    assert err == ""


def test_cli_run_rejects_oversized_piped_stdin(workdir, weft_harness) -> None:
    script = workdir / "stdin_limit_script.py"
    script.write_text(
        "import sys\nprint(sys.stdin.read(), end='')\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["WEFT_MAX_MESSAGE_SIZE"] = "8"

    rc, out, err = run_cli(
        "run",
        "--",
        sys.executable,
        str(script),
        cwd=workdir,
        harness=weft_harness,
        stdin="123456789",
        env=env,
    )

    assert rc != 0
    combined = f"{out}\n{err}"
    assert "Input exceeds maximum size of 8 bytes" in combined


def test_cli_run_spec_path(workdir, weft_harness) -> None:
    context = build_context(spec_context=workdir)
    _write_queue_message(
        context,
        "cli_spec.inbox",
        json.dumps({"args": ["from-spec"]}),
    )

    spec_path = workdir / "task.json"
    spec_payload = {
        "tid": "1760000000000000100",
        "name": "cli-spec-task",
        "version": "1.0",
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:echo_payload",
            "weft_context": str(workdir),
            "reserved_policy_on_stop": "keep",
            "reserved_policy_on_error": "keep",
        },
        "io": {
            "inputs": {"inbox": "cli_spec.inbox"},
            "outputs": {"outbox": "cli_spec.outbox"},
            "control": {"ctrl_in": "cli_spec.ctrl_in", "ctrl_out": "cli_spec.ctrl_out"},
        },
        "state": {"status": "created"},
        "metadata": {},
    }
    spec_path.write_text(json.dumps(spec_payload, indent=2), encoding="utf-8")

    rc, out, err = run_cli(
        "run",
        "--spec",
        spec_path,
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    assert out == "from-spec"
    assert err == ""


def test_cli_run_spec_without_run_input_rejects_extra_declared_args(
    workdir,
    weft_harness,
) -> None:
    spec_path = workdir / "task_without_run_input.json"
    _write_json(
        spec_path,
        {
            "name": "plain-spec",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
                "weft_context": str(workdir),
                "reserved_policy_on_stop": "keep",
                "reserved_policy_on_error": "keep",
            },
            "metadata": {},
        },
    )

    rc, out, err = run_cli(
        "run",
        "--spec",
        spec_path,
        "--prompt",
        "Summarize",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 2
    assert out == ""
    assert "does not declare spec.run_input" in err


def test_cli_run_spec_path_reads_piped_stdin_into_function(
    workdir, weft_harness
) -> None:
    spec_path = workdir / "task_stdin_function.json"
    spec_payload = {
        "tid": "1760000000000000101",
        "name": "cli-spec-stdin-function",
        "version": "1.0",
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:echo_payload",
            "weft_context": str(workdir),
            "reserved_policy_on_stop": "keep",
            "reserved_policy_on_error": "keep",
        },
        "io": {
            "inputs": {"inbox": "cli_spec_stdin_function.inbox"},
            "outputs": {"outbox": "cli_spec_stdin_function.outbox"},
            "control": {
                "ctrl_in": "cli_spec_stdin_function.ctrl_in",
                "ctrl_out": "cli_spec_stdin_function.ctrl_out",
            },
        },
        "state": {"status": "created"},
        "metadata": {},
    }
    spec_path.write_text(json.dumps(spec_payload, indent=2), encoding="utf-8")

    rc, out, err = run_cli(
        "run",
        "--spec",
        spec_path,
        cwd=workdir,
        harness=weft_harness,
        stdin="from-stdin",
    )

    assert rc == 0
    assert out == "from-stdin"
    assert err == ""


def test_cli_run_spec_bundle_resolves_bundle_local_function_target(
    workdir, weft_harness
) -> None:
    weft_harness.ensure_foreground_manager()
    bundle_dir = workdir / "bundle-task"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "helper_module.py").write_text(
        "\n".join(
            [
                "def bundle_echo(payload: str) -> str:",
                "    return f'bundle:{payload}'",
                "",
            ]
        ),
        encoding="utf-8",
    )
    spec_payload = {
        "name": "bundle-task",
        "version": "1.0",
        "spec": {
            "type": "function",
            "function_target": "helper_module:bundle_echo",
            "weft_context": str(workdir),
            "reserved_policy_on_stop": "keep",
            "reserved_policy_on_error": "keep",
        },
        "io": {
            "inputs": {"inbox": "bundle_task.inbox"},
            "outputs": {"outbox": "bundle_task.outbox"},
            "control": {
                "ctrl_in": "bundle_task.ctrl_in",
                "ctrl_out": "bundle_task.ctrl_out",
            },
        },
        "state": {"status": "created"},
        "metadata": {},
    }
    _write_json(bundle_dir / "taskspec.json", spec_payload)

    rc, out, err = run_cli(
        "run",
        "--spec",
        bundle_dir,
        cwd=workdir,
        harness=weft_harness,
        stdin="from-bundle",
    )

    assert rc == 0
    assert out == "bundle:from-bundle"
    assert err == ""


def test_cli_run_spec_bundle_passes_plain_json_object_stdin_to_function_target(
    workdir, weft_harness
) -> None:
    weft_harness.ensure_foreground_manager()
    bundle_dir = workdir / "bundle-json-task"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "helper_module.py").write_text(
        "\n".join(
            [
                "import json",
                "",
                "def echo_mapping(payload: dict[str, object]) -> str:",
                "    return json.dumps(payload, sort_keys=True)",
                "",
            ]
        ),
        encoding="utf-8",
    )
    spec_payload = {
        "name": "bundle-json-task",
        "version": "1.0",
        "spec": {
            "type": "function",
            "function_target": "helper_module:echo_mapping",
            "weft_context": str(workdir),
            "reserved_policy_on_stop": "keep",
            "reserved_policy_on_error": "keep",
        },
        "io": {
            "inputs": {"inbox": "bundle_json_task.inbox"},
            "outputs": {"outbox": "bundle_json_task.outbox"},
            "control": {
                "ctrl_in": "bundle_json_task.ctrl_in",
                "ctrl_out": "bundle_json_task.ctrl_out",
            },
        },
        "state": {"status": "created"},
        "metadata": {},
    }
    _write_json(bundle_dir / "taskspec.json", spec_payload)

    rc, out, err = run_cli(
        "run",
        "--spec",
        bundle_dir,
        cwd=workdir,
        harness=weft_harness,
        stdin='{"providers":["bogus-provider"],"refresh":true}',
    )

    assert rc == 0
    assert out == '{"providers": ["bogus-provider"], "refresh": true}'
    assert err == ""


def test_cli_run_spec_bundle_declared_args_shape_work_item(
    workdir,
    weft_harness,
) -> None:
    weft_harness.ensure_foreground_manager()
    bundle_dir = workdir / "run-input-bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "helper_module.py").write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "def build_work_item(request):",
                "    document = request.arguments.get('document')",
                "    if document and request.stdin_text is not None:",
                "        raise ValueError('Provide either --document or stdin, not both.')",
                "    if document:",
                "        return f\"{request.arguments['prompt']}|PATH={document}\"",
                "    if request.stdin_text is not None:",
                "        return f\"{request.arguments['prompt']}|STDIN={request.stdin_text}\"",
                "    raise ValueError('Provide --document or stdin.')",
                "",
            ]
        ),
        encoding="utf-8",
    )
    _write_json(
        bundle_dir / "taskspec.json",
        {
            "name": "run-input-bundle",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
                "run_input": {
                    "adapter_ref": "helper_module:build_work_item",
                    "arguments": {
                        "prompt": {
                            "type": "string",
                            "required": True,
                        },
                        "document": {
                            "type": "path",
                        },
                    },
                    "stdin": {
                        "type": "text",
                    },
                },
                "weft_context": str(workdir),
                "reserved_policy_on_stop": "keep",
                "reserved_policy_on_error": "keep",
            },
            "metadata": {},
        },
    )

    rc, out, err = run_cli(
        "run",
        "--spec",
        bundle_dir,
        "--prompt",
        "Summarize this document",
        cwd=workdir,
        harness=weft_harness,
        stdin="from-stdin",
    )

    assert rc == 0
    assert out == "Summarize this document|STDIN=from-stdin"
    assert err == ""


def test_cli_run_spec_builtin_arguments_payload_shapes_work_item(
    workdir,
    weft_harness,
) -> None:
    spec_path = workdir / "run-input-flat-payload.json"
    _write_json(
        spec_path,
        {
            "name": "run-input-flat-payload",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:json_payload",
                "run_input": {
                    "adapter_ref": "weft.builtins.run_input:arguments_payload",
                    "arguments": {
                        "case_id": {
                            "type": "string",
                            "required": True,
                        }
                    },
                },
                "weft_context": str(workdir),
                "reserved_policy_on_stop": "keep",
                "reserved_policy_on_error": "keep",
            },
            "metadata": {},
        },
    )

    rc, out, err = run_cli(
        "run",
        "--spec",
        spec_path,
        "--case-id",
        "abc",
        "--json",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    result_payload = json.loads(out)
    assert result_payload["result"] == {"case_id": "abc"}
    assert err == ""


def test_cli_run_spec_builtin_keyword_arguments_payload_shapes_work_item(
    workdir,
    weft_harness,
) -> None:
    spec_path = workdir / "run-input-kwargs-payload.json"
    _write_json(
        spec_path,
        {
            "name": "run-input-kwargs-payload",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:json_kwargs",
                "run_input": {
                    "adapter_ref": "weft.builtins.run_input:keyword_arguments_payload",
                    "arguments": {
                        "case_id": {
                            "type": "string",
                            "required": True,
                        }
                    },
                },
                "weft_context": str(workdir),
                "reserved_policy_on_stop": "keep",
                "reserved_policy_on_error": "keep",
            },
            "metadata": {},
        },
    )

    rc, out, err = run_cli(
        "run",
        "--spec",
        spec_path,
        "--case-id",
        "abc",
        "--json",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    result_payload = json.loads(out)
    assert result_payload["result"] == {"case_id": "abc"}
    assert err == ""


def test_cli_run_spec_bundle_declared_path_arg_normalizes_to_absolute_path(
    workdir,
    weft_harness,
) -> None:
    weft_harness.ensure_foreground_manager()
    bundle_dir = workdir / "run-input-path-bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "helper_module.py").write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "def build_work_item(request):",
                "    return request.arguments['document']",
                "",
            ]
        ),
        encoding="utf-8",
    )
    _write_json(
        bundle_dir / "taskspec.json",
        {
            "name": "run-input-path-bundle",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
                "run_input": {
                    "adapter_ref": "helper_module:build_work_item",
                    "arguments": {
                        "document": {
                            "type": "path",
                            "required": True,
                        }
                    },
                },
                "weft_context": str(workdir),
                "reserved_policy_on_stop": "keep",
                "reserved_policy_on_error": "keep",
            },
            "metadata": {},
        },
    )
    document_path = workdir / "doc.txt"
    document_path.write_text("hello", encoding="utf-8")

    rc, out, err = run_cli(
        "run",
        "--spec",
        bundle_dir,
        "--document",
        "doc.txt",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    assert out == str(document_path.resolve())
    assert err == ""


def test_cli_run_spec_bundle_declared_args_require_declared_option(
    workdir,
    weft_harness,
) -> None:
    bundle_dir = workdir / "run-input-required-bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "helper_module.py").write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "def build_work_item(request):",
                "    return request.arguments['prompt']",
                "",
            ]
        ),
        encoding="utf-8",
    )
    _write_json(
        bundle_dir / "taskspec.json",
        {
            "name": "run-input-required-bundle",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
                "run_input": {
                    "adapter_ref": "helper_module:build_work_item",
                    "arguments": {
                        "prompt": {
                            "type": "string",
                            "required": True,
                        }
                    },
                },
                "weft_context": str(workdir),
                "reserved_policy_on_stop": "keep",
                "reserved_policy_on_error": "keep",
            },
            "metadata": {},
        },
    )

    rc, out, err = run_cli(
        "run",
        "--spec",
        bundle_dir,
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 2
    assert out == ""
    assert "Missing required declared option(s): --prompt" in err


def test_cli_run_spec_bundle_declared_args_reject_undeclared_option(
    workdir,
    weft_harness,
) -> None:
    bundle_dir = workdir / "run-input-undeclared-bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "helper_module.py").write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "def build_work_item(request):",
                "    return request.arguments.get('prompt', '')",
                "",
            ]
        ),
        encoding="utf-8",
    )
    _write_json(
        bundle_dir / "taskspec.json",
        {
            "name": "run-input-undeclared-bundle",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
                "run_input": {
                    "adapter_ref": "helper_module:build_work_item",
                    "arguments": {
                        "prompt": {
                            "type": "string",
                            "required": True,
                        }
                    },
                },
                "weft_context": str(workdir),
                "reserved_policy_on_stop": "keep",
                "reserved_policy_on_error": "keep",
            },
            "metadata": {},
        },
    )

    rc, out, err = run_cli(
        "run",
        "--spec",
        bundle_dir,
        "--prompt",
        "Summarize",
        "--unknown",
        "value",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 2
    assert out == ""
    assert "Option '--unknown' is not declared by this TaskSpec" in err


def test_cli_run_spec_bundle_parameterization_materializes_before_run_input(
    workdir,
    weft_harness,
) -> None:
    weft_harness.ensure_foreground_manager()
    bundle_dir = workdir / "parameterized-bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "helper_module.py").write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "import copy",
                "",
                "def materialize(request):",
                "    payload = copy.deepcopy(dict(request.taskspec_payload))",
                "    provider = request.arguments['provider']",
                "    payload['name'] = f'example-{provider}'",
                "    payload['spec']['run_input']['adapter_ref'] = f'helper_module:build_{provider}'",
                "    return payload",
                "",
                "def build_codex(request):",
                "    return f\"codex|{request.arguments['prompt']}\"",
                "",
                "def build_gemini(request):",
                "    return f\"gemini|{request.arguments['prompt']}\"",
                "",
            ]
        ),
        encoding="utf-8",
    )
    _write_json(
        bundle_dir / "taskspec.json",
        {
            "name": "parameterized-bundle",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
                "parameterization": {
                    "adapter_ref": "helper_module:materialize",
                    "arguments": {
                        "provider": {
                            "type": "string",
                            "required": False,
                            "default": "codex",
                            "choices": ["codex", "gemini"],
                        }
                    },
                },
                "run_input": {
                    "adapter_ref": "helper_module:build_codex",
                    "arguments": {
                        "prompt": {
                            "type": "string",
                            "required": True,
                        }
                    },
                },
                "weft_context": str(workdir),
                "reserved_policy_on_stop": "keep",
                "reserved_policy_on_error": "keep",
            },
            "metadata": {},
        },
    )

    rc, out, err = run_cli(
        "run",
        "--spec",
        bundle_dir,
        "--prompt",
        "Summarize",
        "--provider",
        "gemini",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    assert out == "gemini|Summarize"
    assert err == ""


def test_cli_run_spec_bundle_parameterization_requires_declared_parameter(
    workdir,
    weft_harness,
) -> None:
    bundle_dir = workdir / "parameterized-required-bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "helper_module.py").write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "def materialize(request):",
                "    return dict(request.taskspec_payload)",
                "",
                "def build_work_item(request):",
                "    return request.arguments['prompt']",
                "",
            ]
        ),
        encoding="utf-8",
    )
    _write_json(
        bundle_dir / "taskspec.json",
        {
            "name": "parameterized-required-bundle",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
                "parameterization": {
                    "adapter_ref": "helper_module:materialize",
                    "arguments": {
                        "provider": {
                            "type": "string",
                            "required": True,
                        }
                    },
                },
                "run_input": {
                    "adapter_ref": "helper_module:build_work_item",
                    "arguments": {
                        "prompt": {
                            "type": "string",
                            "required": True,
                        }
                    },
                },
                "weft_context": str(workdir),
                "reserved_policy_on_stop": "keep",
                "reserved_policy_on_error": "keep",
            },
            "metadata": {},
        },
    )

    rc, out, err = run_cli(
        "run",
        "--spec",
        bundle_dir,
        "--prompt",
        "Summarize",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 2
    assert out == ""
    assert "Missing required declared option(s): --provider" in err


def test_cli_run_spec_path_reads_piped_stdin_into_command(
    workdir, weft_harness
) -> None:
    script = workdir / "stdin_spec_script.py"
    script.write_text(
        "import sys\ndata = sys.stdin.read()\nprint(data.upper(), end='')\n",
        encoding="utf-8",
    )

    spec_path = workdir / "task_stdin_command.json"
    spec_payload = {
        "tid": "1760000000000000102",
        "name": "cli-spec-stdin-command",
        "version": "1.0",
        "spec": {
            "type": "command",
            "process_target": sys.executable,
            "args": [str(script)],
            "weft_context": str(workdir),
            "reserved_policy_on_stop": "keep",
            "reserved_policy_on_error": "keep",
        },
        "io": {
            "inputs": {"inbox": "cli_spec_stdin_command.inbox"},
            "outputs": {"outbox": "cli_spec_stdin_command.outbox"},
            "control": {
                "ctrl_in": "cli_spec_stdin_command.ctrl_in",
                "ctrl_out": "cli_spec_stdin_command.ctrl_out",
            },
        },
        "state": {"status": "created"},
        "metadata": {},
    }
    spec_path.write_text(json.dumps(spec_payload, indent=2), encoding="utf-8")

    rc, out, err = run_cli(
        "run",
        "--spec",
        spec_path,
        cwd=workdir,
        harness=weft_harness,
        stdin="from-stdin",
    )

    assert rc == 0
    assert out == "FROM-STDIN"
    assert err == ""


def test_cli_run_persistent_spec_no_wait_consumes_initial_piped_stdin(
    workdir, weft_harness
) -> None:
    weft_harness.ensure_foreground_manager()
    spec_path = workdir / "persistent_stdin_spec.json"
    # This test covers stdin handoff through a live manager. Let live
    # submissions allocate a current broker timestamp instead of preserving a
    # historical fixture TID that can be behind the manager's spawn cursor.
    spec_payload = {
        "name": "cli-persistent-stdin",
        "version": "1.0",
        "spec": {
            "type": "function",
            "persistent": True,
            "function_target": "tests.tasks.sample_targets:echo_payload",
            "weft_context": str(workdir),
            "reserved_policy_on_stop": "keep",
            "reserved_policy_on_error": "keep",
        },
        "io": {
            "inputs": {"inbox": "cli_persistent_stdin.inbox"},
            "outputs": {"outbox": "cli_persistent_stdin.outbox"},
            "control": {
                "ctrl_in": "cli_persistent_stdin.ctrl_in",
                "ctrl_out": "cli_persistent_stdin.ctrl_out",
            },
        },
        "state": {"status": "created"},
        "metadata": {},
    }
    spec_path.write_text(json.dumps(spec_payload, indent=2), encoding="utf-8")

    rc, out, err = run_cli(
        "run",
        "--spec",
        spec_path,
        "--no-wait",
        cwd=workdir,
        harness=weft_harness,
        stdin="hello-from-stdin",
    )

    assert rc == 0
    assert err == ""
    tid = out.strip()
    result_timeout = (
        "20" if os.name == "nt" or active_test_backend() == "postgres" else "5"
    )

    rc, out, err = run_cli(
        "result",
        tid,
        "--timeout",
        result_timeout,
        "--json",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    result_payload = json.loads(out)
    assert result_payload["result"] == "hello-from-stdin"
    assert err == ""


def test_cli_run_agent_spec_path(workdir, weft_harness) -> None:
    context = build_context(spec_context=workdir)
    _write_queue_message(context, "cli_agent.inbox", "hello")

    spec_path = workdir / "agent_task.json"
    spec_payload = taskspec_fixtures.create_valid_agent_taskspec(
        tid="1760000000000000200",
        name="cli-agent-task",
    ).model_dump(mode="json")
    spec_payload["spec"]["weft_context"] = str(workdir)
    spec_payload["io"] = {
        "inputs": {"inbox": "cli_agent.inbox"},
        "outputs": {"outbox": "cli_agent.outbox"},
        "control": {
            "ctrl_in": "cli_agent.ctrl_in",
            "ctrl_out": "cli_agent.ctrl_out",
        },
    }
    spec_path.write_text(json.dumps(spec_payload, indent=2), encoding="utf-8")

    rc, out, err = run_cli(
        "run",
        "--spec",
        spec_path,
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    assert out == "text:hello"
    assert err == ""


def test_cli_run_agent_spec_no_wait_and_result(workdir, weft_harness) -> None:
    context = build_context(spec_context=workdir)
    _write_queue_message(context, "cli_agent_result.inbox", "hello")

    spec_path = workdir / "agent_result_task.json"
    spec_payload = taskspec_fixtures.create_valid_agent_taskspec(
        tid="1760000000000000201",
        name="cli-agent-result-task",
    ).model_dump(mode="json")
    spec_payload["spec"]["weft_context"] = str(workdir)
    spec_payload["io"] = {
        "inputs": {"inbox": "cli_agent_result.inbox"},
        "outputs": {"outbox": "cli_agent_result.outbox"},
        "control": {
            "ctrl_in": "cli_agent_result.ctrl_in",
            "ctrl_out": "cli_agent_result.ctrl_out",
        },
    }
    spec_path.write_text(json.dumps(spec_payload, indent=2), encoding="utf-8")

    rc, out, err = run_cli(
        "run",
        "--spec",
        spec_path,
        "--no-wait",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    assert err == ""
    tid = out.strip()
    weft_harness.wait_for_completion(tid)

    rc, out, err = run_cli(
        "result",
        tid,
        "--timeout",
        "5",
        "--json",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    result_payload = json.loads(out)
    assert result_payload["status"] == "completed"
    assert result_payload["result"] == "text:hello"
    assert err == ""


def test_cli_run_persistent_agent_spec_continues_conversation(
    workdir, weft_harness
) -> None:
    context = build_context(spec_context=workdir)
    weft_harness.ensure_foreground_manager()
    spec_path = workdir / "persistent_agent.json"
    spec_payload = taskspec_fixtures.create_valid_agent_taskspec(
        tid="1760000000000000202",
        name="cli-persistent-agent",
    ).model_dump(mode="json")
    spec_payload.pop("tid", None)
    spec_payload["spec"]["persistent"] = True
    spec_payload["spec"]["agent"]["conversation_scope"] = "per_task"
    spec_payload["spec"]["weft_context"] = str(workdir)
    spec_payload["io"] = {
        "inputs": {"inbox": "cli_persistent_agent.inbox"},
        "outputs": {"outbox": "cli_persistent_agent.outbox"},
        "control": {
            "ctrl_in": "cli_persistent_agent.ctrl_in",
            "ctrl_out": "cli_persistent_agent.ctrl_out",
        },
    }
    spec_path.write_text(json.dumps(spec_payload, indent=2), encoding="utf-8")

    rc, out, err = run_cli(
        "run",
        "--spec",
        spec_path,
        "--no-wait",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    assert err == ""
    tid = out.strip()
    _wait_for_task_waiting_on_input(
        context,
        tid=tid,
        queue_name="cli_persistent_agent.inbox",
    )

    _write_queue_message(context, "cli_persistent_agent.inbox", "hello")
    result_timeout = "20" if os.name == "nt" else "10"

    rc, out, err = run_cli(
        "result",
        tid,
        "--timeout",
        result_timeout,
        "--json",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    first_payload = json.loads(out)
    assert first_payload["result"] == "text:hello"
    assert err == ""

    _write_queue_message(context, "cli_persistent_agent.inbox", "__history__")

    rc, out, err = run_cli(
        "result",
        tid,
        "--timeout",
        result_timeout,
        "--json",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    second_payload = json.loads(out)
    assert second_payload["result"] == "history:hello"
    assert err == ""


def test_cli_run_persistent_spec_name_claims_and_releases_endpoint(
    workdir: Path,
    weft_harness: WeftTestHarness,
) -> None:
    weft_harness.ensure_foreground_manager()
    spec_path = workdir / "persistent_named_endpoint.json"
    spec_payload = {
        "name": "persistent-worker",
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:echo_payload",
            "persistent": True,
            "weft_context": str(workdir),
        },
        "metadata": {},
    }
    spec_path.write_text(json.dumps(spec_payload, indent=2), encoding="utf-8")

    rc, out, err = run_cli(
        "run",
        "--spec",
        spec_path,
        "--name",
        "mayor",
        "--no-wait",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    assert err == ""
    tid = out.strip()

    resolved_payload = _wait_for_endpoint_claim(
        weft_harness.context,
        name="mayor",
        tid=tid,
    )
    assert resolved_payload["tid"] == tid

    rc, out, err = run_cli(
        "queue",
        "resolve",
        "mayor",
        "--json",
        cwd=workdir,
        harness=weft_harness,
    )
    assert rc == 0
    assert json.loads(out)["tid"] == tid
    assert err == ""

    snapshot = task_cmd.task_status(tid, context_path=workdir)
    assert snapshot is not None
    assert snapshot.name == "mayor"

    rc, out, err = run_cli(
        "task",
        "stop",
        tid,
        cwd=workdir,
        harness=weft_harness,
    )
    assert rc == 0
    assert err == ""

    _wait_for_endpoint_release(
        weft_harness.context,
        name="mayor",
        tid=tid,
    )
    rc, out, err = run_cli(
        "queue",
        "resolve",
        "mayor",
        cwd=workdir,
        harness=weft_harness,
    )
    assert rc == 2
    assert out == ""
    assert "No active endpoint named 'mayor'" in err


def test_cli_run_persistent_spec_rejects_wait(workdir, weft_harness) -> None:
    spec_path = workdir / "persistent_wait_rejected.json"
    spec_payload = taskspec_fixtures.create_valid_agent_taskspec(
        tid="1760000000000000203",
        name="cli-persistent-wait",
    ).model_dump(mode="json")
    spec_payload["spec"]["persistent"] = True
    spec_payload["spec"]["agent"]["conversation_scope"] = "per_task"
    spec_payload["spec"]["weft_context"] = str(workdir)
    spec_path.write_text(json.dumps(spec_payload, indent=2), encoding="utf-8")

    rc, out, err = run_cli(
        "run",
        "--spec",
        spec_path,
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc != 0
    combined = f"{out}\n{err}"
    assert "--wait is not supported for persistent TaskSpecs" in combined


def test_cli_run_once_overrides_persistent_spec(workdir, weft_harness) -> None:
    spec_path = workdir / "persistent_once_override.json"
    spec_payload = {
        "tid": "1760000000000000204",
        "name": "persistent-once-override",
        "version": "1.0",
        "spec": {
            "type": "function",
            "persistent": True,
            "function_target": "tests.tasks.sample_targets:provide_payload",
            "weft_context": str(workdir),
        },
        "io": {
            "inputs": {"inbox": "once_override.inbox"},
            "outputs": {"outbox": "once_override.outbox"},
            "control": {
                "ctrl_in": "once_override.ctrl_in",
                "ctrl_out": "once_override.ctrl_out",
            },
        },
        "state": {"status": "created"},
        "metadata": {},
    }
    spec_path.write_text(json.dumps(spec_payload, indent=2), encoding="utf-8")

    rc, out, err = run_cli(
        "run",
        "--spec",
        spec_path,
        "--once",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    payload = json.loads(out)
    assert payload["data"] == "payload"
    assert err == ""


def test_cli_run_continuous_overrides_nonpersistent_spec(workdir, weft_harness) -> None:
    context = build_context(spec_context=workdir)
    spec_path = workdir / "continuous_override.json"
    spec_payload = {
        "tid": "1760000000000000205",
        "name": "continuous-override",
        "version": "1.0",
        "spec": {
            "type": "function",
            "persistent": False,
            "function_target": "tests.tasks.sample_targets:echo_payload",
            "weft_context": str(workdir),
        },
        "io": {
            "inputs": {"inbox": "continuous_override.inbox"},
            "outputs": {"outbox": "continuous_override.outbox"},
            "control": {
                "ctrl_in": "continuous_override.ctrl_in",
                "ctrl_out": "continuous_override.ctrl_out",
            },
        },
        "state": {"status": "created"},
        "metadata": {},
    }
    spec_path.write_text(json.dumps(spec_payload, indent=2), encoding="utf-8")

    rc, out, err = run_cli(
        "run",
        "--spec",
        spec_path,
        "--continuous",
        "--no-wait",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    assert err == ""
    tid = out.strip()
    result_timeout = "30" if os.name == "nt" else "15"

    _write_queue_message(
        context,
        "continuous_override.inbox",
        json.dumps({"args": ["one"]}),
    )

    rc, out, err = run_cli(
        "result",
        tid,
        "--timeout",
        result_timeout,
        "--json",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    first_payload = json.loads(out)
    assert first_payload["result"] == "one"
    assert err == ""

    _write_queue_message(
        context,
        "continuous_override.inbox",
        json.dumps({"args": ["two"]}),
    )

    rc, out, err = run_cli(
        "result",
        tid,
        "--timeout",
        result_timeout,
        "--json",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    second_payload = json.loads(out)
    assert second_payload["result"] == "two"
    assert err == ""


def test_cli_run_interactive_command_streams(workdir, weft_harness) -> None:
    weft_harness.ensure_foreground_manager()
    rc, out, err = run_cli(
        "run",
        "--interactive",
        "--",
        sys.executable,
        "-u",  # Unbuffered mode for proper interactive I/O
        str(INTERACTIVE_SCRIPT),
        cwd=workdir,
        harness=weft_harness,
        stdin="hello\nquit\n",
        timeout=40.0,
    )

    assert rc == 0
    record = _latest_completed_record(weft_harness)
    assert record is not None
    latest_tid, completion = record
    assert completion.get("result_bytes", 0) > 0
    outbox = weft_harness.context.queue(
        f"T{latest_tid}.outbox",
        persistent=True,
    )
    try:
        records = outbox.peek_many(limit=100) or []
    finally:
        outbox.close()
    messages = []
    for payload in records:
        if isinstance(payload, tuple):
            payload = payload[0]
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            messages.append(payload)
            continue
        if isinstance(data, dict) and data.get("type") == "stream":
            messages.append(data.get("data", ""))
    combined = "".join(messages)
    if not combined:
        combined = out
    assert "echo: hello" in combined
    assert "goodbye" in combined
    assert err == ""


def test_cli_run_requires_target(workdir, weft_harness) -> None:
    rc, out, err = run_cli("run", cwd=workdir, harness=weft_harness)
    assert rc != 0
    combined = f"{out}\n{err}"
    assert "Provide a command" in combined


def test_cli_run_command_and_function_conflict(workdir, weft_harness) -> None:
    rc, out, err = run_cli(
        "run",
        "--function",
        "tests.tasks.sample_targets:echo_payload",
        "--",
        sys.executable,
        "-c",
        "print('hi')",
        cwd=workdir,
        harness=weft_harness,
    )
    assert rc != 0
    combined = f"{out}\n{err}"
    assert "Cannot execute a shell command and --function simultaneously." in combined


def test_cli_run_monitor_requires_spec(workdir, weft_harness) -> None:
    rc, out, err = run_cli(
        "run",
        "--monitor",
        "--function",
        "tests.tasks.sample_targets:echo_payload",
        "--arg",
        "payload",
        cwd=workdir,
        harness=weft_harness,
    )
    assert rc != 0
    combined = f"{out}\n{err}"
    assert "--monitor is only supported together with --spec." in combined


def test_cli_run_interactive_json_conflict(workdir, weft_harness) -> None:
    rc, out, err = run_cli(
        "run",
        "--interactive",
        "--json",
        "--",
        sys.executable,
        "-c",
        "print('hi')",
        cwd=workdir,
        harness=weft_harness,
    )
    assert rc != 0
    combined = f"{out}\n{err}"
    assert "--json is not supported together with --interactive" in combined


def test_cli_run_interactive_requires_command_target(workdir, weft_harness) -> None:
    rc, out, err = run_cli(
        "run",
        "--interactive",
        "--function",
        "tests.tasks.sample_targets:echo_payload",
        "--arg",
        "payload",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc != 0
    combined = f"{out}\n{err}"
    assert "--interactive is only supported for command targets" in combined


def test_cli_run_function_json_output(workdir, weft_harness) -> None:
    rc, out, err = run_cli(
        "run",
        "--function",
        "tests.tasks.sample_targets:provide_payload",
        "--json",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["status"] == "completed"
    assert payload["result"]["data"] == "payload"


def test_cli_run_command_with_env(workdir, weft_harness) -> None:
    script = workdir / "env_script.py"
    script.write_text(
        "import os\nprint(os.environ['RUN_ENV_VALUE'])\n",
        encoding="utf-8",
    )

    rc, out, err = run_cli(
        "run",
        "--env",
        "RUN_ENV_VALUE=via-cli",
        "--",
        sys.executable,
        str(script),
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    assert err == ""
    assert out == "via-cli"


def test_cli_run_function_with_kwargs(workdir, weft_harness) -> None:
    rc, out, err = run_cli(
        "run",
        "--function",
        "tests.tasks.sample_targets:echo_payload",
        "--arg",
        "base",
        "--kw",
        'suffix="!"',
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    assert err == ""
    assert out == "base!"


def test_cli_run_cpu_limit_validation(workdir, weft_harness) -> None:
    rc, out, err = run_cli(
        "run",
        "--function",
        "tests.tasks.sample_targets:echo_payload",
        "--arg",
        "payload",
        "--cpu",
        "200",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc != 0
    combined = f"{out}\n{err}"
    assert "CPU limit must be between 1 and 100 percent" in combined


def test_cli_run_spec_invalid_json(workdir, weft_harness) -> None:
    spec_path = workdir / "invalid_taskspec.json"
    spec_path.write_text("{ invalid json", encoding="utf-8")

    rc, out, err = run_cli(
        "run",
        "--spec",
        spec_path,
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 2
    assert out == ""
    assert err == ""


def test_cli_run_no_wait_returns_tid(workdir, weft_harness) -> None:
    rc, out, err = run_cli(
        "run",
        "--function",
        "tests.tasks.sample_targets:echo_payload",
        "--arg",
        "payload",
        "--no-wait",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    assert err == ""
    assert len(out) == 19 and out.isdigit()
    weft_harness.wait_for_completion(out)


def test_cli_run_no_wait_survives_short_manager_lifetime(workdir, weft_harness) -> None:
    env = os.environ.copy()
    manager_lifetime = 1.0 if os.name == "nt" else 0.2
    task_duration = 1.5 if os.name == "nt" else 0.5
    completion_timeout = 45.0 if os.name == "nt" else 30.0
    env["WEFT_MANAGER_LIFETIME_TIMEOUT"] = str(manager_lifetime)

    rc, out, err = run_cli(
        "run",
        "--function",
        "tests.tasks.sample_targets:simulate_work",
        "--kw",
        f"duration={task_duration}",
        "--kw",
        "result=payload",
        "--no-wait",
        cwd=workdir,
        env=env,
        harness=weft_harness,
    )

    assert rc == 0
    assert err == ""
    assert len(out) == 19 and out.isdigit()
    weft_harness.wait_for_completion(out, timeout=completion_timeout)


def test_harness_wait_for_completion_reports_cancelled_task(
    workdir, weft_harness
) -> None:
    weft_harness.ensure_foreground_manager()
    rc, out, err = run_cli(
        "run",
        "--function",
        "tests.tasks.sample_targets:simulate_work",
        "--kw",
        "duration=5",
        "--no-wait",
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

    rc, _out, err = run_cli(
        "task",
        "stop",
        tid,
        cwd=workdir,
        harness=weft_harness,
    )
    assert rc == 0, err
    with pytest.raises(RuntimeError, match=rf"Task {tid} reported control_stop"):
        weft_harness.wait_for_completion(tid, timeout=10.0)
    _wait_for_task_process_exit(weft_harness, tid=tid)


def test_cli_run_wait_reports_cancelled_task(workdir, weft_harness) -> None:
    weft_harness.ensure_foreground_manager()
    constrained_backend = active_test_backend(os.environ) == "postgres"
    constrained_runtime = sys.platform == "win32" or constrained_backend
    run_timeout = 45.0 if constrained_runtime else 30.0
    result_timeout = 50.0 if constrained_runtime else 35.0
    release_file = workdir / "cancel-release"

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            run_cli,
            "run",
            "--function",
            "tests.tasks.sample_targets:wait_for_file",
            "--arg",
            str(release_file),
            "--kw",
            "timeout=60",
            cwd=workdir,
            harness=weft_harness,
            timeout=run_timeout,
        )

        task_tid = _wait_for_started_task_tid(
            weft_harness,
            task_name="wait_for_file",
        )
        rc, _out, err = run_cli(
            "task",
            "stop",
            task_tid,
            cwd=workdir,
            harness=weft_harness,
        )
        assert rc == 0, err

        rc, out, err = future.result(timeout=result_timeout)

    assert rc == 1
    assert out == ""
    assert "Task cancelled" in err
    _wait_for_task_process_exit(weft_harness, tid=task_tid)


def test_cli_run_wait_returns_timeout_exit_code(workdir, weft_harness) -> None:
    weft_harness.ensure_foreground_manager()
    constrained_backend = active_test_backend(os.environ) == "postgres"
    constrained_runtime = sys.platform == "win32" or constrained_backend
    run_timeout = 120.0 if constrained_runtime else 20.0
    rc, out, err = run_cli(
        "run",
        "--timeout",
        "0.1",
        "--",
        sys.executable,
        "-c",
        "import time; time.sleep(1)",
        cwd=workdir,
        harness=weft_harness,
        timeout=run_timeout,
    )

    assert rc == 124
    assert out == ""
    assert "timed out" in err.lower()


def test_cli_run_prunes_stale_manager(workdir, weft_harness) -> None:
    weft_harness.ensure_foreground_manager()
    context = weft_harness.context
    registry = context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
    try:
        stale_pid = 999_999
        registry.write(
            json.dumps(
                _manager_service_payload(
                    context,
                    tid="1762000000000000000",
                    name="stale-manager",
                    runtime_handle=_host_runtime_handle(stale_pid),
                )
            )
        )

        rc, out, err = run_cli(
            "run",
            "--function",
            "tests.tasks.sample_targets:echo_payload",
            "--arg",
            "hello",
            cwd=workdir,
            harness=weft_harness,
        )

        assert rc == 0
        payloads = [
            json.loads(item)
            for item, _ in registry.peek_many(limit=100, with_timestamps=True)
        ]
        assert all(
            stale_pid
            not in record.get("runtime_handle", {})
            .get("observations", {})
            .get("host_pids", [])
            for record in payloads
        )
        assert any(record.get("role") == "manager" for record in payloads)
    finally:
        registry.close()


def test_cli_run_parallel_no_wait_adopts_active_manager(workdir, weft_harness) -> None:
    weft_harness.ensure_foreground_manager()
    env = os.environ.copy()
    env["WEFT_MANAGER_REUSE_ENABLED"] = "1"
    constrained_backend = active_test_backend(env) == "postgres"
    constrained_runtime = os.name == "nt" or constrained_backend
    submit_timeout = 120.0 if constrained_runtime else 60.0
    status_timeout = 20.0 if constrained_runtime else 5.0
    task_completion_timeout = (
        PARALLEL_MANAGER_REUSE_CONSTRAINED_TASK_TIMEOUT if constrained_runtime else 10.0
    )

    _run_parallel_manager_reuse_cycle(
        root=workdir,
        env=env,
        harness=weft_harness,
        max_workers=4,
        submit_timeout=submit_timeout,
        status_timeout=status_timeout,
        task_completion_timeout=task_completion_timeout,
    )


@pytest.mark.sqlite_only
def test_parallel_manager_reuse_converges_to_single_manager_under_repeated_bootstrap() -> (
    None
):
    constrained_parallelism = os.name == "nt" or sys.platform == "darwin"
    iterations = 3 if constrained_parallelism else 8
    max_workers = 2 if constrained_parallelism else 4
    submit_timeout = 120.0 if constrained_parallelism else 60.0
    status_timeout = 10.0 if constrained_parallelism else 5.0
    task_completion_timeout = (
        PARALLEL_MANAGER_REUSE_CONSTRAINED_TASK_TIMEOUT
        if constrained_parallelism
        else 10.0
    )

    for _ in range(iterations):
        harness = WeftTestHarness()
        harness.__enter__()
        env = os.environ.copy()
        env["WEFT_MANAGER_REUSE_ENABLED"] = "1"

        try:
            _run_parallel_manager_reuse_cycle(
                root=harness.root,
                env=env,
                harness=harness,
                max_workers=max_workers,
                submit_timeout=submit_timeout,
                status_timeout=status_timeout,
                task_completion_timeout=task_completion_timeout,
            )
        finally:
            if not harness._closed:
                harness.cleanup()
            else:
                try:
                    harness._tempdir.cleanup()
                except PermissionError:
                    pass


@pytest.mark.sqlite_only
def test_weft_harness_cleanup_preserves_sqlite_integrity_for_parallel_manager_reuse() -> (
    None
):
    constrained_parallelism = os.name == "nt" or sys.platform == "darwin"
    iterations = 3 if constrained_parallelism else 5
    max_workers = 2 if constrained_parallelism else 4
    submit_timeout = 120.0 if constrained_parallelism else 60.0
    status_timeout = 10.0 if constrained_parallelism else 5.0
    task_completion_timeout = (
        PARALLEL_MANAGER_REUSE_CONSTRAINED_TASK_TIMEOUT
        if constrained_parallelism
        else 10.0
    )

    for _ in range(iterations):
        harness = WeftTestHarness()
        harness.__enter__()
        db_path = harness.context.database_path
        assert db_path is not None
        env = os.environ.copy()
        env["WEFT_MANAGER_REUSE_ENABLED"] = "1"

        try:
            _run_parallel_manager_reuse_cycle(
                root=harness.root,
                env=env,
                harness=harness,
                max_workers=max_workers,
                submit_timeout=submit_timeout,
                status_timeout=status_timeout,
                task_completion_timeout=task_completion_timeout,
            )
            _assert_sqlite_integrity(db_path)
            harness.cleanup(preserve_database=True)
            _assert_sqlite_integrity(db_path)
        finally:
            if not harness._closed:
                harness.cleanup()
            else:
                try:
                    harness._tempdir.cleanup()
                except PermissionError:
                    pass
