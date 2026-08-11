"""Tests for project-local delegated provider CLI settings."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import weft.core.agents.provider_cli.settings as settings_module
from weft.core.agents.provider_cli.settings import (
    ensure_provider_cli_project_executable,
    load_provider_cli_project_settings,
    record_provider_cli_health,
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


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "Invalid agents.json: root value must be an object"),
        (
            {"provider_cli": []},
            (
                "Invalid delegated agent settings in the project-local settings file: "
                "'provider_cli' must be an object"
            ),
        ),
        (
            {"provider_cli": {"providers": []}},
            (
                "Invalid delegated agent settings in the project-local settings file: "
                "'provider_cli.providers' must be an object"
            ),
        ),
        (
            {"provider_cli": {"providers": {"claude_code": []}}},
            (
                "Invalid delegated agent settings in the project-local settings file: "
                "provider 'claude_code' must map to an object"
            ),
        ),
    ],
)
def test_load_rejects_malformed_settings_values_as_value_error(
    tmp_path: Path,
    payload: object,
    message: str,
) -> None:
    weft_dir = tmp_path / ".weft"
    weft_dir.mkdir()
    (weft_dir / "agents.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_provider_cli_project_settings("claude_code", spec_context=tmp_path)
    assert type(exc_info.value) is ValueError
    assert str(exc_info.value) == message
    assert exc_info.value.__cause__ is None


def test_ensure_rejects_malformed_existing_provider_as_value_error(
    tmp_path: Path,
) -> None:
    weft_dir = tmp_path / ".weft"
    weft_dir.mkdir()
    (weft_dir / "agents.json").write_text(
        json.dumps({"provider_cli": {"providers": {"claude_code": []}}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as exc_info:
        ensure_provider_cli_project_executable(
            "claude_code",
            executable="/usr/local/bin/claude",
            spec_context=tmp_path,
        )
    assert type(exc_info.value) is ValueError
    assert str(exc_info.value) == (
        "Invalid delegated agent settings in the project-local settings file: "
        "provider 'claude_code' must map to an object"
    )
    assert exc_info.value.__cause__ is None


def test_settings_write_failure_propagates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / ".weft").mkdir()

    def fail_write(path: Path, payload: dict[str, object]) -> None:
        del path, payload
        raise OSError("settings publish failed")

    monkeypatch.setattr(settings_module, "write_json_atomically", fail_write)

    with pytest.raises(OSError, match="settings publish failed"):
        ensure_provider_cli_project_executable(
            "claude_code",
            executable="/usr/local/bin/claude",
            spec_context=tmp_path,
        )


def test_advisory_health_write_failure_is_suppressed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / ".weft").mkdir()

    def fail_write(path: Path, payload: dict[str, object]) -> None:
        del path, payload
        raise OSError("health publish failed")

    monkeypatch.setattr(settings_module, "write_json_atomically", fail_write)

    record_provider_cli_health(
        "claude_code",
        executable="/usr/local/bin/claude",
        command_class="prompt",
        status="success",
        spec_context=tmp_path,
    )
