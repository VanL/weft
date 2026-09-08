"""Tests for the serve CLI command helper."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import psutil
import pytest

from tests.helpers.test_backend import prepare_project_root
from weft._constants import (
    MANAGER_PONG_LIVE_AT_KEY,
    MANAGER_SERVE_LOG_ACTIVE_CONFIG_KEY,
    WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS,
    WEFT_SERVICES_REGISTRY_QUEUE,
)
from weft._exceptions import CommandExecutionError, WeftError
from weft.client import WeftClient
from weft.context import WeftContext, build_context
from weft.core import manager_runtime as core_manager_runtime
from weft.core.service_convergence import (
    build_manager_service_payload,
    manager_service_key,
    project_manager_service_record,
)
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
        "observations": {
            "host_processes": [
                {"pid": pid, "create_time": psutil.Process(pid).create_time()}
            ]
        },
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
            record = project_manager_service_record(
                payload,
                timestamp=timestamp,
                service_key=manager_service_key(context),
            )
            if record is None or record.get("tid") != tid:
                continue
            if latest is None or latest[1] < timestamp:
                latest = (record, timestamp)
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

    monkeypatch.setattr(
        core_manager_runtime,
        "serve_manager_foreground",
        fake_serve_manager,
    )

    result = serve_cmd.cmd_manager_serve(context=context_root)

    assert result is None
    assert calls == ["serve"]
    assert context_calls
    assert context_calls[0][0] == context_root
    assert context_calls[0][1][MANAGER_SERVE_LOG_ACTIVE_CONFIG_KEY] is True


def test_cmd_manager_serve_returns_none_and_raises_typed_runtime_failures(
    tmp_path, monkeypatch
) -> None:
    from weft.commands import serve as serve_cmd

    context_root = prepare_project_root(tmp_path / "proj")
    context = build_context(context_root)
    monkeypatch.setattr(
        serve_cmd,
        "build_context",
        lambda spec_context=None, *, config=None: context,
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "serve_manager_foreground",
        lambda context_arg: (0, None),
    )
    assert serve_cmd.cmd_manager_serve(context=context_root) is None

    monkeypatch.setattr(
        core_manager_runtime,
        "serve_manager_foreground",
        lambda context_arg: (1, "foreground failed"),
    )
    with pytest.raises(CommandExecutionError, match="foreground failed"):
        serve_cmd.cmd_manager_serve(context=context_root)


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
        core_manager_runtime,
        "serve_manager_foreground",
        lambda context_arg: (
            1,
            "Manager 1761000000000000001 already running (pid 54321)",
        ),
    )

    with pytest.raises(WeftError) as caught:
        serve_cmd.cmd_manager_serve(context=context_root)
    message = str(caught.value)

    assert isinstance(caught.value, WeftError)
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

    monkeypatch.setattr(core_manager_runtime, "replace_active_manager", fake_replace)
    monkeypatch.setattr(core_manager_runtime, "serve_manager_foreground", fake_serve)

    result = serve_cmd.cmd_manager_serve(context=context_root, replace=True)

    assert result is None
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
        core_manager_runtime,
        "replace_active_manager",
        lambda *args, **kwargs: (False, "failed to send STOP"),
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "serve_manager_foreground",
        lambda *args, **kwargs: calls.append("serve"),
    )

    with pytest.raises(WeftError) as caught:
        serve_cmd.cmd_manager_serve(context=context_root, replace=True)
    message = str(caught.value)

    assert isinstance(caught.value, WeftError)
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
    pong_tids: list[str] = []

    def matched_pong(_context, record, **_kwargs):
        pong_tids.append(record["tid"])
        record[MANAGER_PONG_LIVE_AT_KEY] = record["timestamp"]
        return True

    monkeypatch.setattr(
        core_manager_runtime,
        "_manager_record_has_matched_pong",
        matched_pong,
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
    assert pong_tids and set(pong_tids) == {tid}


def test_serve_foreground_supersedes_unconfirmed_external_record_when_starting(
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

    probe_tids: list[str] = []

    def unmatched_pong(_context, record, **_kwargs):
        probe_tids.append(record["tid"])
        return False

    monkeypatch.setattr(
        core_manager_runtime, "_manager_record_has_matched_pong", unmatched_pong
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
    assert latest["status"] == "superseded"
    with context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False) as queue:
        history = [
            row
            for row, _mid in iter_queue_json_entries(queue)
            if row.get("owner_tid") == stale_tid
        ]
    assert [row["status"] for row in history] == ["active", "superseded"]
    assert stale_tid in probe_tids


@pytest.mark.parametrize("unknown_first", [True, False])
def test_serve_foreground_supersedes_in_tid_order_before_live_blocker(
    tmp_path,
    monkeypatch,
    unknown_first: bool,
) -> None:
    context_root = prepare_project_root(tmp_path / "proj")
    context = build_context(context_root)
    stale_tid = "1761000000000000006"
    live_tid = "1761000000000000007"
    if not unknown_first:
        stale_tid, live_tid = live_tid, stale_tid
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

    probe_tids: list[str] = []

    def unmatched_pong(_context, record, **_kwargs):
        probe_tids.append(record["tid"])
        return False

    monkeypatch.setattr(
        core_manager_runtime, "_manager_record_has_matched_pong", unmatched_pong
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
    assert latest["status"] == ("superseded" if unknown_first else "active")
    assert stale_tid in probe_tids


def test_client_serve_preserves_explicit_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Client serve retains the supplied broker and runtime configuration [PY-2]."""
    original = build_context(prepare_project_root(tmp_path / "project"))
    context = replace(
        original,
        config={**original.config, WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS: 137.0},
    )
    seen: list[WeftContext] = []

    def serve(resolved: WeftContext) -> tuple[int, None]:
        seen.append(resolved)
        return 0, None

    monkeypatch.setattr(core_manager_runtime, "serve_manager_foreground", serve)
    result = WeftClient.from_weft_context(context).managers.serve()

    assert result is None
    assert len(seen) == 1
    assert seen[0] is context
    assert seen[0].config[WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS] == 137.0
    assert seen[0].broker_target is context.broker_target


