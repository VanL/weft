"""CLI tests for `weft spec` commands."""

from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import run_cli
from tests.fixtures.provider_cli_fixture import write_provider_cli_wrapper
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
    assert Path(out.strip()).as_posix().endswith(".weft/tasks/demo.json")
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
    assert Path(out.strip()).as_posix().endswith(".weft/tasks/demo-agent.json")
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


def test_spec_list_and_show_builtin_task_spec(workdir) -> None:
    rc, out, err = run_cli(
        "spec",
        "list",
        "--context",
        workdir,
        cwd=workdir,
    )

    assert rc == 0
    assert "task: probe-agents (builtin)" in out
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
    builtin = next(
        item
        for item in data
        if item["type"] == "task" and item["name"] == "probe-agents"
    )
    assert builtin["source"] == "builtin"
    assert builtin["path"].endswith("weft/builtins/tasks/probe-agents.json")
    assert err == ""

    rc, out, err = run_cli(
        "spec",
        "show",
        "probe-agents",
        "--type",
        "task",
        "--context",
        workdir,
        cwd=workdir,
    )

    assert rc == 0
    payload = json.loads(out)
    assert (
        payload["spec"]["function_target"]
        == "weft.builtins.agent_probe:probe_agents_task"
    )
    assert err == ""


def test_spec_show_builtin_dockerized_agent(workdir) -> None:
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
    builtin = next(
        item
        for item in data
        if item["type"] == "task" and item["name"] == "dockerized-agent"
    )
    assert builtin["source"] == "builtin"
    assert builtin["path"].endswith(
        "weft/builtins/tasks/dockerized-agent/taskspec.json"
    )
    assert err == ""

    rc, out, err = run_cli(
        "spec",
        "show",
        "dockerized-agent",
        "--type",
        "task",
        "--context",
        workdir,
        cwd=workdir,
    )

    assert rc == 0
    payload = json.loads(out)
    assert payload["spec"]["type"] == "agent"
    assert payload["spec"]["agent"]["runtime"] == "provider_cli"
    assert payload["spec"]["agent"]["runtime_config"]["provider"] == "codex"
    assert payload["spec"]["runner"]["name"] == "docker"
    assert (
        payload["spec"]["runner"]["environment_profile_ref"]
        == "dockerized_agent:dockerized_agent_environment_profile"
    )
    assert (
        payload["spec"]["parameterization"]["adapter_ref"]
        == "dockerized_agent:dockerized_agent_parameterization"
    )
    assert (
        payload["spec"]["run_input"]["adapter_ref"]
        == "dockerized_agent:dockerized_agent_run_input"
    )
    assert (
        payload["spec"]["agent"]["runtime_config"]["tool_profile_ref"]
        == "dockerized_agent:dockerized_agent_tool_profile"
    )
    assert "explain_mounted" in payload["spec"]["agent"]["templates"]
    assert "explain_inline" in payload["spec"]["agent"]["templates"]
    assert err == ""


def test_spec_delete_rejects_builtin_only_spec(workdir) -> None:
    rc, out, err = run_cli(
        "spec",
        "delete",
        "probe-agents",
        "--type",
        "task",
        "--context",
        workdir,
        cwd=workdir,
    )

    assert rc == 2
    assert out == ""
    assert "read-only" in err


def test_spec_list_and_show_prefer_local_shadow_over_builtin(workdir) -> None:
    wrapper = write_provider_cli_wrapper(workdir, "codex")
    source_path = workdir / "local_probe_agents.json"
    _write_json(
        source_path,
        {
            "name": "local-probe-agents",
            "spec": {
                "type": "agent",
                "agent": {
                    "runtime": "provider_cli",
                    "authority_class": "general",
                    "instructions": "shadow",
                    "output_mode": "text",
                    "conversation_scope": "per_message",
                    "runtime_config": {
                        "provider": "codex",
                        "executable": str(wrapper),
                    },
                },
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
        "spec",
        "list",
        "--json",
        "--context",
        workdir,
        cwd=workdir,
    )

    assert rc == 0
    data = json.loads(out)
    matches = [
        item
        for item in data
        if item["type"] == "task" and item["name"] == "probe-agents"
    ]
    assert len(matches) == 1
    assert matches[0]["source"] == "stored"
    assert err == ""

    rc, out, err = run_cli(
        "spec",
        "show",
        "probe-agents",
        "--type",
        "task",
        "--context",
        workdir,
        cwd=workdir,
    )

    assert rc == 0
    payload = json.loads(out)
    assert payload["metadata"]["shadow"] is True
    assert err == ""
