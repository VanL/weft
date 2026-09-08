"""Claimed result JSON stays available through the live CLI [CLI-1.2.2]."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.helpers.test_backend import prepare_project_root
from weft.cli.app import app
from weft.commands import result as result_cmd
from weft.context import build_context
from weft.core import task_evidence

pytestmark = [pytest.mark.shared]


def test_result_claimed_outbox_json_keeps_reconciliation_without_waiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = str(time.time_ns())
    taskspec = {
        "tid": tid,
        "name": "claimed-task",
        "spec": {"type": "function", "runner": {"name": "host", "options": {}}},
        "io": {
            "outputs": {"outbox": f"T{tid}.outbox"},
            "control": {"ctrl_out": f"T{tid}.ctrl_out"},
        },
        "state": {"status": "running", "started_at": time.time_ns()},
        "metadata": {},
    }
    monkeypatch.setattr(task_evidence, "STATUS_RUNTIMELESS_STALE_AFTER_SECONDS", -1.0)

    def unexpected_wait(*args: object, **kwargs: object) -> None:
        raise AssertionError("claimed result must not wait for completion")

    monkeypatch.setattr(result_cmd, "_await_single_result", unexpected_wait)
    log = ctx.queue("weft.log.tasks", persistent=False)
    outbox = ctx.queue(f"T{tid}.outbox", persistent=True)
    try:
        log.write(
            json.dumps(
                {
                    "tid": tid,
                    "status": "running",
                    "event": "work_started",
                    "taskspec": taskspec,
                }
            )
        )
        outbox.write(json.dumps({"ok": True}))
        assert outbox.read_one() is not None
        result = CliRunner().invoke(
            app, ["result", tid, "--json", "--timeout", "2", "--context", str(root)]
        )
        assert result.exit_code == 1
        assert result.stderr == ""
        payload = json.loads(result.stdout)
        assert set(payload) == {"tid", "status", "result", "error", "reconciliation"}
        assert payload["tid"] == tid
        assert payload["status"] == "failed"
        assert payload["result"] is None
        assert "claimed" in payload["error"]
        assert (
            payload["reconciliation"]["classification"]
            == "claimed_result_without_terminal"
        )
        assert outbox.stats().total == 1
    finally:
        outbox.close()
        log.close()
