"""Import Weft database state from JSONL format."""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from simplebroker import Queue
from simplebroker.db import BrokerDB
from weft.context import WeftContext, build_context


@dataclass
class ImportReport:
    """Summary of what would be imported or was imported."""

    aliases_to_create: dict[str, str] = field(default_factory=dict)
    aliases_to_update: dict[str, tuple[str, str]] = field(
        default_factory=dict
    )  # old -> new target
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

        # Metadata info
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

        # Aliases
        if self.aliases_to_create:
            lines.append(f"  Aliases to create: {len(self.aliases_to_create)}")
            for alias, target in list(self.aliases_to_create.items())[
                :5
            ]:  # Show first 5
                lines.append(f"    - {alias} -> {target}")
            if len(self.aliases_to_create) > 5:
                lines.append(f"    ... and {len(self.aliases_to_create) - 5} more")

        if self.aliases_to_update:
            lines.append(f"  Aliases to update: {len(self.aliases_to_update)}")
            for alias, (old, new) in list(self.aliases_to_update.items())[:3]:
                lines.append(f"    - {alias}: {old} -> {new}")
            if len(self.aliases_to_update) > 3:
                lines.append(f"    ... and {len(self.aliases_to_update) - 3} more")

        # Queues
        if self.queues_to_create:
            lines.append(f"  Queues to create: {len(self.queues_to_create)}")
            for queue_name in self.queues_to_create[:5]:
                msg_count = self.message_counts_by_queue.get(queue_name, 0)
                lines.append(f"    - {queue_name} ({msg_count} messages)")
            if len(self.queues_to_create) > 5:
                lines.append(f"    ... and {len(self.queues_to_create) - 5} more")

        # Summary
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

        # Warnings
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


def _parse_metadata_section(input_lines: list[str]) -> dict[str, Any]:
    """Parse the metadata section from the beginning of the file."""
    metadata = {}

    # Look for the first line which should be the meta record
    if input_lines:
        first_line = input_lines[0]
        try:
            record = json.loads(first_line)
            if record.get("type") == "meta":
                # Copy all fields except "type" as metadata
                metadata = {k: v for k, v in record.items() if k != "type"}
        except json.JSONDecodeError:
            pass

    return metadata


def _validate_compatibility(metadata: dict[str, Any]) -> list[str]:
    """Validate that the import is compatible with current version."""
    warnings = []

    schema_version = metadata.get("schema_version")
    if not schema_version:
        warnings.append("No schema version found in export")
    # schema_version from simplebroker is an integer
    elif schema_version not in [4]:
        warnings.append(f"Schema version {schema_version} may not be fully compatible")

    return warnings


