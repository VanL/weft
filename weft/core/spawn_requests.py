"""Shared spawn-request helpers for manager-launched task processes.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-6]
- docs/specifications/07-System_Invariants.md [MANAGER.4]
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from simplebroker import BrokerTarget, Queue
from weft._constants import WEFT_SPAWN_REQUESTS_QUEUE, WORK_ENVELOPE_START
from weft.core.taskspec import (
    TaskSpec,
    apply_bundle_root_to_taskspec_payload,
    bundle_root_from_taskspec_payload,
    resolve_taskspec_payload,
)


def _normalize_broker_target(target: BrokerTarget | str | Path) -> BrokerTarget | str:
    if isinstance(target, Path):
        return str(target)
    return target


def _taskspec_payload_for_spawn(
    taskspec: TaskSpec | Mapping[str, Any],
    *,
    tid: str,
    inherited_weft_context: str | None = None,
) -> dict[str, Any]:
    payload = (
        taskspec.model_dump(mode="json")
        if isinstance(taskspec, TaskSpec)
        else dict(taskspec)
    )
    bundle_root = (
        taskspec.get_bundle_root()
        if isinstance(taskspec, TaskSpec)
        else bundle_root_from_taskspec_payload(payload)
    )
    apply_bundle_root_to_taskspec_payload(payload, bundle_root)
    return resolve_taskspec_payload(
        payload,
        tid=tid,
        inherited_weft_context=inherited_weft_context,
    )


def generate_spawn_request_timestamp(
    broker_target: BrokerTarget | str | Path,
    *,
    config: Mapping[str, Any] | None = None,
) -> int:
    """Return a broker-valid task timestamp from the spawn-request queue."""

    queue_config = dict(config) if config is not None else None
    queue = Queue(
        WEFT_SPAWN_REQUESTS_QUEUE,
        db_path=_normalize_broker_target(broker_target),
        persistent=False,
        config=queue_config,
    )
    try:
        return int(queue.generate_timestamp())
    finally:
        queue.close()


def submit_spawn_request(
    broker_target: BrokerTarget | str | Path,
    *,
    taskspec: TaskSpec | Mapping[str, Any],
    work_payload: Any,
    config: Mapping[str, Any] | None = None,
    tid: str | int | None = None,
    inherited_weft_context: str | None = None,
    seed_start_envelope: bool = True,
) -> int:
    """Write a manager spawn request using exact-timestamp TID correlation."""

    resolved_tid = (
        str(tid)
        if tid is not None
        else str(
            generate_spawn_request_timestamp(
                broker_target,
                config=config,
            )
        )
    )
    taskspec_payload = _taskspec_payload_for_spawn(
        taskspec,
        tid=resolved_tid,
        inherited_weft_context=inherited_weft_context,
    )

    inbox_message = work_payload
    if (
        inbox_message is None
        and seed_start_envelope
        and not bool(taskspec_payload.get("spec", {}).get("persistent"))
    ):
        inbox_message = WORK_ENVELOPE_START

    message = {
        "taskspec": taskspec_payload,
        "inbox_message": inbox_message,
    }
    message_json = json.dumps(message)
    message_timestamp = int(resolved_tid)

    queue_config = dict(config) if config is not None else None
    queue = Queue(
        WEFT_SPAWN_REQUESTS_QUEUE,
        db_path=_normalize_broker_target(broker_target),
        persistent=False,
        config=queue_config,
    )
    try:
        with queue.get_connection() as db:
            db._run_with_retry(
                lambda: db._do_write_transaction(
                    WEFT_SPAWN_REQUESTS_QUEUE,
                    message_json,
                    message_timestamp,
                )
            )
    finally:
        queue.close()

    return message_timestamp


def delete_spawn_request(
    broker_target: BrokerTarget | str | Path,
    *,
    message_timestamp: int,
    config: Mapping[str, Any] | None = None,
) -> bool:
    """Best-effort removal of a queued spawn request after setup failure."""

    queue_config = dict(config) if config is not None else None
    queue = Queue(
        WEFT_SPAWN_REQUESTS_QUEUE,
        db_path=_normalize_broker_target(broker_target),
        persistent=False,
        config=queue_config,
    )
    try:
        return bool(queue.delete(message_id=message_timestamp))
    except Exception:
        return False
    finally:
        queue.close()
