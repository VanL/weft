"""Shared keyed control-channel probe helpers.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-3]
- docs/specifications/07-System_Invariants.md [MANAGER.8]
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from simplebroker import Queue
from simplebroker.ext import BrokerError
from weft._constants import (
    CONTROL_PING,
    CONTROL_SURFACE_WAIT_INTERVAL,
    CONTROL_SURFACE_WAIT_TIMEOUT,
    SERVICE_STATUS_DRAINING,
    WEFT_SPAWN_REQUESTS_QUEUE,
)
from weft.context import WeftContext
from weft.helpers import closing_queue_iterator

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MatchedPong:
    """A PONG payload matched to the probe that requested it."""

    payload: dict[str, Any]
    observed_at: int
    request_id: str


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
    Non-matching, malformed, stale, or legacy responses remain visible in the
    broker and are ignored by this helper.
    """

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if str(payload.get("command", "")).strip().upper() != CONTROL_PING:
        return None
    if str(payload.get("status", "")).strip().lower() != "ok":
        return None
    if payload.get("message") != "PONG":
        return None
    if payload.get("request_id") != request_id:
        return None
    if payload.get("tid") != tid:
        return None
    task_status = payload.get("task_status")
    if not isinstance(task_status, str) or not task_status:
        return None
    return payload


def reply_bears_request_id(raw: str, *, request_id: str) -> bool:
    """Whether a ctrl_out row is a keyed reply to the given probe request.

    Sweep predicate for keyed-reply retirement: the prober that issued
    ``request_id`` owns every reply keyed to it, including malformed or late
    ones that ``coerce_pong_response`` would reject. Rows without the exact
    ``request_id`` — other probes' replies, terminal envelopes, legacy
    responses, non-JSON bodies — never match.

    Spec: [MF-3], [MANAGER.8]
    """

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    return payload.get("request_id") == request_id


def pong_proves_dispatch_eligible(
    payload: Mapping[str, Any],
    *,
    record: Mapping[str, Any],
    ctrl_in_name: str,
    ctrl_out_name: str,
    root_context: str,
) -> bool:
    """Whether a matched PONG proves a record is a dispatch-eligible manager.

    Shared authority gate for the in-process Manager and the out-of-process
    runtime so both reach the same decision from the same fields. Absent
    manager-selection fields are accepted (legacy-compatible); only
    present-but-mismatched values reject. An empty ``weft_context`` string means
    "no context present" and falls back to ``root_context``.

    Callers may add their own narrowing (for example, the runtime additionally
    requires the manager outbox queue) after this gate passes.

    Spec: [MA-1] item 4, [MANAGER.8]
    """

    task_status = payload.get("task_status")
    if task_status in {
        SERVICE_STATUS_DRAINING,
        "stopping",
        "cancelled",
        "completed",
        "failed",
        "timeout",
        "killed",
    }:
        return False
    if payload.get("should_stop") is True:
        return False
    role = payload.get("role")
    if role is not None and role != "manager":
        return False
    requests = payload.get("requests")
    if requests is not None and requests != WEFT_SPAWN_REQUESTS_QUEUE:
        return False
    ctrl_in = payload.get("ctrl_in")
    if ctrl_in is not None and ctrl_in != ctrl_in_name:
        return False
    ctrl_out = payload.get("ctrl_out")
    if ctrl_out is not None and ctrl_out != ctrl_out_name:
        return False
    record_context = record.get("weft_context")
    expected_context = root_context
    if isinstance(record_context, str) and record_context:
        expected_context = record_context
    weft_context = payload.get("weft_context")
    return weft_context is None or weft_context == expected_context


def _retire_reply_row(ctrl_out: Queue, ctrl_out_name: str, message_id: int) -> None:
    """Best-effort exact-ID delete of one probe-owned ctrl_out reply row.

    Retirement is hygiene, not proof: failures never change the caller's
    probe outcome, so a matched probe stays matched even if the delete loses
    a race with task-exit purge or terminal/dead-TID cleanup.
    """

    try:
        ctrl_out.delete(message_id=message_id)
    except (BrokerError, OSError, RuntimeError):  # pragma: no cover - defensive
        logger.debug(
            "Failed to retire keyed probe reply",
            extra={"queue": ctrl_out_name, "message_id": message_id},
            exc_info=True,
        )


