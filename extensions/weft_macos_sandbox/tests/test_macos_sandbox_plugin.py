"""Tests for the macOS sandbox runner extension package."""

from __future__ import annotations

import pytest
from weft_macos_sandbox import get_runner_plugin


def test_macos_sandbox_runner_requires_profile() -> None:
    plugin = get_runner_plugin()

    with pytest.raises(ValueError, match="requires spec.runner.options.profile"):
        plugin.validate_taskspec(
            {
                "spec": {
                    "type": "command",
                    "runner": {"name": "macos-sandbox", "options": {}},
                }
            }
        )


def test_macos_sandbox_runner_preflight_checks_binary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    plugin = get_runner_plugin()
    profile = tmp_path / "sandbox.sb"
    profile.write_text("(version 1)\n(allow default)\n", encoding="utf-8")
    monkeypatch.setattr("weft_macos_sandbox.plugin.sys.platform", "darwin")
    monkeypatch.setattr("weft_macos_sandbox.plugin.shutil.which", lambda name: None)

    with pytest.raises(ValueError, match="sandbox-exec"):
        plugin.validate_taskspec(
            {
                "spec": {
                    "type": "command",
                    "runner": {
                        "name": "macos-sandbox",
                        "options": {"profile": str(profile)},
                    },
                }
            },
            preflight=True,
        )
