"""Tests for dump and load commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from simplebroker.db import BrokerDB
from weft.commands.dump import cmd_dump
from weft.commands.load import ImportReport, cmd_load
from weft.context import build_context


@pytest.fixture
def sample_data_context(tmp_path: Path):
    """Create a test context with sample data."""
    ctx = build_context(spec_context=tmp_path)

    # Add some test messages
    queue1 = ctx.queue("test.queue1", persistent=True)
    queue1.write("message1")
    queue1.write("message2")

    queue2 = ctx.queue("test.queue2", persistent=True)
    queue2.write("message3")

    # Add some aliases
    with BrokerDB(str(ctx.database_path)) as db:
        db.add_alias("alias1", "test.queue1")
        db.add_alias("alias2", "test.queue2")

    return ctx


def test_cmd_dump_basic(sample_data_context):
    """Test basic dump functionality."""
    ctx = sample_data_context
    export_path = ctx.weft_dir / "test_export.jsonl"

    exit_code, message = cmd_dump(output=str(export_path), context_path=str(ctx.root))

    assert exit_code == 0
    assert "Exported 3 messages from 2 queues and 2 aliases" in message
    assert str(export_path) in message
    assert export_path.exists()


def test_cmd_dump_default_path(sample_data_context):
    """Test dump with default output path."""
    ctx = sample_data_context

    exit_code, message = cmd_dump(context_path=str(ctx.root))

    assert exit_code == 0
    default_path = ctx.weft_dir / "weft_export.jsonl"
    assert default_path.exists()


def test_dump_export_format(sample_data_context):
    """Test that dump creates correctly formatted JSONL."""
    ctx = sample_data_context
    export_path = ctx.weft_dir / "test_export.jsonl"

    cmd_dump(output=str(export_path), context_path=str(ctx.root))

    lines = export_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) >= 6  # meta + 2 aliases + 3 messages

    # First line should be meta
    meta_record = json.loads(lines[0])
    assert meta_record["type"] == "meta"
    assert "schema_version" in meta_record
    assert "magic" in meta_record

    # Check that aliases come after meta
    alias_lines = [line for line in lines if json.loads(line).get("type") == "alias"]
    assert len(alias_lines) == 2

    # Check that messages come after aliases
    message_lines = [
        line for line in lines if json.loads(line).get("type") == "message"
    ]
    assert len(message_lines) == 3

    # Verify order: meta first, then aliases, then messages
    record_types = [json.loads(line)["type"] for line in lines]
    assert record_types[0] == "meta"

    # Find first alias and first message indices
    first_alias_idx = record_types.index("alias") if "alias" in record_types else -1
    first_message_idx = (
        record_types.index("message") if "message" in record_types else -1
    )

    assert first_alias_idx > 0  # aliases after meta
    assert first_message_idx > first_alias_idx  # messages after aliases


def test_cmd_load_dry_run(sample_data_context):
    """Test load with dry-run flag."""
    ctx = sample_data_context
    export_path = ctx.weft_dir / "test_export.jsonl"

    # Create export first
    cmd_dump(output=str(export_path), context_path=str(ctx.root))

    # Test dry run
    exit_code, message = cmd_load(
        input_file=str(export_path), dry_run=True, context_path=str(ctx.root)
    )

    assert exit_code == 0
    assert "Import Preview:" in message
    assert "Total messages: 3" in message
    assert "schema v4" in message


def test_cmd_load_actual_import(tmp_path):
    """Test actual import functionality."""
    # Create empty context
    ctx = build_context(spec_context=tmp_path)

    # Create test JSONL data
    export_path = tmp_path / "test_import.jsonl"
    test_data = [
        {"type": "meta", "schema_version": 4, "magic": "simplebroker-v1"},
        {"type": "alias", "alias": "test-alias", "target": "test.queue"},
        {
            "type": "message",
            "queue": "test.queue",
            "timestamp": 1000,
            "body": "test message",
        },
    ]

    with open(export_path, "w", encoding="utf-8") as f:
        for record in test_data:
            f.write(json.dumps(record) + "\n")

    # Import the data
    exit_code, message = cmd_load(
        input_file=str(export_path), dry_run=False, context_path=str(ctx.root)
    )

    assert exit_code == 0
    assert "✓" in message
    assert "Import completed successfully" in message

    # Verify the data was imported
    with BrokerDB(str(ctx.database_path)) as db:
        aliases = list(db.list_aliases())
        assert ("test-alias", "test.queue") in aliases

        queues = list(db.list_queues())
        queue_names = [name for name, _ in queues]
        assert "test.queue" in queue_names


def test_load_missing_file(tmp_path):
    """Test load with missing input file."""
    exit_code, message = cmd_load(
        input_file="/nonexistent/file.jsonl", context_path=str(tmp_path)
    )

    assert exit_code == 2
    assert "input file not found" in message


def test_load_invalid_context():
    """Test load with invalid context."""
    exit_code, message = cmd_load(context_path="/nonexistent/path")

    assert exit_code == 1
    assert "failed to resolve context" in message


def test_dump_invalid_context():
    """Test dump with invalid context."""
    exit_code, message = cmd_dump(context_path="/nonexistent/path")

    assert exit_code == 1
    assert "failed to resolve context" in message


def test_import_report_formatting():
    """Test ImportReport formatting methods."""
    report = ImportReport()
    report.aliases_to_create = {"alias1": "target1", "alias2": "target2"}
    report.aliases_to_update = {"alias3": ("old_target", "new_target")}
    report.queues_to_create = ["queue1", "queue2"]
    report.message_counts_by_queue = {"queue1": 5, "queue2": 3}
    report.total_messages = 8
    report.timestamp_range = (1000, 2000)
    report.metadata = {"schema_version": 4, "magic": "simplebroker-v1"}

    # Test preview format
    preview = report.format_preview()
    assert "Import Preview:" in preview
    assert "Aliases to create: 2" in preview
    assert "Aliases to update: 1" in preview
    assert "Queues to create: 2" in preview
    assert "Total messages: 8" in preview
    assert "schema v4" in preview

    # Test completion format
    completion = report.format_completion()
    assert "✓" in completion
    assert "Created 2 aliases" in completion
    assert "Updated 1 aliases" in completion
    assert "Created 2 queues" in completion
    assert "Imported 8 messages" in completion
    assert "Import completed successfully" in completion


def test_round_trip_consistency(sample_data_context):
    """Test that dump -> load produces consistent results."""
    ctx = sample_data_context
    export_path = ctx.weft_dir / "roundtrip_export.jsonl"

    # Get initial state
    with BrokerDB(str(ctx.database_path)) as db:
        initial_queues = {name for name, _ in db.list_queues()}
        initial_aliases = dict(db.list_aliases())
        initial_total_messages = sum(count for _, count in db.list_queues())

    # Export data
    cmd_dump(output=str(export_path), context_path=str(ctx.root))

    # Create new context and import
    new_ctx = build_context(spec_context=ctx.root.parent / "roundtrip_test")
    cmd_load(input_file=str(export_path), context_path=str(new_ctx.root))

    # Compare final state
    with BrokerDB(str(new_ctx.database_path)) as db:
        final_queues = {name for name, _ in db.list_queues()}
        final_aliases = dict(db.list_aliases())
        final_total_messages = sum(count for _, count in db.list_queues())

    assert final_queues == initial_queues
    assert final_aliases == initial_aliases
    assert final_total_messages == initial_total_messages


def test_empty_database_dump(tmp_path):
    """Test dump of empty database."""
    ctx = build_context(spec_context=tmp_path)
    export_path = ctx.weft_dir / "empty_export.jsonl"

    exit_code, message = cmd_dump(output=str(export_path), context_path=str(ctx.root))

    assert exit_code == 0
    assert "Exported 0 messages from 0 queues" in message
    assert export_path.exists()

    # Should still have meta line
    lines = export_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) >= 1
    meta_record = json.loads(lines[0])
    assert meta_record["type"] == "meta"


def test_load_with_alias_conflicts(tmp_path):
    """Test load when aliases already exist with different targets."""
    ctx = build_context(spec_context=tmp_path)

    # Add existing alias
    with BrokerDB(str(ctx.database_path)) as db:
        db.add_alias("existing_alias", "old_target")

    # Create import data with conflicting alias
    export_path = tmp_path / "conflict_test.jsonl"
    test_data = [
        {"type": "meta", "schema_version": 4, "magic": "simplebroker-v1"},
        {"type": "alias", "alias": "existing_alias", "target": "new_target"},
    ]

    with open(export_path, "w", encoding="utf-8") as f:
        for record in test_data:
            f.write(json.dumps(record) + "\n")

    # Test dry run should show the conflict
    exit_code, message = cmd_load(
        input_file=str(export_path), dry_run=True, context_path=str(ctx.root)
    )

    assert exit_code == 0
    assert "Aliases to update: 1" in message

    # Actual import should update the alias
    exit_code, message = cmd_load(
        input_file=str(export_path), context_path=str(ctx.root)
    )

    assert exit_code == 0

    # Verify alias was updated
    with BrokerDB(str(ctx.database_path)) as db:
        aliases = dict(db.list_aliases())
        assert aliases["existing_alias"] == "new_target"


def test_export_large_message_data(tmp_path):
    """Test export with messages containing large JSON payloads."""
    ctx = build_context(spec_context=tmp_path)

    # Create a large message
    large_data = {"data": "x" * 10000, "numbers": list(range(1000))}
    queue = ctx.queue("large.queue", persistent=True)
    queue.write(json.dumps(large_data))

    export_path = ctx.weft_dir / "large_export.jsonl"
    exit_code, message = cmd_dump(output=str(export_path), context_path=str(ctx.root))

    assert exit_code == 0
    assert export_path.exists()

    # Verify the large message is correctly exported
    lines = export_path.read_text(encoding="utf-8").strip().split("\n")
    message_lines = [
        line for line in lines if json.loads(line).get("type") == "message"
    ]
    assert len(message_lines) == 1

    message_record = json.loads(message_lines[0])
    body = json.loads(message_record["body"])
    assert body["data"] == "x" * 10000
    assert len(body["numbers"]) == 1000
