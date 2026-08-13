"""Tests for dump and load commands."""

from __future__ import annotations

import json
import os
import stat
import sys
from io import StringIO
from pathlib import Path

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft._constants import WEFT_SPAWN_REQUESTS_QUEUE
from weft.commands import dump as dump_command
from weft.commands import load as load_command
from weft.commands.dump import cmd_dump
from weft.commands.load import ImportReport, cmd_load
from weft.context import WeftContext, build_context
from weft.core.spawn_requests import submit_spawn_request
from weft.core.taskspec import resolve_taskspec_payload

pytestmark = [pytest.mark.shared]


class UnexpectedLoadFailure(Exception):
    """Failure outside the command's expected operational exception families."""


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


def _queue_rows_with_timestamps(
    context: WeftContext,
    queue_name: str,
) -> list[tuple[str, int]]:
    queue = context.queue(queue_name, persistent=True)
    try:
        stats = queue.stats()
    finally:
        queue.close()
    with context.broker() as broker:
        return [
            (str(body), int(timestamp))
            for body, timestamp in broker.peek_many(
                queue_name,
                limit=int(stats.pending),
                with_timestamps=True,
            )
        ]


@pytest.fixture
def sample_data_context(tmp_path: Path) -> WeftContext:
    """Create a test context with sample data."""

    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)

    queue1 = ctx.queue("test.queue1", persistent=True)
    queue1.write("message1")
    queue1.write("message2")

    queue2 = ctx.queue("test.queue2", persistent=True)
    queue2.write("message3")

    with ctx.broker() as broker:
        broker.add_alias("alias1", "test.queue1")
        broker.add_alias("alias2", "test.queue2")

    return ctx


def test_cmd_dump_basic(sample_data_context: WeftContext) -> None:
    """Test basic dump functionality."""

    ctx = sample_data_context
    export_path = ctx.weft_dir / "test_export.jsonl"

    exit_code, message = cmd_dump(output=str(export_path), context_path=str(ctx.root))

    assert exit_code == 0
    assert "Exported 3 messages from 2 queues and 2 aliases" in message
    assert str(export_path) in message
    assert export_path.exists()


def test_cmd_dump_default_path(sample_data_context: WeftContext) -> None:
    """Test dump with default output path."""

    ctx = sample_data_context

    exit_code, _message = cmd_dump(context_path=str(ctx.root))

    assert exit_code == 0
    default_path = ctx.weft_dir / "weft_export.jsonl"
    assert default_path.exists()


def test_dump_export_format(sample_data_context: WeftContext) -> None:
    """Test that dump creates correctly formatted JSONL."""

    ctx = sample_data_context
    export_path = ctx.weft_dir / "test_export.jsonl"

    cmd_dump(output=str(export_path), context_path=str(ctx.root))

    lines = export_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) >= 6

    header_record = json.loads(lines[0])
    assert header_record["type"] == "header"
    assert header_record["format"] == "simplebroker-dump"
    assert header_record["version"] == 1
    assert isinstance(header_record["last_ts"], str)
    assert len(header_record["last_ts"]) == 19
    assert header_record["last_ts"].isascii()
    assert header_record["last_ts"].isdigit()

    alias_lines = [line for line in lines if json.loads(line).get("type") == "alias"]
    assert len(alias_lines) == 2

    message_lines = [
        line for line in lines if json.loads(line).get("type") == "message"
    ]
    assert len(message_lines) == 3
    message_ids = [json.loads(line)["id"] for line in message_lines]
    assert all(isinstance(message_id, str) for message_id in message_ids)
    assert all(len(message_id) == 19 for message_id in message_ids)
    assert all(message_id.isascii() for message_id in message_ids)
    assert all(message_id.isdigit() for message_id in message_ids)

    record_types = [json.loads(line)["type"] for line in lines]
    assert record_types[0] == "header"

    first_alias_idx = record_types.index("alias") if "alias" in record_types else -1
    first_message_idx = (
        record_types.index("message") if "message" in record_types else -1
    )

    assert first_alias_idx > 0
    assert first_message_idx > first_alias_idx


