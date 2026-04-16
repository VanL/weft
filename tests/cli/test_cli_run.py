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
from tests.helpers.weft_harness import WeftTestHarness
from tests.taskspec import fixtures as taskspec_fixtures
from weft._constants import (
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_MANAGERS_REGISTRY_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
)
from weft.commands import manager as manager_cmd
from weft.commands import tasks as task_cmd
from weft.context import build_context
from weft.helpers import pid_is_live

PROCESS_SCRIPT = Path(__file__).resolve().parents[1] / "tasks" / "process_target.py"
INTERACTIVE_SCRIPT = (
    Path(__file__).resolve().parents[1] / "tasks" / "interactive_echo.py"
)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


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
    timeout: float = 10.0,
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
) -> None:
    wrappers = [
        write_provider_cli_wrapper(workdir, provider_name)
        for provider_name in PROVIDER_FIXTURE_NAMES
    ]
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join([str(workdir), env.get("PATH", "")])

    for wrapper in wrappers:
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
    for wrapper in wrappers:
        provider_name = "claude_code" if wrapper.name == "claude" else wrapper.name
        assert providers[provider_name]["executable"] == str(wrapper)


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

    inbox = context.queue("cli_spec.inbox", persistent=True)
    outbox = context.queue("cli_spec.outbox", persistent=True)  # noqa: F841
    inbox.write(json.dumps({"args": ["from-spec"]}))

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
        "tid": "1760000000000000105",
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
        "tid": "1760000000000000106",
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


def test_cli_run_spec_bundle_declared_path_arg_normalizes_to_absolute_path(
    workdir,
    weft_harness,
) -> None:
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
    spec_path = workdir / "persistent_stdin_spec.json"
    spec_payload = {
        "tid": "1760000000000000103",
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
    assert result_payload["result"] == "hello-from-stdin"
    assert err == ""


def test_cli_run_agent_spec_path(workdir, weft_harness) -> None:
    context = build_context(spec_context=workdir)
    inbox = context.queue("cli_agent.inbox", persistent=True)
    inbox.write("hello")

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
    inbox = context.queue("cli_agent_result.inbox", persistent=True)
    inbox.write("hello")

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
    spec_path = workdir / "persistent_agent.json"
    spec_payload = taskspec_fixtures.create_valid_agent_taskspec(
        tid="1760000000000000202",
        name="cli-persistent-agent",
    ).model_dump(mode="json")
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

    inbox = context.queue("cli_persistent_agent.inbox", persistent=True)
    inbox.write("hello")

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
    first_payload = json.loads(out)
    assert first_payload["result"] == "text:hello"
    assert err == ""

    inbox.write("__history__")

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
    second_payload = json.loads(out)
    assert second_payload["result"] == "history:hello"
    assert err == ""


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

    inbox = context.queue("continuous_override.inbox", persistent=True)
    inbox.write(json.dumps({"args": ["one"]}))

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
    first_payload = json.loads(out)
    assert first_payload["result"] == "one"
    assert err == ""

    inbox.write(json.dumps({"args": ["two"]}))

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
    second_payload = json.loads(out)
    assert second_payload["result"] == "two"
    assert err == ""


def test_cli_run_interactive_command_streams(workdir, weft_harness) -> None:
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
    env["WEFT_MANAGER_LIFETIME_TIMEOUT"] = "0.2"

    rc, out, err = run_cli(
        "run",
        "--function",
        "tests.tasks.sample_targets:simulate_work",
        "--kw",
        "duration=0.5",
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
    weft_harness.wait_for_completion(out, timeout=10.0)


def test_harness_wait_for_completion_reports_cancelled_task(
    workdir, weft_harness
) -> None:
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
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            run_cli,
            "run",
            "--function",
            "tests.tasks.sample_targets:simulate_work",
            "--kw",
            "duration=5",
            cwd=workdir,
            harness=weft_harness,
            timeout=15.0,
        )

        task_tid = _wait_for_started_task_tid(
            weft_harness,
            task_name="simulate_work",
        )
        rc, _out, err = run_cli(
            "task",
            "stop",
            task_tid,
            cwd=workdir,
            harness=weft_harness,
        )
        assert rc == 0, err

        rc, out, err = future.result(timeout=20.0)

    assert rc == 1
    assert out == ""
    assert "Task cancelled" in err
    _wait_for_task_process_exit(weft_harness, tid=task_tid)


def test_cli_run_wait_returns_timeout_exit_code(workdir, weft_harness) -> None:
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
        timeout=20.0,
    )

    assert rc == 124
    assert out == ""
    assert "timed out" in err.lower()


def test_cli_run_prunes_stale_manager(workdir, weft_harness) -> None:
    context = weft_harness.context
    registry = context.queue(WEFT_MANAGERS_REGISTRY_QUEUE, persistent=False)
    stale_pid = 999_999
    registry.write(
        json.dumps(
            {
                "tid": "1762000000000000000",
                "name": "stale-manager",
                "status": "active",
                "pid": stale_pid,
                "role": "manager",
                "requests": WEFT_SPAWN_REQUESTS_QUEUE,
            }
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
    assert all(record.get("pid") != stale_pid for record in payloads)
    assert any(record.get("role") == "manager" for record in payloads)


def test_cli_run_parallel_no_wait_adopts_active_manager(workdir, weft_harness) -> None:
    env = os.environ.copy()
    env["WEFT_MANAGER_REUSE_ENABLED"] = "1"

    _run_parallel_manager_reuse_cycle(
        root=workdir,
        env=env,
        harness=weft_harness,
        max_workers=4,
        submit_timeout=60.0,
        status_timeout=5.0,
    )


@pytest.mark.sqlite_only
def test_parallel_manager_reuse_converges_to_single_manager_under_repeated_bootstrap() -> (
    None
):
    iterations = 3 if os.name == "nt" else 8
    max_workers = 2 if os.name == "nt" else 4
    submit_timeout = 120.0 if os.name == "nt" else 60.0
    status_timeout = 10.0 if os.name == "nt" else 5.0

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
    iterations = 3 if os.name == "nt" else 5
    max_workers = 2 if os.name == "nt" else 4
    submit_timeout = 120.0 if os.name == "nt" else 60.0
    status_timeout = 10.0 if os.name == "nt" else 5.0

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
