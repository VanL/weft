"""Rendering contracts retained from retired command wrappers ([PY-2], [CLI-6])."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.helpers.test_backend import prepare_project_root
from weft import commands
from weft._constants import WEFT_GLOBAL_LOG_QUEUE
from weft.cli.app import app
from weft.commands.types import RunExecutionResult
from weft.context import WeftContext, build_context
from weft.core import manager_runtime

pytestmark = [pytest.mark.shared]


def test_run_live_cli_keeps_degraded_receipt_machine_readable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A readiness warning stays on stderr after the CLI accepts work [CLI-1.1.1]."""

    tid = "1777000000000000999"
    warning = f"Task {tid} was accepted; manager availability is uncertain."
    monkeypatch.setattr(
        commands,
        "cmd_run",
        lambda *_args, **_kwargs: RunExecutionResult(
            tid=tid,
            availability_warning=warning,
        ),
    )

    result = CliRunner().invoke(app, ["run", "--no-wait", "--json", "echo", "ok"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"tid": tid, "status": "queued"}
    assert result.stderr == f"{warning}\n"


@pytest.mark.parametrize("mode", ["populated", "empty", "claimed"])
def test_dump_live_cli_preserves_export_summary(tmp_path: Path, mode: str) -> None:
    context = build_context(prepare_project_root(tmp_path / "project"))
    output = context.weft_dir / "export.jsonl"
    if mode == "populated":
        with context.queue("test.queue1", persistent=False) as queue:
            queue.write("one")
            queue.write("two")
        with context.queue("test.queue2", persistent=False) as queue:
            queue.write("three")
        with context.broker() as broker:
            broker.add_alias("alias1", "test.queue1")
            broker.add_alias("alias2", "test.queue2")
        summary = "Exported 3 messages from 2 queues and 2 aliases"
    elif mode == "claimed":
        with context.queue("claimed.queue", persistent=False) as queue:
            queue.write("claimed")
            assert queue.read_one() == "claimed"
        summary = "Exported 0 messages from 0 queues; omitted 1 claimed messages from 1 queues"
    else:
        summary = "Exported 0 messages from 0 queues"

    result = CliRunner().invoke(
        app, ["system", "dump", "--context", str(context.root), "--output", str(output)]
    )
    assert result.exit_code == 0, result.output
    assert result.stdout == f"{summary} to {output}\n"
    assert result.stderr == ""
    assert output.exists()


@pytest.mark.parametrize("started_here", [False, True])
def test_manager_start_live_cli_preserves_bootstrap_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, started_here: bool
) -> None:
    root = prepare_project_root(tmp_path / "project")
    tid = "1761000000000000000"
    calls: list[Path] = []

    def ensure_manager(context: WeftContext) -> manager_runtime.ManagerEnsureResult:
        calls.append(context.root)
        return manager_runtime.ManagerEnsureResult(
            outcome="ready",
            manager_record={"tid": tid, "status": "active", "name": "manager"},
            started_here=started_here,
            process_handle=None,
            reason="ready",
        )

    monkeypatch.setattr(manager_runtime, "ensure_manager", ensure_manager)
    result = CliRunner().invoke(app, ["manager", "start", "--context", str(root)])
    assert result.exit_code == 0, result.output
    assert result.stdout == (
        f"Started manager {tid}\n"
        if started_here
        else f"Manager {tid} already running\n"
    )
    assert result.stderr == ""
    assert calls == [root]


def test_prune_live_cli_reports_write_failure_after_successful_delete(
    tmp_path: Path,
) -> None:
    context = build_context(prepare_project_root(tmp_path / "project"))
    tid = "1770000000000001008"
    with context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False) as queue:
        old_id = queue.write(json.dumps({"tid": tid, "status": "created"}))
        keep_id = queue.write(json.dumps({"tid": tid, "status": "completed"}))
    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("occupied", encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [
            "system",
            "prune",
            "--family",
            "task-log",
            "--context",
            str(context.root),
            "--apply",
            "--min-age",
            "0",
            "--archive",
            str(tmp_path / "archive.jsonl"),
            "--report",
            str(blocked_parent / "report.jsonl"),
            "--json",
        ],
    )
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["deleted"] == 1
    assert result.stderr.startswith("failed to write report:")
    with context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False) as queue:
        remaining = {
            timestamp for _body, timestamp in queue.peek_generator(with_timestamps=True)
        }
    assert old_id not in remaining
    assert keep_id in remaining