def test_cmd_load_dry_run(sample_data_context: WeftContext) -> None:
    """Test load with dry-run flag."""

    ctx = sample_data_context
    export_path = ctx.weft_dir / "test_export.jsonl"

    cmd_dump(output=str(export_path), context_path=str(ctx.root))
    header_record = json.loads(export_path.read_text(encoding="utf-8").splitlines()[0])

    exit_code, message = cmd_load(
        input_file=str(export_path), dry_run=True, context_path=str(ctx.root)
    )

    assert exit_code == 0
    assert "Import Preview:" in message
    assert "Total messages: 3" in message
    assert f"{header_record['format']} v{header_record['version']}" in message


def test_cmd_load_actual_import(tmp_path: Path) -> None:
    """Test actual import functionality."""

    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)

    export_path = tmp_path / "test_import.jsonl"
    test_data = [
        {
            "type": "header",
            "format": "simplebroker-dump",
            "version": 1,
            "backend": "test",
            "last_ts": 0,
        },
        {"type": "alias", "alias": "test-alias", "target": "test.queue"},
        {
            "type": "message",
            "queue": "test.queue",
            "id": 1000,
            "body": "test message",
        },
    ]

    export_path.write_text(
        "".join(json.dumps(record) + "\n" for record in test_data),
        encoding="utf-8",
    )

    exit_code, message = cmd_load(
        input_file=str(export_path), dry_run=False, context_path=str(ctx.root)
    )

    assert exit_code == 0
    assert "✓" in message
    assert "Import completed successfully" in message

    aliases, queues = _snapshot_broker_state(ctx)
    assert aliases["test-alias"] == "test.queue"
    assert queues["test.queue"] == ["test message"]


@pytest.mark.parametrize("as_string", [False, True])
def test_parse_import_normalizes_exact_ids_and_builds_canonical_apply_lines(
    as_string: bool,
) -> None:
    """Dump IDs normalize to integers before canonical apply-line projection."""

    last_ts = 1_779_400_000_000_000_002
    message_id = 1_779_400_000_000_000_001
    records = [
        {
            "type": "header",
            "format": "simplebroker-dump",
            "version": 1,
            "backend": "test",
            "last_ts": str(last_ts) if as_string else last_ts,
        },
        {
            "type": "message",
            "queue": "exact.queue",
            "id": str(message_id) if as_string else message_id,
            "body": "exact",
        },
    ]
    source = StringIO("".join(json.dumps(record) + "\n" for record in records))

    plan = load_command._parse_import_file(source)
    apply_lines = load_command._build_apply_lines(plan)

    assert plan.report.metadata["last_ts"] == last_ts
    assert isinstance(plan.report.metadata["last_ts"], int)
    assert plan.message_records[0].message_id == message_id
    assert isinstance(plan.message_records[0].message_id, int)
    assert plan.report.message_id_range == (message_id, message_id)
    assert all(isinstance(value, int) for value in plan.report.message_id_range)
    assert json.loads(apply_lines[0])["last_ts"] == "1779400000000000002"
    assert json.loads(apply_lines[1])["id"] == "1779400000000000001"


@pytest.mark.parametrize("last_ts", [0, "0000000000000000000"])
def test_parse_import_accepts_zero_header_checkpoint(last_ts: int | str) -> None:
    """The dump checkpoint origin remains valid in either accepted input form."""

    records = [
        {
            "type": "header",
            "format": "simplebroker-dump",
            "version": 1,
            "backend": "test",
            "last_ts": last_ts,
        }
    ]
    source = StringIO("".join(json.dumps(record) + "\n" for record in records))

    plan = load_command._parse_import_file(source)

    assert plan.report.metadata["last_ts"] == 0
    assert isinstance(plan.report.metadata["last_ts"], int)
    assert json.loads(plan.header_line or "null")["last_ts"] == ("0000000000000000000")


