"""Queue passthrough helpers for CLI commands.

Rather than re-implementing SimpleBroker's CLI surface, these helpers resolve a
Weft context and delegate to :mod:`simplebroker.commands`. This keeps the Weft
CLI in sync with SimpleBroker (minus ``init``, which already exists as
``weft init``) while still respecting Weft configuration and project discovery.
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any, cast

from simplebroker import commands as sb_commands
from simplebroker._timestamp import TimestampGenerator
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

    env_context = os.environ.get("WEFT_CONTEXT")
    if env_context:
        return build_context(spec_context=env_context)

    return build_context(spec_context=os.getcwd())


def _run_simplebroker_command(
    fn: Callable[..., int], *args: object, **kwargs: object
) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = fn(*args, **kwargs)
    return exit_code, stdout.getvalue(), stderr.getvalue()


def read_messages(
    ctx: WeftContext,
    queue_name: str,
    *,
    all_messages: bool = False,
    with_timestamps: bool = False,
) -> list[QueueMessage]:
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
    queue = ctx.queue(queue_name, persistent=True)
    queue.write(message)


def peek_messages(
    ctx: WeftContext,
    queue_name: str,
    *,
    all_messages: bool = False,
    with_timestamps: bool = False,
) -> list[QueueMessage]:
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
    ctx: WeftContext,
    source: str,
    destination: str,
    *,
    limit: int | None = None,
) -> int:
    src_queue = ctx.queue(source, persistent=True)
    moved = src_queue.move_many(
        destination,
        limit=limit or 1000,
        with_timestamps=False,
    )
    return len(moved)


def list_queues(
    ctx: WeftContext,
    *,
    include_stats: bool = False,
    pattern: str | None = None,
) -> list[QueueInfo]:
    queues: list[QueueInfo] = []
    with BrokerDB(str(ctx.database_path)) as db:
        stats = db.get_queue_stats()

    for name, unclaimed, total in stats:
        if pattern and not fnmatchcase(name, pattern):
            continue

        unclaimed_count = int(unclaimed)
        total_count = int(total)

        if not include_stats and unclaimed_count <= 0:
            continue

        queues.append(
            QueueInfo(
                name=name,
                unclaimed=unclaimed_count,
                total=total_count,
            )
        )
    return queues


def watch_queue(
    ctx: WeftContext,
    queue_name: str,
    *,
    interval: float = 0.5,
    max_messages: int | None = None,
    with_timestamps: bool = False,
    json_output: bool = False,
    peek: bool = False,
    since: int | None = None,
    move_to: str | None = None,
) -> Iterator[QueueMessage]:
    queue = ctx.queue(queue_name, persistent=True)
    emitted = 0
    last_timestamp = since

    while max_messages is None or emitted < max_messages:
        if move_to:
            generator = queue.move_generator(
                move_to,
                with_timestamps=True,
                since_timestamp=last_timestamp,
            )
        elif peek:
            generator = queue.peek_generator(
                with_timestamps=True,
                since_timestamp=last_timestamp,
            )
        else:
            generator = queue.read_generator(
                with_timestamps=True,
                since_timestamp=last_timestamp,
            )

        found = False
        for item in generator:
            body, timestamp = cast(tuple[Any, Any], item)
            found = True
            last_timestamp = int(timestamp)
            emitted += 1
            yield QueueMessage(
                str(body), int(timestamp) if with_timestamps or json_output else None
            )
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
    message_id: str | None = None,
    since: str | None = None,
    spec_context: str | None = None,
) -> tuple[int, str, str]:
    ctx = _context(spec_context)
    return _run_simplebroker_command(
        sb_commands.cmd_read,
        str(ctx.database_path),
        queue_name,
        all_messages=all_messages,
        json_output=json_output,
        show_timestamps=with_timestamps,
        since_str=since,
        message_id_str=message_id,
    )


def write_command(queue_name: str, message: str | None) -> tuple[int, str, str]:
    ctx = _context()
    payload = message if message is not None else "-"
    return _run_simplebroker_command(
        sb_commands.cmd_write, str(ctx.database_path), queue_name, payload
    )


def peek_command(
    queue_name: str,
    *,
    all_messages: bool = False,
    with_timestamps: bool = False,
    json_output: bool = False,
    message_id: str | None = None,
    since: str | None = None,
    spec_context: str | None = None,
) -> tuple[int, str, str]:
    ctx = _context(spec_context)
    return _run_simplebroker_command(
        sb_commands.cmd_peek,
        str(ctx.database_path),
        queue_name,
        all_messages=all_messages,
        json_output=json_output,
        show_timestamps=with_timestamps,
        since_str=since,
        message_id_str=message_id,
    )


def move_command(
    source_queue: str,
    destination_queue: str,
    *,
    limit: int | None = None,
    all_messages: bool = False,
    json_output: bool = False,
    with_timestamps: bool = False,
    message_id: str | None = None,
    since: str | None = None,
    spec_context: str | None = None,
) -> tuple[int, str, str]:
    ctx = _context(spec_context)

    if limit is not None:
        moved = move_messages(ctx, source_queue, destination_queue, limit=limit)
        if moved == 0:
            return 2, "", ""

        lines: list[str] = [
            f"Moved {moved} messages from {source_queue} to {destination_queue}"
        ]

        if json_output or with_timestamps:
            dest_queue = ctx.queue(destination_queue, persistent=True)
            iterator = dest_queue.peek_generator(with_timestamps=True)
            payload_lines: list[str] = []
            count = 0
            for item in iterator:
                body, timestamp = cast(tuple[Any, Any], item)
                if json_output:
                    payload_lines.append(
                        json.dumps(
                            {"message": body, "timestamp": timestamp},
                            ensure_ascii=False,
                        )
                    )
                elif with_timestamps:
                    payload_lines.append(f"{timestamp}\t{body}")
                count += 1
                if count >= moved:
                    break
            lines.append("\n".join(payload_lines))

        return 0, "\n".join(filter(None, lines)), ""

    return _run_simplebroker_command(
        sb_commands.cmd_move,
        str(ctx.database_path),
        source_queue,
        destination_queue,
        all_messages=all_messages,
        json_output=json_output,
        show_timestamps=with_timestamps,
        message_id_str=message_id,
        since_str=since,
    )


def list_command(
    *,
    json_output: bool = False,
    stats: bool = False,
    pattern: str | None = None,
    spec_context: str | None = None,
) -> tuple[int, str, str]:
    ctx = _context(spec_context)

    if json_output:
        queues = list_queues(ctx, include_stats=stats, pattern=pattern)
        payload = json.dumps(
            [info.to_payload(include_stats=stats) for info in queues],
            ensure_ascii=False,
        )
        return 0, payload, ""

    return _run_simplebroker_command(
        sb_commands.cmd_list,
        str(ctx.database_path),
        show_stats=stats,
        pattern=pattern,
    )


def delete_command(
    queue_name: str | None,
    *,
    delete_all: bool,
    message_id: str | None,
    spec_context: str | None = None,
) -> tuple[int, str, str]:
    ctx = _context(spec_context)
    target_queue = None if delete_all else queue_name
    return _run_simplebroker_command(
        sb_commands.cmd_delete,
        str(ctx.database_path),
        target_queue,
        message_id_str=message_id,
    )


def broadcast_command(
    message: str | None,
    *,
    pattern: str | None = None,
    spec_context: str | None = None,
) -> tuple[int, str, str]:
    ctx = _context(spec_context)
    payload = message if message is not None else "-"
    return _run_simplebroker_command(
        sb_commands.cmd_broadcast, str(ctx.database_path), payload, pattern=pattern
    )


def watch_command(
    queue_name: str,
    *,
    limit: int | None,
    interval: float,
    with_timestamps: bool,
    json_output: bool,
    peek: bool,
    since: str | None,
    quiet: bool,
    move_to: str | None,
    spec_context: str | None = None,
) -> tuple[int, str, str]:
    ctx = _context(spec_context)

    if limit is None:
        exit_code = sb_commands.cmd_watch(
            str(ctx.database_path),
            queue_name,
            peek=peek,
            json_output=json_output,
            show_timestamps=with_timestamps,
            since_str=since,
            quiet=quiet,
            move_to=move_to,
        )
        return exit_code, "", ""

    try:
        since_timestamp = (
            TimestampGenerator.validate(since) if since is not None else None
        )
    except ValueError as exc:
        return 1, "", str(exc)

    if not quiet:
        mode = "peek" if peek else "consume"
        if move_to:
            mode = f"move to {move_to}"
        print(
            f"Watching queue '{queue_name}' ({mode} mode)...",
            file=sys.stderr,
            flush=True,
        )

    for message in watch_queue(
        ctx,
        queue_name,
        interval=interval,
        max_messages=limit,
        with_timestamps=with_timestamps,
        json_output=json_output,
        peek=peek,
        since=since_timestamp,
        move_to=move_to,
    ):
        if json_output:
            payload = json.dumps(message.as_dict(), ensure_ascii=False)
        else:
            payload = message.as_text(with_timestamps)
        print(payload, flush=True)

    return 0, "", ""


def alias_add_command(
    alias: str,
    target: str,
    *,
    quiet: bool = False,
    spec_context: str | None = None,
) -> tuple[int, str, str]:
    ctx = _context(spec_context)
    return _run_simplebroker_command(
        sb_commands.cmd_alias_add,
        str(ctx.database_path),
        alias,
        target,
        quiet=quiet,
    )


def alias_list_command(
    *,
    target: str | None = None,
    spec_context: str | None = None,
) -> tuple[int, str, str]:
    ctx = _context(spec_context)
    return _run_simplebroker_command(
        sb_commands.cmd_alias_list,
        str(ctx.database_path),
        target=target,
    )


def alias_remove_command(
    alias: str,
    *,
    spec_context: str | None = None,
) -> tuple[int, str, str]:
    ctx = _context(spec_context)
    return _run_simplebroker_command(
        sb_commands.cmd_alias_remove,
        str(ctx.database_path),
        alias,
    )


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
    "delete_command",
    "broadcast_command",
    "watch_command",
    "alias_add_command",
    "alias_list_command",
    "alias_remove_command",
]
