"""Shared keyed control-channel probe helpers.

Spec references:
- docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]
- docs/specifications/05-Message_Flow_and_State.md [MF-3]
- docs/specifications/07-System_Invariants.md [MANAGER.8]
- docs/specifications/10-CLI_Interface.md [CLI-1.2.1] (status-command PING round)
"""

from __future__ import annotations

import logging
import math
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from simplebroker.ext import BrokerError
from weft._constants import (
    CONTROL_PING,
    CONTROL_PING_MAX_TIMEOUT_SECONDS,
    CONTROL_SURFACE_WAIT_TIMEOUT,
    QUEUE_CTRL_IN_SUFFIX,
    WEFT_SPAWN_REQUESTS_QUEUE,
)
from weft._exceptions import CommandUsageError
from weft.context import WeftContext
from weft.core.control_messages import decode_control_object, encode_control_message
from weft.core.spawn_requests import generate_spawn_request_timestamp
from weft.core.tasks.multiqueue_watcher import MultiQueueWatcher, QueueMessageContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MatchedPong:
    """A PONG payload matched to the probe that requested it."""

    payload: dict[str, Any]
    observed_at: int
    request_id: str


@dataclass(frozen=True, slots=True)
class KeyedPingTarget:
    """One target of a keyed PING round: its TID, ``ctrl_in`` and request key."""

    tid: str
    ctrl_in_name: str
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass(frozen=True, slots=True)
class ControlProbeResult:
    """Outcome of one keyed PING probe against a task control surface."""

    request_id: str
    matched: MatchedPong | None = None
    timed_out: bool = False
    error: str | None = None


def coerce_pong_response(
    raw: str,
    *,
    tid: str,
    request_id: str,
) -> dict[str, Any] | None:
    """Return a matched structured PONG response or None.

    A matched PONG is a positive liveness proof for the exact task and probe.
    Non-matching, malformed, stale, or noncanonical responses are ignored by this
    helper. The owning requester decides whether those private queue rows are
    consumed or retained.

    Spec: [MF-3]
    """

    payload = decode_control_object(raw)
    if payload is None:
        return None
    return _matched_pong_payload(payload, tid=tid, request_id=request_id)


def _matched_pong_payload(
    payload: dict[str, Any],
    *,
    tid: str,
    request_id: str,
) -> dict[str, Any] | None:
    """Return a decoded reply if it is the exact PONG for ``tid``/``request_id``."""

    if payload.get("command") != CONTROL_PING:
        return None
    if payload.get("status") != "ok":
        return None
    if payload.get("message") != "PONG":
        return None
    if payload.get("request_id") != request_id:
        return None
    if payload.get("tid") != tid:
        return None
    task_status = payload.get("task_status")
    if not isinstance(task_status, str) or task_status not in {
        "created",
        "spawning",
        "running",
        "completed",
        "failed",
        "timeout",
        "cancelled",
        "killed",
    }:
        return None
    return payload


def pong_proves_dispatch_eligible(
    payload: Mapping[str, Any],
    *,
    record: Mapping[str, Any],
    ctrl_in_name: str,
    ctrl_out_name: str,
    outbox_name: str,
    root_context: str,
) -> bool:
    """Whether a matched PONG proves a record is a dispatch-eligible manager.

    Shared authority gate for the in-process Manager and the out-of-process
    runtime so both reach the same decision from the same complete field set.
    An empty record ``weft_context`` falls back to ``root_context`` when
    selecting the expected context, but the PONG must carry that exact value.

    Spec: [MA-1] item 4, [MANAGER.8]
    """

    task_status = payload.get("task_status")
    if not isinstance(task_status, str) or task_status not in {
        "created",
        "spawning",
        "running",
    }:
        return False
    if payload.get("should_stop") is not False:
        return False
    if payload.get("role") != "manager":
        return False
    if payload.get("requests") != WEFT_SPAWN_REQUESTS_QUEUE:
        return False
    if payload.get("ctrl_in") != ctrl_in_name:
        return False
    if payload.get("ctrl_out") != ctrl_out_name:
        return False
    if payload.get("outbox") != outbox_name:
        return False
    record_context = record.get("weft_context")
    expected_context = root_context
    if isinstance(record_context, str) and record_context:
        expected_context = record_context
    return payload.get("weft_context") == expected_context


def _unused_probe_handler(
    _message: str,
    _timestamp: int,
    _context: QueueMessageContext,
) -> None:
    """Satisfy the watcher contract; manual probe waits dispatch no handlers."""


