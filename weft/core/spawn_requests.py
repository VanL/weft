"""Shared spawn-request helpers for manager-launched task processes.

Spec references:
- docs/specifications/03-Manager_Architecture.md [MA-1.4]
- docs/specifications/05-Message_Flow_and_State.md [MF-1], [MF-5], [MF-6]
- docs/specifications/07-System_Invariants.md [MANAGER.4]
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from simplebroker import BrokerSession, BrokerTarget, Queue
from simplebroker.ext import BrokerError
from weft._constants import (
    INTERNAL_RUNTIME_ENDPOINT_NAME_KEY,
    INTERNAL_RUNTIME_ENVELOPE_ENDPOINT_NAME_KEY,
    INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY,
    INTERNAL_RUNTIME_TASK_CLASS_KEY,
    PUBLIC_RESERVED_SERVICE_METADATA_KEYS,
    QUEUE_INTERNAL_RESERVED_SUFFIX,
    QUEUE_RESERVED_SUFFIX,
    WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE,
    WEFT_SERVICES_REGISTRY_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    WORK_ENVELOPE_START,
    resolve_runtime_config,
)
from weft.context import WeftContext
from weft.core.endpoints import is_reserved_internal_endpoint_name
from weft.core.queue_window import iter_broker_queue_json_entries, queue_broker
from weft.core.runtime_identity import (
    canonical_runtime_context,
    get_runtime_identity,
    submission_lineage,
)
from weft.core.service_convergence import (
    manager_service_key,
    project_manager_service_record,
)
from weft.core.taskspec import (
    TaskSpec,
    decode_taskspec_transport_payload,
    encode_taskspec_transport_payload,
    validate_taskspec_payload,
)
from weft.helpers import closing_queue_iterator, is_canonical_manager_record


@dataclass(frozen=True, slots=True)
class SpawnRequestCustody:
    """Exact pending-request evidence, independent of task lifecycle [MF-5]."""

    state: Literal["queued", "reserved", "absent"]
    reserved_queue: str | None = None


def iter_spawn_reserved_routes(
    context: WeftContext, *, broker: Any | None = None
) -> Iterator[str]:
    """Read retained canonical manager routes without bootstrap effects.

    Fold valid records before canonical filtering so a newer noncanonical
    publication cannot revive an older route. Stopped and superseded owners
    remain potential custodians. Unsupported schemas are ignored.

    Spec: [MA-1.4], [MF-5].
    """
    latest: dict[str, dict[str, Any]] = {}
    service_key = manager_service_key(context)
    with (
        queue_broker(context, WEFT_SERVICES_REGISTRY_QUEUE, broker=broker) as db,
        closing_queue_iterator(
            iter_broker_queue_json_entries(
                db, WEFT_SERVICES_REGISTRY_QUEUE, strict=True
            )
        ) as entries,
    ):
        for payload, timestamp in entries:
            record = project_manager_service_record(
                payload, timestamp=timestamp, service_key=service_key
            )
            if record is None:
                continue
            owner = str(record["tid"])
            previous = latest.get(owner)
            if previous is None or previous["timestamp"] < timestamp:
                latest[owner] = record
    seen: set[str] = set()
    for owner, record in latest.items():
        if not is_canonical_manager_record(record):
            continue
        for suffix in (QUEUE_RESERVED_SUFFIX, QUEUE_INTERNAL_RESERVED_SUFFIX):
            route = f"T{owner}.{suffix}"
            if route not in seen:
                seen.add(route)
                yield route


def observe_spawn_request_custody(
    context: WeftContext,
    tid: str,
    *,
    reserved_routes: Iterable[str],
    broker: Any | None = None,
) -> SpawnRequestCustody:
    """Observe exact pending custody, inspecting reserved routes lazily.

    Source queues precede reserved queues in the manager handoff direction.
    This neither inspects request payloads nor claims an atomic queue snapshot.
    The caller owns task-evidence precedence and any registry bootstrap policy.

    Spec: [MF-5], [MF-6].
    """
    message_timestamp = int(tid)
    with queue_broker(context, WEFT_SPAWN_REQUESTS_QUEUE, broker=broker) as db:
        for source in (WEFT_SPAWN_REQUESTS_QUEUE, WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE):
            if (
                db.peek_one(
                    source, exact_timestamp=message_timestamp, with_timestamps=True
                )
                is not None
            ):
                return SpawnRequestCustody("queued")
    # Release an owned source lease before a lazy iterator opens registry I/O.
    for route in reserved_routes:
        with queue_broker(context, route, broker=broker) as db:
            if (
                db.peek_one(
                    route, exact_timestamp=message_timestamp, with_timestamps=True
                )
                is not None
            ):
                return SpawnRequestCustody("reserved", route)
    return SpawnRequestCustody("absent")


class _ExactSpawnInserter(Protocol):
    """Broker APIs for exact-ID spawn submission."""

    def insert_messages(self, records: Iterable[tuple[str, str, int]]) -> None:
        """Insert pending messages with exact IDs."""
        ...


def _normalize_broker_target(target: BrokerTarget | str | Path) -> BrokerTarget | str:
    if isinstance(target, Path):
        return str(target)
    return target


def _taskspec_payload_for_spawn(
    taskspec: TaskSpec | Mapping[str, Any],
    *,
    tid: str | None,
    inherited_weft_context: str | None = None,
) -> dict[str, Any]:
    model = (
        taskspec
        if isinstance(taskspec, TaskSpec)
        else decode_taskspec_transport_payload(taskspec, template=True)
    )
    bundle_root = model.get_bundle_root()
    if tid is None:
        payload = model.model_dump(mode="json")
        spec_section = payload.get("spec")
        if (
            inherited_weft_context
            and isinstance(spec_section, dict)
            and not spec_section.get("weft_context")
        ):
            spec_section["weft_context"] = inherited_weft_context
        model = validate_taskspec_payload(
            payload,
            bundle_root=bundle_root,
            template=True,
        )
        return encode_taskspec_transport_payload(model)
    model = validate_taskspec_payload(
        model.model_dump(mode="json"),
        bundle_root=bundle_root,
        resolved_tid=tid,
        inherited_weft_context=inherited_weft_context,
    )
    return encode_taskspec_transport_payload(model)


@contextmanager
def _spawn_broker(
    broker_target: BrokerTarget | str | Path,
    queue_name: str,
    config: Mapping[str, Any] | None,
    broker: Any | None,
) -> Iterator[Any]:
    """Borrow a task connection or own one pre-context lease [SB-0.4]."""
    if broker is not None:
        yield broker
        return
    target = _normalize_broker_target(broker_target)
    resolved_config = resolve_runtime_config(config)
    with (
        BrokerSession.connect(target, config=resolved_config),
        Queue(
            queue_name,
            db_path=target,
            persistent=True,
            config=resolved_config,
        ) as queue,
        queue.get_connection() as opened,
    ):
        yield opened


def generate_spawn_request_timestamp(
    broker_target: BrokerTarget | str | Path,
    *,
    config: Mapping[str, Any] | None = None,
    broker: Any | None = None,
) -> int:
    """Return a broker-valid task timestamp from the spawn-request queue."""

    with _spawn_broker(broker_target, WEFT_SPAWN_REQUESTS_QUEUE, config, broker) as db:
        return int(db.generate_timestamp())


def _write_spawn_request_with_timestamp(
    db: object,
    *,
    queue_name: str,
    message: str,
    timestamp: int,
) -> None:
    """Write a spawn request at its preallocated TID timestamp.

    SimpleBroker's public ``Queue.write()`` API always generates a new
    timestamp. Weft's spawn submission contract needs the queued message
    timestamp to match the externally returned TID, so this path uses
    SimpleBroker's exact-ID insert API.
    """

    if not callable(getattr(db, "insert_messages", None)):
        raise RuntimeError(  # noqa: TRY004 approved [TS-3.1] [RUFF-SUP-263] exception
            "exact-ID spawn request writes require a broker connection with "
            "insert_messages support"
        )

    message_id = int(timestamp)
    inserter = cast(_ExactSpawnInserter, db)
    inserter.insert_messages([(queue_name, message, message_id)])


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
    broker: Any | None = None,
) -> int:
    """Write a manager spawn request and return its authoritative TID.

    Implicit submissions use SimpleBroker's committed ``Queue.write()`` ID.
    Explicit ``tid`` submissions preserve exact-ID insertion for callers whose
    task graph already embeds that identifier.

    Spec: [MF-1], [MANAGER.4]
    """

    submitted_tid, _payload = _submit_spawn_request_with_payload(
        broker_target,
        taskspec=taskspec,
        work_payload=work_payload,
        config=config,
        tid=tid,
        inherited_weft_context=inherited_weft_context,
        seed_start_envelope=seed_start_envelope,
        allow_internal_runtime=allow_internal_runtime,
        spawn_queue_name=spawn_queue_name,
        broker=broker,
    )
    return submitted_tid


def _submit_spawn_request_with_payload(
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
    broker: Any | None = None,
) -> tuple[int, dict[str, Any]]:
    """Commit one request and retain the exact shaped TaskSpec transport.

    The caller records acceptance before binding implicit TID-default routes.
    This is the sole shaping/write path for both return forms.

    Spec: [MF-1], [MF-5], [MANAGER.4]
    """

    if spawn_queue_name not in {
        WEFT_SPAWN_REQUESTS_QUEUE,
        WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE,
    }:
        raise ValueError(f"unsupported spawn queue {spawn_queue_name!r}")

    resolved_tid = str(tid) if tid is not None else None
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

    # Lineage belongs to the shared writer, including trusted internal participants.
    # All ordinary work keeps primary custody even when a client lends its facade.
    identity = get_runtime_identity()
    submitter = None
    if (
        identity is not None
        and canonical_runtime_context(_normalize_broker_target(broker_target))
        == identity.context
    ):
        submitter = submission_lineage(identity)

    message = {
        "taskspec": taskspec_payload,
        "inbox_message": inbox_message,
    }
    if submitter is not None:
        message["submitted_by"] = submitter
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
    message_timestamp = int(resolved_tid) if resolved_tid is not None else None

    with _spawn_broker(broker_target, spawn_queue_name, config, broker) as db:
        if message_timestamp is None:
            return int(db.write(spawn_queue_name, message_json)), taskspec_payload
        _write_spawn_request_with_timestamp(
            db,
            queue_name=spawn_queue_name,
            message=message_json,
            timestamp=message_timestamp,
        )

    return message_timestamp, taskspec_payload


def delete_spawn_request(
    broker_target: BrokerTarget | str | Path,
    *,
    message_timestamp: int,
    config: Mapping[str, Any] | None = None,
    broker: Any | None = None,
) -> bool:
    """Best-effort removal of a queued spawn request after setup failure."""

    try:
        with _spawn_broker(
            broker_target, WEFT_SPAWN_REQUESTS_QUEUE, config, broker
        ) as db:
            return bool(
                db.delete_message_ids(WEFT_SPAWN_REQUESTS_QUEUE, [message_timestamp])
            )
    except (
        BrokerError,
        OSError,
        RuntimeError,
    ):  # pragma: no cover - spawn cleanup best effort
        return False
