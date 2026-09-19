"""Tests for keyed control-channel PING/PONG probing."""

from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

import weft.core.control_probe as control_probe_mod
from simplebroker.ext import BrokerError
from tests.helpers.test_backend import prepare_project_root
from weft._constants import (
    CONTROL_PING_MAX_TIMEOUT_SECONDS,
    SERVICE_STATUS_DRAINING,
    TASK_MONITOR_DEAD_TID_CLEANUP_MIN_AGE_SECONDS,
    WEFT_MANAGER_OUTBOX_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
)
from weft._exceptions import CommandUsageError
from weft.commands.tasks import list_tasks
from weft.context import WeftContext, build_context
from weft.core.control_probe import (
    coerce_pong_response,
    pong_proves_dispatch_eligible,
    send_keyed_ping_probe,
)
from weft.core.monitor.policies.runtime_control import (
    select_runtime_dead_task_cleanup_candidates,
)

pytestmark = [pytest.mark.shared]


def _queue_names(ctx: WeftContext) -> set[str]:
    with ctx.broker() as broker:
        return set(broker.list_queues())


def _start_pong_responder(
    ctx: WeftContext,
    *,
    tid: str,
    rows_before_pong: tuple[str, ...] = (),
) -> tuple[threading.Thread, list[dict[str, Any]]]:
    observed: list[dict[str, Any]] = []

    def respond() -> None:
        ctrl_in = ctx.queue(f"T{tid}.ctrl_in", persistent=False)
        try:
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                raw = ctrl_in.read_one()
                if raw is None:
                    time.sleep(0.001)
                    continue
                request = json.loads(str(raw))
                observed.append(request)
                reply = ctx.queue(request["reply_to"], persistent=False)
                try:
                    for row in rows_before_pong:
                        reply.write(row)
                    reply.write(
                        json.dumps(
                            {
                                "command": "PING",
                                "status": "ok",
                                "message": "PONG",
                                "tid": tid,
                                "request_id": request["request_id"],
                                "task_status": "running",
                            }
                        )
                    )
                finally:
                    reply.close()
                return
            raise AssertionError("probe PING did not arrive")
        finally:
            ctrl_in.close()

    thread = threading.Thread(target=respond, daemon=True)
    thread.start()
    return thread, observed


def test_send_keyed_ping_probe_routes_pong_to_ephemeral_ctrl_in(
    tmp_path: Path,
) -> None:
    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    tid = "1775622400000000101"
    thread, observed = _start_pong_responder(
        ctx,
        tid=tid,
        rows_before_pong=("not-json",),
    )

    result = send_keyed_ping_probe(
        ctx,
        tid=tid,
        ctrl_in_name=f"T{tid}.ctrl_in",
        request_id="probe-request-1",
        timeout=1.0,
    )
    thread.join(timeout=3.0)

    assert not thread.is_alive()
    assert result.error is None
    assert result.timed_out is False
    assert result.matched is not None
    assert result.matched.payload["task_status"] == "running"
    assert len(observed) == 1
    assert observed[0] == {
        "command": "PING",
        "request_id": "probe-request-1",
        "reply_to": observed[0]["reply_to"],
    }
    reply_to = observed[0]["reply_to"]
    assert reply_to.startswith("T") and reply_to.endswith(".ctrl_in")
    assert reply_to not in _queue_names(ctx)
    assert f"T{tid}.ctrl_out" not in _queue_names(ctx)


def test_send_keyed_ping_probe_timeout_retires_ephemeral_queue(tmp_path: Path) -> None:
    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    tid = "1775622400000000102"

    result = send_keyed_ping_probe(
        ctx,
        tid=tid,
        ctrl_in_name=f"T{tid}.ctrl_in",
        timeout=0.0,
    )

    assert result.matched is None
    assert result.timed_out is True
    assert not any(
        name.endswith(".ctrl_in") and name != f"T{tid}.ctrl_in"
        for name in _queue_names(ctx)
    )


def test_ephemeral_probe_creates_no_task_identity_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    requester_tid = "1775622400000000109"
    monkeypatch.setattr(
        control_probe_mod,
        "generate_spawn_request_timestamp",
        lambda *_args, **_kwargs: int(requester_tid),
    )

    result = send_keyed_ping_probe(
        ctx,
        tid="1775622400000000110",
        ctrl_in_name="T1775622400000000110.ctrl_in",
        timeout=0.0,
    )

    assert result.timed_out
    assert not any(name.startswith(f"T{requester_tid}.") for name in _queue_names(ctx))
    assert all(snapshot.tid != requester_tid for snapshot in list_tasks(context=ctx))


