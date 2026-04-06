"""SQLite-only context assertions for file-backed broker behavior."""

from __future__ import annotations

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