def _analyze_import_data(input_file: TextIO, context: WeftContext) -> ImportReport:
    """Analyze the import data and return a preview report."""
    # Read all lines to analyze
    input_lines = [line.strip() for line in input_file if line.strip()]

    report = ImportReport()

    # Parse metadata
    report.metadata = _parse_metadata_section(input_lines)
    report.validation_warnings.extend(_validate_compatibility(report.metadata))

    # Get existing state
    try:
        with BrokerDB(str(context.database_path)) as db:
            try:
                existing_aliases = dict(db.list_aliases())
            except Exception:
                existing_aliases = {}

            try:
                existing_queues = {name for name, _count in db.list_queues()}
            except Exception:
                existing_queues = set()
    except Exception:
        existing_aliases = {}
        existing_queues = set()

    # Analyze each record
    for line_num, line in enumerate(input_lines, 1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            report.validation_warnings.append(f"Line {line_num}: Invalid JSON")
            continue

        record_type = record.get("type")

        if record_type == "alias":
            alias = record.get("alias")
            target = record.get("target")
            if not alias or not target:
                report.validation_warnings.append(
                    f"Line {line_num}: Invalid alias record"
                )
                continue

            if alias in existing_aliases:
                if existing_aliases[alias] != target:
                    report.alias_conflicts.add(alias)
            else:
                report.aliases_to_create[alias] = target

        elif record_type == "message":
            queue_name = record.get("queue")
            timestamp = record.get("timestamp")

            if not queue_name:
                report.validation_warnings.append(
                    f"Line {line_num}: Missing queue name"
                )
                continue

            if not isinstance(timestamp, int) or timestamp < 0:
                report.validation_warnings.append(f"Line {line_num}: Invalid timestamp")
                continue

            # Track queue creation
            if (
                queue_name not in existing_queues
                and queue_name not in report.queues_to_create
            ):
                report.queues_to_create.append(queue_name)

            # Count messages per queue
            report.message_counts_by_queue[queue_name] = (
                report.message_counts_by_queue.get(queue_name, 0) + 1
            )
            report.total_messages += 1

            # Track timestamp range
            if report.timestamp_range[0] is None:
                report.timestamp_range = (timestamp, timestamp)
            else:
                min_ts, max_ts = report.timestamp_range
                report.timestamp_range = (
                    min(min_ts or timestamp, timestamp),
                    max(max_ts or timestamp, timestamp),
                )

    return report


def _execute_import(input_file: TextIO, context: WeftContext) -> ImportReport:
    """Execute the actual import operation."""
    # Create backup of current database
    backup_path = None
    try:
        if context.database_path.exists():
            backup_path = Path(tempfile.mktemp(suffix=".backup.db"))
            shutil.copy2(context.database_path, backup_path)
    except Exception as exc:
        raise ImportError(f"Failed to create backup: {exc}") from exc

    try:
        with BrokerDB(str(context.database_path)) as db:
            # Get current state for reporting
            try:
                existing_aliases = dict(db.list_aliases())
            except Exception:
                existing_aliases = {}

            try:
                existing_queues = {name for name, _count in db.list_queues()}
            except Exception:
                existing_queues = set()

            report = ImportReport()

            # Process each line
            queue_cache: dict[str, Queue] = {}

            for line in input_file:
                line = line.strip()
                if not line:
                    continue

                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                record_type = record.get("type")

                if record_type == "meta":
                    # Copy all fields except "type" as metadata
                    report.metadata.update(
                        {k: v for k, v in record.items() if k != "type"}
                    )

                elif record_type == "alias":
                    alias = record.get("alias")
                    target = record.get("target")
                    if alias and target:
                        # Track changes
                        if alias in existing_aliases:
                            if existing_aliases[alias] != target:
                                report.alias_conflicts.add(alias)
                                continue
                        else:
                            report.aliases_to_create[alias] = target

                        db.add_alias(alias, target)

                elif record_type == "message":
                    queue_name = record.get("queue")
                    timestamp = record.get("timestamp")
                    body = record.get("body")

                    if queue_name and isinstance(timestamp, int) and body is not None:
                        # Track queue creation
                        if (
                            queue_name not in existing_queues
                            and queue_name not in report.queues_to_create
                        ):
                            report.queues_to_create.append(queue_name)

                        queue = queue_cache.get(queue_name)
                        if queue is None:
                            queue = Queue(
                                queue_name,
                                db_path=str(context.database_path),
                                persistent=True,
                                config=context.broker_config,
                            )
                            queue_cache[queue_name] = queue

                        queue.write(str(body))

                        # Update counts
                        report.message_counts_by_queue[queue_name] = (
                            report.message_counts_by_queue.get(queue_name, 0) + 1
                        )
                        report.total_messages += 1

                        # Track timestamp range
                        if report.timestamp_range[0] is None:
                            report.timestamp_range = (timestamp, timestamp)
                        else:
                            min_ts, max_ts = report.timestamp_range
                            report.timestamp_range = (
                                min(min_ts or timestamp, timestamp),
                                max(max_ts or timestamp, timestamp),
                            )

    except Exception as exc:
        # Restore from backup if import failed
        if backup_path and backup_path.exists():
            try:
                shutil.copy2(backup_path, context.database_path)
            except Exception:
                pass  # Best effort restore
        raise ImportError(f"Import failed: {exc}") from exc

    finally:
        # Clean up backup
        if backup_path and backup_path.exists():
            try:
                backup_path.unlink()
            except Exception:
                pass

    return report


def cmd_load(
    *,
    input_file: str | None = None,
    dry_run: bool = False,
    context_path: str | None = None,
) -> tuple[int, str | None]:
    """Import database state from JSONL format.

    Args:
        input_file: Input file path, defaults to .weft/weft_export.jsonl
        dry_run: Preview what would be imported without making changes
        context_path: Weft context directory

    Returns:
        (exit_code, message)
    """
    try:
        context = build_context(spec_context=context_path)
    except Exception as exc:
        return 1, f"weft load: failed to resolve context: {exc}"

    # Determine input path
    if input_file is None:
        input_path = context.weft_dir / "weft_export.jsonl"
    else:
        input_path = Path(input_file)
        if not input_path.is_absolute():
            input_path = Path.cwd() / input_path

    # Check input file exists
    if not input_path.exists():
        return 2, f"weft load: input file not found: {input_path}"

    try:
        with open(input_path, encoding="utf-8") as f:
            if dry_run:
                report = _analyze_import_data(f, context)
                if report.alias_conflicts:
                    conflict_list = ", ".join(sorted(report.alias_conflicts))
                    return (
                        3,
                        "weft load: alias conflicts detected; resolve and rerun\n"
                        f"Conflicting aliases: {conflict_list}",
                    )
                return 0, report.format_preview()
            else:
                report = _execute_import(f, context)
                if report.alias_conflicts:
                    conflict_list = ", ".join(sorted(report.alias_conflicts))
                    return (
                        3,
                        "weft load: alias conflicts detected; resolve and rerun\n"
                        f"Conflicting aliases: {conflict_list}",
                    )
                return 0, report.format_completion()

    except ImportError as exc:
        return 1, f"weft load: {exc}"
    except Exception as exc:
        return 1, f"weft load: import failed: {exc}"


__all__ = ["cmd_load", "ImportReport"]