def test_dump_load_round_trip_preserves_adjacent_unsafe_message_ids(
    tmp_path: Path,
) -> None:
    """Canonical JSON strings keep adjacent IDs above JavaScript's safe range."""

    message_ids = (1_779_400_000_000_000_001, 1_779_400_000_000_000_002)
    source_root = prepare_project_root(tmp_path / "source")
    source_context = build_context(spec_context=source_root)
    with source_context.broker() as broker:
        broker.insert_messages(
            [
                ("unsafe.queue", "first", message_ids[0]),
                ("unsafe.queue", "second", message_ids[1]),
            ]
        )
    export_path = source_context.weft_dir / "unsafe-export.jsonl"

    dump_code, dump_message = cmd_dump(
        output=str(export_path),
        context_path=str(source_context.root),
    )

    assert dump_code == 0, dump_message
    records = [
        json.loads(line)
        for line in export_path.read_text(encoding="utf-8").splitlines()
    ]
    header = records[0]
    messages = [record for record in records if record["type"] == "message"]
    assert isinstance(header["last_ts"], str)
    assert len(header["last_ts"]) == 19
    assert header["last_ts"].isascii()
    assert header["last_ts"].isdigit()
    assert int(header["last_ts"]) >= message_ids[-1]
    assert [record["id"] for record in messages] == [
        "1779400000000000001",
        "1779400000000000002",
    ]

    destination_root = prepare_project_root(tmp_path / "destination")
    destination_context = build_context(spec_context=destination_root)
    load_code, load_message = cmd_load(
        input_file=str(export_path),
        context_path=str(destination_context.root),
    )

    assert load_code == 0, load_message
    assert _queue_rows_with_timestamps(destination_context, "unsafe.queue") == [
        ("first", message_ids[0]),
        ("second", message_ids[1]),
    ]


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("last_ts", True),
        ("last_ts", 1.0),
        ("last_ts", 1e18),
        ("last_ts", -1),
        ("last_ts", 9_999_999_999_999_999_999),
        ("last_ts", "9999999999999999999"),
        ("last_ts", " 1779400000000000002 "),
        ("last_ts", "１７７９４０００００００００００００２"),
        ("last_ts", "177940000000000002"),
        ("id", True),
        ("id", 1.0),
        ("id", 1e18),
        ("id", -1),
        ("id", 9_999_999_999_999_999_999),
        ("id", "9999999999999999999"),
        ("id", " 1779400000000000001 "),
        ("id", "１７７９４０００００００００００００１"),
        ("id", "177940000000000001"),
    ],
)
def test_cmd_load_rejects_noncanonical_exact_ids_before_writes(
    tmp_path: Path,
    field: str,
    invalid_value: object,
) -> None:
    """Invalid header or message IDs fail validation without broker mutation."""

    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    before = _snapshot_broker_state(context)
    header_last_ts: object = 1_779_400_000_000_000_002
    message_id: object = 1_779_400_000_000_000_001
    if field == "last_ts":
        header_last_ts = invalid_value
    else:
        message_id = invalid_value
    records = [
        {
            "type": "header",
            "format": "simplebroker-dump",
            "version": 1,
            "backend": "test",
            "last_ts": header_last_ts,
        },
        {"type": "alias", "alias": "invalid", "target": "invalid.queue"},
        {
            "type": "message",
            "queue": "invalid.queue",
            "id": message_id,
            "body": "must not be written",
        },
    ]
    export_path = tmp_path / "invalid-id.jsonl"
    export_path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )

    exit_code, message = cmd_load(
        input_file=str(export_path),
        context_path=str(context.root),
    )

    assert exit_code == 1
    assert f"'{field}'" in (message or "")
    assert _snapshot_broker_state(context) == before


