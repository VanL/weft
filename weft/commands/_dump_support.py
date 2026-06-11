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
from weft.context import build_context


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
    """Export database state to JSONL format.

    Args:
        output: Output file path, defaults to `weft_export.jsonl` under the
            active Weft metadata directory.
        context_path: Weft context directory

    Returns:
        (exit_code, message)
    """
    try:
        context = build_context(spec_context=context_path)
    except Exception as exc:  # pragma: no cover - command error boundary
        return 1, f"weft dump: failed to resolve context: {exc}"

    # Determine output path
    if output is None:
        output_path = context.weft_dir / "weft_export.jsonl"
    else:
        output_path = Path(output)
        if not output_path.is_absolute():
            output_path = Path.cwd() / output_path

    # Ensure output directory exists
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return 1, f"weft dump: failed to create output directory: {exc}"

    alias_count = 0
    exported_queues = 0
    exported_messages = 0
    claimed_queues = 0
    claimed_messages = 0

    try:
        with context.broker() as db:
            with open(output_path, "w", encoding="utf-8") as f:
                (
                    alias_count,
                    exported_messages,
                    exported_queues,
                ) = _write_dump(f, db)
            claimed_queues, claimed_messages = _claimed_summary(db)

    except Exception as exc:  # pragma: no cover - command error boundary
        return 1, f"weft dump: export failed: {exc}"

    # Success message
    message = f"Exported {exported_messages} messages from {exported_queues} queues"
    if alias_count > 0:
        message += f" and {alias_count} aliases"
    if claimed_messages > 0:
        message += (
            f"; omitted {claimed_messages} claimed messages from "
            f"{claimed_queues} queues"
        )
    message += f" to {output_path}"

    return 0, message


__all__ = ["cmd_dump"]
