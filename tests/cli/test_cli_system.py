"""CLI tests for system maintenance commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import run_cli
from tests.helpers.test_backend import prepare_project_root
from weft.context import build_context

pytestmark = [pytest.mark.shared]


def _write_message(context, queue_name: str, body: str) -> None:
    queue = context.queue(queue_name, persistent=True)
    queue.write(body)
    queue.close()


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


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