def test_ephemeral_probe_uses_manual_wait_without_starting_drive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    monkeypatch.setattr(
        control_probe_mod.MultiQueueWatcher,
        "run_in_thread",
        lambda *_args, **_kwargs: pytest.fail("probe must not start a drive thread"),
    )
    monkeypatch.setattr(
        control_probe_mod.MultiQueueWatcher,
        "run_forever",
        lambda *_args, **_kwargs: pytest.fail("probe must not start a drive loop"),
    )

    result = send_keyed_ping_probe(
        ctx,
        tid="1775622400000000111",
        ctrl_in_name="T1775622400000000111.ctrl_in",
        timeout=0.0,
    )

    assert result.timed_out


def test_probe_broker_error_after_watcher_setup_retires_reply_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    requester_tid = "1775622400000000112"
    monkeypatch.setattr(
        control_probe_mod,
        "generate_spawn_request_timestamp",
        lambda *_args, **_kwargs: int(requester_tid),
    )
    monkeypatch.setattr(
        control_probe_mod,
        "_write_probe_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(BrokerError("write failed")),
    )

    result = send_keyed_ping_probe(
        ctx,
        tid="1775622400000000113",
        ctrl_in_name="T1775622400000000113.ctrl_in",
        timeout=1.0,
    )

    assert result.error == "write failed"
    assert not any(name.startswith(f"T{requester_tid}.") for name in _queue_names(ctx))


def test_cleanup_failures_do_not_replace_matched_probe_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    tid = "1775622400000000114"
    thread, _observed = _start_pong_responder(ctx, tid=tid)
    original_stop = control_probe_mod.MultiQueueWatcher.stop
    stop_calls = 0

    def flaky_stop(watcher: Any, *args: Any, **kwargs: Any) -> None:
        nonlocal stop_calls
        stop_calls += 1
        if stop_calls == 1:
            raise RuntimeError("first cleanup attempt failed")
        original_stop(watcher, *args, **kwargs)

    monkeypatch.setattr(control_probe_mod.MultiQueueWatcher, "stop", flaky_stop)
    monkeypatch.setattr(
        control_probe_mod,
        "_retire_probe_reply_queue",
        lambda _queue: None,
    )

    result = send_keyed_ping_probe(
        ctx,
        tid=tid,
        ctrl_in_name=f"T{tid}.ctrl_in",
        request_id="cleanup-result",
        timeout=1.0,
    )
    thread.join(timeout=3.0)

    assert result.matched is not None
    assert stop_calls == 2


def test_late_pong_stays_with_dead_requester_until_existing_sweep_selects_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    requester_tid = "1775622400000000115"
    target_tid = "1775622400000000116"
    monkeypatch.setattr(
        control_probe_mod,
        "generate_spawn_request_timestamp",
        lambda *_args, **_kwargs: int(requester_tid),
    )

    result = send_keyed_ping_probe(
        ctx,
        tid=target_tid,
        ctrl_in_name=f"T{target_tid}.ctrl_in",
        timeout=0.0,
    )
    target = ctx.queue(f"T{target_tid}.ctrl_in")
    raw = target.read_one()
    target.close()
    assert raw is not None
    request = json.loads(str(raw))
    reply_to = str(request["reply_to"])
    late = ctx.queue(reply_to)
    late.write(
        json.dumps(
            {
                "command": "PING",
                "status": "ok",
                "message": "PONG",
                "tid": target_tid,
                "request_id": request["request_id"],
                "task_status": "running",
            }
        )
    )
    late.close()

    assert result.timed_out
    assert reply_to in _queue_names(ctx)
    age_ns = int(TASK_MONITOR_DEAD_TID_CLEANUP_MIN_AGE_SECONDS * 1_000_000_000)
    young = select_runtime_dead_task_cleanup_candidates(
        (reply_to,),
        now_ns=int(requester_tid) + age_ns - 1,
        min_age_seconds=TASK_MONITOR_DEAD_TID_CLEANUP_MIN_AGE_SECONDS,
        retention_seconds=172800.0,
        limit=1,
        active_tids=set(),
        task_record=lambda _tid: None,
        deadline_reached=lambda: False,
    )
    old = select_runtime_dead_task_cleanup_candidates(
        (reply_to,),
        now_ns=int(requester_tid) + age_ns + 1,
        min_age_seconds=TASK_MONITOR_DEAD_TID_CLEANUP_MIN_AGE_SECONDS,
        retention_seconds=172800.0,
        limit=1,
        active_tids=set(),
        task_record=lambda _tid: None,
        deadline_reached=lambda: False,
    )

    assert young.tids == ()
    assert old.tids == (requester_tid,)


