"""Tests for project-local delegated provider CLI settings."""

from __future__ import annotations

from pathlib import Path

import pytest

from weft.core.agents.provider_cli.settings import (
    ensure_provider_cli_project_executable,
    load_provider_cli_project_settings,
)

pytestmark = [pytest.mark.shared]


def test_load_without_project_settings_returns_empty(tmp_path: Path) -> None:
    settings = load_provider_cli_project_settings("claude_code", spec_context=tmp_path)
    assert settings.executable is None


def test_ensure_then_load_round_trip(tmp_path: Path) -> None:
    (tmp_path / ".weft").mkdir()
    result = ensure_provider_cli_project_executable(
        "claude_code",
        executable="/usr/local/bin/claude",
        spec_context=tmp_path,
    )
    assert result.action == "created"
    assert result.executable == "/usr/local/bin/claude"

    settings = load_provider_cli_project_settings("claude_code", spec_context=tmp_path)
    assert settings.executable == "/usr/local/bin/claude"


def test_ensure_preserves_existing_executable(tmp_path: Path) -> None:
    (tmp_path / ".weft").mkdir()
    ensure_provider_cli_project_executable(
        "claude_code", executable="/first", spec_context=tmp_path
    )
    result = ensure_provider_cli_project_executable(
        "claude_code", executable="/second", spec_context=tmp_path
    )
    assert result.action == "preserved"
    assert result.executable == "/first"


def test_invalid_settings_payload_raises(tmp_path: Path) -> None:
    weft_dir = tmp_path / ".weft"
    weft_dir.mkdir()
    (weft_dir / "agents.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        load_provider_cli_project_settings("claude_code", spec_context=tmp_path)
