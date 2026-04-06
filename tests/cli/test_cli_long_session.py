"""Long-session CLI integration test for backend-neutral manager reuse."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import run_cli
from tests.helpers.long_session_utils import (
    ALIAS_INTERVAL,
    BULK_ROUNDS,
    INTERACTIVE_INTERVAL,
    INTERACTIVE_SCRIPT,
    PERSISTENT_WORK_ITEMS,
    SESSION_TIMEOUT_SECONDS,
    SubmittedTask,
    interactive_expected,
    session_env,
    wait_for_terminal_statuses,
    write_command_script,
    write_persistent_spec,
)
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import WEFT_COMPLETED_RESULT_GRACE_SECONDS

pytestmark = [pytest.mark.shared, pytest.mark.slow]


def _run_session_cli(
    workdir: Path,
    harness: WeftTestHarness,
    env: dict[str, str],
    *args: object,
    stdin: str | None = None,
    timeout: float = 20.0,
) -> tuple[int, str, str]:
    return run_cli(
        *args,
        cwd=workdir,
        harness=harness,
        env=env,
        stdin=stdin,
        timeout=timeout,
    )


def _status_payload(
    workdir: Path,
    harness: WeftTestHarness,
    env: dict[str, str],
    *,
    all_tasks: bool = False,
) -> dict[str, Any]:
    args: list[object] = ["status", "--json"]
    if all_tasks:
        args.append("--all")
    rc, out, err = _run_session_cli(
        workdir,
        harness,
        env,
        *args,
        timeout=30.0,
    )
    assert rc == 0, err
    return json.loads(out)


def _assert_single_active_manager(
    workdir: Path,
    harness: WeftTestHarness,
    env: dict[str, str],
) -> str:
    payload = _status_payload(workdir, harness, env)
    managers = payload["managers"]
    assert len(managers) == 1, payload
    manager = managers[0]
    assert manager["status"] == "active"
    return str(manager["tid"])


def _submit_no_wait_task(
    workdir: Path,
    harness: WeftTestHarness,
    env: dict[str, str],
    *,
    label: str,
    expected: str,
    name: str,
    tags: tuple[str, ...],
    run_args: list[str],
) -> SubmittedTask:
    cli_args: list[object] = ["run", "--no-wait", "--name", name]
    for tag in tags:
        cli_args.extend(["--tag", tag])
    cli_args.extend(run_args)
    rc, out, err = _run_session_cli(
        workdir,
        harness,
        env,
        *cli_args,
    )
    assert rc == 0, err
    tid = out.strip()
    assert len(tid) == 19 and tid.isdigit()
    return SubmittedTask(
        label=label,
        tid=tid,
        expected=expected,
        name=name,
        tags=tags,
    )


def test_cli_long_session_produces_identical_transcript_across_backends(
    workdir: Path,
    weft_harness: WeftTestHarness,
) -> None:
    env = session_env()
    command_script = workdir / "session_command.py"
    write_command_script(command_script)

    persistent_inbox = "session.persistent.jobs"
    alias_name = "session.jobs"
    # SimpleBroker queue commands only resolve aliases when referenced as @alias.
    alias_ref = f"@{alias_name}"
    persistent_spec_path = workdir / "session_persistent.json"
    write_persistent_spec(
        persistent_spec_path,
        workdir=workdir,
        inbox=persistent_inbox,
    )

    rc, out, err = _run_session_cli(
        workdir,
        weft_harness,
        env,
        "run",
        "--spec",
        persistent_spec_path,
        "--no-wait",
    )
    assert rc == 0, err
    persistent_tid = out.strip()

    rc, out, err = _run_session_cli(
        workdir,
        weft_harness,
        env,
        "queue",
        "alias",
        "add",
        alias_name,
        persistent_inbox,
    )
    assert rc == 0, err
    assert out == ""

    with weft_harness.context.broker() as broker:
        assert dict(broker.list_aliases())[alias_name] == persistent_inbox

    manager_tid: str | None = None
    bulk_tasks: list[SubmittedTask] = []
    interactive_outputs: list[str] = []
    interactive_expected_outputs: list[str] = []
    persistent_results: list[str] = []
    persistent_expected: list[str] = []
    sample_tasks: list[SubmittedTask] = []

    for round_index in range(BULK_ROUNDS):
        function_label = f"fn-{round_index:03d}"
        function_name = f"session-function-{round_index:03d}"
        function_expected = f"{function_label}::fn"
        function_task = _submit_no_wait_task(
            workdir,
            weft_harness,
            env,
            label=function_label,
            expected=function_expected,
            name=function_name,
            tags=("kind:function", "session:long"),
            run_args=[
                "--function",
                "tests.tasks.sample_targets:echo_payload",
                "--arg",
                function_label,
                "--kw",
                'suffix="::fn"',
            ],
        )
        bulk_tasks.append(function_task)

        work_delay_ms = (round_index % 4) * 2
        work_delay = f"{work_delay_ms / 1000:.3f}"
        work_label = f"wrk-{round_index:03d}"
        work_name = f"session-work-{round_index:03d}"
        work_expected = f"{work_label}@{work_delay_ms}ms"
        work_task = _submit_no_wait_task(
            workdir,
            weft_harness,
            env,
            label=work_label,
            expected=work_expected,
            name=work_name,
            tags=("kind:work", "session:long"),
            run_args=[
                "--function",
                "tests.tasks.sample_targets:simulate_work",
                "--kw",
                f"duration={work_delay}",
                "--kw",
                f'result="{work_expected}"',
                "--timeout",
                "15",
            ],
        )
        bulk_tasks.append(work_task)

        command_delay_ms = (round_index % 5) * 3
        command_delay = f"{command_delay_ms / 1000:.3f}"
        command_label = f"cmd-{round_index:03d}"
        command_name = f"session-command-{round_index:03d}"
        command_expected = f"cmd:{command_label}:{command_delay_ms}"
        command_task = _submit_no_wait_task(
            workdir,
            weft_harness,
            env,
            label=command_label,
            expected=command_expected,
            name=command_name,
            tags=("kind:command", "session:long"),
            run_args=[
                "--env",
                "SESSION_PREFIX=cmd",
                "--timeout",
                "15",
                "--",
                sys.executable,
                str(command_script),
                command_label,
                command_delay,
            ],
        )
        bulk_tasks.append(command_task)

        if round_index == 0:
            sample_tasks.extend([function_task, work_task, command_task])
        elif round_index == BULK_ROUNDS - 1:
            sample_tasks.extend([function_task, work_task, command_task])

        if round_index in {0, 31, 63, BULK_ROUNDS - 1}:
            current_manager_tid = _assert_single_active_manager(
                workdir,
                weft_harness,
                env,
            )
            if manager_tid is None:
                manager_tid = current_manager_tid
            else:
                assert current_manager_tid == manager_tid

        if (round_index + 1) % ALIAS_INTERVAL == 0:
            alias_label = f"alias-{len(persistent_expected):02d}"
            expected_alias_result = f"<{alias_label}>"
            rc, out, err = _run_session_cli(
                workdir,
                weft_harness,
                env,
                "queue",
                "write",
                alias_ref,
                json.dumps(
                    {
                        "args": [alias_label],
                        "kwargs": {"prefix": "<", "suffix": ">"},
                    }
                ),
            )
            assert rc == 0, err
            assert out == ""

            rc, out, err = _run_session_cli(
                workdir,
                weft_harness,
                env,
                "result",
                persistent_tid,
                "--timeout",
                "20",
                "--json",
                timeout=30.0,
            )
            assert rc == 0, err
            payload = json.loads(out)
            persistent_results.append(payload["result"])
            persistent_expected.append(expected_alias_result)

        if (round_index + 1) % INTERACTIVE_INTERVAL == 0:
            interactive_label = (
                f"interactive-{(round_index + 1) // INTERACTIVE_INTERVAL:02d}"
            )
            rc, out, err = _run_session_cli(
                workdir,
                weft_harness,
                env,
                "run",
                "--interactive",
                "--name",
                f"session-{interactive_label}",
                "--tag",
                "kind:interactive",
                "--tag",
                "session:long",
                "--",
                sys.executable,
                "-u",
                str(INTERACTIVE_SCRIPT),
                stdin=f"{interactive_label}-a\n{interactive_label}-b\nquit\n",
                timeout=40.0,
            )
            assert rc == 0, err
            interactive_outputs.append(out)
            interactive_expected_outputs.append(interactive_expected(interactive_label))

    assert manager_tid is not None
    assert len(persistent_results) == PERSISTENT_WORK_ITEMS
    assert len(interactive_outputs) == BULK_ROUNDS // INTERACTIVE_INTERVAL

    statuses = wait_for_terminal_statuses(
        weft_harness,
        {task.tid for task in bulk_tasks},
        timeout=SESSION_TIMEOUT_SECONDS,
    )
    assert set(statuses.values()) == {"completed"}

    time.sleep(WEFT_COMPLETED_RESULT_GRACE_SECONDS + 0.15)

    rc, out, err = _run_session_cli(
        workdir,
        weft_harness,
        env,
        "result",
        "--all",
        "--json",
        timeout=60.0,
    )
    assert rc == 0, err
    result_payload = json.loads(out)
    result_map = {item["tid"]: item["result"] for item in result_payload["results"]}
    assert set(result_map) == {task.tid for task in bulk_tasks}

    # Compare a canonical transcript so backend ordering differences do not matter.
    actual_transcript = {
        "persistent": persistent_results,
        "interactive": interactive_outputs,
        "bulk": [
            {"label": task.label, "result": result_map[task.tid]}
            for task in sorted(bulk_tasks, key=lambda task: task.label)
        ],
    }
    expected_transcript = {
        "persistent": persistent_expected,
        "interactive": interactive_expected_outputs,
        "bulk": [
            {"label": task.label, "result": task.expected}
            for task in sorted(bulk_tasks, key=lambda task: task.label)
        ],
    }
    assert actual_transcript == expected_transcript

    all_status_payload = _status_payload(
        workdir,
        weft_harness,
        env,
        all_tasks=True,
    )
    task_records = {
        entry["tid"]: entry
        for entry in all_status_payload["tasks"]
        if isinstance(entry, dict) and "tid" in entry
    }
    for task in sample_tasks:
        record = task_records[task.tid]
        assert record["name"] == task.name
        assert record["metadata"]["tags"] == list(task.tags)

    rc, out, err = _run_session_cli(
        workdir,
        weft_harness,
        env,
        "task",
        "stop",
        persistent_tid,
        timeout=30.0,
    )
    assert rc == 0, err

    persistent_statuses = wait_for_terminal_statuses(
        weft_harness,
        {persistent_tid},
        timeout=30.0,
    )
    assert persistent_statuses[persistent_tid] in {"cancelled", "completed"}

    with weft_harness.context.broker() as broker:
        aliases = dict(broker.list_aliases())
        assert aliases[alias_name] == persistent_inbox

    rc, out, err = _run_session_cli(
        workdir,
        weft_harness,
        env,
        "queue",
        "alias",
        "remove",
        alias_name,
    )
    assert rc == 0, err
    assert out == ""

    with weft_harness.context.broker() as broker:
        assert alias_name not in dict(broker.list_aliases())

    rc, out, err = _run_session_cli(
        workdir,
        weft_harness,
        env,
        "worker",
        "stop",
        manager_tid,
        "--timeout",
        "10",
        timeout=30.0,
    )
    assert rc == 0, err

    final_status_payload = _status_payload(workdir, weft_harness, env)
    assert final_status_payload["managers"] == []
    assert final_status_payload["tasks"] == []