@pytest.mark.parametrize(
    "kind",
    [
        "nonforeground",
        "draining",
        "stopped",
        "noncanonical",
        "newest_draining",
        "other_service",
    ],
)
def test_foreground_takeover_excludes_unqualified_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    context = build_context(prepare_project_root(tmp_path / "project"))
    tid = "1761000000000000021"
    handle = _external_supervisor_runtime_handle(
        foreground_serve=kind != "nonforeground"
    )
    payload = _manager_service_payload(
        context,
        tid,
        runtime_handle=handle,
        status=kind if kind in {"draining", "stopped"} else "active",
    )
    if kind == "noncanonical":
        payload["queues"]["requests"] = "private.requests"
    if kind == "other_service":
        payload["service_key"] = "manager:another-service"
    with context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False) as queue:
        first = queue.write(json.dumps(payload))
        ids = [first]
        if kind == "newest_draining":
            ids.append(queue.write(json.dumps({**payload, "status": "draining"})))
        monkeypatch.setattr(
            core_manager_runtime,
            "_manager_record_has_matched_pong",
            lambda *args, **kwargs: False,
        )
        assert core_manager_runtime._foreground_serve_blocking_manager(context) is None
        assert [mid for _row, mid in iter_queue_json_entries(queue)] == ids


def test_foreground_takeover_append_failure_blocks_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = build_context(prepare_project_root(tmp_path / "project"))
    tid = "1761000000000000022"
    payload = _manager_service_payload(
        context, tid, runtime_handle=_external_supervisor_runtime_handle()
    )
    with context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False) as queue:
        mid = queue.write(json.dumps(payload))
        monkeypatch.setattr(
            core_manager_runtime,
            "_manager_record_has_matched_pong",
            lambda *args, **kwargs: False,
        )
        monkeypatch.setattr(
            core_manager_runtime, "_mark_manager_stopped", lambda *args, **kwargs: False
        )
        blocker = core_manager_runtime._foreground_serve_blocking_manager(context)
        assert blocker is not None and blocker["tid"] == tid
        assert [
            (row["status"], stamp) for row, stamp in iter_queue_json_entries(queue)
        ] == [("active", mid)]


def test_foreground_takeover_preserves_positive_external_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive external runtime proof blocks takeover without rewriting history."""
    context = build_context(prepare_project_root(tmp_path / "project"))
    tid = "1761000000000000023"
    payload = _manager_service_payload(
        context, tid, runtime_handle=_external_supervisor_runtime_handle()
    )
    with context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False) as queue:
        mid = queue.write(json.dumps(payload))
        monkeypatch.setattr(
            core_manager_runtime,
            "runtime_liveness_from_registered_probe",
            lambda _handle: "live",
        )
        blocker = core_manager_runtime._foreground_serve_blocking_manager(context)
        assert blocker is not None and blocker["tid"] == tid
        assert [
            (row["status"], stamp) for row, stamp in iter_queue_json_entries(queue)
        ] == [("active", mid)]