@pytest.mark.parametrize(
    "timeout",
    [-1.0, math.nan, math.inf, -math.inf, CONTROL_PING_MAX_TIMEOUT_SECONDS + 0.001],
)
def test_send_keyed_ping_probe_rejects_invalid_timeout_before_minting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    timeout: float,
) -> None:
    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    monkeypatch.setattr(
        control_probe_mod,
        "generate_spawn_request_timestamp",
        lambda *args, **kwargs: pytest.fail("invalid timeout must fail before minting"),
    )

    with pytest.raises(CommandUsageError):
        send_keyed_ping_probe(
            ctx,
            tid="1775622400000000103",
            ctrl_in_name="T1775622400000000103.ctrl_in",
            timeout=timeout,
        )


@pytest.mark.parametrize(
    "timeout",
    [0.0, CONTROL_PING_MAX_TIMEOUT_SECONDS - 0.001, CONTROL_PING_MAX_TIMEOUT_SECONDS],
)
def test_send_keyed_ping_probe_accepts_timeout_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    timeout: float,
) -> None:
    ctx = build_context(spec_context=prepare_project_root(tmp_path))

    def accepted(*args: object, **kwargs: object) -> int:
        del args, kwargs
        raise BrokerError("accepted")

    monkeypatch.setattr(control_probe_mod, "generate_spawn_request_timestamp", accepted)
    result = send_keyed_ping_probe(
        ctx,
        tid="1775622400000000104",
        ctrl_in_name="T1775622400000000104.ctrl_in",
        timeout=timeout,
    )
    assert result.error == "accepted"


def test_control_ping_max_timeout_tracks_cleanup_age() -> None:
    assert CONTROL_PING_MAX_TIMEOUT_SECONDS == (
        TASK_MONITOR_DEAD_TID_CLEANUP_MIN_AGE_SECONDS / 2
    )


def test_send_keyed_ping_probe_does_not_relabel_programmer_runtime_error(
    tmp_path: Path,
) -> None:
    ctx = build_context(spec_context=prepare_project_root(tmp_path))

    class BrokenBroker:
        def generate_timestamp(self) -> int:
            return 1775622400000000108

        def write(self, queue_name: str, message: str) -> None:
            del queue_name, message
            raise RuntimeError("programmer defect")

    with pytest.raises(RuntimeError, match="programmer defect"):
        send_keyed_ping_probe(
            ctx,
            tid="1775622400000000108",
            ctrl_in_name="T1775622400000000108.ctrl_in",
            timeout=0.0,
            broker=BrokenBroker(),
        )


def test_coerce_pong_response_rejects_payload_without_task_status() -> None:
    raw = json.dumps(
        {
            "command": "PING",
            "status": "ok",
            "message": "PONG",
            "tid": "1775622400000000103",
            "request_id": "probe-request-3",
        }
    )

    assert (
        coerce_pong_response(
            raw,
            tid="1775622400000000103",
            request_id="probe-request-3",
        )
        is None
    )


@pytest.mark.parametrize(
    "task_status",
    [
        "created",
        "spawning",
        "running",
        "completed",
        "failed",
        "timeout",
        "cancelled",
        "killed",
    ],
)
def test_coerce_pong_response_accepts_each_exact_task_status(
    task_status: str,
) -> None:
    payload = {
        "command": "PING",
        "status": "ok",
        "message": "PONG",
        "tid": "1775622400000000103",
        "request_id": "probe-request-3",
        "task_status": task_status,
    }

    assert (
        coerce_pong_response(
            json.dumps(payload),
            tid="1775622400000000103",
            request_id="probe-request-3",
        )
        == payload
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("command", "ping"),
        ("command", " PING "),
        ("status", "OK"),
        ("status", " ok "),
        ("message", "pong"),
        ("tid", 1775622400000000103),
        ("request_id", 3),
        ("task_status", ""),
        ("task_status", "waiting"),
        ("task_status", 3),
        ("task_status", []),
    ],
)
def test_coerce_pong_response_requires_exact_core_fields(
    field: str,
    value: object,
) -> None:
    payload: dict[str, object] = {
        "command": "PING",
        "status": "ok",
        "message": "PONG",
        "tid": "1775622400000000103",
        "request_id": "probe-request-3",
        "task_status": "running",
    }
    payload[field] = value

    assert (
        coerce_pong_response(
            json.dumps(payload),
            tid="1775622400000000103",
            request_id="probe-request-3",
        )
        is None
    )