def test_execute_import_propagates_unexpected_snapshot_restore_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rollback implementation defect must not be relabeled as import failure."""

    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    snapshot = load_command.SQLiteSnapshot(
        database_path=tmp_path / "broker.db",
        snapshot_dir=tmp_path / "snapshot",
    )
    plan = load_command.ImportPlan(apply_lines=["header", "message"])

    monkeypatch.setattr(
        load_command,
        "_ensure_exact_message_id_import_supported",
        lambda _plan, _context: None,
    )
    monkeypatch.setattr(
        load_command,
        "_sqlite_snapshot_if_file_backed",
        lambda _context: snapshot,
    )

    def fail_apply(_broker: object, _lines: list[str]) -> None:
        raise ValueError("apply failed")

    def fail_restore(_snapshot: load_command.SQLiteSnapshot) -> None:
        raise RuntimeError("restore defect")

    monkeypatch.setattr(load_command, "load_lines", fail_apply)
    monkeypatch.setattr(load_command.SQLiteSnapshot, "restore", fail_restore)

    with pytest.raises(RuntimeError, match="restore defect") as exc_info:
        load_command._execute_import(plan, context)

    assert isinstance(exc_info.value.__context__, ValueError)
    assert str(exc_info.value.__context__) == "apply failed"


def test_execute_import_reports_operational_snapshot_restore_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expected filesystem rollback failure retains both failure details."""

    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    snapshot = load_command.SQLiteSnapshot(
        database_path=tmp_path / "broker.db",
        snapshot_dir=tmp_path / "snapshot",
    )
    plan = load_command.ImportPlan(apply_lines=["header", "message"])

    monkeypatch.setattr(
        load_command,
        "_ensure_exact_message_id_import_supported",
        lambda _plan, _context: None,
    )
    monkeypatch.setattr(
        load_command,
        "_sqlite_snapshot_if_file_backed",
        lambda _context: snapshot,
    )

    def fail_apply(_broker: object, _lines: list[str]) -> None:
        raise ValueError("apply failed")

    def fail_restore(_snapshot: load_command.SQLiteSnapshot) -> None:
        raise OSError("restore failed")

    monkeypatch.setattr(load_command, "load_lines", fail_apply)
    monkeypatch.setattr(load_command.SQLiteSnapshot, "restore", fail_restore)

    with pytest.raises(
        ImportError,
        match=(
            "import failed and file-backed rollback failed: apply failed; "
            "restore failed: restore failed"
        ),
    ) as exc_info:
        load_command._execute_import(plan, context)

    assert isinstance(exc_info.value.__cause__, ValueError)
    assert str(exc_info.value.__cause__) == "apply failed"


@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize("reserved_id", [0, "0000000000000000000"])
def test_cmd_load_rejects_reserved_zero_id_before_writes(
    tmp_path: Path,
    dry_run: bool,
    reserved_id: int | str,
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    before = _snapshot_broker_state(ctx)
    export_path = tmp_path / "reserved-zero-id.jsonl"
    records = [
        {
            "type": "header",
            "format": "simplebroker-dump",
            "version": 1,
            "backend": "test",
            "last_ts": 0,
        },
        {"type": "alias", "alias": "zero-alias", "target": "zero.queue"},
        {
            "type": "message",
            "queue": "zero.queue",
            "id": reserved_id,
            "body": "must not be written",
        },
    ]
    export_path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )

    exit_code, message = cmd_load(
        input_file=str(export_path),
        dry_run=dry_run,
        context_path=str(ctx.root),
    )

    assert exit_code == 1
    assert "positive integer or canonical 19-digit string 'id'" in (message or "")
    assert _snapshot_broker_state(ctx) == before


def test_load_missing_file(tmp_path: Path) -> None:
    """Test load with missing input file."""

    root = prepare_project_root(tmp_path)
    exit_code, message = cmd_load(
        input_file="/nonexistent/file.jsonl", context_path=str(root)
    )

    assert exit_code == 2
    assert "input file not found" in message


def test_load_invalid_context(tmp_path: Path) -> None:
    """Test load with invalid context."""

    invalid_context = tmp_path / "not-a-directory"
    invalid_context.write_text("context", encoding="utf-8")
    exit_code, message = cmd_load(context_path=str(invalid_context))

    assert exit_code == 1
    assert "failed to resolve context" in message


def test_cmd_load_reports_unexpected_context_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The command boundary reports an extension-defined context failure."""

    def fail_context(*, spec_context: str | None) -> WeftContext:
        del spec_context
        raise UnexpectedLoadFailure("private context detail")

    monkeypatch.setattr(load_command, "build_context", fail_context)

    exit_code, message = cmd_load()

    assert exit_code == 1
    assert message == "private context detail"


def test_cmd_load_reports_unexpected_plan_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The command boundary reports an extension-defined import-plan failure."""

    root = prepare_project_root(tmp_path)
    export_path = tmp_path / "unexpected-plan.jsonl"
    export_path.write_text("ignored", encoding="utf-8")

    def fail_plan(_handle: object, _context: WeftContext) -> load_command.ImportPlan:
        raise UnexpectedLoadFailure("private plan detail")

    monkeypatch.setattr(load_command, "_build_import_plan", fail_plan)

    exit_code, message = cmd_load(
        input_file=str(export_path),
        context_path=str(root),
    )

    assert exit_code == 1
    assert message == "private plan detail"


