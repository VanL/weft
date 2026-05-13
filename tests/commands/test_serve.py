"""Tests for the serve CLI command helper."""

from __future__ import annotations

import json
import os

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft._constants import (
    MANAGER_SERVE_LOG_ACTIVE_CONFIG_KEY,
    SERVICE_STATUS_SUPERSEDED,
    WEFT_SERVICES_REGISTRY_QUEUE,
)
from weft.context import build_context
from weft.core import manager_runtime as core_manager_runtime
from weft.core.service_convergence import build_manager_service_payload
from weft.helpers import iter_queue_json_entries

pytestmark = [pytest.mark.shared]


def _external_supervisor_runtime_handle(
    *,
    foreground_serve: bool = True,
) -> dict[str, object]:
    metadata: dict[str, object] = {}
    if foreground_serve:
        metadata["foreground_serve"] = True
    return {
        "runner": "manager-supervisor",
        "kind": "supervised-process",
        "id": "container:weft-manager-1",
        "control": {"authority": "external-supervisor"},
        "observations": {"container_pid": 1, "container_name": "weft-manager-1"},
        "metadata": metadata,
    }


def _host_runtime_handle(pid: int) -> dict[str, object]:
    return {
        "runner": "host",
        "kind": "process",
        "id": str(pid),
        "control": {"authority": "host-pid"},
        "observations": {"host_pids": [pid]},
        "metadata": {},
    }


def _manager_service_payload(
    context,
    tid: str,
    *,
    status: str = "active",
    runtime_handle: dict[str, object] | None = None,
) -> dict[str, object]:
    return build_manager_service_payload(
        context=context,
        tid=tid,
        name="manager",
        status=status,
        queues={
            "requests": "weft.spawn.requests",
            "ctrl_in": f"T{tid}.ctrl_in",
            "ctrl_out": f"T{tid}.ctrl_out",
            "outbox": "weft.manager.outbox",
        },
        runtime_handle=runtime_handle or {},
    )


def _latest_manager_record(context, tid: str) -> dict[str, object] | None:
    queue = context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
    try:
        latest: tuple[dict[str, object], int] | None = None
        for payload, timestamp in iter_queue_json_entries(queue):
            if payload.get("tid") != tid:
                continue
            if latest is None or latest[1] < timestamp:
                latest = (payload, timestamp)
        return None if latest is None else latest[0]
    finally:
        queue.close()


def test_serve_command_delegates_to_shared_foreground_helper(
    tmp_path, monkeypatch
) -> None:
    from weft.commands import serve as serve_cmd

    context_root = prepare_project_root(tmp_path / "proj")
    context = build_context(context_root)
    calls: list[str] = []
    context_calls: list[tuple[object, object]] = []

    def fake_build_context(spec_context=None, *, config=None):
        context_calls.append((spec_context, config))
        return context

    monkeypatch.setattr(serve_cmd, "build_context", fake_build_context)

    def fake_serve_manager(context_arg):
        assert context_arg is context
        calls.append("serve")
        return 0, None

    monkeypatch.setattr(serve_cmd, "_serve_manager_foreground", fake_serve_manager)

    exit_code, message = serve_cmd.serve_command(context_path=context_root)

    assert exit_code == 0
    assert message is None
    assert calls == ["serve"]
    assert context_calls
    assert context_calls[0][0] == context_root
    assert context_calls[0][1][MANAGER_SERVE_LOG_ACTIVE_CONFIG_KEY] is True


def test_serve_command_returns_preflight_message(tmp_path, monkeypatch) -> None:
    from weft.commands import serve as serve_cmd

    context_root = prepare_project_root(tmp_path / "proj")
    context = build_context(context_root)

    monkeypatch.setattr(
        serve_cmd,
        "build_context",
        lambda spec_context=None, *, config=None: context,
    )
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


def test_serve_command_replace_supersedes_before_foreground(
    tmp_path,
    monkeypatch,
) -> None:
    from weft.commands import serve as serve_cmd

    context_root = prepare_project_root(tmp_path / "proj")
    context = build_context(context_root)
    calls: list[str] = []

    monkeypatch.setattr(
        serve_cmd,
        "build_context",
        lambda spec_context=None, *, config=None: context,
    )

    def fake_replace(context_arg, *, timeout):
        assert context_arg is context
        assert timeout > 0
        calls.append("replace")
        return True, None

    def fake_serve(context_arg):
        assert context_arg is context
        calls.append("serve")
        return 0, None

    monkeypatch.setattr(serve_cmd, "_replace_active_manager", fake_replace)
    monkeypatch.setattr(serve_cmd, "_serve_manager_foreground", fake_serve)

    exit_code, message = serve_cmd.serve_command(
        context_path=context_root,
        replace=True,
    )

    assert exit_code == 0
    assert message is None
    assert calls == ["replace", "serve"]


def test_serve_command_replace_failure_does_not_serve(
    tmp_path,
    monkeypatch,
) -> None:
    from weft.commands import serve as serve_cmd

    context_root = prepare_project_root(tmp_path / "proj")
    context = build_context(context_root)
    calls: list[str] = []

    monkeypatch.setattr(
        serve_cmd,
        "build_context",
        lambda spec_context=None, *, config=None: context,
    )
    monkeypatch.setattr(
        serve_cmd,
        "_replace_active_manager",
        lambda *args, **kwargs: (False, "failed to send STOP"),
    )
    monkeypatch.setattr(
        serve_cmd,
        "_serve_manager_foreground",
        lambda *args, **kwargs: calls.append("serve"),
    )

    exit_code, message = serve_cmd.serve_command(
        context_path=context_root,
        replace=True,
    )

    assert exit_code == 1
    assert message == "failed to send STOP"
    assert calls == []


