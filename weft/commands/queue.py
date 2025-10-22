"""Queue passthrough helpers for CLI commands.

Implements queue operations described in
docs/specifications/10-CLI_Interface.md [CLI-1.1.1] and
docs/specifications/04-SimpleBroker_Integration.md [SB-0.1] – [SB-0.4].
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import cast

from simplebroker.db import BrokerDB
from weft.context import WeftContext, build_context


@dataclass
class QueueMessage:
    body: str
    timestamp: int | None = None

    def as_text(self, include_timestamp: bool) -> str:
        if include_timestamp and self.timestamp is not None:
            return f"{self.timestamp} {self.body}"
        return self.body

    def as_dict(self) -> dict[str, object]:
        return {"message": self.body, "timestamp": self.timestamp}


@dataclass
class QueueInfo:
    name: str
    count: int


def _context(spec_context: str | None = None) -> WeftContext:
    """Resolve a :class:`WeftContext` for queue commands (Spec: [CLI-0.3], [SB-0])."""
    if spec_context is not None:
        return build_context(spec_context=spec_context)

    env_context = os.environ.get("WEFT_CONTEXT")
    if env_context:
        return build_context(spec_context=env_context)

    return build_context(spec_context=os.getcwd())


def read_messages(
    ctx: WeftContext,
    queue_name: str,
    *,
    all_messages: bool = False,
    with_timestamps: bool = False,
) -> list[QueueMessage]:
    """Read messages for CLI consumption (Spec: [CLI-1], [SB-0.1])."""
    queue = ctx.queue(queue_name, persistent=True)

    messages: list[QueueMessage] = []

    if all_messages:
        iterator = queue.read_generator(with_timestamps=with_timestamps)
        for item in iterator:
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


def write_message(ctx: WeftContext, queue_name: str, message: str) -> None:
    """Write a message to a queue (Spec: [CLI-1], [SB-0.1])."""
    queue = ctx.queue(queue_name, persistent=True)
    queue.write(message)


def peek_messages(
    ctx: WeftContext,
    queue_name: str,
    *,
    all_messages: bool = False,
    with_timestamps: bool = False,
) -> list[QueueMessage]:
    """Peek at messages without consuming them (Spec: [CLI-1], [SB-0.1], [SB-0.3])."""
    queue = ctx.queue(queue_name, persistent=True)
    messages: list[QueueMessage] = []

    if all_messages:
        iterator = queue.peek_generator(with_timestamps=with_timestamps)
        for item in iterator:
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


def move_messages(
    ctx: WeftContext, source: str, destination: str, *, limit: int | None = None
) -> int:
    """Move messages between queues via SimpleBroker (Spec: [CLI-1], [SB-0.3])."""
    src_queue = ctx.queue(source, persistent=True)
    moved = src_queue.move_many(
        destination,
        limit=limit or 1000,
        with_timestamps=False,
    )
    return len(moved)


def list_queues(ctx: WeftContext) -> list[QueueInfo]:
    """Enumerate queues for reporting (Spec: [CLI-1], [SB-0.1])."""
    queues: list[QueueInfo] = []
    with BrokerDB(str(ctx.database_path)) as db:
        for name, count in db.list_queues():
            queues.append(QueueInfo(name=name, count=int(count)))
    return queues


def watch_queue(
    ctx: WeftContext,
    queue_name: str,
    *,
    interval: float = 0.5,
    max_messages: int | None = None,
    with_timestamps: bool = False,
) -> Iterator[QueueMessage]:
    """Yield queue messages for `weft queue watch` (Spec: [CLI-1], [SB-0.4])."""
    queue = ctx.queue(queue_name, persistent=True)
    since: int | None = None
    emitted = 0

    while max_messages is None or emitted < max_messages:
        found = False
        iterator = queue.peek_generator(with_timestamps=True, since_timestamp=since)
        for item in iterator:
            body, timestamp = cast(tuple[str, int], item)
            found = True
            since = int(timestamp)
            emitted += 1
            yield QueueMessage(str(body), int(timestamp) if with_timestamps else None)
            if max_messages is not None and emitted >= max_messages:
                break

        if max_messages is not None and emitted >= max_messages:
            break

        if not found:
            time.sleep(interval)


def read_command(
    queue_name: str,
    *,
    all_messages: bool = False,
    with_timestamps: bool = False,
    json_output: bool = False,
    spec_context: str | None = None,
) -> tuple[int, str]:
    """Implement the `weft queue read` command (Spec: [CLI-1], [CLI-1.1.1])."""
    ctx = _context(spec_context)
    messages = read_messages(
        ctx, queue_name, all_messages=all_messages, with_timestamps=with_timestamps
    )

    if json_output:
        payload = json.dumps([m.as_dict() for m in messages], ensure_ascii=False)
    else:
        payload = "\n".join(m.as_text(with_timestamps) for m in messages)

    return 0, payload


def write_command(
    queue_name: str,
    message: str | None,
    *,
    spec_context: str | None = None,
) -> int:
    """Implement the `weft queue write` command (Spec: [CLI-1], [CLI-1.1.1])."""
    ctx = _context(spec_context)
    if message is None:
        data = sys.stdin.read().rstrip("\n")
        if not data:
            raise ValueError("No message provided")
        message = data

    write_message(ctx, queue_name, message)
    return 0


def peek_command(
    queue_name: str,
    *,
    all_messages: bool = False,
    with_timestamps: bool = False,
    json_output: bool = False,
    spec_context: str | None = None,
) -> tuple[int, str]:
    """Implement the `weft queue peek` command (Spec: [CLI-1], [CLI-1.1.1])."""
    ctx = _context(spec_context)
    messages = peek_messages(
        ctx, queue_name, all_messages=all_messages, with_timestamps=with_timestamps
    )

    if json_output:
        payload = json.dumps([m.as_dict() for m in messages], ensure_ascii=False)
    else:
        payload = "\n".join(m.as_text(with_timestamps) for m in messages)

    return 0, payload


def move_command(
    source: str,
    destination: str,
    *,
    limit: int | None = None,
    spec_context: str | None = None,
) -> tuple[int, str]:
    """Implement the `weft queue move` command (Spec: [CLI-1], [CLI-1.1.1], [SB-0.3])."""
    ctx = _context(spec_context)
    count = move_messages(ctx, source, destination, limit=limit)
    return 0, f"Moved {count} message(s)"


def list_command(
    *, spec_context: str | None = None, json_output: bool = False
) -> tuple[int, str]:
    """Implement the `weft queue list` command (Spec: [CLI-1], [CLI-1.1.1])."""
    ctx = _context(spec_context)
    queues = list_queues(ctx)

    if json_output:
        payload = json.dumps(
            [{"queue": info.name, "messages": info.count} for info in queues],
            ensure_ascii=False,
        )
    else:
        lines = [f"{info.name}: {info.count}" for info in queues]
        payload = "\n".join(lines)

    return 0, payload


def watch_command(
    queue_name: str,
    *,
    interval: float = 0.5,
    max_messages: int | None = None,
    with_timestamps: bool = False,
    json_output: bool = False,
    spec_context: str | None = None,
) -> Iterator[str]:
    """Implement the `weft queue watch` command (Spec: [CLI-1], [CLI-1.1.1], [SB-0.4])."""
    ctx = _context(spec_context)
    for message in watch_queue(
        ctx,
        queue_name,
        interval=interval,
        max_messages=max_messages,
        with_timestamps=with_timestamps,
    ):
        if json_output:
            yield json.dumps(message.as_dict(), ensure_ascii=False)
        else:
            yield message.as_text(with_timestamps)


__all__ = [
    "QueueMessage",
    "QueueInfo",
    "read_messages",
    "write_message",
    "peek_messages",
    "move_messages",
    "list_queues",
    "watch_queue",
    "read_command",
    "write_command",
    "peek_command",
    "move_command",
    "list_command",
    "watch_command",
]