def _sweep_own_replies(ctrl_out: Queue, ctrl_out_name: str, request_id: str) -> None:
    """Best-effort single sweep deleting every row keyed to ``request_id``.

    Runs once at probe timeout to cover the reply-arrived-after-last-peek
    window. Rows keyed to other request ids are never touched. Failures are
    swallowed so a clean timeout result stays a timeout; an unswept reply's
    lifetime is bounded by task-exit purge and terminal/dead-TID cleanup.

    Spec: [MF-3]
    """

    reply_ids: list[int] = []
    try:
        iterator = ctrl_out.peek_generator(with_timestamps=True)
        with closing_queue_iterator(iterator) as rows:
            for item in rows:
                if not isinstance(item, tuple) or len(item) != 2:
                    continue
                body, timestamp = item
                if reply_bears_request_id(str(body), request_id=request_id):
                    reply_ids.append(int(timestamp))
    except (BrokerError, OSError, RuntimeError):  # pragma: no cover - defensive
        logger.debug(
            "Failed to sweep keyed probe replies",
            extra={"queue": ctrl_out_name, "request_id": request_id},
            exc_info=True,
        )
        return
    for message_id in reply_ids:
        _retire_reply_row(ctrl_out, ctrl_out_name, message_id)


def send_keyed_ping_probe(
    ctx: WeftContext,
    *,
    tid: str,
    ctrl_in_name: str,
    ctrl_out_name: str,
    timeout: float = CONTROL_SURFACE_WAIT_TIMEOUT,
    request_id: str | None = None,
) -> ControlProbeResult:
    """Send a keyed PING, wait for a matching PONG, and retire keyed replies.

    The probe writes one structured PING to ``ctrl_in_name`` and peeks
    ``ctrl_out_name`` until it sees a matching keyed PONG or the bounded wait
    expires. The prober owns the lifecycle of replies keyed to its
    ``request_id`` (single-reader contract): a matched PONG is deleted by
    exact message id on match, and one final sweep at timeout deletes any
    remaining row bearing this probe's ``request_id``. Rows keyed to other
    request ids — other probes' replies, terminal envelopes, legacy
    responses — are never touched. A reply landing after the final sweep can
    still remain; its lifetime is bounded by task-exit purge and
    terminal/dead-TID cleanup. Queue I/O errors are returned as probe errors
    so caller-side liveness decisions can stay conservative.

    Spec: [MF-3], [MANAGER.8]
    """

    probe_request_id = request_id or uuid.uuid4().hex
    try:
        ctrl_in = ctx.queue(ctrl_in_name, persistent=True)
        try:
            ctrl_in.write(
                json.dumps({"command": CONTROL_PING, "request_id": probe_request_id})
            )
        finally:
            ctrl_in.close()
    except (BrokerError, OSError, RuntimeError) as exc:
        return ControlProbeResult(request_id=probe_request_id, error=str(exc))

    deadline = time.monotonic() + max(0.0, timeout)
    try:
        ctrl_out = ctx.queue(ctrl_out_name, persistent=False)
        try:
            while True:
                matched_payload: dict[str, Any] | None = None
                matched_message_id: int | None = None
                iterator = ctrl_out.peek_generator(with_timestamps=True)
                with closing_queue_iterator(iterator) as rows:
                    for item in rows:
                        if not isinstance(item, tuple) or len(item) != 2:
                            continue
                        body, timestamp = item
                        payload = coerce_pong_response(
                            str(body),
                            tid=tid,
                            request_id=probe_request_id,
                        )
                        if payload is None:
                            continue
                        matched_payload = payload
                        matched_message_id = int(timestamp)
                        break
                if matched_payload is not None and matched_message_id is not None:
                    _retire_reply_row(ctrl_out, ctrl_out_name, matched_message_id)
                    return ControlProbeResult(
                        request_id=probe_request_id,
                        matched=MatchedPong(
                            payload=matched_payload,
                            observed_at=matched_message_id,
                            request_id=probe_request_id,
                        ),
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _sweep_own_replies(ctrl_out, ctrl_out_name, probe_request_id)
                    return ControlProbeResult(
                        request_id=probe_request_id,
                        timed_out=True,
                    )
                time.sleep(min(CONTROL_SURFACE_WAIT_INTERVAL, remaining))
        finally:
            ctrl_out.close()
    except (BrokerError, OSError, RuntimeError) as exc:
        return ControlProbeResult(request_id=probe_request_id, error=str(exc))