def test_coerce_pong_response_rejects_conflicting_duplicate_core_fields() -> None:
    raw = (
        '{"command":"STATUS","command":"PING",'
        '"status":"error","status":"ok",'
        '"message":"NOPE","message":"PONG",'
        '"tid":"other","tid":"1775622400000000103",'
        '"request_id":"other","request_id":"probe-request-3",'
        '"task_status":"failed","task_status":"running"}'
    )

    assert (
        coerce_pong_response(
            raw,
            tid="1775622400000000103",
            request_id="probe-request-3",
        )
        is None
    )


_CTRL_IN = "weft.manager.ctrl_in"
_CTRL_OUT = "weft.manager.ctrl_out"
_ROOT = "/projects/demo"


def _eligible_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "task_status": "running",
        "role": "manager",
        "requests": WEFT_SPAWN_REQUESTS_QUEUE,
        "ctrl_in": _CTRL_IN,
        "ctrl_out": _CTRL_OUT,
        "outbox": WEFT_MANAGER_OUTBOX_QUEUE,
        "weft_context": _ROOT,
        "should_stop": False,
    }
    payload.update(overrides)
    return payload


def _gate(
    payload: dict[str, object], record: Mapping[str, object] | None = None
) -> bool:
    return pong_proves_dispatch_eligible(
        payload,
        record={"weft_context": _ROOT} if record is None else record,
        ctrl_in_name=_CTRL_IN,
        ctrl_out_name=_CTRL_OUT,
        outbox_name=WEFT_MANAGER_OUTBOX_QUEUE,
        root_context=_ROOT,
    )


@pytest.mark.parametrize("task_status", ["created", "spawning", "running"])
def test_pong_gate_accepts_fully_specified_manager(task_status: str) -> None:
    assert _gate(_eligible_payload(task_status=task_status)) is True


@pytest.mark.parametrize(
    "field",
    [
        "task_status",
        "should_stop",
        "role",
        "requests",
        "ctrl_in",
        "ctrl_out",
        "outbox",
        "weft_context",
    ],
)
def test_pong_gate_rejects_each_missing_authority_field(field: str) -> None:
    payload = _eligible_payload()
    del payload[field]

    assert _gate(payload) is False


@pytest.mark.parametrize(
    "status",
    [
        SERVICE_STATUS_DRAINING,
        "stopping",
        "cancelled",
        "completed",
        "failed",
        "timeout",
        "killed",
        [],
    ],
)
def test_pong_gate_rejects_terminal_or_stopping_status(status: object) -> None:
    assert _gate(_eligible_payload(task_status=status)) is False


def test_pong_gate_rejects_should_stop_and_mismatches() -> None:
    assert _gate(_eligible_payload(should_stop=True)) is False
    assert _gate(_eligible_payload(role="worker")) is False
    assert _gate(_eligible_payload(requests="other.queue")) is False
    assert _gate(_eligible_payload(ctrl_in="other.ctrl_in")) is False
    assert _gate(_eligible_payload(ctrl_out="other.ctrl_out")) is False
    assert _gate(_eligible_payload(outbox="other.outbox")) is False
    assert _gate(_eligible_payload(weft_context="/projects/other")) is False


def test_pong_gate_empty_record_context_falls_back_to_root() -> None:
    # Decision: an empty weft_context means absent -> fall back to root_context.
    record = {"weft_context": ""}
    assert _gate(_eligible_payload(weft_context=_ROOT), record) is True
    assert _gate(_eligible_payload(weft_context=""), record) is False


def test_pong_gate_nonempty_record_context_overrides_root() -> None:
    record = {"weft_context": "/projects/other"}
    assert _gate(_eligible_payload(weft_context="/projects/other"), record) is True
    assert _gate(_eligible_payload(weft_context=_ROOT), record) is False
