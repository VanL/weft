"""End-to-end tests for `weft run`."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from tests.conftest import run_cli
from weft._constants import WEFT_SPAWN_REQUESTS_QUEUE, WEFT_WORKERS_REGISTRY_QUEUE
from weft.context import build_context

PROCESS_SCRIPT = Path(__file__).resolve().parents[1] / "tasks" / "process_target.py"
INTERACTIVE_SCRIPT = (
    Path(__file__).resolve().parents[1] / "tasks" / "interactive_echo.py"
)


def test_cli_run_function_inline(workdir) -> None:
    rc, out, err = run_cli(
        "run",
        "--function",
        "tests.tasks.sample_targets:echo_payload",
        "--arg",
        "hello",
        cwd=workdir,
    )

    assert rc == 0
    assert out == "hello"
    assert err == ""


def test_cli_run_command_inline(workdir) -> None:
    rc, out, err = run_cli(
        "run",
        "--",
        sys.executable,
        str(PROCESS_SCRIPT),
        "--result",
        "cmd-output",
        cwd=workdir,
    )

    assert rc == 0
    assert "cmd-output" in out
    assert err == ""


def test_cli_run_reads_stdin(workdir) -> None:
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
        stdin="piped data",
    )

    assert rc == 0
    assert out == "PIPED DATA"
    assert err == ""


def test_cli_run_spec_path(workdir) -> None:
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

    rc, out, err = run_cli("run", "--spec", spec_path, cwd=workdir)

    assert rc == 0
    assert out == "from-spec"
    assert err == ""


def test_cli_run_interactive_command_streams(workdir) -> None:
    rc, out, err = run_cli(
        "run",
        "--interactive",
        "--",
        sys.executable,
        str(INTERACTIVE_SCRIPT),
        cwd=workdir,
        stdin="hello\nquit\n",
    )

    assert rc == 0
    assert "echo: hello" in out
    assert "goodbye" in out
    assert err == ""


def test_cli_run_requires_target(workdir) -> None:
    rc, out, err = run_cli("run", cwd=workdir)
    assert rc != 0
    combined = f"{out}\n{err}"
    assert "Provide a command" in combined


def test_cli_run_command_and_function_conflict(workdir) -> None:
    rc, out, err = run_cli(
        "run",
        "--function",
        "tests.tasks.sample_targets:echo_payload",
        "--",
        sys.executable,
        "-c",
        "print('hi')",
        cwd=workdir,
    )
    assert rc != 0
    combined = f"{out}\n{err}"
    assert "Cannot execute a shell command and --function simultaneously." in combined


def test_cli_run_monitor_requires_spec(workdir) -> None:
    rc, out, err = run_cli(
        "run",
        "--monitor",
        "--function",
        "tests.tasks.sample_targets:echo_payload",
        "--arg",
        "payload",
        cwd=workdir,
    )
    assert rc != 0
    combined = f"{out}\n{err}"
    assert "--monitor is only supported together with --spec." in combined


def test_cli_run_interactive_json_conflict(workdir) -> None:
    rc, out, err = run_cli(
        "run",
        "--interactive",
        "--json",
        "--",
        sys.executable,
        "-c",
        "print('hi')",
        cwd=workdir,
    )
    assert rc != 0
    combined = f"{out}\n{err}"
    assert "--json is not supported together with --interactive" in combined


def test_cli_run_function_json_output(workdir) -> None:
    rc, out, err = run_cli(
        "run",
        "--function",
        "tests.tasks.sample_targets:provide_payload",
        "--json",
        cwd=workdir,
    )

    assert rc == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["status"] == "completed"
    assert payload["result"]["data"] == "payload"


def test_cli_run_command_with_env(workdir) -> None:
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
    )

    assert rc == 0
    assert err == ""
    assert out == "via-cli"


def test_cli_run_function_with_kwargs(workdir) -> None:
    rc, out, err = run_cli(
        "run",
        "--function",
        "tests.tasks.sample_targets:echo_payload",
        "--arg",
        "base",
        "--kw",
        'suffix="!"',
        cwd=workdir,
    )

    assert rc == 0
    assert err == ""
    assert out == "base!"


def test_cli_run_cpu_limit_validation(workdir) -> None:
    rc, out, err = run_cli(
        "run",
        "--function",
        "tests.tasks.sample_targets:echo_payload",
        "--arg",
        "payload",
        "--cpu",
        "200",
        cwd=workdir,
    )

    assert rc != 0
    combined = f"{out}\n{err}"
    assert "CPU limit must be between 1 and 100 percent" in combined


def test_cli_run_spec_invalid_json(workdir) -> None:
    spec_path = workdir / "invalid_taskspec.json"
    spec_path.write_text("{ invalid json", encoding="utf-8")

    rc, out, err = run_cli("run", "--spec", spec_path, cwd=workdir)

    assert rc == 2
    assert out == ""
    assert err == ""


def test_cli_run_no_wait_returns_tid(workdir) -> None:
    rc, out, err = run_cli(
        "run",
        "--function",
        "tests.tasks.sample_targets:echo_payload",
        "--arg",
        "payload",
        "--no-wait",
        cwd=workdir,
    )

    assert rc == 0
    assert err == ""
    assert len(out) == 19 and out.isdigit()


def test_cli_run_prunes_stale_manager(workdir) -> None:
    context = build_context(spec_context=workdir)
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
    )

    assert rc == 0
    payloads = [
        json.loads(item)
        for item, _ in registry.peek_many(limit=100, with_timestamps=True)
    ]
    assert all(record.get("pid") != stale_pid for record in payloads)
    assert any(record.get("role") == "manager" for record in payloads)
