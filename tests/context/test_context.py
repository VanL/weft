"""Integration tests for the simplified Weft context helpers."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from weft.context import build_context  # noqa: E402


def test_build_context_creates_structure(tmp_path: Path) -> None:
    """Building a context for a fresh directory materializes all assets."""
    ctx = build_context(spec_context=tmp_path)

    assert ctx.root == tmp_path.resolve()
    assert ctx.weft_dir.is_dir()
    assert ctx.outputs_dir.is_dir()
    assert ctx.logs_dir.is_dir()
    assert ctx.autostart_dir.is_dir()
    assert ctx.autostart_enabled is True
    assert ctx.database_path.exists()
    assert ctx.config_path.is_file()

    metadata = json.loads(ctx.config_path.read_text(encoding="utf-8"))
    assert metadata["project_name"] == ctx.root.name

    queue = ctx.queue("context.test.queue")
    queue.write("payload")
    assert queue.read() == "payload"


def test_build_context_can_disable_autostart(tmp_path: Path) -> None:
    root = tmp_path / "disable-autostart"
    root.mkdir()

    ctx = build_context(spec_context=root, autostart=False)

    assert ctx.autostart_enabled is False
    assert not ctx.autostart_dir.exists()


def test_build_context_discovers_existing_project(tmp_path: Path) -> None:
    """Project databases are discovered via SimpleBroker's project scoping."""
    root_ctx = build_context(spec_context=tmp_path)
    nested_dir = tmp_path / "a" / "b" / "c"
    nested_dir.mkdir(parents=True)

    original_cwd = Path.cwd()
    try:
        os.chdir(nested_dir)
        discovered_ctx = build_context()
    finally:
        os.chdir(original_cwd)

    assert discovered_ctx.root == root_ctx.root
    assert discovered_ctx.database_path == root_ctx.database_path
    assert discovered_ctx.discovered is True


def test_environment_translation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """WEFT_* environment variables are mapped onto BROKER_* settings."""
    monkeypatch.setenv("WEFT_BUSY_TIMEOUT", "2500")
    monkeypatch.setenv("WEFT_PROJECT_SCOPE", "1")

    ctx = build_context(spec_context=tmp_path)

    assert ctx.broker_config["BROKER_BUSY_TIMEOUT"] == "2500"
    assert ctx.broker_config["BROKER_PROJECT_SCOPE"] in {"1", "true", "True", True}


def test_project_config_recovers_from_corruption(tmp_path: Path) -> None:
    """A corrupt config file is replaced with a fresh default."""
    ctx = build_context(spec_context=tmp_path)

    ctx.config_path.write_text("not-json", encoding="utf-8")

    refreshed_ctx = build_context(spec_context=tmp_path)
    data = json.loads(refreshed_ctx.config_path.read_text(encoding="utf-8"))
    assert data["project_name"] == tmp_path.name
    assert "created" in data