def test_cmd_load_reports_unexpected_apply_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The command boundary reports an extension-defined import-apply failure."""

    root = prepare_project_root(tmp_path)
    export_path = tmp_path / "unexpected-apply.jsonl"
    export_path.write_text("ignored", encoding="utf-8")
    plan = load_command.ImportPlan()

    monkeypatch.setattr(
        load_command,
        "_build_import_plan",
        lambda _handle, _context: plan,
    )

    def fail_apply(
        _plan: load_command.ImportPlan,
        _context: WeftContext,
    ) -> load_command.ImportReport:
        raise RuntimeError("private apply detail")

    monkeypatch.setattr(load_command, "_execute_import", fail_apply)

    exit_code, message = cmd_load(
        input_file=str(export_path),
        context_path=str(root),
    )

    assert exit_code == 1
    assert message == "weft load: import failed: private apply detail"


def test_cmd_load_rejects_legacy_weft_dump_format(tmp_path: Path) -> None:
    """Old Weft meta/timestamp dumps are intentionally not accepted."""

    root = prepare_project_root(tmp_path)
    export_path = tmp_path / "legacy.jsonl"
    legacy_data = [
        {"type": "meta", "schema_version": 4, "magic": "simplebroker-v1"},
        {
            "type": "message",
            "queue": "legacy.queue",
            "timestamp": 1000,
            "body": "legacy message",
        },
    ]
    export_path.write_text(
        "".join(json.dumps(record) + "\n" for record in legacy_data),
        encoding="utf-8",
    )

    exit_code, message = cmd_load(input_file=str(export_path), context_path=str(root))

    assert exit_code == 1
    assert "first record must be the dump header" in (message or "")


def test_dump_invalid_context(tmp_path: Path) -> None:
    """Test dump with invalid context."""

    invalid_context = tmp_path / "not-a-directory"
    invalid_context.write_text("context", encoding="utf-8")
    exit_code, message = cmd_dump(context_path=str(invalid_context))

    assert exit_code == 1
    assert "failed to resolve context" in message


def test_cmd_dump_reports_unexpected_context_resolution_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The command boundary converts backend-defined context errors to a result."""

    class ContextResolutionError(Exception):
        pass

    def fail_context_resolution(*_args: object, **_kwargs: object) -> None:
        raise ContextResolutionError("context resolution sentinel")

    monkeypatch.setattr(dump_command, "build_context", fail_context_resolution)

    exit_code, message = cmd_dump(context_path="unused")

    assert exit_code == 1
    assert message == (
        "weft dump: failed to resolve context: context resolution sentinel"
    )


