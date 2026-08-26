"""Builtin delegated-provider probe tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from weft.builtins import agent_probe
from weft.core.agents.provider_cli.registry import get_provider_cli_provider

pytestmark = pytest.mark.shared


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
