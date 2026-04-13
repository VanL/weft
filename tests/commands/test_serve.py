"""Tests for the serve CLI command helper."""

from __future__ import annotations

import pytest

import weft.commands._manager_bootstrap as manager_lifecycle
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


def test_serve_foreground_uses_shared_runtime_invocation_helper(
    tmp_path, monkeypatch
) -> None:
    context_root = prepare_project_root(tmp_path / "proj")
    context = build_context(context_root)
    invocation = manager_lifecycle._ManagerRuntimeInvocation(
        task_cls_path="weft.core.manager.Manager",
        tid="1761000000000000002",
        spec=object(),
    )
    helper_calls: list[tuple[object, object]] = []
    run_calls: list[tuple[object, object, object, object, object]] = []

    monkeypatch.setattr(
        manager_lifecycle,
        "_select_active_manager",
        lambda context_arg: None,
    )

    def _fake_build_invocation(context_arg, *, idle_timeout_override=None):
        helper_calls.append((context_arg, idle_timeout_override))
        return invocation

    def _fake_run_manager_process(
        task_cls_path,
        broker_target,
        spec,
        config,
        poll_interval,
    ):
        run_calls.append((task_cls_path, broker_target, spec, config, poll_interval))

    monkeypatch.setattr(
        manager_lifecycle,
        "_build_manager_runtime_invocation",
        _fake_build_invocation,
    )
    monkeypatch.setattr(
        manager_lifecycle,
        "run_manager_process",
        _fake_run_manager_process,
    )

    exit_code, message = manager_lifecycle._serve_manager_foreground(context)

    assert exit_code == 0
    assert message is None
    assert helper_calls == [(context, 0.0)]
    assert run_calls == [
        (
            invocation.task_cls_path,
            context.broker_target,
            invocation.spec,
            context.config,
            manager_lifecycle._MANAGER_POLL_INTERVAL,
        )
    ]
