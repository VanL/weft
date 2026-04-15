"""Builtin helper task for warming Docker-backed agent images.

Spec references:
- docs/specifications/10B-Builtin_TaskSpecs.md
- docs/specifications/13-Agent_Runtime.md [AR-7]
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from weft._runner_plugins import require_runner_plugin
from weft.context import build_context
from weft.core.agents.provider_cli.registry import list_provider_cli_providers

from .agent_probe import probe_agents


def prepare_agent_images_task(work_item: Any = None) -> dict[str, Any]:
    """Build or reuse supported Docker-backed agent images."""

    try:
        require_runner_plugin("docker")
        from weft_docker.agent_images import ensure_agent_image, get_agent_image_recipe
    except RuntimeError as exc:
        raise RuntimeError(
            "prepare-agent-images requires Docker runner support. Install weft[docker]."
        ) from exc

    request = _parse_prepare_request(work_item)
    requested_providers = request["providers"]
    refresh = request["refresh"]
    context = build_context(create_database=False)

    if requested_providers is None:
        probe_payload = probe_agents(
            project_root=context.root,
            persist_settings=False,
            providers=list_provider_cli_providers(),
        )
        requested_providers = [
            item["provider"]
            for item in probe_payload["providers"]
            if item["probe_status"] == "available"
        ]

    provider_reports: list[dict[str, Any]] = []
    for provider_name in requested_providers:
        recipe = get_agent_image_recipe(provider_name)
        if recipe is None:
            provider_reports.append(
                {
                    "provider": provider_name,
                    "recipe_status": "unsupported",
                    "action": "unsupported",
                    "error": (
                        "No Docker-backed agent image recipe is available for "
                        f"provider '{provider_name}'"
                    ),
                }
            )
            continue

        try:
            result = ensure_agent_image(provider_name, refresh=refresh)
        except Exception as exc:
            provider_reports.append(
                {
                    "provider": provider_name,
                    "recipe_status": "supported",
                    "action": "failed",
                    "error": str(exc),
                }
            )
            continue

        provider_reports.append(
            {
                "provider": provider_name,
                "recipe_status": "supported",
                "action": result.action,
                "image": result.image,
                "cache_key": result.cache_key,
            }
        )

    summary = {
        "requested": len(requested_providers),
        "built": sum(1 for item in provider_reports if item["action"] == "built"),
        "reused": sum(1 for item in provider_reports if item["action"] == "reused"),
        "unsupported": sum(
            1 for item in provider_reports if item["action"] == "unsupported"
        ),
        "failed": sum(1 for item in provider_reports if item["action"] == "failed"),
    }

    return {
        "project_root": str(context.root),
        "summary": summary,
        "providers": provider_reports,
    }


def _parse_prepare_request(work_item: Any) -> dict[str, Any]:
    if work_item in (None, "", {}):
        return {"providers": None, "refresh": False}
    if isinstance(work_item, str):
        stripped = work_item.strip()
        if not stripped:
            return {"providers": None, "refresh": False}
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "prepare-agent-images expects a JSON object input when stdin is provided"
            ) from exc
    elif isinstance(work_item, dict):
        payload = work_item
    else:
        raise ValueError("prepare-agent-images expects a JSON object input")

    if not isinstance(payload, dict):
        raise ValueError("prepare-agent-images input must be a JSON object")
    extra_keys = sorted(set(payload) - {"providers", "refresh"})
    if extra_keys:
        raise ValueError(
            "prepare-agent-images input does not support field(s): "
            + ", ".join(extra_keys)
        )

    providers_obj = payload.get("providers")
    providers: list[str] | None
    if providers_obj is None:
        providers = None
    else:
        providers = _normalize_provider_list(providers_obj)

    refresh_obj = payload.get("refresh", False)
    if not isinstance(refresh_obj, bool):
        raise ValueError("prepare-agent-images input field 'refresh' must be a boolean")

    return {
        "providers": providers,
        "refresh": refresh_obj,
    }


def _normalize_provider_list(value: object) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(
            "prepare-agent-images input field 'providers' must be a list of strings"
        )
    providers: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(
                "prepare-agent-images input field 'providers' must be a list of strings"
            )
        providers.append(item.strip())
    return providers


__all__ = ["prepare_agent_images_task"]
