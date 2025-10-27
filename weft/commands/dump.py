"""Export Weft database state to JSONL format for git-friendly version control."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TextIO

from simplebroker.db import BrokerDB
from weft.context import build_context


def _export_metadata(output: TextIO, db: BrokerDB) -> None:
    """Export metadata from simplebroker meta table."""
    try:
        meta_dict = db.get_meta()
        if not meta_dict:
            return

        # Create single record with all meta data
        record = {"type": "meta", **meta_dict}
        output.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        return


def _export_aliases(output: TextIO, db: BrokerDB) -> int:
    """Export all aliases from the database."""
    try:
        aliases = list(db.list_aliases())
    except Exception:
        # If aliases table doesn't exist or other error, return 0
        return 0

    for alias, target in aliases:
        record = {"type": "alias", "alias": alias, "target": target}
        output.write(json.dumps(record, ensure_ascii=False) + "\n")

    return len(aliases)


def _export_messages(output: TextIO, db: BrokerDB) -> tuple[int, int]:
    """Export all messages from all queues. Returns (queue_count, message_count)."""
    try:
        queues = list(db.list_queues())
    except Exception:
        return 0, 0

    total_messages = 0

    exported_queue_names: list[str] = []
    for queue_name, message_count in queues:
        # Skip empty queues
        if message_count == 0:
            continue

        exported_queue_names.append(queue_name)

        try:
            # Get all messages with timestamps
            messages = list(
                db.peek_many(
                    queue_name,
                    limit=message_count,
                    with_timestamps=True,
                )
            )
        except Exception:
            # Skip queues we can't read
            continue

        for body, timestamp in messages:
            record = {
                "type": "message",
                "queue": queue_name,
                "timestamp": timestamp,
                "body": body,
            }
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
            total_messages += 1

    return len(exported_queue_names), total_messages


def cmd_dump(
    *,
    output: str | None = None,
    context_path: str | None = None,
) -> tuple[int, str | None]:
    """Export database state to JSONL format.

    Args:
        output: Output file path, defaults to .weft/weft_export.jsonl
        context_path: Weft context directory

    Returns:
        (exit_code, message)
    """
    try:
        context = build_context(spec_context=context_path)
    except Exception as exc:
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
    except Exception as exc:
        return 1, f"weft dump: failed to create output directory: {exc}"

    alias_count = 0
    exported_queues = 0
    exported_messages = 0

    try:
        with BrokerDB(str(context.database_path)) as db:
            # Open output file
            with open(output_path, "w", encoding="utf-8") as f:
                # Export in order: metadata, aliases, messages
                _export_metadata(f, db)
                alias_count = _export_aliases(f, db)
                exported_queues, exported_messages = _export_messages(f, db)

    except Exception as exc:
        return 1, f"weft dump: export failed: {exc}"

    # Success message
    message = f"Exported {exported_messages} messages from {exported_queues} queues"
    if alias_count > 0:
        message += f" and {alias_count} aliases"
    message += f" to {output_path}"

    return 0, message


__all__ = ["cmd_dump"]
