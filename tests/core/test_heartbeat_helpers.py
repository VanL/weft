"""Heartbeat helper tests."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from weft._constants import (
    INTERNAL_HEARTBEAT_ENDPOINT_NAME,
    INTERNAL_RUNTIME_ENDPOINT_NAME_KEY,
    INTERNAL_RUNTIME_ENVELOPE_ENDPOINT_NAME_KEY,
    INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY,
    INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT,
    INTERNAL_RUNTIME_TASK_CLASS_KEY,
    WEFT_SPAWN_REQUESTS_QUEUE,
)
from weft.context import build_context
from weft.core.endpoints import EndpointRecord, ResolvedEndpoint
from weft.core.heartbeat import ensure_heartbeat_service, upsert_heartbeat
from weft.core.spawn_requests import submit_spawn_request
from weft.core.taskspec import IOSection, SpecSection, StateSection, TaskSpec

pytestmark = [pytest.mark.shared]


def _resolved_endpoint(tid: str) -> ResolvedEndpoint:
    return ResolvedEndpoint(
        record=EndpointRecord(
            name=INTERNAL_HEARTBEAT_ENDPOINT_NAME,
            tid=tid,
            status="active",
            inbox=f"T{tid}.inbox",
            outbox=f"T{tid}.outbox",
            ctrl_in=f"T{tid}.ctrl_in",
            ctrl_out=f"T{tid}.ctrl_out",
            registered_at=int(time.time_ns()),
            last_seen=int(time.time_ns()),
            metadata={},
        )
    )


def _heartbeat_service_taskspec(tid: str, root: Path) -> TaskSpec:
    return TaskSpec(
        tid=tid,
        name="heartbeat-service",
        spec=SpecSection(
            type="function",
            function_target="weft.tasks:noop",
            persistent=True,
            weft_context=str(root),
        ),
        io=IOSection(
            inputs={"inbox": f"T{tid}.inbox"},
            outputs={"outbox": f"T{tid}.outbox"},
            control={"ctrl_in": f"T{tid}.ctrl_in", "ctrl_out": f"T{tid}.ctrl_out"},
        ),
        state=StateSection(),
        metadata={
            INTERNAL_RUNTIME_TASK_CLASS_KEY: INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT,
            INTERNAL_RUNTIME_ENDPOINT_NAME_KEY: INTERNAL_HEARTBEAT_ENDPOINT_NAME,
        },
    )


def test_ensure_heartbeat_service_starts_service_on_first_use(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_context(spec_context=tmp_path)
    calls: dict[str, int] = {"ensure": 0, "submit": 0}
    resolved = _resolved_endpoint("1777000000000000001")
    responses = iter([None, None, resolved])

    monkeypatch.setattr(
        "weft.core.heartbeat.resolve_endpoint",
        lambda context_arg, name: next(responses),
    )
    monkeypatch.setattr(
        "weft.core.heartbeat._ensure_manager_running",
        lambda context_arg: calls.__setitem__("ensure", calls["ensure"] + 1),
    )
    monkeypatch.setattr(
        "weft.core.heartbeat.submit_spawn_request",
        lambda *args, **kwargs: calls.__setitem__("submit", calls["submit"] + 1),
    )
    monkeypatch.setattr("weft.core.heartbeat.time.sleep", lambda _seconds: None)

    result = ensure_heartbeat_service(context)

    assert result == resolved
    assert calls == {"ensure": 1, "submit": 1}


def test_upsert_heartbeat_reuses_live_service_without_second_startup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_context(spec_context=tmp_path)
    resolved = _resolved_endpoint("1777000000000000002")
    captured: list[dict[str, Any]] = []

    monkeypatch.setattr(
        "weft.core.heartbeat.resolve_endpoint",
        lambda context_arg, name: resolved,
    )
    monkeypatch.setattr(
        "weft.core.heartbeat._ensure_manager_running",
        lambda context_arg: (_ for _ in ()).throw(AssertionError("unexpected ensure")),
    )
    monkeypatch.setattr(
        "weft.core.heartbeat.submit_spawn_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("unexpected spawn")
        ),
    )
    monkeypatch.setattr(
        "weft.core.heartbeat._write_heartbeat_request",
        lambda context_arg, *, resolved, payload: captured.append(dict(payload)) or 1,
    )

    result = upsert_heartbeat(
        context,
        heartbeat_id="build",
        interval_seconds=60,
        destination_queue="build.queue",
        message="go",
    )

    assert result == 1
    assert captured == [
        {
            "action": "upsert",
            "heartbeat_id": "build",
            "interval_seconds": 60,
            "destination_queue": "build.queue",
            "message": "go",
        }
    ]


def test_ensure_heartbeat_service_fails_on_startup_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_context(spec_context=tmp_path)

    monkeypatch.setattr(
        "weft.core.heartbeat.resolve_endpoint",
        lambda context_arg, name: None,
    )
    monkeypatch.setattr(
        "weft.core.heartbeat._ensure_manager_running",
        lambda context_arg: None,
    )
    monkeypatch.setattr(
        "weft.core.heartbeat.submit_spawn_request",
        lambda *args, **kwargs: 1777000000000000003,
    )

    with pytest.raises(RuntimeError, match="did not publish a live endpoint"):
        ensure_heartbeat_service(context, startup_timeout=0.0)


def test_internal_submit_spawn_request_preserves_internal_runtime_envelope(
    tmp_path: Path,
) -> None:
    context = build_context(spec_context=tmp_path)
    tid = str(time.time_ns())

    submit_spawn_request(
        context.broker_target,
        taskspec=_heartbeat_service_taskspec(tid, tmp_path),
        work_payload=None,
        config=context.broker_config,
        tid=tid,
        inherited_weft_context=str(context.root),
        allow_internal_runtime=True,
    )

    queue = context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=False)
    try:
        raw_message = queue.read_one()
    finally:
        queue.close()

    assert isinstance(raw_message, str)
    payload = json.loads(raw_message)
    taskspec_payload = payload["taskspec"]
    assert taskspec_payload["metadata"].get(INTERNAL_RUNTIME_TASK_CLASS_KEY) is None
    assert taskspec_payload["metadata"].get(INTERNAL_RUNTIME_ENDPOINT_NAME_KEY) is None
    assert (
        payload[INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY]
        == INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT
    )
    assert (
        payload[INTERNAL_RUNTIME_ENVELOPE_ENDPOINT_NAME_KEY]
        == INTERNAL_HEARTBEAT_ENDPOINT_NAME
    )
