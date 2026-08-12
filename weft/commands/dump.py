"""Export Weft broker state in SimpleBroker dump format.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-6]
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TextIO

from simplebroker import dump_lines
from simplebroker.ext import BrokerError
from weft._constants import WEFT_STATE_QUEUE_PREFIX
from weft._exceptions import CommandError, CommandExecutionError
from weft.commands.types import SystemDumpResult
from weft.context import WeftContext, build_context
from weft.helpers import open_owner_only_text


def _claimed_summary(db: Any) -> tuple[int, int]:
    """Return claimed-message counts for included queues."""

    try:
        queue_stats = list(db.list_queue_stats())
    except (
        BrokerError,
        OSError,
        RuntimeError,
    ):  # pragma: no cover - queue probe best effort
        return 0, 0

    claimed_queue_count = 0
    claimed_message_count = 0

    for stats in queue_stats:
        queue_name = str(stats.queue)
        if queue_name.startswith(WEFT_STATE_QUEUE_PREFIX):
            continue
        claimed_count = int(getattr(stats, "claimed", 0))
        if claimed_count > 0:
            claimed_queue_count += 1
            claimed_message_count += claimed_count
    return claimed_queue_count, claimed_message_count


def _write_dump(output: TextIO, db: Any) -> tuple[int, int, int]:
    """Write SimpleBroker dump lines and return alias/message/queue counts."""

    alias_count = 0
    message_count = 0
    message_queues: set[str] = set()

    for line in dump_lines(db, exclude=[f"{WEFT_STATE_QUEUE_PREFIX}*"]):
        output.write(line + "\n")
        record = json.loads(line)
        record_type = record.get("type")
        if record_type == "alias":
            alias_count += 1
        elif record_type == "message":
            message_count += 1
            queue_name = record.get("queue")
            if isinstance(queue_name, str):
                message_queues.add(queue_name)

    return alias_count, message_count, len(message_queues)


def cmd_dump(
    *,
    output: str | None = None,
    context_path: str | None = None,
) -> tuple[int, str | None]:
    """Export database state to JSONL format."""
    try:
        result = cmd_system_dump(
            output=output,
            context=Path(context_path) if context_path is not None else None,
        )
    except CommandError as exc:
        return 1, str(exc)
    message = f"Exported {result.messages} messages from {result.queues} queues"
    if result.aliases > 0:
        message += f" and {result.aliases} aliases"
    if result.omitted_claimed_messages > 0:
        message += (
            f"; omitted {result.omitted_claimed_messages} claimed messages from "
            f"{result.omitted_claimed_queues} queues"
        )
    message += f" to {result.path}"
    return 0, message


def cmd_system_dump(
    *,
    output: str | None = None,
    context: Path | None = None,
) -> SystemDumpResult:
    """Export broker state and return exact exported and omitted counts.

    Spec: docs/specifications/14-Python_API_Surfaces.md [PY-2].
    """

    try:
        resolved = build_context(spec_context=context)
    except Exception as exc:  # public command boundary [PY-2]
        raise CommandExecutionError(
            f"weft dump: failed to resolve context: {exc}"
        ) from exc
    output_path = resolved.weft_dir / "weft_export.jsonl" if output is None else Path(output)
    if not output_path.is_absolute():
        output_path = Path.cwd() / output_path
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with resolved.broker() as db:
            with open_owner_only_text(output_path) as output_file:
                aliases, messages, queues = _write_dump(output_file, db)
            omitted_queues, omitted_messages = _claimed_summary(db)
    except Exception as exc:  # public command boundary [PY-2]
        raise CommandExecutionError(f"weft dump: export failed: {exc}") from exc
    return SystemDumpResult(
        path=output_path,
        queues=queues,
        messages=messages,
        aliases=aliases,
        omitted_claimed_queues=omitted_queues,
        omitted_claimed_messages=omitted_messages,
    )


def dump_system(
    context: WeftContext,
    *,
    output: str | Path | None = None,
) -> Path:
    """Dump broker state and return the output path."""

    output_path = (
        context.weft_dir / "weft_export.jsonl"
        if output is None
        else Path(output)
        if Path(output).is_absolute()
        else Path.cwd() / Path(output)
    )
    exit_code, message = cmd_dump(
        output=str(output_path), context_path=str(context.root)
    )
    if exit_code != 0:
        raise RuntimeError(message or "weft dump failed")
    return output_path


__all__ = ["cmd_dump", "cmd_system_dump", "dump_system"]
