"""End-to-end tests for `weft run`."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests.conftest import run_cli
from tests.helpers.weft_harness import WeftTestHarness
from tests.taskspec import fixtures as taskspec_fixtures
from weft._constants import (
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    WEFT_WORKERS_REGISTRY_QUEUE,
)
from weft.commands import tasks as task_cmd
from weft.context import build_context
from weft.helpers import pid_is_live

PROCESS_SCRIPT = Path(__file__).resolve().parents[1] / "tasks" / "process_target.py"
INTERACTIVE_SCRIPT = (
    Path(__file__).resolve().parents[1] / "tasks" / "interactive_echo.py"
)


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


def test_cli_run_prunes_stale_manager(workdir, weft_harness) -> None:
    context = weft_harness.context
    registry = context.queue(WEFT_WORKERS_REGISTRY_QUEUE, persistent=False)
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

    def _submit() -> tuple[int, str, str]:
        return run_cli(
            "run",
            "--no-wait",
            "--function",
            "tests.tasks.sample_targets:simulate_work",
            "--kw",
            "duration=0.2",
            "--kw",
            'result="ok"',
            cwd=workdir,
            env=env,
            prepare_root=False,
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _index: _submit(), range(4)))

    assert all(rc == 0 for rc, _out, _err in results), results
    assert all(err == "" for _rc, _out, err in results), results
    assert all(len(out) == 19 and out.isdigit() for _rc, out, _err in results)

    deadline = time.time() + 5.0
    payload: dict[str, object] | None = None
    while time.time() < deadline:
        rc, out, err = run_cli(
            "status",
            "--json",
            cwd=workdir,
            env=env,
            harness=weft_harness,
        )
        assert rc == 0, err
        payload = json.loads(out)
        managers = payload.get("managers")
        if isinstance(managers, list) and len(managers) == 1:
            break
        time.sleep(0.1)

    assert payload is not None
    managers = payload.get("managers")
    assert isinstance(managers, list)
    assert len(managers) == 1, payload


@pytest.mark.sqlite_only
def test_weft_harness_cleanup_preserves_sqlite_integrity_for_parallel_manager_reuse() -> (
    None
):
    iterations = 5 if os.name == "nt" else 20
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

            def _submit(
                current_root: Path = harness.root,
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
                    prepare_root=False,
                    timeout=current_timeout,
                )

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                results = list(executor.map(lambda _index: _submit(), range(4)))

            assert all(rc == 0 for rc, _out, _err in results), results

            deadline = time.time() + status_timeout
            while time.time() < deadline:
                rc, out, err = run_cli(
                    "status",
                    "--json",
                    cwd=harness.root,
                    env=env,
                    harness=harness,
                )
                assert rc == 0, err
                payload = json.loads(out)
                managers = payload.get("managers")
                if isinstance(managers, list) and len(managers) == 1:
                    break
                time.sleep(0.1)

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