def _validate_probe_timeout(timeout: float) -> float:
    """Return a valid synchronous probe timeout or raise a usage error."""

    try:
        value = float(timeout)
    except (TypeError, ValueError) as exc:
        raise CommandUsageError("PING timeout must be a finite number") from exc
    if (
        not math.isfinite(value)
        or value < 0
        or value > CONTROL_PING_MAX_TIMEOUT_SECONDS
    ):
        raise CommandUsageError(
            "PING timeout must be between 0 and "
            f"{CONTROL_PING_MAX_TIMEOUT_SECONDS:g} seconds"
        )
    return value


def _write_probe_request(
    reply_queue: Any,
    *,
    broker: Any | None,
    ctrl_in_name: str,
    message: str,
) -> int:
    """Write one PING through a borrowed broker or the watcher-owned queue."""

    if broker is not None:
        return int(broker.write(ctrl_in_name, message))
    with reply_queue.get_connection() as opened:
        return int(opened.write(ctrl_in_name, message))


def _retract_probe_requests(
    reply_queue: Any,
    *,
    broker: Any | None,
    requests: Mapping[str, int],
) -> None:
    """Delete each unanswered PING by exact ID, pending or already claimed.

    The target's ``ctrl_in`` keeps no probe row. A target that claimed its PING
    before the deadline may still answer after the round; that late PONG
    recreates the requester's own ``T{tid}.ctrl_in`` with one row, which the
    TaskMonitor dead-TID cleanup retires once the requester TID is old enough
    ([MF-3], [MF-5]).
    """

    for ctrl_in_name, message_id in requests.items():
        try:
            if broker is not None:
                broker.delete_message_ids(ctrl_in_name, [message_id])
                continue
            with reply_queue.get_connection() as opened:
                opened.delete_message_ids(ctrl_in_name, [message_id])
        except (BrokerError, OSError, RuntimeError):
            logger.debug("Failed to retract unanswered PING", exc_info=True)


def _read_matching_pongs(
    reply_queue: Any,
    pending: dict[str, KeyedPingTarget],
    results: dict[str, ControlProbeResult],
) -> None:
    """Consume ready reply rows, recording each exact PONG match as it arrives.

    Each row is decoded once and checked against the target its ``tid`` names.
    A matched target leaves ``pending`` at once, and reading stops when none
    remains, so a later read failure cannot discard proof already recorded.
    """

    while pending:
        item = reply_queue.read_one(with_timestamps=True)
        if item is None:
            return
        body, timestamp = item
        payload = decode_control_object(str(body))
        target = pending.get(str(payload.get("tid"))) if payload is not None else None
        if payload is None or target is None:
            continue
        matched_payload = _matched_pong_payload(
            payload, tid=target.tid, request_id=target.request_id
        )
        if matched_payload is None:
            continue
        results[target.tid] = ControlProbeResult(
            request_id=target.request_id,
            matched=MatchedPong(
                payload=matched_payload,
                observed_at=int(timestamp),
                request_id=target.request_id,
            ),
        )
        del pending[target.tid]


def _retire_probe_reply_queue(reply_queue: Any) -> None:
    """Best-effort cleanup after the watcher has released its queue lease."""

    try:
        reply_queue.delete()
    except (BrokerError, OSError, RuntimeError):  # pragma: no cover - defensive
        logger.debug(
            "Failed to retire ephemeral PING reply queue",
            exc_info=True,
        )
    finally:
        try:
            reply_queue.close()
        except (BrokerError, OSError, RuntimeError):  # pragma: no cover - defensive
            logger.debug(
                "Failed to close ephemeral PING reply queue",
                exc_info=True,
            )


def _stop_probe_watcher(watcher: MultiQueueWatcher) -> None:
    """Best-effort release without replacing the probe's primary outcome."""

    try:
        watcher.stop()
    except (BrokerError, OSError, RuntimeError):  # pragma: no cover - defensive
        logger.debug("Failed to stop ephemeral PING watcher", exc_info=True)
        try:
            watcher.stop()
        except (BrokerError, OSError, RuntimeError):  # pragma: no cover - defensive
            logger.debug("Failed to retry ephemeral PING watcher stop", exc_info=True)


def send_keyed_ping_probe(
    ctx: WeftContext,
    *,
    tid: str,
    ctrl_in_name: str,
    timeout: float = CONTROL_SURFACE_WAIT_TIMEOUT,
    request_id: str | None = None,
    broker: Any | None = None,
) -> ControlProbeResult:
    """Send a keyed PING and wait on an ephemeral requester control queue.

    The synchronous caller mints a task-shaped requester identity, owns its
    ``T{tid}.ctrl_in`` queue, and waits through the same watcher path used by
    long-lived tasks. The target writes its PONG directly to that queue. This
    is the one-target case of ``send_keyed_ping_round``: an unanswered PING is
    deleted by exact ID, and the watcher and reply queue are retired on every
    exit path.

    Spec: [MF-3], [MANAGER.8]
    """

    target = KeyedPingTarget(
        tid=tid,
        ctrl_in_name=ctrl_in_name,
        request_id=request_id or uuid.uuid4().hex,
    )
    return send_keyed_ping_round(ctx, (target,), timeout=timeout, broker=broker)[tid]


