"""SQLite-only context assertions for file-backed broker behavior."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from weft.context import build_context

pytestmark = [pytest.mark.sqlite_only]


def test_build_context_creates_file_backed_sqlite_target(tmp_path: Path) -> None:
    """Default local contexts should create the expected SQLite broker file."""

    ctx = build_context(spec_context=tmp_path)

    assert ctx.database_path is not None
    assert ctx.database_path.exists()
    assert ctx.broker_target.target_path == ctx.database_path
    assert ctx.backend_name == "sqlite"
    assert ctx.is_file_backed is True


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("WEFT_DEBUG", "1"),
        ("WEFT_BACKEND", "sqlite"),
        ("WEFT_BACKEND_PORT", "5432"),
        ("WEFT_PROJECT_SCOPE", "1"),
        ("WEFT_PROJECT_SCOPE", "0"),
        ("WEFT_DEFAULT_DB_LOCATION", None),
        ("WEFT_DEFAULT_DB_NAME", "alternate.db"),
    ],
)
def test_build_context_fallback_keeps_sqlite_root_with_broker_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str | None,
) -> None:
    """Default-valued and inert broker settings do not redirect real queue writes."""
    for key in tuple(os.environ):
        if key.startswith("WEFT_"):
            monkeypatch.delenv(key)
    fallback = tmp_path / "application"
    cwd = tmp_path / "cwd"
    elsewhere = tmp_path / "elsewhere"
    for root in (fallback, cwd, elsewhere):
        root.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setenv(name, str(elsewhere) if value is None else value)

    ctx = build_context(fallback_root=fallback)

    expected_name = (
        "alternate.db" if name == "WEFT_DEFAULT_DB_NAME" else ".weft/broker.db"
    )
    assert ctx.root == fallback.resolve()
    assert ctx.weft_dir == fallback.resolve() / ".weft"
    assert ctx.database_path == fallback.resolve() / expected_name
    assert ctx.database_path.is_file()
    assert ctx.discovered is False
    monkeypatch.setenv("WEFT_CONTEXT", str(elsewhere))
    monkeypatch.setenv("WEFT_CACHE_MB", "not-an-integer")
    queue = ctx.queue("fallback-configuration")
    try:
        queue.write("selected-root")
        assert queue.read_one() == "selected-root"
    finally:
        queue.close()
    assert list(cwd.iterdir()) == []
    assert list(elsewhere.iterdir()) == []


def test_build_context_environment_context_beats_fallback_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Normal environment loading selects WEFT_CONTEXT before the fallback anchor."""
    for key in tuple(os.environ):
        if key.startswith("WEFT_"):
            monkeypatch.delenv(key)
    configured = tmp_path / "configured"
    fallback = tmp_path / "fallback"
    cwd = tmp_path / "cwd"
    for root in (configured, fallback, cwd):
        root.mkdir()
    monkeypatch.setenv("WEFT_CONTEXT", str(configured))
    monkeypatch.chdir(cwd)

    ctx = build_context(fallback_root=fallback)

    assert ctx.root == configured.resolve()
    assert ctx.config["CONTEXT"] == str(configured)
    assert ctx.database_path == configured.resolve() / ".weft" / "broker.db"
    assert ctx.database_path.is_file()
    assert list(fallback.iterdir()) == []
    assert list(cwd.iterdir()) == []
