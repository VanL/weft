"""Tests for keyed control-channel PING/PONG probing."""

from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Callable, Mapping
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
    load_config,
)
from weft._exceptions import CommandUsageError
from weft.commands.tasks import list_tasks
from weft.context import WeftContext, build_context
from weft.core.control_probe import (
    KeyedPingTarget,
    coerce_pong_response,
    pong_proves_dispatch_eligible,
    send_keyed_ping_probe,
    send_keyed_ping_round,
)
from weft.core.monitor.policies.runtime_control import (
    runtime_dead_task_record_probe_tids,
)

pytestmark = [pytest.mark.shared]


def _queue_names(ctx: WeftContext) -> set[str]:
    with ctx.broker() as broker:
        return set(broker.list_queues())


def _respond_to_probe_write(
    ctx: WeftContext,
    monkeypatch: pytest.MonkeyPatch,
    *,
    tid: str,
    rows_before_pong: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    observed: list[dict[str, Any]] = []
    original_write = control_probe_mod._write_probe_request

    def write_and_respond(
        reply_queue: Any,
        *,
        broker: Any | None,
        ctrl_in_name: str,
        message: str,
    ) -> None:
        original_write(
            reply_queue,
            broker=broker,
            ctrl_in_name=ctrl_in_name,
            message=message,
        )
        with ctx.queue(ctrl_in_name, persistent=False) as ctrl_in:
            raw = ctrl_in.read_one()
        assert raw is not None
        request = json.loads(str(raw))
        observed.append(request)
        with ctx.queue(request["reply_to"], persistent=False) as reply:
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

    # Routing and cleanup do not promise a responder's wall-clock latency.
    # Publish through the real request route before the probe deadline starts.
    monkeypatch.setattr(control_probe_mod, "_write_probe_request", write_and_respond)

    return observed


def test_send_keyed_ping_probe_routes_pong_to_ephemeral_ctrl_in(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    tid = "1775622400000000101"
    observed = _respond_to_probe_write(
        ctx, monkeypatch, tid=tid, rows_before_pong=("not-json",)
    )

    result = send_keyed_ping_probe(
        ctx,
        tid=tid,
        ctrl_in_name=f"T{tid}.ctrl_in",
        request_id="probe-request-1",
        timeout=1.0,
    )
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


def _pong_row(tid: str, request_id: str) -> str:
    return json.dumps(
        {
            "command": "PING",
            "status": "ok",
            "message": "PONG",
            "tid": tid,
            "request_id": request_id,
            "task_status": "running",
        }
    )


def _answer_round_writes(
    ctx: WeftContext,
    monkeypatch: pytest.MonkeyPatch,
    *,
    answering: set[str],
) -> None:
    """Answer the PINGs written to ``answering`` targets at write time."""

    original_write = control_probe_mod._write_probe_request

    def write_and_answer(
        reply_queue: Any,
        *,
        broker: Any | None,
        ctrl_in_name: str,
        message: str,
    ) -> int:
        message_id = original_write(
            reply_queue, broker=broker, ctrl_in_name=ctrl_in_name, message=message
        )
        tid = ctrl_in_name.removeprefix("T").removesuffix(".ctrl_in")
        if tid in answering:
            with ctx.queue(ctrl_in_name, persistent=False) as ctrl_in:
                raw = ctrl_in.read_one()
            assert raw is not None
            request = json.loads(str(raw))
            with ctx.queue(request["reply_to"], persistent=False) as reply:
                reply.write(_pong_row(tid, request["request_id"]))
        return message_id

    monkeypatch.setattr(control_probe_mod, "_write_probe_request", write_and_answer)


class _ReplyReadsThenFail:
    """Delegate to the probe's reply queue; fail every read after ``ok_reads``."""

    def __init__(self, queue: Any, *, ok_reads: int) -> None:
        self._queue = queue
        self._reads_left = ok_reads
        self.reads = 0

    def read_one(self, **kwargs: Any) -> Any:
        self.reads += 1
        if self._reads_left <= 0:
            raise BrokerError("injected reply read failure")
        self._reads_left -= 1
        return self._queue.read_one(**kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._queue, name)


def _fail_reply_reads_after(
    monkeypatch: pytest.MonkeyPatch, *, ok_reads: int
) -> list[_ReplyReadsThenFail]:
    original_get_queue = control_probe_mod.MultiQueueWatcher.get_queue
    proxies: list[_ReplyReadsThenFail] = []

    def get_queue(watcher: Any, name: str) -> Any:
        queue = original_get_queue(watcher, name)
        if queue is None:
            return None
        proxies.append(_ReplyReadsThenFail(queue, ok_reads=ok_reads))
        return proxies[-1]

    monkeypatch.setattr(control_probe_mod.MultiQueueWatcher, "get_queue", get_queue)
    return proxies


def test_send_keyed_ping_probe_keeps_match_when_a_later_reply_read_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A matched PONG ends the read; a later read failure cannot discard it [MF-3]."""

    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    tid = "1775622400000000131"
    _respond_to_probe_write(ctx, monkeypatch, tid=tid)
    proxies = _fail_reply_reads_after(monkeypatch, ok_reads=1)

    result = send_keyed_ping_probe(
        ctx,
        tid=tid,
        ctrl_in_name=f"T{tid}.ctrl_in",
        request_id="kept-match",
        timeout=1.0,
    )

    assert result.error is None
    assert result.matched is not None
    assert result.matched.request_id == "kept-match"
    # Baseline contract: reading stops at the match; no further read is tried.
    assert [proxy.reads for proxy in proxies] == [1]


def test_ping_round_keeps_earlier_match_when_a_later_reply_read_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A read failure after one PONG keeps that match; only the rest error [MF-3]."""

    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    answering = "1775622400000000132"
    silent = "1775622400000000133"
    _answer_round_writes(ctx, monkeypatch, answering={answering})
    _fail_reply_reads_after(monkeypatch, ok_reads=1)

    results = send_keyed_ping_round(
        ctx,
        [
            KeyedPingTarget(tid=answering, ctrl_in_name=f"T{answering}.ctrl_in"),
            KeyedPingTarget(tid=silent, ctrl_in_name=f"T{silent}.ctrl_in"),
        ],
        timeout=1.0,
    )

    assert results[answering].matched is not None
    assert results[answering].error is None
    assert results[silent].matched is None
    assert results[silent].error == "injected reply read failure"
    with ctx.queue(f"T{silent}.ctrl_in", persistent=False) as ctrl_in:
        assert ctrl_in.peek_many(limit=10, include_claimed=True) == []


def test_ping_round_keeps_earlier_match_when_a_later_wait_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wait failure after one PONG keeps that match; only the rest error [MF-3]."""

    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    answering = "1775622400000000134"
    silent = "1775622400000000135"
    _answer_round_writes(ctx, monkeypatch, answering={answering})
    original_wait = control_probe_mod.MultiQueueWatcher.wait_for_activity
    waits = 0

    def wait_then_fail(watcher: Any, timeout: float | None) -> None:
        nonlocal waits
        waits += 1
        if waits > 1:
            raise BrokerError("injected wait failure")
        original_wait(watcher, timeout)

    monkeypatch.setattr(
        control_probe_mod.MultiQueueWatcher, "wait_for_activity", wait_then_fail
    )

    results = send_keyed_ping_round(
        ctx,
        [
            KeyedPingTarget(tid=answering, ctrl_in_name=f"T{answering}.ctrl_in"),
            KeyedPingTarget(tid=silent, ctrl_in_name=f"T{silent}.ctrl_in"),
        ],
        timeout=1.0,
    )

    assert waits == 2
    assert results[answering].matched is not None
    assert results[silent].matched is None
    assert results[silent].error == "injected wait failure"


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
    _respond_to_probe_write(ctx, monkeypatch, tid=tid)
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

    assert result.matched is not None
    assert stop_calls == 2


def test_unclaimed_ping_is_retracted_when_the_probe_times_out(
    tmp_path: Path,
) -> None:
    """A silent target's ``ctrl_in`` keeps no probe row [MF-3]."""

    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    target_tid = "1775622400000000117"

    result = send_keyed_ping_probe(
        ctx,
        tid=target_tid,
        ctrl_in_name=f"T{target_tid}.ctrl_in",
        timeout=0.0,
    )

    assert result.timed_out
    with ctx.queue(f"T{target_tid}.ctrl_in", persistent=False) as target:
        assert target.peek_many(limit=10, include_claimed=True) == []
    assert not any(name.endswith(".ctrl_in") for name in _queue_names(ctx))


def test_claimed_late_pong_stays_with_dead_requester_until_existing_sweep_selects_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A target that claims its PING and answers late leaves one requester row.

    The target's ``ctrl_in`` stays clean; the late PONG recreates only the dead
    requester's reply queue, which the name-derived dead-TID cleanup selects
    once the requester TID is old enough [MF-3], [MF-5].
    """

    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    requester_tid = "1775622400000000115"
    target_tid = "1775622400000000116"
    monkeypatch.setattr(
        control_probe_mod,
        "generate_spawn_request_timestamp",
        lambda *_args, **_kwargs: int(requester_tid),
    )
    claimed: list[dict[str, Any]] = []
    original_write = control_probe_mod._write_probe_request

    def write_and_claim(
        reply_queue: Any,
        *,
        broker: Any | None,
        ctrl_in_name: str,
        message: str,
    ) -> int:
        message_id = original_write(
            reply_queue, broker=broker, ctrl_in_name=ctrl_in_name, message=message
        )
        with ctx.queue(ctrl_in_name, persistent=False) as target:
            raw = target.read_one()
        assert raw is not None
        claimed.append(json.loads(str(raw)))
        return message_id

    monkeypatch.setattr(control_probe_mod, "_write_probe_request", write_and_claim)

    result = send_keyed_ping_probe(
        ctx,
        tid=target_tid,
        ctrl_in_name=f"T{target_tid}.ctrl_in",
        timeout=0.0,
    )
    reply_to = str(claimed[0]["reply_to"])
    with ctx.queue(reply_to, persistent=False) as late:
        late.write(_pong_row(target_tid, claimed[0]["request_id"]))

    assert result.timed_out
    with ctx.queue(f"T{target_tid}.ctrl_in", persistent=False) as target:
        assert target.peek_many(limit=10, include_claimed=True) == []
    assert reply_to == f"T{requester_tid}.ctrl_in"
    assert reply_to in _queue_names(ctx)
    age_ns = int(TASK_MONITOR_DEAD_TID_CLEANUP_MIN_AGE_SECONDS * 1_000_000_000)
    young = runtime_dead_task_record_probe_tids(
        (reply_to,),
        now_ns=int(requester_tid) + age_ns - 1,
        min_age_seconds=TASK_MONITOR_DEAD_TID_CLEANUP_MIN_AGE_SECONDS,
        retention_seconds=172800.0,
        observation_retention_seconds=172800.0,
        active_tids=set(),
    )
    old = runtime_dead_task_record_probe_tids(
        (reply_to,),
        now_ns=int(requester_tid) + age_ns + 1,
        min_age_seconds=TASK_MONITOR_DEAD_TID_CLEANUP_MIN_AGE_SECONDS,
        retention_seconds=172800.0,
        observation_retention_seconds=172800.0,
        active_tids=set(),
    )

    assert young == ()
    assert old == (requester_tid,)


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


class _WakeableCancelEvent:
    """Wakeable cancel event matching Weft's `_register_wakeup` contract."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._wakeups: dict[object, Callable[[], None]] = {}

    def is_set(self) -> bool:
        return self._event.is_set()

    def _register_wakeup(self, callback: Callable[[], None]) -> Callable[[], None]:
        fire_immediately = False
        token = object()
        with self._lock:
            if self._event.is_set():
                fire_immediately = True
            else:
                self._wakeups[token] = callback
        if fire_immediately:
            callback()
            return lambda: None

        def _deregister() -> None:
            with self._lock:
                self._wakeups.pop(token, None)

        return _deregister

    def set(self) -> None:
        with self._lock:
            callbacks = (
                tuple(self._wakeups.values()) if not self._event.is_set() else ()
            )
            self._event.set()
        for callback in callbacks:
            callback()


def test_send_keyed_ping_probe_wakes_immediately_on_cancel_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A parked keyed PING probe interrupts immediately when cancel_event fires."""

    ctx = build_context(
        spec_context=prepare_project_root(tmp_path),
        config={**load_config(), "INITIAL_CHECKS": 0, "MAX_INTERVAL": 50.0},
    )
    tid = "1775622400000000901"
    ctrl_in_name = f"T{tid}.ctrl_in"
    cancel_event = _WakeableCancelEvent()
    probe_written = threading.Event()
    original_write = control_probe_mod._write_probe_request

    def _signal_write(*args: Any, **kwargs: Any) -> int:
        row_id = original_write(*args, **kwargs)
        probe_written.set()
        return row_id

    monkeypatch.setattr(control_probe_mod, "_write_probe_request", _signal_write)
    cancel_set_at: list[float] = []

    def _cancel_after_park() -> None:
        assert probe_written.wait(timeout=2.0)
        # Allow the caller thread to park inside a multi-hundred-millisecond wait.
        time.sleep(0.25)
        cancel_set_at.append(time.monotonic())
        cancel_event.set()

    canceller = threading.Thread(target=_cancel_after_park, daemon=True)
    canceller.start()
    result = send_keyed_ping_probe(
        ctx,
        tid=tid,
        ctrl_in_name=ctrl_in_name,
        timeout=5.0,
        cancel_event=cancel_event,
    )
    finished_at = time.monotonic()
    canceller.join(timeout=1.0)

    assert cancel_set_at, "canceller thread never fired"
    elapsed_after_cancel = finished_at - cancel_set_at[0]
    assert result.matched is None
    assert result.error is None
    assert result.timed_out is True
    assert elapsed_after_cancel < 0.15, (
        f"cancel_event failed to interrupt parked probe wait ({elapsed_after_cancel:.3f}s)"
    )
    with ctx.queue(ctrl_in_name, persistent=False) as ctrl_in:
        assert ctrl_in.peek_many(limit=10, include_claimed=True) == []
    assert not any(
        name.endswith(".ctrl_in") and name != ctrl_in_name for name in _queue_names(ctx)
    )


def test_send_keyed_ping_probe_matches_pong_with_unset_cancel_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Providing an unset cancel_event still receives and matches a live PONG."""

    ctx = build_context(spec_context=prepare_project_root(tmp_path))
    tid = "1775622400000000902"
    cancel_event = _WakeableCancelEvent()
    _respond_to_probe_write(ctx, monkeypatch, tid=tid)

    result = send_keyed_ping_probe(
        ctx,
        tid=tid,
        ctrl_in_name=f"T{tid}.ctrl_in",
        request_id="cancel-capable-match",
        timeout=1.0,
        cancel_event=cancel_event,
    )

    assert result.error is None
    assert result.timed_out is False
    assert result.matched is not None
    assert result.matched.request_id == "cancel-capable-match"
