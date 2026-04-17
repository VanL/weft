"""Project-local delegated-agent settings and advisory health helpers.

Spec references:
- docs/specifications/04-SimpleBroker_Integration.md [SB-0]
- docs/specifications/13-Agent_Runtime.md [AR-5], [AR-7]
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from os import PathLike
from pathlib import Path
from typing import Any, Literal

from weft._constants import WEFT_AGENT_HEALTH_FILENAME, WEFT_AGENT_SETTINGS_FILENAME
from weft.context import build_context, find_existing_weft_dir
from weft.helpers import write_json_atomically


@dataclass(frozen=True, slots=True)
class ProviderCLIProjectSettings:
    """Resolved project-local delegated CLI settings for one provider."""

    executable: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderCLIProjectSettingsWriteResult:
    """Result of writing or preserving one delegated CLI executable setting."""

    action: Literal["created", "preserved"]
    executable: str
    path: Path


def load_provider_cli_project_settings(
    provider_name: str,
    *,
    spec_context: str | PathLike[str] | None = None,
) -> ProviderCLIProjectSettings:
    """Return project-local delegated launch settings for *provider_name*.

    Missing project-local agent settings are treated as "no project-local
    settings".
    Invalid settings are user-authored configuration errors and are raised.
    """
    payload = _load_agent_settings_payload(spec_context=spec_context)
    providers = _provider_settings_mapping(payload)
    provider_payload = providers.get(provider_name)
    if provider_payload is None:
        return ProviderCLIProjectSettings()
    if not isinstance(provider_payload, Mapping):
        raise ValueError(
            "Invalid delegated agent settings in the project-local settings file: "
            f"provider '{provider_name}' must map to an object"
        )
    executable = provider_payload.get("executable")
    if executable is None:
        return ProviderCLIProjectSettings()
    if not isinstance(executable, str) or not executable.strip():
        raise ValueError(
            "Invalid delegated agent settings in the project-local settings file: "
            f"provider '{provider_name}' executable must be a non-empty string"
        )
    return ProviderCLIProjectSettings(executable=executable.strip())


def ensure_provider_cli_project_executable(
    provider_name: str,
    *,
    executable: str,
    spec_context: str | PathLike[str] | None = None,
) -> ProviderCLIProjectSettingsWriteResult:
    """Persist a delegated provider executable if no explicit setting exists."""
    if not isinstance(executable, str) or not executable.strip():
        raise ValueError("Delegated provider executable must be a non-empty string")

    settings_path = settings_file_path(spec_context=spec_context)
    if settings_path is None:
        context = build_context(
            spec_context=spec_context,
            create_database=False,
        )
        settings_path = context.weft_dir / WEFT_AGENT_SETTINGS_FILENAME

    payload = _load_json_mapping(settings_path, label=WEFT_AGENT_SETTINGS_FILENAME)
    _provider_settings_mapping(payload)
    provider_payloads = _ensure_provider_payloads(payload)

    existing_entry = provider_payloads.get(provider_name)
    if existing_entry is None:
        provider_payloads[provider_name] = {"executable": executable.strip()}
        write_json_atomically(settings_path, payload)
        return ProviderCLIProjectSettingsWriteResult(
            action="created",
            executable=executable.strip(),
            path=settings_path,
        )
    if not isinstance(existing_entry, dict):
        raise ValueError(
            "Invalid delegated agent settings in the project-local settings file: "
            f"provider '{provider_name}' must map to an object"
        )

    existing_executable = existing_entry.get("executable")
    if existing_executable is not None:
        if not isinstance(existing_executable, str) or not existing_executable.strip():
            raise ValueError(
                "Invalid delegated agent settings in the project-local settings file: "
                f"provider '{provider_name}' executable must be a non-empty string"
            )
        return ProviderCLIProjectSettingsWriteResult(
            action="preserved",
            executable=existing_executable.strip(),
            path=settings_path,
        )

    existing_entry["executable"] = executable.strip()
    write_json_atomically(settings_path, payload)
    return ProviderCLIProjectSettingsWriteResult(
        action="created",
        executable=executable.strip(),
        path=settings_path,
    )


def record_provider_cli_health(
    provider_name: str,
    *,
    executable: str,
    command_class: str,
    status: str,
    detail: str | None = None,
    spec_context: str | PathLike[str] | None = None,
) -> None:
    """Best-effort record of observed delegated CLI success or failure.

    Health observations are advisory only. Missing or malformed health files do
    not block execution, and write failures are intentionally ignored.
    """
    weft_dir = find_existing_weft_dir(spec_context)
    if weft_dir is None:
        return

    health_path = weft_dir / WEFT_AGENT_HEALTH_FILENAME
    try:
        payload = _load_json_mapping(health_path, label=WEFT_AGENT_HEALTH_FILENAME)
    except ValueError:
        payload = {}

    provider_payloads = _ensure_provider_payloads(payload)
    provider_payload = provider_payloads.setdefault(provider_name, {})
    if not isinstance(provider_payload, dict):
        provider_payload = {}
        provider_payloads[provider_name] = provider_payload

    event = {
        "at": datetime.now(UTC).isoformat(),
        "executable": executable,
        "command_class": command_class,
    }
    if detail:
        event["detail"] = detail

    if status == "success":
        provider_payload["last_success"] = event
    elif status == "failure":
        provider_payload["last_failure"] = event
    else:
        raise ValueError(f"Unsupported delegated agent health status: {status!r}")

    try:
        write_json_atomically(health_path, payload)
    except OSError:
        return


def health_file_path(
    *,
    spec_context: str | PathLike[str] | None = None,
) -> Path | None:
    """Return the advisory delegated-agent health path when inside a project."""
    weft_dir = find_existing_weft_dir(spec_context)
    if weft_dir is None:
        return None
    return weft_dir / WEFT_AGENT_HEALTH_FILENAME


def settings_file_path(
    *,
    spec_context: str | PathLike[str] | None = None,
) -> Path | None:
    """Return the project-local delegated-agent settings path when inside a project."""
    weft_dir = find_existing_weft_dir(spec_context)
    if weft_dir is None:
        return None
    return weft_dir / WEFT_AGENT_SETTINGS_FILENAME


def _load_agent_settings_payload(
    *,
    spec_context: str | PathLike[str] | None = None,
) -> dict[str, Any]:
    settings_path = settings_file_path(spec_context=spec_context)
    if settings_path is None:
        return {}
    return _load_json_mapping(settings_path, label=WEFT_AGENT_SETTINGS_FILENAME)


def _provider_settings_mapping(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    provider_cli = payload.get("provider_cli")
    if provider_cli is None:
        return {}
    if not isinstance(provider_cli, Mapping):
        raise ValueError(
            "Invalid delegated agent settings in the project-local settings file: "
            "'provider_cli' must be an object"
        )
    providers = provider_cli.get("providers")
    if providers is None:
        return {}
    if not isinstance(providers, Mapping):
        raise ValueError(
            "Invalid delegated agent settings in the project-local settings file: "
            "'provider_cli.providers' must be an object"
        )
    return providers


def _ensure_provider_payloads(payload: dict[str, Any]) -> dict[str, Any]:
    provider_cli = payload.get("provider_cli")
    if not isinstance(provider_cli, dict):
        provider_cli = {}
        payload["provider_cli"] = provider_cli
    providers = provider_cli.get("providers")
    if not isinstance(providers, dict):
        providers = {}
        provider_cli["providers"] = providers
    return providers


def _load_json_mapping(path: Path, *, label: str) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to parse {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid {label}: root value must be an object")
    return payload


__all__ = [
    "ProviderCLIProjectSettings",
    "ProviderCLIProjectSettingsWriteResult",
    "ensure_provider_cli_project_executable",
    "health_file_path",
    "load_provider_cli_project_settings",
    "record_provider_cli_health",
    "settings_file_path",
]
