"""Builtin delegated-provider probe tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.fixtures.provider_cli_fixture import write_provider_cli_wrapper
from weft.builtins import agent_probe
from weft.core.agents.provider_cli.registry import get_provider_cli_provider

pytestmark = pytest.mark.shared


def _configure_project_relative_codex(project_root: Path, script: Path) -> None:
    """Point the project's codex provider at ``./bin/<script name>``.

    The script comes from ``write_provider_cli_wrapper`` so it is runnable on
    every platform (a ``.cmd`` wrapper on Windows, a shell script elsewhere).
    """

    settings = {
        "provider_cli": {"providers": {"codex": {"executable": f"./bin/{script.name}"}}}
    }
    (project_root / ".weft" / "agents.json").write_text(
        json.dumps(settings) + "\n",
        encoding="utf-8",
    )


def test_probe_agents_reports_opencode_help_process_facts_without_support_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome: dict[str, object] = {
        "attempted": True,
        "timed_out": False,
        "returncode": 0,
        "detail": "stdout=localized help",
    }
    monkeypatch.setattr(agent_probe.shutil, "which", lambda value: f"/bin/{value}")
    monkeypatch.setattr(
        agent_probe,
        "probe_provider_cli_version",
        lambda executable, *, provider_name: "1.2.3",
    )
    monkeypatch.setattr(
        agent_probe,
        "probe_opencode_run_help",
        lambda executable: outcome,
    )

    result = agent_probe.probe_agents(
        project_root=tmp_path,
        persist_settings=False,
        providers=(get_provider_cli_provider("opencode"),),
    )

    provider_report = result["providers"][0]
    assert provider_report["run_help"] == outcome
    assert "run_support" not in provider_report
    assert "run_support_error" not in provider_report


def test_probe_agents_reports_unattempted_opencode_help_when_executable_is_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_probe.shutil, "which", lambda value: None)

    result = agent_probe.probe_agents(
        project_root=tmp_path,
        persist_settings=False,
        providers=(get_provider_cli_provider("opencode"),),
    )

    run_help = result["providers"][0]["run_help"]
    assert {key: run_help[key] for key in ("attempted", "timed_out", "returncode")} == {
        "attempted": False,
        "timed_out": False,
        "returncode": None,
    }
    assert "not found" in str(run_help["detail"])


def test_probe_agents_resolves_project_relative_executable_against_project_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project"
    other_cwd = tmp_path / "elsewhere"
    (project_root / ".weft").mkdir(parents=True)
    other_cwd.mkdir(parents=True)
    script = write_provider_cli_wrapper(project_root / "bin", "codex")
    _configure_project_relative_codex(project_root, script)
    monkeypatch.chdir(other_cwd)

    result = agent_probe.probe_agents(
        project_root=project_root,
        persist_settings=False,
        providers=(get_provider_cli_provider("codex"),),
    )

    report = result["providers"][0]
    assert report["probe_status"] == "available"
    assert Path(report["resolved_executable"]).resolve() == script.resolve()


def test_probe_agents_missing_project_relative_executable_ignores_cwd_binary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project"
    other_cwd = tmp_path / "elsewhere"
    (project_root / ".weft").mkdir(parents=True)
    script = write_provider_cli_wrapper(other_cwd / "bin", "codex")
    _configure_project_relative_codex(project_root, script)
    monkeypatch.chdir(other_cwd)

    result = agent_probe.probe_agents(
        project_root=project_root,
        persist_settings=False,
        providers=(get_provider_cli_provider("codex"),),
    )

    report = result["providers"][0]
    assert report["probe_status"] == "not_found"
    assert report["resolved_executable"] is None
