"""Structured public contracts for system commands [PY-2]."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft.commands.builtins import cmd_system_builtins
from weft.commands.dump import cmd_system_dump
from weft.commands.load import cmd_system_load
from weft.commands.prune import cmd_system_prune
from weft.commands.task_monitor import (
    TaskMonitorRecord,
    TaskMonitorResult,
    cmd_system_task_monitor,
)
from weft.commands.tidy import cmd_system_tidy
from weft.commands.types import (
    BuiltinSpecRecord,
    SystemDumpResult,
    SystemPruneResult,
    SystemTidyResult,
)
from weft.context import build_context

pytestmark = [pytest.mark.shared]


def test_cmd_system_tidy_returns_structured_target(tmp_path: Path) -> None:
    root = prepare_project_root(tmp_path / "proj")

    result = cmd_system_tidy(context=root)

    assert isinstance(result, SystemTidyResult)
    assert result.target


def test_cmd_system_dump_returns_exact_export_counts(tmp_path: Path) -> None:
    root = prepare_project_root(tmp_path / "proj")
    context = build_context(root)
    queue = context.queue("example.queue", persistent=True)
    try:
        queue.write("one")
        queue.write("two")
    finally:
        queue.close()
    with context.broker() as broker:
        broker.add_alias("example.alias", "example.queue")
    output = tmp_path / "export.jsonl"

    result = cmd_system_dump(output=str(output), context=root)

    assert result == SystemDumpResult(
        path=output,
        queues=1,
        messages=2,
        aliases=1,
        omitted_claimed_queues=0,
        omitted_claimed_messages=0,
    )


def test_cmd_system_builtins_returns_typed_inventory() -> None:
    result = cmd_system_builtins()

    assert result
    assert all(isinstance(item, BuiltinSpecRecord) for item in result)
    assert all(item.source == "builtin" for item in result)


def test_cmd_system_load_dry_run_returns_structured_counts(tmp_path: Path) -> None:
    source_root = prepare_project_root(tmp_path / "source")
    source = build_context(source_root)
    queue = source.queue("load.queue", persistent=True)
    try:
        queue.write("payload")
    finally:
        queue.close()
    dump_path = tmp_path / "load.jsonl"
    cmd_system_dump(output=str(dump_path), context=source_root)
    target_root = prepare_project_root(tmp_path / "target")

    result = cmd_system_load(input=str(dump_path), dry_run=True, context=target_root)

    assert result.imported is False
    assert result.message.startswith("Import Preview:")
    assert result.aliases_created == 0
    assert result.aliases_updated == 0
    assert result.queues_created == 1
    assert result.total_messages == 1


def test_cmd_system_prune_returns_lossless_structured_details(tmp_path: Path) -> None:
    root = prepare_project_root(tmp_path / "proj")

    result = cmd_system_prune(family="runtime-state", context=root)

    assert isinstance(result, SystemPruneResult)
    assert result.families == ("runtime-state",)
    assert result.applied is False
    assert result.candidates == 0
    assert result.deleted == 0
    assert result.failed == 0
    assert result.details["runtime_state"]["record_type"] == "runtime_prune_completed"


def test_cmd_system_task_monitor_returns_ordered_structured_records(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path / "proj")

    result = cmd_system_task_monitor(
        context=root,
        no_checkpoint=True,
        since=0,
    )

    assert isinstance(result, TaskMonitorResult)
    assert all(isinstance(record, TaskMonitorRecord) for record in result.records)
    assert [item.record["record_type"] for item in result.records] == [
        "monitor_run_started",
        "monitor_run_completed",
    ]


def test_cmd_system_task_monitor_follow_returns_idempotently_closable_stream(
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path / "proj")

    stream = cmd_system_task_monitor(context=root, follow=True)
    stream.close()
    stream.close()

    with pytest.raises(StopIteration):
        next(stream)