def _write_round_requests(
    reply_queue: Any,
    targets: Sequence[KeyedPingTarget],
    *,
    broker: Any | None,
    reply_to: str,
) -> tuple[dict[str, tuple[str, int]], dict[str, ControlProbeResult]]:
    """Write each target's PING; return written row IDs and failed writes."""

    written: dict[str, tuple[str, int]] = {}
    failed: dict[str, ControlProbeResult] = {}
    for target in targets:
        ping = encode_control_message(
            CONTROL_PING, request_id=target.request_id, reply_to=reply_to
        )
        try:
            message_id = _write_probe_request(
                reply_queue,
                broker=broker,
                ctrl_in_name=target.ctrl_in_name,
                message=ping,
            )
        except (BrokerError, OSError) as exc:
            failed[target.tid] = ControlProbeResult(
                request_id=target.request_id, error=str(exc)
            )
            continue
        if isinstance(message_id, int):
            written[target.tid] = (target.ctrl_in_name, message_id)
    return written, failed


def _await_round_pongs(
    watcher: MultiQueueWatcher,
    reply_queue: Any,
    pending: dict[str, KeyedPingTarget],
    results: dict[str, ControlProbeResult],
    *,
    timeout: float,
) -> None:
    """Wait once, until every pending target answered or ``timeout`` elapsed.

    Matches go into ``results`` as they arrive, so a failing later wait keeps
    them; targets still pending at the deadline time out.
    """

    deadline = time.monotonic() + timeout
    while pending:
        watcher.wait_for_activity(max(0.0, deadline - time.monotonic()))
        _read_matching_pongs(reply_queue, pending, results)
        if time.monotonic() >= deadline:
            break
    for target in pending.values():
        results[target.tid] = ControlProbeResult(
            request_id=target.request_id, timed_out=True
        )


def send_keyed_ping_round(
    ctx: WeftContext,
    targets: Sequence[KeyedPingTarget],
    *,
    timeout: float = CONTROL_SURFACE_WAIT_TIMEOUT,
    broker: Any | None = None,
) -> dict[str, ControlProbeResult]:
    """Send keyed PINGs to several targets and wait once for all PONGs.

    One requester identity owns one ``T{tid}.ctrl_in`` reply queue for the
    round; each target gets its own ``request_id``, so the round costs at most
    one ``timeout``. A failed write leaves that target unanswered without
    failing the others, and a later read or wait failure keeps every match
    already recorded. Each PING still unanswered at the end is deleted by
    exact ID, and the watcher and reply queue are retired on every exit path;
    a reply written after that is the late-reply case of
    ``_retract_probe_requests``.

    Spec: [MF-3], [MANAGER.8], [CLI-1.2.1]
    """

    probe_timeout = _validate_probe_timeout(timeout)
    pending = {target.tid: target for target in targets}
    results: dict[str, ControlProbeResult] = {}
    written: dict[str, tuple[str, int]] = {}
    watcher: MultiQueueWatcher | None = None
    reply_queue: Any | None = None
    if not pending:
        return results
    try:
        requester_tid = generate_spawn_request_timestamp(
            ctx.broker_target,
            config=ctx.broker_config,
            broker=broker,
        )
        reply_queue_name = f"T{requester_tid}.{QUEUE_CTRL_IN_SUFFIX}"
        watcher = MultiQueueWatcher(
            {
                reply_queue_name: {
                    # Manual waits only observe readiness; they never dispatch.
                    "handler": _unused_probe_handler,
                }
            },
            db=ctx.broker_target,
            persistent=False,
            config=ctx.broker_config,
        )
        reply_queue = watcher.get_queue(reply_queue_name)
        assert reply_queue is not None  # constructor invariant
        written, failed = _write_round_requests(
            reply_queue, targets, broker=broker, reply_to=reply_queue_name
        )
        results.update(failed)
        for tid in failed:
            pending.pop(tid)
        _await_round_pongs(
            watcher, reply_queue, pending, results, timeout=probe_timeout
        )
    except (BrokerError, OSError) as exc:
        for target in pending.values():
            results.setdefault(
                target.tid,
                ControlProbeResult(request_id=target.request_id, error=str(exc)),
            )
    finally:
        if reply_queue is not None:
            _retract_probe_requests(
                reply_queue,
                broker=broker,
                requests=dict(written[tid] for tid in pending if tid in written),
            )
        if watcher is not None:
            _stop_probe_watcher(watcher)
        if reply_queue is not None:
            _retire_probe_reply_queue(reply_queue)
    return results
