"""Tests for the serve CLI command helper."""

from __future__ import annotations

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft.context import build_context

pytestmark = [pytest.mark.shared]


def test_serve_command_delegates_to_shared_foreground_helper(
    tmp_path, monkeypatch
) -> None:
    from weft.commands import serve as serve_cmd

    context_root = prepare_project_root(tmp_path / "proj")
    context = build_context(context_root)
    calls: list[str] = []

    monkeypatch.setattr(serve_cmd, "build_context", lambda spec_context=None: context)

    def fake_serve_manager(context_arg):
        assert context_arg is context
        calls.append("serve")
        return 0, None

    monkeypatch.setattr(serve_cmd, "_serve_manager_foreground", fake_serve_manager)

    exit_code, message = serve_cmd.serve_command(context_path=context_root)

    assert exit_code == 0
    assert message is None
    assert calls == ["serve"]


def test_serve_command_returns_preflight_message(tmp_path, monkeypatch) -> None:
    from weft.commands import serve as serve_cmd

    context_root = prepare_project_root(tmp_path / "proj")
    context = build_context(context_root)

    monkeypatch.setattr(serve_cmd, "build_context", lambda spec_context=None: context)
    monkeypatch.setattr(
        serve_cmd,
        "_serve_manager_foreground",
        lambda context_arg: (
            1,
            "Manager 1761000000000000001 already running (pid 54321)",
        ),
    )

    exit_code, message = serve_cmd.serve_command(context_path=context_root)

    assert exit_code == 1
    assert message == "Manager 1761000000000000001 already running (pid 54321)"
