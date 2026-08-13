"""Queue passthrough helpers for CLI commands.

Rather than re-implementing SimpleBroker's CLI surface, these helpers resolve a
Weft context and delegate to :mod:`simplebroker.commands`. This keeps the Weft
CLI in sync with SimpleBroker (minus ``init``, which already exists as
``weft init``) while still respecting Weft configuration and project discovery.

Spec references:
- docs/specifications/04-SimpleBroker_Integration.md [SB-0.1], [SB-0.3], [SB-0.5]
- docs/specifications/10-CLI_Interface.md [CLI-4], [CLI-4.1]
- docs/specifications/14-Python_API_Surfaces.md [PY-2]
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, cast

from simplebroker import format_message_id
from simplebroker.ext import TimestampError, TimestampGenerator
from weft._constants import WEFT_CONTEXT_ENV
from weft._exceptions import CommandExecutionError, CommandUsageError
from weft.commands._boundary import typed_queue_command_errors
from weft.commands.types import (
    CommandStream,
    EndpointResolution,
    QueueAliasRecord,
    QueueBroadcastReceipt,
    QueueDeleteReceipt,
    QueueEntry,
    QueueInfo,
    QueueMoveReceipt,
    QueueMoveResult,
    QueueWriteReceipt,
)
from weft.context import WeftContext, build_context
from weft.core.endpoints import (
    ResolvedEndpoint,
    list_resolved_endpoints,
    normalize_endpoint_name,
    resolve_endpoint,
)
from weft.core.queue_wait import QueueChangeMonitor
from weft.helpers import (
    closing_queue_iterator,
    resolve_broker_max_message_size,
)
from weft.helpers.message_ids import normalize_exact_message_id


@dataclass
class QueueMessage:
    body: str
    timestamp: int | None = None

    def as_text(self, include_timestamp: bool) -> str:
        if include_timestamp and self.timestamp is not None:
            return f"{self.timestamp} {self.body}"
        return self.body

    def as_dict(self) -> dict[str, object]:
        timestamp = (
            format_message_id(self.timestamp) if self.timestamp is not None else None
        )
        return {"message": self.body, "timestamp": timestamp}


@dataclass
class _QueueInfo:
    name: str
    unclaimed: int
    total: int | None = None

    def to_payload(self, include_stats: bool) -> dict[str, int | str]:
        payload: dict[str, int | str] = {
            "queue": self.name,
            "messages": self.unclaimed,
        }

        if include_stats and self.total is not None:
            claimed = max(self.total - self.unclaimed, 0)
            payload["total_messages"] = self.total
            payload["claimed_messages"] = claimed

        return payload


def _context(spec_context: str | None = None) -> WeftContext:
    if spec_context is not None:
        return build_context(spec_context=spec_context)

    env_context = os.environ.get(WEFT_CONTEXT_ENV)
    if env_context:
        return build_context(spec_context=env_context)

    return build_context(spec_context=os.getcwd())


def _read_generator_after(
    queue: Any,
    *,
    with_timestamps: bool,
    after_timestamp: int | None,
    before_timestamp: int | None = None,
) -> Iterator[Any]:
    yield from queue.read_generator(
        with_timestamps=with_timestamps,
        after_timestamp=after_timestamp,
        before_timestamp=before_timestamp,
    )


def _peek_generator_after(
    queue: Any,
    *,
    with_timestamps: bool,
    after_timestamp: int | None,
    before_timestamp: int | None = None,
) -> Iterator[Any]:
    yield from queue.peek_generator(
        with_timestamps=with_timestamps,
        after_timestamp=after_timestamp,
        before_timestamp=before_timestamp,
    )


def _move_generator_after(
    queue: Any,
    destination: str,
    *,
    with_timestamps: bool,
    after_timestamp: int | None,
    before_timestamp: int | None = None,
) -> Iterator[Any]:
    yield from queue.move_generator(
        destination,
        with_timestamps=with_timestamps,
        after_timestamp=after_timestamp,
        before_timestamp=before_timestamp,
    )


def _queue_entry(queue_name: str, message: QueueMessage) -> QueueEntry:
    return QueueEntry(
        queue=queue_name,
        message=message.body,
        timestamp=message.timestamp,
    )


def _endpoint_resolution(record: ResolvedEndpoint) -> EndpointResolution:
    payload = record.to_dict()
    return EndpointResolution(
        name=str(payload["name"]),
        tid=str(payload["tid"]),
        status=str(payload["status"]),
        inbox=str(payload["inbox"]),
        outbox=str(payload["outbox"]),
        ctrl_in=str(payload["ctrl_in"]),
        ctrl_out=str(payload["ctrl_out"]),
        registered_at=(
            int(payload["registered_at"])
            if payload.get("registered_at") is not None
            else None
        ),
        last_seen=(
            int(payload["last_seen"]) if payload.get("last_seen") is not None else None
        ),
        live_candidates=int(payload["live_candidates"]),
        metadata=(
            dict(payload["metadata"])
            if isinstance(payload.get("metadata"), dict)
            else None
        ),
    )


def read_messages(
    ctx: WeftContext,
    queue_name: str,
    *,
    all_messages: bool = False,
    with_timestamps: bool = False,
) -> list[QueueMessage]:
    queue = ctx.queue(queue_name, persistent=True)
    try:
        messages: list[QueueMessage] = []

        if all_messages:
            iterator = queue.read_generator(with_timestamps=with_timestamps)
            with closing_queue_iterator(iterator) as rows:
                for item in rows:
                    if with_timestamps:
                        body, timestamp = cast(tuple[str, int], item)
                        messages.append(QueueMessage(str(body), int(timestamp)))
                    else:
                        text = cast(str, item)
                        messages.append(QueueMessage(text))
        else:
            single_item = queue.read_one(with_timestamps=with_timestamps)
            if single_item is None:
                return []
            if with_timestamps:
                body, timestamp = cast(tuple[str, int], single_item)
                messages.append(QueueMessage(str(body), int(timestamp)))
            else:
                text = cast(str, single_item)
                messages.append(QueueMessage(text))

        return messages
    finally:
        queue.close()


def read_queue(
    ctx: WeftContext,
    queue_name: str,
    *,
    all_messages: bool = False,
    message_id: int | str | None = None,
    after: int | None = None,
    before: int | None = None,
) -> list[QueueEntry]:
    """Read queue messages as structured entries."""

    normalized_message_id = (
        normalize_exact_message_id(message_id) if message_id is not None else None
    )
    queue = ctx.queue(queue_name, persistent=True)
    try:
        entries: list[QueueEntry] = []
        if normalized_message_id is not None:
            item = queue.read_one(
                exact_timestamp=normalized_message_id,
                with_timestamps=True,
            )
            if item is None:
                return []
            body, timestamp = cast(tuple[str, int], item)
            return [
                QueueEntry(
                    queue=queue_name, message=str(body), timestamp=int(timestamp)
                )
            ]

        iterator = _read_generator_after(
            queue,
            with_timestamps=True,
            after_timestamp=after,
            before_timestamp=before,
        )
        with closing_queue_iterator(iterator) as rows:
            for index, item in enumerate(rows):
                body, timestamp = cast(tuple[Any, Any], item)
                entries.append(
                    QueueEntry(
                        queue=queue_name, message=str(body), timestamp=int(timestamp)
                    )
                )
                if not all_messages and index == 0:
                    break
        return entries
    finally:
        queue.close()


def write_message(ctx: WeftContext, queue_name: str, message: str) -> None:
    queue = ctx.queue(queue_name, persistent=True)
    try:
        queue.write(message)
    finally:
        queue.close()


def write_queue(ctx: WeftContext, queue_name: str, message: str) -> QueueWriteReceipt:
    """Write one message to a queue."""

    write_message(ctx, queue_name, message)
    return QueueWriteReceipt(queue=queue_name, message=message)


def write_endpoint(
    ctx: WeftContext,
    endpoint_name: str,
    message: str,
) -> QueueWriteReceipt:
    """Resolve an endpoint and write to its inbox queue."""

    resolved = resolve_endpoint(ctx, endpoint_name)
    if resolved is None:
        normalized = normalize_endpoint_name(endpoint_name)
        raise LookupError(f"No active endpoint named '{normalized}'")
    return write_queue(ctx, resolved.record.inbox, message)


def peek_messages(
    ctx: WeftContext,
    queue_name: str,
    *,
    all_messages: bool = False,
    with_timestamps: bool = False,
) -> list[QueueMessage]:
    queue = ctx.queue(queue_name, persistent=True)
    try:
        messages: list[QueueMessage] = []

        if all_messages:
            iterator = queue.peek_generator(with_timestamps=with_timestamps)
            with closing_queue_iterator(iterator) as rows:
                for item in rows:
                    if with_timestamps:
                        body, timestamp = cast(tuple[str, int], item)
                        messages.append(QueueMessage(str(body), int(timestamp)))
                    else:
                        text = cast(str, item)
                        messages.append(QueueMessage(text))
        else:
            single_item = queue.peek_one(with_timestamps=with_timestamps)
            if single_item is None:
                return []
            if with_timestamps:
                body, timestamp = cast(tuple[str, int], single_item)
                messages.append(QueueMessage(str(body), int(timestamp)))
            else:
                text = cast(str, single_item)
                messages.append(QueueMessage(text))

        return messages
    finally:
        queue.close()


def peek_queue(
    ctx: WeftContext,
    queue_name: str,
    *,
    all_messages: bool = False,
    message_id: int | str | None = None,
    after: int | None = None,
    before: int | None = None,
) -> list[QueueEntry]:
    """Peek queue messages as structured entries."""

    normalized_message_id = (
        normalize_exact_message_id(message_id) if message_id is not None else None
    )
    queue = ctx.queue(queue_name, persistent=True)
    try:
        entries: list[QueueEntry] = []
        if normalized_message_id is not None:
            item = queue.peek_one(
                exact_timestamp=normalized_message_id,
                with_timestamps=True,
            )
            if item is None:
                return []
            body, timestamp = cast(tuple[str, int], item)
            return [
                QueueEntry(
                    queue=queue_name, message=str(body), timestamp=int(timestamp)
                )
            ]

        iterator = _peek_generator_after(
            queue,
            with_timestamps=True,
            after_timestamp=after,
            before_timestamp=before,
        )
        with closing_queue_iterator(iterator) as rows:
            for index, item in enumerate(rows):
                body, timestamp = cast(tuple[Any, Any], item)
                entries.append(
                    QueueEntry(
                        queue=queue_name, message=str(body), timestamp=int(timestamp)
                    )
                )
                if not all_messages and index == 0:
                    break
        return entries
    finally:
        queue.close()


def move_messages(
    ctx: WeftContext,
    source: str,
    destination: str,
    *,
    limit: int | None = None,
    after: int | None = None,
    before: int | None = None,
) -> int:
    src_queue = ctx.queue(source, persistent=True)
    try:
        moved = src_queue.move_many(
            destination,
            limit=limit or 1000,
            with_timestamps=False,
            after_timestamp=after,
            before_timestamp=before,
        )
        return len(moved)
    finally:
        src_queue.close()


def move_queue_messages(
    ctx: WeftContext,
    source: str,
    destination: str,
    *,
    limit: int | None = None,
    all_messages: bool = False,
    message_id: int | str | None = None,
    after: int | None = None,
    before: int | None = None,
) -> QueueMoveReceipt:
    """Move queue messages and return a structured receipt."""

    normalized_message_id = (
        normalize_exact_message_id(message_id) if message_id is not None else None
    )
    queue = ctx.queue(source, persistent=True)
    try:
        if normalized_message_id is not None:
            iterator = queue.move_generator(
                destination,
                with_timestamps=True,
                exact_timestamp=normalized_message_id,
            )
            with closing_queue_iterator(iterator) as rows:
                moved = list(rows)
            return QueueMoveReceipt(
                source=source,
                destination=destination,
                moved_count=len(moved),
            )
        if limit is not None:
            moved_count = move_messages(
                ctx,
                source,
                destination,
                limit=limit,
                after=after,
                before=before,
            )
            return QueueMoveReceipt(
                source=source,
                destination=destination,
                moved_count=moved_count,
            )
        if not all_messages:
            iterator = _move_generator_after(
                queue,
                destination,
                with_timestamps=True,
                after_timestamp=after,
                before_timestamp=before,
            )
            moved_count = 0
            with closing_queue_iterator(iterator) as rows:
                for _item in rows:
                    moved_count += 1
                    break
            return QueueMoveReceipt(
                source=source,
                destination=destination,
                moved_count=moved_count,
            )
        iterator = _move_generator_after(
            queue,
            destination,
            with_timestamps=True,
            after_timestamp=after,
            before_timestamp=before,
        )
        with closing_queue_iterator(iterator) as rows:
            moved = list(rows)
        return QueueMoveReceipt(
            source=source,
            destination=destination,
            moved_count=len(moved),
        )
    finally:
        queue.close()


def list_queues(
    ctx: WeftContext,
    *,
    include_stats: bool = False,
    pattern: str | None = None,
    prefix: str | None = None,
) -> list[_QueueInfo]:
    if pattern is not None and prefix is not None:
        raise ValueError("pattern and prefix cannot both be specified")

    queues: list[_QueueInfo] = []
    with ctx.broker() as db:
        stats = db.list_queue_stats(prefix=prefix, pattern=pattern)

    for item in stats:
        unclaimed_count = int(item.pending)
        total_count = int(item.total)

        if not include_stats and unclaimed_count <= 0:
            continue

        queues.append(
            _QueueInfo(
                name=item.queue,
                unclaimed=unclaimed_count,
                total=total_count,
            )
        )
    return queues


def list_queue_infos(
    ctx: WeftContext,
    *,
    pattern: str | None = None,
    prefix: str | None = None,
    include_stats: bool = False,
    include_endpoints: bool = False,
) -> list[QueueInfo]:
    """Return queue or endpoint listings as structured rows."""

    if pattern is not None and prefix is not None:
        raise ValueError("pattern and prefix cannot both be specified")

    if include_endpoints:
        return [
            QueueInfo(
                name=record.record.name,
                messages=0,
                is_endpoint=True,
            )
            for record in list_resolved_endpoints(ctx, pattern=pattern)
        ]
    return [
        QueueInfo(
            name=item.name,
            messages=item.unclaimed,
            total_messages=item.total if include_stats else None,
            claimed_messages=(
                max(item.total - item.unclaimed, 0)
                if include_stats and item.total is not None
                else None
            ),
        )
        for item in list_queues(
            ctx,
            include_stats=include_stats,
            pattern=pattern,
            prefix=prefix,
        )
    ]


def queue_exists(ctx: WeftContext, queue_name: str) -> bool:
    """Return whether a queue exists, including queues with claimed rows."""

    with ctx.broker() as db:
        return bool(db.queue_exists(queue_name))


def queue_info(ctx: WeftContext, queue_name: str) -> QueueInfo:
    """Return detailed queue counts for one queue."""

    with ctx.broker() as db:
        stats = db.get_queue_stat(queue_name)
    return QueueInfo(
        name=stats.queue,
        messages=int(stats.pending),
        total_messages=int(stats.total),
        claimed_messages=int(stats.claimed),
    )


def watch_queue(
    ctx: WeftContext,
    queue_name: str,
    *,
    interval: float = 0.5,
    max_messages: int | None = None,
    with_timestamps: bool = False,
    json_output: bool = False,
    peek: bool = False,
    after: int | None = None,
    before: int | None = None,
    move_to: str | None = None,
) -> Iterator[QueueMessage]:
    queue = ctx.queue(queue_name, persistent=True)
    watch_queue = ctx.queue(queue_name, persistent=True)
    monitor: QueueChangeMonitor | None = None
    try:
        monitor = QueueChangeMonitor([watch_queue], config=ctx.config)
        emitted = 0
        last_timestamp = after

        while max_messages is None or emitted < max_messages:
            if move_to:
                generator = _move_generator_after(
                    queue,
                    move_to,
                    with_timestamps=True,
                    after_timestamp=last_timestamp,
                    before_timestamp=before,
                )
            elif peek:
                generator = _peek_generator_after(
                    queue,
                    with_timestamps=True,
                    after_timestamp=last_timestamp,
                    before_timestamp=before,
                )
            else:
                generator = _read_generator_after(
                    queue,
                    with_timestamps=True,
                    after_timestamp=last_timestamp,
                    before_timestamp=before,
                )

            found = False
            try:
                for item in generator:
                    body, timestamp = cast(tuple[Any, Any], item)
                    found = True
                    last_timestamp = int(timestamp)
                    emitted += 1
                    yield QueueMessage(
                        str(body),
                        int(timestamp) if with_timestamps or json_output else None,
                    )
                    if max_messages is not None and emitted >= max_messages:
                        break
            finally:
                close_generator = getattr(generator, "close", None)
                if callable(close_generator):
                    close_generator()

            if max_messages is not None and emitted >= max_messages:
                break

            if not found:
                monitor.wait(interval)
    finally:
        if monitor is not None:
            monitor.close()
        watch_queue.close()
        queue.close()


def watch_queue_entries(
    ctx: WeftContext,
    queue_name: str,
    *,
    limit: int | None = None,
    interval: float = 0.5,
    peek: bool = False,
    after: int | None = None,
    before: int | None = None,
    move_to: str | None = None,
) -> Iterator[QueueEntry]:
    """Yield queue activity as structured entries."""

    for message in watch_queue(
        ctx,
        queue_name,
        interval=interval,
        max_messages=limit,
        with_timestamps=True,
        json_output=False,
        peek=peek,
        after=after,
        before=before,
        move_to=move_to,
    ):
        yield _queue_entry(queue_name, message)


def resolve_queue_endpoint(
    ctx: WeftContext,
    endpoint_name: str,
) -> EndpointResolution | None:
    """Resolve a runtime endpoint to its live queue surface."""

    resolved = resolve_endpoint(ctx, endpoint_name)
    if resolved is None:
        return None
    return _endpoint_resolution(resolved)


def delete_queue_messages(
    ctx: WeftContext,
    queue_name: str | None = None,
    *,
    all_queues: bool = False,
    message_id: int | str | None = None,
) -> QueueDeleteReceipt:
    """Delete a queue, all queues, or one exact message."""

    if all_queues and queue_name is not None:
        raise ValueError("queue_name cannot be used with all_queues")

    if message_id is not None:
        if all_queues:
            raise ValueError("message_id cannot be used with all_queues")
        if queue_name is None:
            raise ValueError("queue_name is required when message_id is used")

        normalized_message_id = normalize_exact_message_id(message_id)
        queue = ctx.queue(queue_name, persistent=True)
        try:
            deleted = queue.delete(message_id=normalized_message_id)
            return QueueDeleteReceipt(
                queue=queue_name,
                deleted_count=1 if deleted else 0,
                all_queues=False,
            )
        finally:
            queue.close()

    if queue_name is None and not all_queues:
        raise ValueError("queue_name is required unless all_queues=True")

    target_queue = None if all_queues else queue_name
    with ctx.broker() as db:
        deleted_count = int(db.delete(target_queue))
    return QueueDeleteReceipt(
        queue=target_queue,
        deleted_count=deleted_count,
        all_queues=all_queues,
    )


def broadcast(
    ctx: WeftContext,
    message: str,
    *,
    pattern: str | None = None,
) -> QueueBroadcastReceipt:
    """Broadcast one message to matching queues."""

    with ctx.broker() as db:
        target_count = int(db.broadcast(message, pattern=pattern))
    return QueueBroadcastReceipt(pattern=pattern, target_count=target_count)


def add_alias(ctx: WeftContext, alias: str, target: str) -> QueueAliasRecord:
    """Create or update one queue alias."""

    with ctx.broker() as db:
        db.add_alias(alias, target)
    return QueueAliasRecord(alias=alias, target=target)


def list_alias_records(
    ctx: WeftContext,
    *,
    target: str | None = None,
) -> list[QueueAliasRecord]:
    """Return queue alias rows."""

    with ctx.broker() as db:
        aliases = list(db.list_aliases())
    return [
        QueueAliasRecord(alias=alias, target=alias_target)
        for alias, alias_target in aliases
        if target is None or alias_target == target
    ]


def remove_alias(ctx: WeftContext, alias: str) -> None:
    """Remove one queue alias."""

    with ctx.broker() as db:
        db.remove_alias(alias)


def _public_command_context() -> WeftContext:
    """Resolve the current command context or raise the public typed error."""

    try:
        return _context()
    except Exception as exc:
        raise CommandExecutionError(f"failed to resolve queue context: {exc}") from exc


def _selection_timestamp(value: int | str | None, *, option: str) -> int | None:
    """Normalize one parsed selection timestamp."""

    if value is None:
        return None
    try:
        return int(TimestampGenerator.validate(str(value)))
    except (TimestampError, ValueError, TypeError) as exc:
        raise CommandUsageError(f"invalid {option} timestamp: {exc}") from exc


def _exact_message_id(value: int | str | None) -> int | None:
    """Normalize an exact message ID for a public queue command."""

    if value is None:
        return None
    try:
        return normalize_exact_message_id(value)
    except (TypeError, ValueError) as exc:
        raise CommandUsageError(f"invalid message ID: {exc}") from exc


def _canonical_queue_operand(ctx: WeftContext, name: str) -> str:
    """Resolve the SimpleBroker ``@alias`` syntax at the command boundary."""

    if not name.startswith("@"):
        return name
    with ctx.broker() as broker:
        return str(broker.canonicalize_queue(name))


def _require_message_within_limit(context: WeftContext, message: str) -> None:
    """Reject text larger than the resolved broker context accepts."""

    maximum = resolve_broker_max_message_size(context.config)
    if len(message.encode("utf-8")) > maximum:
        raise CommandUsageError(f"Message exceeds maximum size of {maximum} bytes")


@typed_queue_command_errors
def cmd_queue_read(
    name: str,
    *,
    all: bool = False,
    message: int | str | None = None,
    after: int | str | None = None,
    before: int | str | None = None,
) -> tuple[QueueEntry, ...]:
    """Read structured entries from one queue.

    Args:
        name: Source queue name.
        all: Consume every matching entry instead of at most one.
        message: Optional exact message ID.
        after: Optional exclusive lower message-ID bound.
        before: Optional exclusive upper message-ID bound.

    Returns:
        Matching entries in broker order.

    Raises:
        CommandUsageError: If selection options conflict or an ID is invalid.
        CommandExecutionError: If context or broker access fails.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2]
    """

    if message is not None and (all or after is not None or before is not None):
        raise CommandUsageError("message cannot be used with all, after, or before")
    context = _public_command_context()
    return tuple(
        read_queue(
            context,
            _canonical_queue_operand(context, name),
            all_messages=all,
            message_id=_exact_message_id(message),
            after=_selection_timestamp(after, option="after"),
            before=_selection_timestamp(before, option="before"),
        )
    )


@typed_queue_command_errors
def cmd_queue_write(
    queue_name: str,
    message: str | None = None,
    *,
    endpoint: str | None = None,
) -> QueueWriteReceipt:
    """Write explicit text to a queue or named endpoint.

    With ``endpoint``, ``queue_name`` carries the CLI overload's sole
    positional message and ``message`` must be omitted. Process stdin is never
    read here.

    Args:
        queue_name: Queue name, or message text when ``endpoint`` is set.
        message: Explicit message text for a direct queue write.
        endpoint: Optional named endpoint to resolve before writing.

    Returns:
        The resolved destination and submitted message.

    Raises:
        CommandUsageError: If explicit message input is missing or conflicts.
        CommandExecutionError: If resolution or broker writing fails.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2]
    """

    if endpoint is not None:
        if message is not None:
            raise CommandUsageError(
                "when endpoint is used, provide at most one positional message"
            )
        context = _public_command_context()
        _require_message_within_limit(context, queue_name)
        return write_endpoint(context, endpoint, queue_name)
    if message is None:
        raise CommandUsageError("message is required")
    context = _public_command_context()
    _require_message_within_limit(context, message)
    return write_queue(
        context,
        _canonical_queue_operand(context, queue_name),
        message,
    )


@typed_queue_command_errors
def cmd_queue_peek(
    name: str,
    *,
    all: bool = False,
    message: int | str | None = None,
    after: int | str | None = None,
    before: int | str | None = None,
) -> tuple[QueueEntry, ...]:
    """Peek structured entries without consuming them.

    Args mirror :func:`cmd_queue_read`.

    Returns:
        Matching entries in broker order.

    Raises:
        CommandUsageError: If selection options conflict or an ID is invalid.
        CommandExecutionError: If context or broker access fails.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2]
    """

    if message is not None and (all or after is not None or before is not None):
        raise CommandUsageError("message cannot be used with all, after, or before")
    context = _public_command_context()
    return tuple(
        peek_queue(
            context,
            _canonical_queue_operand(context, name),
            all_messages=all,
            message_id=_exact_message_id(message),
            after=_selection_timestamp(after, option="after"),
            before=_selection_timestamp(before, option="before"),
        )
    )


def _move_queue_entries(
    ctx: WeftContext,
    source: str,
    destination: str,
    *,
    limit: int | None,
    all_messages: bool,
    message_id: int | None,
    after: int | None,
    before: int | None,
) -> QueueMoveResult:
    """Move and retain the exact ordered entry set for the public result."""

    queue = ctx.queue(source, persistent=True)
    try:
        if message_id is not None:
            iterator = queue.move_generator(
                destination,
                with_timestamps=True,
                exact_timestamp=message_id,
            )
        elif limit is not None:
            moved = queue.move_many(
                destination,
                limit=limit,
                with_timestamps=True,
                after_timestamp=after,
                before_timestamp=before,
            )
            entries = tuple(
                QueueEntry(source, str(item[0]), int(item[1]))
                for item in cast(list[tuple[Any, Any]], moved)
            )
            return QueueMoveResult(source, destination, entries, len(entries))
        else:
            iterator = _move_generator_after(
                queue,
                destination,
                with_timestamps=True,
                after_timestamp=after,
                before_timestamp=before,
            )

        entries_list: list[QueueEntry] = []
        with closing_queue_iterator(iterator) as rows:
            for item in rows:
                body, timestamp = cast(tuple[Any, Any], item)
                entries_list.append(QueueEntry(source, str(body), int(timestamp)))
                if message_id is None and not all_messages:
                    break
        entries = tuple(entries_list)
        return QueueMoveResult(source, destination, entries, len(entries))
    finally:
        queue.close()


@typed_queue_command_errors
def cmd_queue_move(
    source: str,
    destination: str,
    *,
    limit: int | None = None,
    all: bool = False,
    message: int | str | None = None,
    after: int | str | None = None,
    before: int | str | None = None,
) -> QueueMoveResult:
    """Move queue entries and return the exact ordered moved set.

    Args:
        source: Source queue name.
        destination: Destination queue name.
        limit: Optional maximum number of entries to move.
        all: Move every matching entry when no explicit limit is set.
        message: Optional exact message ID.
        after: Optional exclusive lower message-ID bound.
        before: Optional exclusive upper message-ID bound.

    Returns:
        Source, destination, and exact entries moved in broker order.

    Raises:
        CommandUsageError: If selection options conflict or are invalid.
        CommandExecutionError: If context or broker access fails.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2]
    """

    if limit is not None and limit < 0:
        raise CommandUsageError("limit must be non-negative")
    if message is not None and (
        limit is not None or all or after is not None or before is not None
    ):
        raise CommandUsageError(
            "message cannot be used with limit, all, after, or before"
        )
    context = _public_command_context()
    canonical_source = _canonical_queue_operand(context, source)
    canonical_destination = _canonical_queue_operand(context, destination)
    if canonical_source == canonical_destination:
        raise CommandUsageError("source and destination queues cannot be the same")
    return _move_queue_entries(
        context,
        canonical_source,
        canonical_destination,
        limit=limit,
        all_messages=all,
        message_id=_exact_message_id(message),
        after=_selection_timestamp(after, option="after"),
        before=_selection_timestamp(before, option="before"),
    )


@typed_queue_command_errors
def cmd_queue_list(
    *,
    stats: bool = False,
    endpoints: bool = False,
    pattern: str | None = None,
    prefix: str | None = None,
) -> tuple[QueueInfo, ...] | tuple[EndpointResolution, ...]:
    """List queues or resolved endpoints as structured rows.

    Args:
        stats: Include total and claimed queue counts.
        endpoints: List canonical live endpoints instead of raw queues.
        pattern: Optional fnmatch-style filter.
        prefix: Optional literal queue-name prefix.

    Returns:
        Queue rows, or endpoint rows when ``endpoints`` is true.

    Raises:
        CommandUsageError: If filters or modes conflict.
        CommandExecutionError: If context or broker access fails.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2]
    """

    if pattern is not None and prefix is not None:
        raise CommandUsageError("pattern and prefix cannot be used together")
    if endpoints and stats:
        raise CommandUsageError("stats is not supported with endpoints")
    if endpoints and prefix is not None:
        raise CommandUsageError("prefix is not supported with endpoints")
    context = _public_command_context()
    if endpoints:
        return tuple(
            _endpoint_resolution(record)
            for record in list_resolved_endpoints(context, pattern=pattern)
        )
    return tuple(
        list_queue_infos(
            context,
            pattern=pattern,
            prefix=prefix,
            include_stats=stats,
        )
    )


@typed_queue_command_errors
def cmd_queue_exists(name: str) -> bool:
    """Return whether ``name`` currently exists.

    Raises:
        CommandUsageError: If the queue name is invalid.
        CommandExecutionError: If context or broker access fails.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2]
    """

    context = _public_command_context()
    return queue_exists(context, _canonical_queue_operand(context, name))


@typed_queue_command_errors
def cmd_queue_stats(name: str) -> QueueInfo:
    """Return one queue's structured counts.

    Raises:
        CommandUsageError: If the queue name is invalid.
        CommandExecutionError: If context or broker access fails.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2]
    """

    context = _public_command_context()
    return queue_info(context, _canonical_queue_operand(context, name))


@typed_queue_command_errors
def cmd_queue_resolve(endpoint_name: str) -> EndpointResolution:
    """Resolve one active named endpoint.

    Raises:
        CommandUsageError: If the endpoint name is invalid.
        CommandExecutionError: If no live endpoint exists or lookup fails.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2]
    """

    resolved = resolve_queue_endpoint(_public_command_context(), endpoint_name)
    if resolved is None:
        try:
            normalized = normalize_endpoint_name(endpoint_name)
        except ValueError as exc:
            raise CommandUsageError(str(exc)) from exc
        raise CommandExecutionError(f"No active endpoint named '{normalized}'")
    return resolved


@typed_queue_command_errors
def cmd_queue_watch(
    name: str,
    *,
    limit: int | None = None,
    interval: float = 0.5,
    peek: bool = False,
    after: int | str | None = None,
    move: str | None = None,
) -> CommandStream[QueueEntry]:
    """Return a closable stream of structured queue entries.

    Args:
        name: Queue to observe.
        limit: Optional maximum emitted entry count.
        interval: Maximum polling interval in seconds.
        peek: Observe without consuming entries.
        after: Optional exclusive lower message-ID bound.
        move: Optional destination that receives observed entries.

    Returns:
        A closable structured entry stream.

    Raises:
        CommandUsageError: If stream modes or values conflict.
        CommandExecutionError: If setup or iteration fails.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2]
    """

    if limit is not None and limit < 0:
        raise CommandUsageError("limit must be non-negative")
    if interval < 0:
        raise CommandUsageError("interval must be non-negative")
    if peek and move is not None:
        raise CommandUsageError("peek cannot be used with move")
    if move is not None and after is not None:
        raise CommandUsageError("move cannot be used with after")
    context = _public_command_context()
    after_timestamp = _selection_timestamp(after, option="after")
    canonical_name = _canonical_queue_operand(context, name)
    canonical_move = (
        _canonical_queue_operand(context, move) if move is not None else None
    )
    source = watch_queue_entries(
        context,
        canonical_name,
        limit=limit,
        interval=interval,
        peek=peek,
        after=after_timestamp,
        move_to=canonical_move,
    )

    def _stream() -> Iterator[QueueEntry]:
        try:
            yield from source
        finally:
            close = getattr(source, "close", None)
            if callable(close):
                close()

    return cast(CommandStream[QueueEntry], _stream())


@typed_queue_command_errors
def cmd_queue_delete(
    name: str | None = None,
    *,
    all: bool = False,
    message: int | str | None = None,
) -> QueueDeleteReceipt:
    """Delete one message, one queue, or all queues.

    Args:
        name: Queue name for queue or exact-message deletion.
        all: Delete messages from every queue.
        message: Optional exact message ID.

    Returns:
        Counts and exact target details for the deletion.

    Raises:
        CommandUsageError: If target modes conflict or an ID is invalid.
        CommandExecutionError: If context or broker access fails.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2]
    """

    if all and name is not None:
        raise CommandUsageError("all cannot be used with a queue name")
    if message is not None and all:
        raise CommandUsageError("message cannot be used with all")
    if message is not None and name is None:
        raise CommandUsageError("queue name is required when message is used")
    if name is None and not all:
        raise CommandUsageError("queue name is required unless all=True")
    context = _public_command_context()
    canonical_name = (
        _canonical_queue_operand(context, name) if name is not None else None
    )
    exact_message = _exact_message_id(message)
    if exact_message is not None:
        receipt = delete_queue_messages(
            context,
            canonical_name,
            message_id=exact_message,
        )
        return QueueDeleteReceipt(
            queue=receipt.queue,
            deleted_count=receipt.deleted_count,
            queues_deleted=0,
            all_queues=False,
            exact_message=format_message_id(exact_message),
        )
    with context.broker() as db:
        existing = {str(item.queue) for item in db.list_queue_stats()}
    receipt = delete_queue_messages(context, canonical_name, all_queues=all)
    queues_deleted = (
        len(existing) if all else int(cast(str, canonical_name) in existing)
    )
    return QueueDeleteReceipt(
        queue=receipt.queue,
        deleted_count=receipt.deleted_count,
        queues_deleted=queues_deleted,
        all_queues=all,
        exact_message=None,
    )


@typed_queue_command_errors
def cmd_queue_broadcast(
    message: str | None = None,
    *,
    pattern: str | None = None,
) -> QueueBroadcastReceipt:
    """Broadcast explicit text to matching queues.

    Args:
        message: Explicit message text; this function never reads stdin.
        pattern: Optional fnmatch-style queue filter.

    Returns:
        The filter and number of destinations written.

    Raises:
        CommandUsageError: If message input or the pattern is invalid.
        CommandExecutionError: If context or broker access fails.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2]
    """

    if message is None:
        raise CommandUsageError("message is required")
    context = _public_command_context()
    _require_message_within_limit(context, message)
    return broadcast(context, message, pattern=pattern)


@typed_queue_command_errors
def cmd_queue_alias_add(alias: str, target: str) -> QueueAliasRecord:
    """Create or replace one queue alias and return it.

    Raises:
        CommandUsageError: If the alias or target is invalid.
        CommandExecutionError: If context or broker access fails.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2]
    """

    return add_alias(_public_command_context(), alias, target)


@typed_queue_command_errors
def cmd_queue_alias_list(*, target: str | None = None) -> tuple[QueueAliasRecord, ...]:
    """List queue aliases, optionally filtered by target.

    Raises:
        CommandUsageError: If the target name is invalid.
        CommandExecutionError: If context or broker access fails.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2]
    """

    return tuple(list_alias_records(_public_command_context(), target=target))


@typed_queue_command_errors
def cmd_queue_alias_remove(alias: str) -> QueueAliasRecord:
    """Remove and return one queue alias record.

    Raises:
        CommandUsageError: If the alias is invalid.
        CommandExecutionError: If the alias is absent or broker access fails.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2]
    """

    context = _public_command_context()
    existing = next(
        (record for record in list_alias_records(context) if record.alias == alias),
        None,
    )
    if existing is None:
        raise CommandExecutionError(f"Queue alias '{alias}' not found")
    remove_alias(context, alias)
    return existing


__all__ = [  # noqa: RUF022 approved [TS-3.1] [RUFF-SUP-246] exception
    "QueueMessage",
    "read_messages",
    "write_message",
    "peek_messages",
    "move_messages",
    "list_queues",
    "watch_queue",
    "cmd_queue_alias_add",
    "cmd_queue_alias_list",
    "cmd_queue_alias_remove",
    "cmd_queue_broadcast",
    "cmd_queue_delete",
    "cmd_queue_exists",
    "cmd_queue_list",
    "cmd_queue_move",
    "cmd_queue_peek",
    "cmd_queue_read",
    "cmd_queue_resolve",
    "cmd_queue_stats",
    "cmd_queue_watch",
    "cmd_queue_write",
]
