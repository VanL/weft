"""Builtin helper task for probing delegated agent CLIs.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-1.1.1], [CLI-1.4]
- docs/specifications/13-Agent_Runtime.md [AR-7]
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from weft.context import build_context
from weft.core.agents.provider_cli.probes import (
    ensure_opencode_run_support,
    probe_provider_cli_version,
)
from weft.core.agents.provider_cli.registry import (
    ProviderCLIProvider,
    list_provider_cli_providers,
)
from weft.core.agents.provider_cli.settings import (
    ensure_provider_cli_project_executable,
    load_provider_cli_project_settings,
    settings_file_path,
)


def probe_agents_task() -> dict[str, Any]:
    """Probe known delegated provider CLIs and populate missing project defaults."""
    context = build_context(create_database=False)
    return probe_agents(project_root=context.root, persist_settings=True)


def probe_agents(
    *,
    project_root: Path,
    persist_settings: bool,
    providers: Sequence[ProviderCLIProvider] | None = None,
) -> dict[str, Any]:
    """Probe delegated provider CLIs with optional settings persistence."""

    settings_path = settings_file_path(spec_context=project_root)
    provider_list = (
        tuple(providers) if providers is not None else list_provider_cli_providers()
    )
    provider_reports = [
        _probe_provider(
            project_root=project_root,
            provider=provider,
            persist_settings=persist_settings,
        )
        for provider in provider_list
    ]

    summary = {
        "available": sum(
            1 for item in provider_reports if item["probe_status"] == "available"
        ),
        "not_found": sum(
            1 for item in provider_reports if item["probe_status"] == "not_found"
        ),
        "probe_failed": sum(
            1 for item in provider_reports if item["probe_status"] == "probe_failed"
        ),
        "settings_created": sum(
            1 for item in provider_reports if item["settings_action"] == "created"
        ),
        "settings_preserved": sum(
            1 for item in provider_reports if item["settings_action"] == "preserved"
        ),
    }

    return {
        "project_root": str(project_root),
        "settings_path": str(settings_path) if settings_path is not None else None,
        "summary": summary,
        "providers": provider_reports,
    }


def _probe_provider(
    *,
    project_root: Path,
    provider: ProviderCLIProvider,
    persist_settings: bool,
) -> dict[str, Any]:
    settings_path = settings_file_path(spec_context=project_root)
    configured = load_provider_cli_project_settings(
        provider.name,
        spec_context=project_root,
    )
    configured_executable = configured.executable
    candidate = configured_executable or provider.default_executable
    resolved = shutil.which(candidate)

    report: dict[str, Any] = {
        "provider": provider.name,
        "default_executable": provider.default_executable,
        "configured_executable": configured_executable,
        "candidate_executable": candidate,
        "resolved_executable": resolved,
        "settings_action": "preserved" if configured_executable else "unchanged",
    }

    if resolved is None:
        report["probe_status"] = "not_found"
        report["probe_error"] = (
            f"Unable to locate executable '{candidate}' for provider '{provider.name}'"
        )
        return report

    if configured_executable is None and persist_settings:
        write_result = ensure_provider_cli_project_executable(
            provider.name,
            executable=resolved,
            spec_context=project_root,
        )
        report["settings_action"] = write_result.action
        report["settings_path"] = str(write_result.path)
    elif configured_executable is None:
        report["settings_action"] = "unchanged"
        report["settings_path"] = (
            str(settings_path) if settings_path is not None else None
        )
    else:
        settings_path = settings_file_path(spec_context=project_root)
        report["settings_path"] = (
            str(settings_path) if settings_path is not None else None
        )

    try:
        report["version"] = probe_provider_cli_version(
            resolved,
            provider_name=provider.name,
        )
        report["probe_status"] = "available"
    except RuntimeError as exc:
        report["probe_status"] = "probe_failed"
        report["probe_error"] = str(exc)

    if provider.name == "opencode":
        try:
            ensure_opencode_run_support(resolved)
            report["run_support"] = "supported"
        except RuntimeError as exc:
            report["run_support"] = "unsupported"
            report["run_support_error"] = str(exc)

    return report


__all__ = ["probe_agents", "probe_agents_task"]
