"""Shared spawn-request helpers for manager-launched task processes.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-6]
- docs/specifications/07-System_Invariants.md [MANAGER.4]
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from simplebroker import BrokerTarget, Queue
from simplebroker.ext import BrokerError
from weft._constants import (
    INTERNAL_RUNTIME_ENDPOINT_NAME_KEY,
    INTERNAL_RUNTIME_ENVELOPE_ENDPOINT_NAME_KEY,
    INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY,
    INTERNAL_RUNTIME_TASK_CLASS_KEY,
    PUBLIC_RESERVED_SERVICE_METADATA_KEYS,
    WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    WORK_ENVELOPE_START,
)
from weft.core.endpoints import is_reserved_internal_endpoint_name
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


def _write_spawn_request_with_timestamp(
    db: object,
    *,
    queue_name: str,
    message: str,
    timestamp: int,
) -> None:
    """Write a spawn request at its preallocated TID timestamp.

    SimpleBroker's public ``Queue.write()`` API always generates a new
    timestamp. Weft's spawn submission contract needs the spawn-request message
    timestamp to match the externally returned TID, so this narrow adapter owns
    the private SimpleBroker exact-timestamp call until SimpleBroker exposes a
    public equivalent.
    """

    db_core = cast(Any, db)
    db_core._run_with_retry(
        lambda: db_core._do_write_transaction(
            queue_name,
            message,
            timestamp,
        )
    )


def _prepare_spawn_metadata(
    metadata: dict[str, Any],
    *,
    allow_internal_runtime: bool,
) -> tuple[str | None, str | None]:
    """Strip public-only reserved metadata and return internal envelope claims."""

    internal_runtime_task_class = metadata.pop(INTERNAL_RUNTIME_TASK_CLASS_KEY, None)
    internal_endpoint_name = None
    endpoint_name = metadata.get(INTERNAL_RUNTIME_ENDPOINT_NAME_KEY)
    if (
        isinstance(endpoint_name, str)
        and endpoint_name
        and is_reserved_internal_endpoint_name(endpoint_name)
    ):
        if allow_internal_runtime:
            internal_endpoint_name = metadata.pop(
                INTERNAL_RUNTIME_ENDPOINT_NAME_KEY, None
            )
        else:
            metadata.pop(INTERNAL_RUNTIME_ENDPOINT_NAME_KEY, None)

    if not allow_internal_runtime:
        for key in PUBLIC_RESERVED_SERVICE_METADATA_KEYS:
            metadata.pop(key, None)

    return (
        internal_runtime_task_class
        if isinstance(internal_runtime_task_class, str)
        else None,
        internal_endpoint_name if isinstance(internal_endpoint_name, str) else None,
    )


def submit_spawn_request(
    broker_target: BrokerTarget | str | Path,
    *,
    taskspec: TaskSpec | Mapping[str, Any],
    work_payload: Any,
    config: Mapping[str, Any] | None = None,
    tid: str | int | None = None,
    inherited_weft_context: str | None = None,
    seed_start_envelope: bool = True,
    allow_internal_runtime: bool = False,
    spawn_queue_name: str = WEFT_SPAWN_REQUESTS_QUEUE,
) -> int:
    """Write a manager spawn request using exact-timestamp TID correlation."""

    if spawn_queue_name not in {
        WEFT_SPAWN_REQUESTS_QUEUE,
        WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE,
    }:
        raise ValueError(f"unsupported spawn queue {spawn_queue_name!r}")

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

    metadata = taskspec_payload.get("metadata")
    internal_runtime_task_class = None
    internal_endpoint_name = None
    if isinstance(metadata, dict):
        internal_runtime_task_class, internal_endpoint_name = _prepare_spawn_metadata(
            metadata,
            allow_internal_runtime=allow_internal_runtime,
        )

    message = {
        "taskspec": taskspec_payload,
        "inbox_message": inbox_message,
    }
    if allow_internal_runtime:
        if isinstance(internal_runtime_task_class, str) and internal_runtime_task_class:
            message[INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY] = (
                internal_runtime_task_class
            )
        if isinstance(internal_endpoint_name, str) and internal_endpoint_name:
            message[INTERNAL_RUNTIME_ENVELOPE_ENDPOINT_NAME_KEY] = (
                internal_endpoint_name
            )
    message_json = json.dumps(message)
    message_timestamp = int(resolved_tid)

    queue_config = dict(config) if config is not None else None
    queue = Queue(
        spawn_queue_name,
        db_path=_normalize_broker_target(broker_target),
        persistent=False,
        config=queue_config,
    )
    try:
        with queue.get_connection() as db:
            _write_spawn_request_with_timestamp(
                db,
                queue_name=spawn_queue_name,
                message=message_json,
                timestamp=message_timestamp,
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
    except (
        BrokerError,
        OSError,
        RuntimeError,
    ):  # pragma: no cover - spawn cleanup best effort
        return False
    finally:
        queue.close()
