"""Import Weft broker state from JSONL format.

Spec references:
- docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]
- docs/specifications/10-CLI_Interface.md [CLI-6]
"""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from simplebroker.ext import BrokerError
from weft._constants import (
    SQLITE_SNAPSHOT_SUFFIXES,
    SUPPORTED_IMPORT_SCHEMA_VERSIONS,
    WEFT_STATE_QUEUE_PREFIX,
)
from weft.context import WeftContext, build_context


@dataclass(frozen=True)
class AliasImportRecord:
    """Alias entry parsed from a dump file."""

    alias: str
    target: str


@dataclass(frozen=True)
class MessageImportRecord:
    """Queue message entry parsed from a dump file."""

    queue_name: str
    timestamp: int
    body: str


@dataclass
class ImportReport:
    """Summary of what would be imported or was imported."""

    aliases_to_create: dict[str, str] = field(default_factory=dict)
    aliases_to_update: dict[str, tuple[str, str]] = field(default_factory=dict)
    alias_conflicts: set[str] = field(default_factory=set)
    queues_to_create: list[str] = field(default_factory=list)
    message_counts_by_queue: dict[str, int] = field(default_factory=dict)
    total_messages: int = 0
    timestamp_range: tuple[int | None, int | None] = field(
        default_factory=lambda: (None, None)
    )
    validation_warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def format_preview(self) -> str:
        """Format a human-readable preview of the import."""
        lines = ["Import Preview:"]

        if self.metadata:
            schema_version = self.metadata.get("schema_version", "unknown")
            export_timestamp = self.metadata.get("export_timestamp", "unknown")
            source_context = self.metadata.get("context_path", "unknown")
            lines.extend(
                [
                    f"  Source: {source_context} (schema v{schema_version})",
                    f"  Exported: {export_timestamp}",
                    "",
                ]
            )

        if self.aliases_to_create:
            lines.append(f"  Aliases to create: {len(self.aliases_to_create)}")
            for alias, target in list(self.aliases_to_create.items())[:5]:
                lines.append(f"    - {alias} -> {target}")
            if len(self.aliases_to_create) > 5:
                lines.append(f"    ... and {len(self.aliases_to_create) - 5} more")

        if self.aliases_to_update:
            lines.append(f"  Aliases to update: {len(self.aliases_to_update)}")
            for alias, (old, new) in list(self.aliases_to_update.items())[:3]:
                lines.append(f"    - {alias}: {old} -> {new}")
            if len(self.aliases_to_update) > 3:
                lines.append(f"    ... and {len(self.aliases_to_update) - 3} more")

        if self.queues_to_create:
            lines.append(f"  Queues to create: {len(self.queues_to_create)}")
            for queue_name in self.queues_to_create[:5]:
                msg_count = self.message_counts_by_queue.get(queue_name, 0)
                lines.append(f"    - {queue_name} ({msg_count} messages)")
            if len(self.queues_to_create) > 5:
                lines.append(f"    ... and {len(self.queues_to_create) - 5} more")

        lines.extend(
            [
                "",
                f"  Total messages: {self.total_messages:,}",
            ]
        )

        if self.timestamp_range[0] is not None and self.timestamp_range[1] is not None:
            lines.append(
                f"  Timestamp range: {self.timestamp_range[0]} to {self.timestamp_range[1]}"
            )

        if self.validation_warnings:
            lines.extend(
                [
                    "",
                    f"  Warnings: {len(self.validation_warnings)}",
                ]
            )
            for warning in self.validation_warnings[:3]:
                lines.append(f"    - {warning}")
            if len(self.validation_warnings) > 3:
                lines.append(f"    ... and {len(self.validation_warnings) - 3} more")

        return "\n".join(lines)

    def format_completion(self) -> str:
        """Format a completion message."""
        parts = []
        if self.aliases_to_create:
            parts.append(f"Created {len(self.aliases_to_create)} aliases")
        if self.aliases_to_update:
            parts.append(f"Updated {len(self.aliases_to_update)} aliases")
        if self.queues_to_create:
            parts.append(f"Created {len(self.queues_to_create)} queues")
        parts.append(f"Imported {self.total_messages:,} messages")

        return "✓ " + ", ".join(parts) + "\nImport completed successfully."


@dataclass
class ImportPlan:
    """Parsed import data plus destination-state analysis."""

    report: ImportReport = field(default_factory=ImportReport)
    alias_records: list[AliasImportRecord] = field(default_factory=list)
    message_records: list[MessageImportRecord] = field(default_factory=list)


@dataclass(frozen=True)
class SQLiteSnapshot:
    """Best-effort file snapshot used to roll back sqlite imports."""

    database_path: Path
    snapshot_dir: Path

    def restore(self) -> None:
        """Restore the captured database and sidecar files."""

        for live_path in _sqlite_artifact_paths(self.database_path):
            snapshot_path = self.snapshot_dir / live_path.name
            if snapshot_path.exists():
                shutil.copy2(snapshot_path, live_path)
            elif live_path.exists():
                live_path.unlink()

    def cleanup(self) -> None:
        """Remove the temporary snapshot directory."""

        shutil.rmtree(self.snapshot_dir, ignore_errors=True)


