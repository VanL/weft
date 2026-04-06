"""CLI tests for `weft spec` commands."""

from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import run_cli
from tests.taskspec import fixtures as taskspec_fixtures


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_spec_create_list_show_delete(workdir) -> None:
    spec_path = workdir / "task.json"
    _write_json(
        spec_path,
        {
            "name": "demo-task",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
            "metadata": {},
        },
    )

    rc, out, err = run_cli(
        "spec",
        "create",
        "demo",
        "--type",
        "task",
        "--file",
        spec_path,
        "--context",
        workdir,
        cwd=workdir,
    )
    assert rc == 0
    assert out.strip().endswith(".weft/tasks/demo.json")
    assert err == ""

    rc, out, err = run_cli(
        "spec",
        "list",
        "--json",
        "--context",
        workdir,
        cwd=workdir,
    )
    assert rc == 0
    data = json.loads(out)
    assert any(item["name"] == "demo" and item["type"] == "task" for item in data)

    rc, out, err = run_cli(
        "spec",
        "show",
        "demo",
        "--type",
        "task",
        "--context",
        workdir,
        cwd=workdir,
    )
    assert rc == 0
    payload = json.loads(out)
    assert payload["spec"]["function_target"]
    assert err == ""

    rc, out, err = run_cli(
        "spec",
        "delete",
        "demo",
        "--type",
        "task",
        "--context",
        workdir,
        cwd=workdir,
    )
    assert rc == 0
    assert "Deleted" in out
    assert err == ""


def test_spec_validate_and_generate(workdir) -> None:
    spec_path = workdir / "task.json"
    _write_json(
        spec_path,
        {
            "name": "demo-task",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
            "metadata": {},
        },
    )

    rc, out, err = run_cli("spec", "validate", spec_path, cwd=workdir)
    assert rc == 0
    assert "Spec is valid" in out
    assert err == ""

    rc, out, err = run_cli("spec", "generate", "--type", "task", cwd=workdir)
    assert rc == 0
    payload = json.loads(out)
    assert "spec" in payload
    assert "name" in payload
    assert err == ""


def test_spec_create_list_show_delete_agent(workdir) -> None:
    spec_path = workdir / "agent_task.json"
    payload = taskspec_fixtures.create_valid_agent_taskspec(
        tid=taskspec_fixtures.VALID_TEST_TID,
        name="demo-agent-task",
    ).model_dump(mode="json")
    payload.pop("tid", None)
    payload["io"] = {}
    payload["state"] = {}
    _write_json(spec_path, payload)

    rc, out, err = run_cli(
        "spec",
        "create",
        "demo-agent",
        "--type",
        "task",
        "--file",
        spec_path,
        "--context",
        workdir,
        cwd=workdir,
    )
    assert rc == 0
    assert out.strip().endswith(".weft/tasks/demo-agent.json")
    assert err == ""

    rc, out, err = run_cli(
        "spec",
        "list",
        "--json",
        "--context",
        workdir,
        cwd=workdir,
    )
    assert rc == 0
    data = json.loads(out)
    assert any(item["name"] == "demo-agent" and item["type"] == "task" for item in data)

    rc, out, err = run_cli(
        "spec",
        "show",
        "demo-agent",
        "--type",
        "task",
        "--context",
        workdir,
        cwd=workdir,
    )
    assert rc == 0
    payload = json.loads(out)
    assert payload["spec"]["type"] == "agent"
    assert payload["spec"]["agent"]["runtime"] == "llm"
    assert err == ""

    rc, out, err = run_cli(
        "spec",
        "validate",
        spec_path,
        cwd=workdir,
    )
    assert rc == 0
    assert "Spec is valid" in out
    assert err == ""

    rc, out, err = run_cli(
        "spec",
        "delete",
        "demo-agent",
        "--type",
        "task",
        "--context",
        workdir,
        cwd=workdir,
    )
    assert rc == 0
    assert "Deleted" in out
    assert err == ""
