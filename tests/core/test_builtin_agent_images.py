"""Tests for the prepare-agent-images builtin helper."""

from __future__ import annotations

import os
import sys
import types
from types import SimpleNamespace

import pytest

from weft.builtins.agent_images import prepare_agent_images_task

pytestmark = [
    pytest.mark.skipif(
        os.name == "nt",
        reason="Docker builtins are currently supported only on Linux and macOS",
    )
]


def test_prepare_agent_images_task_requires_docker_extension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "weft.builtins.agent_images.require_runner_plugin",
        lambda name: (_ for _ in ()).throw(
            RuntimeError(
                f"Requested runner '{name}' is not available. Install weft[docker]."
            )
        ),
    )

    with pytest.raises(
        RuntimeError,
        match=(
            r"prepare-agent-images requires Docker runner support\. "
            r"Install weft\[docker\]\."
        ),
    ):
        prepare_agent_images_task()


def test_prepare_agent_images_task_uses_probe_results_without_persisting_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    fake_module = types.ModuleType("weft_docker.agent_images")
    fake_module.get_agent_image_recipe = lambda provider_name: (
        object() if provider_name == "codex" else None
    )
    fake_module.ensure_agent_image = lambda provider_name, refresh=False: (
        SimpleNamespace(  # noqa: E731
            action="reused",
            image=f"weft-agent-{provider_name}:cached",
            cache_key=f"{provider_name}-cache-key",
        )
    )
    monkeypatch.setitem(sys.modules, "weft_docker.agent_images", fake_module)
    monkeypatch.setattr(
        "weft.builtins.agent_images.require_runner_plugin",
        lambda name: SimpleNamespace(name=name),
    )
    monkeypatch.setattr(
        "weft.builtins.agent_images.build_context",
        lambda create_database=False: SimpleNamespace(root=tmp_path),
    )
    monkeypatch.setattr(
        "weft.builtins.agent_images.list_provider_cli_providers",
        lambda: ("codex", "claude_code"),
    )

    def fake_probe_agents(
        *,
        project_root,
        persist_settings: bool,
        providers,
    ) -> dict[str, object]:
        assert project_root == tmp_path
        assert persist_settings is False
        assert tuple(providers) == ("codex", "claude_code")
        return {
            "providers": [
                {"provider": "codex", "probe_status": "available"},
                {"provider": "claude_code", "probe_status": "not_found"},
            ]
        }

    monkeypatch.setattr("weft.builtins.agent_images.probe_agents", fake_probe_agents)

    payload = prepare_agent_images_task()

    assert payload["project_root"] == str(tmp_path)
    assert payload["summary"] == {
        "requested": 1,
        "built": 0,
        "reused": 1,
        "unsupported": 0,
        "failed": 0,
    }
    assert payload["providers"] == [
        {
            "provider": "codex",
            "recipe_status": "supported",
            "action": "reused",
            "image": "weft-agent-codex:cached",
            "cache_key": "codex-cache-key",
        }
    ]
    assert not (tmp_path / ".weft" / "agents.json").exists()


def test_prepare_agent_images_task_explicit_providers_skip_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    fake_module = types.ModuleType("weft_docker.agent_images")
    fake_module.get_agent_image_recipe = lambda provider_name: (
        object() if provider_name == "codex" else None
    )
    fake_module.ensure_agent_image = lambda provider_name, refresh=False: (
        SimpleNamespace(  # noqa: E731
            action="built",
            image=f"weft-agent-{provider_name}:fresh",
            cache_key=f"{provider_name}-cache-key",
        )
    )
    monkeypatch.setitem(sys.modules, "weft_docker.agent_images", fake_module)
    monkeypatch.setattr(
        "weft.builtins.agent_images.require_runner_plugin",
        lambda name: SimpleNamespace(name=name),
    )
    monkeypatch.setattr(
        "weft.builtins.agent_images.build_context",
        lambda create_database=False: SimpleNamespace(root=tmp_path),
    )
    monkeypatch.setattr(
        "weft.builtins.agent_images.probe_agents",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("probe_agents should not run")
        ),
    )

    payload = prepare_agent_images_task({"providers": ["codex"], "refresh": True})

    assert payload["project_root"] == str(tmp_path)
    assert payload["summary"] == {
        "requested": 1,
        "built": 1,
        "reused": 0,
        "unsupported": 0,
        "failed": 0,
    }
    assert payload["providers"] == [
        {
            "provider": "codex",
            "recipe_status": "supported",
            "action": "built",
            "image": "weft-agent-codex:fresh",
            "cache_key": "codex-cache-key",
        }
    ]
