"""Tests for the ``weft status`` command helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft.commands import status as status_cmd
from weft.commands.status import cmd_status, collect_status
from weft.context import build_context
from weft.ext import RunnerRuntimeDescription

pytestmark = [pytest.mark.shared]


def test_collect_status_reports_message_counts(tmp_path):
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    queue = ctx.queue("status.queue", persistent=True)
    queue.write("hello")
    queue.write("there")

    snapshot = collect_status(ctx)

    assert snapshot.total_messages == 2
    assert snapshot.db_size >= 0


def test_cmd_status_text_output(tmp_path):
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    queue = ctx.queue("status.queue", persistent=True)
    queue.write("payload")

    exit_code, payload = cmd_status(spec_context=root)

    assert exit_code == 0
    assert payload is not None
    lines = payload.splitlines()
    assert lines[0].startswith("total_messages: ")

    ts_line = next(line for line in lines if line.startswith("last_timestamp: "))
    assert ts_line.endswith(")")
    assert "(" in ts_line

    size_line = next(line for line in lines if line.startswith("db_size: "))
    assert "bytes" in size_line
    assert "(" in size_line


def test_cmd_status_json_output(tmp_path):
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    queue = ctx.queue("status.queue", persistent=True)
    queue.write("payload")

    exit_code, payload = cmd_status(json_output=True, spec_context=root)

    assert exit_code == 0
    assert payload is not None
    data = json.loads(payload)
    assert data["broker"]["total_messages"] >= 1
    assert "db_size" in data["broker"]
    assert isinstance(data["managers"], list)


def test_cmd_status_json_includes_runner_runtime_details(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    tid = "1844674407370955161"
    started = 1_762_000_000_000_000_000
    log_queue = ctx.queue("weft.log.tasks", persistent=False)
    mapping_queue = ctx.queue("weft.state.tid_mappings", persistent=False)
    log_queue.write(
        json.dumps(
            {
                "event": "work_started",
                "status": "running",
                "tid": tid,
                "taskspec": {
                    "name": "docker-task",
                    "spec": {
                        "runner": {
                            "name": "docker",
                            "options": {"image": "python:3.13-alpine"},
                        }
                    },
                    "state": {
                        "status": "running",
                        "started_at": started,
                        "completed_at": None,
                    },
                    "metadata": {"owner": "tests"},
                },
            }
        )
    )
    mapping_queue.write(
        json.dumps(
            {
                "short": tid[-10:],
                "full": tid,
                "pid": 11111,
                "task_pid": 11111,
                "managed_pids": [],
                "runner": "docker",
                "runtime_handle": {
                    "runner_name": "docker",
                    "runtime_id": "container-123",
                    "host_pids": [],
                    "metadata": {"image": "python:3.13-alpine"},
                },
            }
        )
    )

    class FakeRunnerPlugin:
        def describe(self, handle: Any) -> RunnerRuntimeDescription | None:
            return RunnerRuntimeDescription(
                runner_name=handle.runner_name,
                runtime_id=handle.runtime_id,
                state="running",
                metadata={"image": "python:3.13-alpine", "cpu_percent": 0.5},
            )

    monkeypatch.setattr(
        status_cmd,
        "require_runner_plugin",
        lambda name: FakeRunnerPlugin(),
    )

    exit_code, payload = cmd_status(
        json_output=True, include_terminal=True, spec_context=root
    )

    assert exit_code == 0
    assert payload is not None
    data = json.loads(payload)
    assert len(data["tasks"]) == 1
    entry = data["tasks"][0]
    assert entry["tid"] == tid
    assert entry["runner"] == "docker"
    assert entry["runtime_handle"]["runner_name"] == "docker"
    assert entry["runtime_handle"]["runtime_id"] == "container-123"
    assert entry["runtime"]["runner_name"] == "docker"
    assert entry["runtime"]["runtime_id"] == "container-123"
    assert entry["runtime"]["state"] == "running"
    assert entry["runtime"]["metadata"]["image"] == "python:3.13-alpine"
    assert entry["metadata"]["owner"] == "tests"


def test_cmd_status_discovers_parent_context_from_subdirectory(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "project"
    nested = root / "subdir" / "child"
    nested.mkdir(parents=True)

    prepared_root = prepare_project_root(root)
    ctx = build_context(spec_context=prepared_root)
    queue = ctx.queue("status.queue", persistent=True)
    queue.write("payload")

    monkeypatch.chdir(Path(nested))

    exit_code, payload = cmd_status()

    assert exit_code == 0
    assert payload is not None
    assert "total_messages: 1" in payload