def _validate_compatibility(metadata: dict[str, Any]) -> list[str]:
    """Validate that the import is compatible with the current schema."""

    warnings = []
    schema_version = metadata.get("schema_version")
    if not schema_version:
        warnings.append("No schema version found in export")
    elif schema_version not in SUPPORTED_IMPORT_SCHEMA_VERSIONS:
        warnings.append(f"Schema version {schema_version} may not be fully compatible")
    return warnings


def _build_import_plan(input_file: TextIO, context: WeftContext) -> ImportPlan:
    """Parse import records and enrich them with destination broker state."""

    plan = _parse_import_file(input_file)
    _enrich_import_plan(plan, context)
    return plan


def _parse_import_file(input_file: TextIO) -> ImportPlan:
    """Parse the JSONL input into one reusable import plan."""

    plan = ImportPlan()
    skipped_runtime: set[str] = set()

    for line_num, raw_line in enumerate(input_file, 1):
        line = raw_line.strip()
        if not line:
            continue

        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            plan.report.validation_warnings.append(f"Line {line_num}: Invalid JSON")
            continue

        record_type = record.get("type")

        if record_type == "meta":
            plan.report.metadata.update(
                {key: value for key, value in record.items() if key != "type"}
            )
            continue

        if record_type == "alias":
            alias = record.get("alias")
            target = record.get("target")
            if not alias or not target:
                plan.report.validation_warnings.append(
                    f"Line {line_num}: Invalid alias record"
                )
                continue

            plan.alias_records.append(AliasImportRecord(alias=alias, target=target))
            continue

        if record_type != "message":
            plan.report.validation_warnings.append(
                f"Line {line_num}: Unsupported record type"
            )
            continue

        queue_name = record.get("queue")
        timestamp = record.get("timestamp")
        body = record.get("body")

        if queue_name and queue_name.startswith(WEFT_STATE_QUEUE_PREFIX):
            if queue_name not in skipped_runtime:
                plan.report.validation_warnings.append(
                    f"Skipping runtime queue {queue_name}"
                )
                skipped_runtime.add(queue_name)
            continue

        if not queue_name:
            plan.report.validation_warnings.append(
                f"Line {line_num}: Missing queue name"
            )
            continue

        if not isinstance(timestamp, int) or timestamp < 0:
            plan.report.validation_warnings.append(
                f"Line {line_num}: Invalid timestamp"
            )
            continue

        if body is None:
            plan.report.validation_warnings.append(
                f"Line {line_num}: Missing message body"
            )
            continue

        plan.message_records.append(
            MessageImportRecord(
                queue_name=queue_name,
                timestamp=timestamp,
                body=str(body),
            )
        )
        plan.report.message_counts_by_queue[queue_name] = (
            plan.report.message_counts_by_queue.get(queue_name, 0) + 1
        )
        plan.report.total_messages += 1
        plan.report.timestamp_range = _update_timestamp_range(
            plan.report.timestamp_range,
            timestamp,
        )

    plan.report.validation_warnings.extend(
        _validate_compatibility(plan.report.metadata)
    )
    return plan


def _enrich_import_plan(plan: ImportPlan, context: WeftContext) -> None:
    """Compare parsed import records against the destination broker state."""

    with context.broker() as broker:
        try:
            existing_aliases = dict(broker.list_aliases())
        except (
            BrokerError,
            OSError,
            RuntimeError,
        ):  # pragma: no cover - broker probe best effort
            existing_aliases = {}

        try:
            existing_queues = {name for name, _count in broker.list_queues()}
        except (
            BrokerError,
            OSError,
            RuntimeError,
        ):  # pragma: no cover - broker probe best effort
            existing_queues = set()

        try:
            destination_meta = broker.get_meta()
        except (
            BrokerError,
            OSError,
            RuntimeError,
        ):  # pragma: no cover - broker probe best effort
            destination_meta = {}

    source_schema = plan.report.metadata.get("schema_version")
    destination_schema = destination_meta.get("schema_version")
    if (
        isinstance(source_schema, int)
        and isinstance(destination_schema, int)
        and source_schema != destination_schema
    ):
        plan.report.validation_warnings.append(
            "Destination schema version differs from the export schema version"
        )

    seen_create_queues: set[str] = set()
    for alias_record in plan.alias_records:
        existing_target = existing_aliases.get(alias_record.alias)
        if existing_target is None:
            plan.report.aliases_to_create[alias_record.alias] = alias_record.target
            continue
        if existing_target != alias_record.target:
            plan.report.alias_conflicts.add(alias_record.alias)

    for message_record in plan.message_records:
        if message_record.queue_name in existing_queues:
            continue
        if message_record.queue_name in seen_create_queues:
            continue
        seen_create_queues.add(message_record.queue_name)
        plan.report.queues_to_create.append(message_record.queue_name)


