"""CLI tests for system maintenance commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import run_cli
from tests.helpers.test_backend import prepare_project_root
from weft._constants import WEFT_GLOBAL_LOG_QUEUE, WEFT_TID_MAPPINGS_QUEUE
from weft.context import build_context
from weft.helpers import iter_queue_json_entries

pytestmark = [pytest.mark.shared]


def _write_message(context, queue_name: str, body: str) -> None:
    queue = context.queue(queue_name, persistent=True)
    queue.write(body)
    queue.close()


def _write_task_log(context, payload: dict[str, object]) -> None:
    queue = context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    try:
        queue.write(json.dumps(payload))
    finally:
        queue.close()


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_queue_json(context, queue_name: str, payload: dict[str, object]) -> int:
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


def _read_queue_ids(context, queue_name: str) -> set[int]:
    queue = context.queue(queue_name, persistent=False)
    try:
        return {
            int(message_id) for _payload, message_id in iter_queue_json_entries(queue)
        }
    finally:
        queue.close()


def test_system_builtins_lists_shipped_inventory(workdir) -> None:
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


def test_system_builtins_json_reports_builtin_metadata(workdir) -> None:
    rc, out, err = run_cli(
        "system",
        "builtins",
        "--json",
        cwd=workdir,
    )

    assert rc == 0
    payload = json.loads(out)
    builtin = next(item for item in payload if item["name"] == "probe-agents")
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


def test_system_builtins_ignores_local_project_shadow(workdir) -> None:
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

    rc, out, err = run_cli(
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


def test_system_prune_dry_run_json_deletes_nothing(workdir) -> None:
    context = build_context(spec_context=workdir)
    old_id = _write_queue_json(
        context,
        WEFT_TID_MAPPINGS_QUEUE,
        {"short": "111", "full": "1770000000000000500"},
    )
    new_id = _write_queue_json(
        context,
        WEFT_TID_MAPPINGS_QUEUE,
        {"short": "222", "full": "1770000000000000500"},
    )

    rc, out, err = run_cli(
        "system",
        "prune",
        "--dry-run",
        "--json",
        "--min-age",
        "0",
        "--queue",
        "tid-mappings",
        "--context",
        workdir,
        cwd=workdir,
    )

    assert rc == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["dry_run"] is True
    assert payload["candidates"] == 1
    assert payload["classification_counts"] == {"superseded_tid_mapping": 1}
    assert _read_queue_ids(context, WEFT_TID_MAPPINGS_QUEUE) >= {old_id, new_id}


def test_system_prune_apply_deletes_candidate(workdir) -> None:
    context = build_context(spec_context=workdir)
    old_id = _write_queue_json(
        context,
        WEFT_TID_MAPPINGS_QUEUE,
        {"short": "111", "full": "1770000000000000501"},
    )
    new_id = _write_queue_json(
        context,
        WEFT_TID_MAPPINGS_QUEUE,
        {"short": "222", "full": "1770000000000000501"},
    )

    rc, out, err = run_cli(
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

    assert rc == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["dry_run"] is False
    assert payload["deleted"] == 1
    remaining_ids = _read_queue_ids(context, WEFT_TID_MAPPINGS_QUEUE)
    assert old_id not in remaining_ids
    assert new_id in remaining_ids


def test_system_prune_rejects_invalid_options(workdir) -> None:
    rc, _out, err = run_cli(
        "system",
        "prune",
        "--queue",
        "nonsense",
        "--context",
        workdir,
        cwd=workdir,
    )

    assert rc == 1
    assert "unknown runtime-state queue filter" in err

    rc, _out, err = run_cli(
        "system",
        "prune",
        "--keep-recent-per-key",
        "0",
        "--context",
        workdir,
        cwd=workdir,
    )

    assert rc == 1
    assert "--keep-recent-per-key must be >= 1" in err

    rc, out, err = run_cli(
        "system",
        "builtins",
        "--json",
        cwd=workdir,
    )

    assert rc == 0
    payload = json.loads(out)
    builtin = next(item for item in payload if item["name"] == "probe-agents")
    assert builtin["source"] == "builtin"
    assert (
        Path(builtin["path"])
        .as_posix()
        .endswith("weft/builtins/tasks/probe-agents.json")
    )
    assert builtin["description"].startswith("Probe known delegated provider CLIs")
    assert err == ""


def test_system_dump_exports_messages(workdir) -> None:
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


def test_system_dump_excludes_runtime_queues(workdir) -> None:
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


def test_system_load_imports_dump(workdir) -> None:
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


def test_system_lifecycle_monitor_stdout_jsonl(workdir) -> None:
    context = build_context(spec_context=workdir)
    tid = "1778084345905438730"
    _write_task_log(
        context,
        {
            "event": "work_completed",
            "status": "completed",
            "tid": tid,
            "taskspec": {
                "tid": tid,
                "name": "cli-lifecycle",
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
        "lifecycle-monitor",
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


def test_system_lifecycle_monitor_rejects_stdout_json(workdir) -> None:
    rc, out, err = run_cli(
        "system",
        "lifecycle-monitor",
        "--sink",
        "stdout",
        "--json",
        "--context",
        workdir,
        cwd=workdir,
    )

    assert rc == 1
    assert out == ""
    assert "cannot be combined" in err


def test_system_lifecycle_monitor_disk_json_summary(workdir) -> None:
    context = build_context(spec_context=workdir)
    tid = "1778084345905438731"
    _write_task_log(
        context,
        {
            "event": "work_completed",
            "status": "completed",
            "tid": tid,
            "taskspec": {
                "tid": tid,
                "name": "cli-lifecycle-disk",
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
    archive_dir = workdir / "archive"
    checkpoint = workdir / "checkpoint.json"

    rc, out, err = run_cli(
        "system",
        "lifecycle-monitor",
        "--once",
        "--sink",
        "disk",
        "--archive-dir",
        archive_dir,
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
    assert list(archive_dir.glob("*.jsonl"))