def test_serve_foreground_uses_shared_runtime_invocation_helper(
    tmp_path, monkeypatch
) -> None:
    context_root = prepare_project_root(tmp_path / "proj")
    context = build_context(context_root)
    invocation = core_manager_runtime.ManagerRuntimeInvocation(
        task_cls_path="weft.core.manager.Manager",
        tid="1761000000000000002",
        spec=object(),
    )
    helper_calls: list[tuple[object, object]] = []
    run_calls: list[tuple[object, object]] = []

    manager_selection_calls: list[object] = []

    def _fake_blocking_manager(context_arg):
        manager_selection_calls.append(context_arg)
        return None

    monkeypatch.setattr(
        core_manager_runtime,
        "_foreground_serve_blocking_manager",
        _fake_blocking_manager,
    )

    def _fake_build_invocation(context_arg, *, idle_timeout_override=None):
        helper_calls.append((context_arg, idle_timeout_override))
        return invocation

    def _fake_run_manager_process_foreground(invocation_arg, context_arg):
        run_calls.append((invocation_arg, context_arg))

    monkeypatch.setattr(
        core_manager_runtime,
        "_build_manager_runtime_invocation",
        _fake_build_invocation,
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "_run_manager_process_foreground",
        _fake_run_manager_process_foreground,
    )

    exit_code, message = core_manager_runtime.serve_manager_foreground(context)

    assert exit_code == 0
    assert message is None
    assert manager_selection_calls == [context]
    assert helper_calls == [(context, 0.0)]
    assert run_calls == [(invocation, context)]


def test_serve_foreground_blocks_positive_external_supervisor_duplicate(
    tmp_path,
    monkeypatch,
) -> None:
    context_root = prepare_project_root(tmp_path / "proj")
    context = build_context(context_root)
    tid = "1761000000000000003"
    registry_queue = context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
    try:
        registry_queue.write(
            json.dumps(
                _manager_service_payload(
                    context,
                    tid,
                    runtime_handle=_external_supervisor_runtime_handle(),
                )
            )
        )
    finally:
        registry_queue.close()
    run_calls: list[object] = []

    monkeypatch.setattr(
        core_manager_runtime,
        "_manager_record_has_matched_pong",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "_run_manager_process_foreground",
        lambda *args, **kwargs: run_calls.append(args),
    )

    exit_code, message = core_manager_runtime.serve_manager_foreground(context)

    assert exit_code == 1
    assert message == f"Manager {tid} already running"
    assert run_calls == []


def test_serve_foreground_replaces_ambiguous_external_supervisor_record(
    tmp_path,
    monkeypatch,
) -> None:
    context_root = prepare_project_root(tmp_path / "proj")
    context = build_context(context_root)
    stale_tid = "1761000000000000004"
    invocation = core_manager_runtime.ManagerRuntimeInvocation(
        task_cls_path="weft.core.manager.Manager",
        tid="1761000000000000005",
        spec=object(),
    )
    registry_queue = context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
    try:
        registry_queue.write(
            json.dumps(
                _manager_service_payload(
                    context,
                    stale_tid,
                    runtime_handle=_external_supervisor_runtime_handle(),
                )
            )
        )
    finally:
        registry_queue.close()
    run_calls: list[tuple[object, object]] = []

    monkeypatch.setattr(
        core_manager_runtime,
        "_manager_record_has_matched_pong",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "_build_manager_runtime_invocation",
        lambda context_arg, *, idle_timeout_override=None: invocation,
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "_run_manager_process_foreground",
        lambda invocation_arg, context_arg: run_calls.append(
            (invocation_arg, context_arg)
        ),
    )

    exit_code, message = core_manager_runtime.serve_manager_foreground(context)

    assert exit_code == 0
    assert message is None
    assert run_calls == [(invocation, context)]
    latest = _latest_manager_record(context, stale_tid)
    assert latest is not None
    assert latest["status"] == SERVICE_STATUS_SUPERSEDED


def test_serve_foreground_rechecks_after_replacing_ambiguous_record(
    tmp_path,
    monkeypatch,
) -> None:
    context_root = prepare_project_root(tmp_path / "proj")
    context = build_context(context_root)
    stale_tid = "1761000000000000006"
    live_tid = "1761000000000000007"
    registry_queue = context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
    try:
        registry_queue.write(
            json.dumps(
                _manager_service_payload(
                    context,
                    stale_tid,
                    runtime_handle=_external_supervisor_runtime_handle(),
                )
            )
        )
        registry_queue.write(
            json.dumps(
                _manager_service_payload(
                    context,
                    live_tid,
                    runtime_handle=_host_runtime_handle(os.getpid()),
                )
            )
        )
    finally:
        registry_queue.close()
    run_calls: list[object] = []

    monkeypatch.setattr(
        core_manager_runtime,
        "_manager_record_has_matched_pong",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "_run_manager_process_foreground",
        lambda *args, **kwargs: run_calls.append(args),
    )

    exit_code, message = core_manager_runtime.serve_manager_foreground(context)

    assert exit_code == 1
    assert message == f"Manager {live_tid} already running"
    assert run_calls == []
    latest = _latest_manager_record(context, stale_tid)
    assert latest is not None
    assert latest["status"] == SERVICE_STATUS_SUPERSEDED