def _execute_import(plan: ImportPlan, context: WeftContext) -> ImportReport:
    """Apply a preflighted import plan using backend-neutral broker APIs."""

    snapshot = _sqlite_snapshot_if_file_backed(context)
    queue_cache: dict[str, Any] = {}
    writes_started = False

    try:
        with context.broker() as broker:
            for alias_record in plan.alias_records:
                if alias_record.alias not in plan.report.aliases_to_create:
                    continue
                writes_started = True
                broker.add_alias(alias_record.alias, alias_record.target)

        for message_record in plan.message_records:
            queue = queue_cache.get(message_record.queue_name)
            if queue is None:
                queue = context.queue(message_record.queue_name, persistent=True)
                queue_cache[message_record.queue_name] = queue
            writes_started = True
            queue.write(message_record.body)

    except Exception as exc:  # pragma: no cover - rollback must run on any failure
        _close_import_queues(queue_cache)

        if snapshot is not None:
            try:
                snapshot.restore()
            except Exception as restore_exc:  # pragma: no cover - rollback failure
                raise ImportError(
                    f"import failed and file-backed rollback failed: {exc}; restore failed: {restore_exc}"
                ) from exc
            raise ImportError(
                f"import failed and restored file-backed snapshot: {exc}"
            ) from exc

        if writes_started:
            raise ImportError(
                f"import failed after writes began; partial import may have occurred: {exc}"
            ) from exc

        raise ImportError(f"import failed: {exc}") from exc

    finally:
        _close_import_queues(queue_cache)
        if snapshot is not None:
            snapshot.cleanup()

    return plan.report


def _close_import_queues(queue_cache: dict[str, Any]) -> None:
    """Close any cached queue handles used during import."""

    for queue in queue_cache.values():
        close = getattr(queue, "close", None)
        if callable(close):
            close()


def _sqlite_snapshot_if_file_backed(context: WeftContext) -> SQLiteSnapshot | None:
    """Create a snapshot for sqlite/file-backed contexts before import apply."""

    if context.backend_name != "sqlite" or context.database_path is None:
        return None

    snapshot_dir = Path(tempfile.mkdtemp(prefix="weft-load-snapshot-"))
    for artifact_path in _sqlite_artifact_paths(context.database_path):
        if artifact_path.exists():
            shutil.copy2(artifact_path, snapshot_dir / artifact_path.name)

    return SQLiteSnapshot(
        database_path=context.database_path,
        snapshot_dir=snapshot_dir,
    )


def _sqlite_artifact_paths(database_path: Path) -> tuple[Path, ...]:
    """Return the sqlite database file plus common sidecar files."""

    return tuple(
        database_path
        if suffix == ""
        else database_path.with_name(f"{database_path.name}{suffix}")
        for suffix in SQLITE_SNAPSHOT_SUFFIXES
    )


def _update_timestamp_range(
    timestamp_range: tuple[int | None, int | None],
    timestamp: int,
) -> tuple[int | None, int | None]:
    """Expand a timestamp range to include the supplied value."""

    start, end = timestamp_range
    if start is None or end is None:
        return (timestamp, timestamp)
    return (min(start, timestamp), max(end, timestamp))


def _format_alias_conflicts(conflicts: set[str]) -> tuple[int, str]:
    """Return the standard alias-conflict exit payload."""

    conflict_list = ", ".join(sorted(conflicts))
    return (
        3,
        "weft load: alias conflicts detected; resolve and rerun\n"
        f"Conflicting aliases: {conflict_list}",
    )


def cmd_load(
    *,
    input_file: str | None = None,
    dry_run: bool = False,
    context_path: str | None = None,
) -> tuple[int, str | None]:
    """Import broker state from JSONL format.

    Args:
        input_file: Input file path, defaults to `weft_export.jsonl` under the
            active Weft metadata directory.
        dry_run: Preview what would be imported without making changes.
        context_path: Weft context directory.

    Returns:
        `(exit_code, message)`
    """

    try:
        context = build_context(spec_context=context_path)
    except Exception as exc:  # pragma: no cover - command error boundary
        return 1, f"weft load: failed to resolve context: {exc}"

    if input_file is None:
        input_path = context.weft_dir / "weft_export.jsonl"
    else:
        input_path = Path(input_file)
        if not input_path.is_absolute():
            input_path = Path.cwd() / input_path

    if not input_path.exists():
        return 2, f"weft load: input file not found: {input_path}"

    try:
        with open(input_path, encoding="utf-8") as handle:
            plan = _build_import_plan(handle, context)
    except Exception as exc:  # pragma: no cover - command error boundary
        return 1, f"weft load: import failed: {exc}"

    if plan.report.alias_conflicts:
        return _format_alias_conflicts(plan.report.alias_conflicts)

    if dry_run:
        return 0, plan.report.format_preview()

    try:
        report = _execute_import(plan, context)
    except ImportError as exc:
        return 1, f"weft load: {exc}"
    except Exception as exc:  # pragma: no cover - command error boundary
        return 1, f"weft load: import failed: {exc}"

    return 0, report.format_completion()


__all__ = ["cmd_load", "ImportReport"]
