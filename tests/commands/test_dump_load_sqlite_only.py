"""SQLite-only coverage for dump/load rollback details."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from weft.commands.load import cmd_load
from weft.context import WeftContext, build_context

pytestmark = [pytest.mark.sqlite_only]


def _snapshot_broker_state(
    context: WeftContext,
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Capture aliases and queue contents for exact before/after comparisons."""

    with context.broker() as broker:
        aliases = dict(broker.list_aliases())
        queues: dict[str, list[str]] = {}
        for stats in broker.list_queue_stats():
            queue_name = str(stats.queue)
            message_count = int(stats.pending)
            queues[queue_name] = (
                [
                    str(message)
                    for message in broker.peek_many(
                        queue_name,
                        limit=message_count,
                        with_timestamps=False,
                    )
                ]
                if message_count > 0
                else []
            )

    return aliases, queues


def test_cmd_load_rolls_back_sqlite_snapshot_on_apply_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """File-backed sqlite imports should restore pre-import state after a write failure."""

    monkeypatch.setenv("BROKER_MAX_MESSAGE_SIZE", "32")
    ctx = build_context(spec_context=tmp_path)
    before_aliases, before_queues = _snapshot_broker_state(ctx)

    export_path = tmp_path / "rollback.jsonl"
    test_data = [
        {
            "type": "header",
            "format": "simplebroker-dump",
            "version": 1,
            "backend": "test",
            "last_ts": 0,
        },
        {"type": "alias", "alias": "restored_alias", "target": "restored.queue"},
        {
            "type": "message",
            "queue": "restored.queue",
            "id": 1000,
            "body": "x" * 256,
        },
    ]
    export_path.write_text(
        "".join(json.dumps(record) + "\n" for record in test_data),
        encoding="utf-8",
    )

    exit_code, message = cmd_load(
        input_file=str(export_path), context_path=str(ctx.root)
    )

    after_aliases, after_queues = _snapshot_broker_state(ctx)

    assert exit_code == 1
    assert "import failed" in (message or "").lower()
    assert after_aliases == before_aliases
    assert after_queues == before_queues


def test_cmd_load_rolls_back_sqlite_snapshot_on_duplicate_message_id(
    tmp_path: Path,
) -> None:
    """Duplicate exact IDs should fail without rewriting imported messages."""

    ctx = build_context(spec_context=tmp_path)
    with ctx.broker() as broker:
        broker.insert_messages([("existing.queue", "keep", 1000)])
    before_aliases, before_queues = _snapshot_broker_state(ctx)

    export_path = tmp_path / "duplicate-id.jsonl"
    test_data = [
        {
            "type": "header",
            "format": "simplebroker-dump",
            "version": 1,
            "backend": "test",
            "last_ts": 0,
        },
        {
            "type": "message",
            "queue": "new.queue",
            "id": 1000,
            "body": "duplicate",
        },
    ]
    export_path.write_text(
        "".join(json.dumps(record) + "\n" for record in test_data),
        encoding="utf-8",
    )

    exit_code, message = cmd_load(
        input_file=str(export_path), context_path=str(ctx.root)
    )

    after_aliases, after_queues = _snapshot_broker_state(ctx)

    assert exit_code == 1
    assert "import failed" in (message or "").lower()
    assert "exact message ID import failed" in (message or "")
    assert after_aliases == before_aliases
    assert after_queues == before_queues