def test_cmd_dump_reports_unexpected_export_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The command boundary converts backend-defined export errors to a result."""

    class ExportAdapterError(Exception):
        pass

    root = prepare_project_root(tmp_path)

    def fail_export(_output: object, _db: object) -> tuple[int, int, int]:
        raise ExportAdapterError("export adapter sentinel")

    monkeypatch.setattr(dump_command, "_write_dump", fail_export)

    exit_code, message = cmd_dump(context_path=str(root))

    assert exit_code == 1
    assert message == "weft dump: export failed: export adapter sentinel"


def test_import_report_formatting() -> None:
    """Test ImportReport formatting methods."""

    report = ImportReport()
    report.aliases_to_create = {"alias1": "target1", "alias2": "target2"}
    report.queues_to_create = ["queue1", "queue2"]
    report.message_counts_by_queue = {"queue1": 5, "queue2": 3}
    report.total_messages = 8
    report.message_id_range = (1000, 2000)
    report.metadata = {
        "format": "simplebroker-dump",
        "version": 1,
        "backend": "test",
    }

    preview = report.format_preview()
    assert "Import Preview:" in preview
    assert "Aliases to create: 2" in preview
    assert "Queues to create: 2" in preview
    assert "Total messages: 8" in preview
    assert "simplebroker-dump v1" in preview

    completion = report.format_completion()
    assert "✓" in completion
    assert "Created 2 aliases" in completion
    assert "Created 2 queues" in completion
    assert "Imported 8 messages" in completion
    assert "Import completed successfully" in completion


def test_round_trip_consistency(sample_data_context: WeftContext) -> None:
    """Test that dump -> load produces consistent results."""

    ctx = sample_data_context
    export_path = ctx.weft_dir / "roundtrip_export.jsonl"

    initial_aliases, initial_queues = _snapshot_broker_state(ctx)

    cmd_dump(output=str(export_path), context_path=str(ctx.root))

    new_root = prepare_project_root(ctx.root.parent / "roundtrip_test")
    new_ctx = build_context(spec_context=new_root)
    cmd_load(input_file=str(export_path), context_path=str(new_ctx.root))

    final_aliases, final_queues = _snapshot_broker_state(new_ctx)

    assert final_aliases == initial_aliases
    assert final_queues == initial_queues


def test_dump_load_preserves_spawn_request_message_id(tmp_path: Path) -> None:
    root = prepare_project_root(tmp_path / "source")
    ctx = build_context(spec_context=root)
    tid = submit_spawn_request(
        ctx.broker_target,
        taskspec={
            "name": "spawned",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
            "metadata": {},
        },
        work_payload={"args": ["hello"]},
        config=ctx.broker_config,
        inherited_weft_context=str(ctx.root),
    )
    export_path = ctx.weft_dir / "spawn-export.jsonl"

    dump_code, dump_message = cmd_dump(
        output=str(export_path),
        context_path=str(ctx.root),
    )
    assert dump_code == 0, dump_message

    new_root = prepare_project_root(tmp_path / "loaded")
    new_ctx = build_context(spec_context=new_root)
    load_code, load_message = cmd_load(
        input_file=str(export_path),
        context_path=str(new_ctx.root),
    )
    assert load_code == 0, load_message

    rows = _queue_rows_with_timestamps(new_ctx, WEFT_SPAWN_REQUESTS_QUEUE)
    assert len(rows) == 1
    body, timestamp = rows[0]
    assert timestamp == tid
    payload = json.loads(body)
    assert payload["taskspec"].get("tid") is None
    resolved = resolve_taskspec_payload(payload["taskspec"], tid=str(timestamp))
    assert resolved["tid"] == str(tid)


def test_dump_warns_when_claimed_messages_are_omitted(tmp_path: Path) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    queue = ctx.queue("claimed.queue", persistent=True)
    queue.write("claimed")
    assert queue.read_one() == "claimed"

    export_path = ctx.weft_dir / "claimed-export.jsonl"
    exit_code, message = cmd_dump(output=str(export_path), context_path=str(ctx.root))

    assert exit_code == 0
    assert "omitted 1 claimed messages from 1 queues" in (message or "")
    message_records = [
        json.loads(line)
        for line in export_path.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("type") == "message"
    ]
    assert message_records == []


def test_empty_database_dump(tmp_path: Path) -> None:
    """Test dump of empty database."""

    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    export_path = ctx.weft_dir / "empty_export.jsonl"

    exit_code, message = cmd_dump(output=str(export_path), context_path=str(ctx.root))

    assert exit_code == 0
    assert "Exported 0 messages from 0 queues" in message
    assert export_path.exists()

    lines = export_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) >= 1
    header_record = json.loads(lines[0])
    assert header_record["type"] == "header"
    assert header_record["format"] == "simplebroker-dump"


def test_cmd_load_dry_run_reports_alias_conflicts_without_writes(
    tmp_path: Path,
) -> None:
    """Dry-run should report fatal alias conflicts without mutating broker state."""

    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    queue = ctx.queue("existing.queue", persistent=True)
    queue.write("keep")
    with ctx.broker() as broker:
        broker.add_alias("existing_alias", "old_target")

    before_aliases, before_queues = _snapshot_broker_state(ctx)

    export_path = tmp_path / "conflict_dry_run.jsonl"
    test_data = [
        {
            "type": "header",
            "format": "simplebroker-dump",
            "version": 1,
            "backend": "test",
            "last_ts": 0,
        },
        {"type": "alias", "alias": "existing_alias", "target": "new_target"},
        {
            "type": "message",
            "queue": "new.queue",
            "id": 1000,
            "body": "new message",
        },
    ]
    export_path.write_text(
        "".join(json.dumps(record) + "\n" for record in test_data),
        encoding="utf-8",
    )

    exit_code, message = cmd_load(
        input_file=str(export_path), dry_run=True, context_path=str(ctx.root)
    )

    after_aliases, after_queues = _snapshot_broker_state(ctx)

    assert exit_code == 3
    assert "alias conflicts" in (message or "").lower()
    assert "existing_alias" in (message or "")
    assert after_aliases == before_aliases
    assert after_queues == before_queues


def test_cmd_load_rejects_alias_conflicts_before_any_writes(tmp_path: Path) -> None:
    """Apply mode should stop on alias conflicts before it creates queues or aliases."""

    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    queue = ctx.queue("existing.queue", persistent=True)
    queue.write("keep")
    with ctx.broker() as broker:
        broker.add_alias("existing_alias", "old_target")

    before_aliases, before_queues = _snapshot_broker_state(ctx)

    export_path = tmp_path / "conflict_apply.jsonl"
    test_data = [
        {
            "type": "header",
            "format": "simplebroker-dump",
            "version": 1,
            "backend": "test",
            "last_ts": 0,
        },
        {"type": "alias", "alias": "existing_alias", "target": "new_target"},
        {
            "type": "message",
            "queue": "new.queue",
            "id": 1000,
            "body": "new message",
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

    assert exit_code == 3
    assert "alias conflicts" in (message or "").lower()
    assert "existing_alias" in (message or "")
    assert after_aliases == before_aliases
    assert after_queues == before_queues


def test_cmd_load_treats_same_target_existing_alias_as_noop(tmp_path: Path) -> None:
    """Existing aliases with the same target should not be reapplied."""

    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    with ctx.broker() as broker:
        broker.add_alias("existing_alias", "same_target")

    export_path = tmp_path / "same_target_alias.jsonl"
    test_data = [
        {
            "type": "header",
            "format": "simplebroker-dump",
            "version": 1,
            "backend": "test",
            "last_ts": 0,
        },
        {"type": "alias", "alias": "existing_alias", "target": "same_target"},
        {
            "type": "message",
            "queue": "new.queue",
            "id": 1000,
            "body": "new message",
        },
    ]
    export_path.write_text(
        "".join(json.dumps(record) + "\n" for record in test_data),
        encoding="utf-8",
    )

    exit_code, message = cmd_load(
        input_file=str(export_path), context_path=str(ctx.root)
    )

    assert exit_code == 0, message
    assert "Created 1 aliases" not in (message or "")
    aliases, queues = _snapshot_broker_state(ctx)
    assert aliases["existing_alias"] == "same_target"
    assert queues["new.queue"] == ["new message"]


def test_export_large_message_data(tmp_path: Path) -> None:
    """Test export with messages containing large JSON payloads."""

    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)

    large_data = {"data": "x" * 10000, "numbers": list(range(1000))}
    queue = ctx.queue("large.queue", persistent=True)
    queue.write(json.dumps(large_data))

    export_path = ctx.weft_dir / "large_export.jsonl"
    exit_code, _message = cmd_dump(output=str(export_path), context_path=str(ctx.root))

    assert exit_code == 0
    assert export_path.exists()

    lines = export_path.read_text(encoding="utf-8").strip().split("\n")
    message_lines = [
        line for line in lines if json.loads(line).get("type") == "message"
    ]
    assert len(message_lines) == 1

    message_record = json.loads(message_lines[0])
    body = json.loads(message_record["body"])
    assert body["data"] == "x" * 10000
    assert len(body["numbers"]) == 1000


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_cmd_dump_output_file_is_owner_only(tmp_path: Path) -> None:
    root = prepare_project_root(tmp_path)  # the module's fixtures already use this
    out_path = tmp_path / "export.jsonl"
    out_path.write_text("stale", encoding="utf-8")
    os.chmod(out_path, 0o644)

    exit_code, _message = cmd_dump(output=str(out_path), context_path=str(root))

    assert exit_code == 0
    assert stat.S_IMODE(out_path.stat().st_mode) == 0o600
