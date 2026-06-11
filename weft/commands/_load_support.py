"""Import Weft broker state from SimpleBroker dump format.

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

from simplebroker import load_lines
from simplebroker.ext import BrokerError, IntegrityError
from weft._constants import (
    SIMPLEBROKER_DUMP_FORMAT,
    SIMPLEBROKER_DUMP_VERSION,
    SQLITE_SNAPSHOT_SUFFIXES,
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
    message_id: int
    body: str


@dataclass
class ImportReport:
    """Summary of what would be imported or was imported."""

    aliases_to_create: dict[str, str] = field(default_factory=dict)
    alias_conflicts: set[str] = field(default_factory=set)
    queues_to_create: list[str] = field(default_factory=list)
    message_counts_by_queue: dict[str, int] = field(default_factory=dict)
    total_messages: int = 0
    message_id_range: tuple[int | None, int | None] = field(
        default_factory=lambda: (None, None)
    )
    validation_warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def format_preview(self) -> str:
        """Format a human-readable preview of the import."""
        lines = ["Import Preview:"]

        if self.metadata:
            dump_format = self.metadata.get("format", "unknown")
            dump_version = self.metadata.get("version", "unknown")
            source_backend = self.metadata.get("backend", "unknown")
            lines.extend(
                [
                    f"  Source: {dump_format} v{dump_version}",
                    f"  Backend: {source_backend}",
                    "",
                ]
            )

        if self.aliases_to_create:
            lines.append(f"  Aliases to create: {len(self.aliases_to_create)}")
            for alias, target in list(self.aliases_to_create.items())[:5]:
                lines.append(f"    - {alias} -> {target}")
            if len(self.aliases_to_create) > 5:
                lines.append(f"    ... and {len(self.aliases_to_create) - 5} more")

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

        if (
            self.message_id_range[0] is not None
            and self.message_id_range[1] is not None
        ):
            lines.append(
                f"  Message ID range: {self.message_id_range[0]} to {self.message_id_range[1]}"
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
    header_line: str | None = None
    apply_lines: list[str] = field(default_factory=list)


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


def _dump_error(line_number: int, problem: str) -> ValueError:
    """Return a line-numbered dump parse error."""

    return ValueError(f"invalid dump input at line {line_number}: {problem}")


def _dump_line(record: dict[str, Any]) -> str:
    """Serialize one normalized SimpleBroker dump record."""

    return json.dumps(record, ensure_ascii=False, sort_keys=True)


def _is_runtime_name(name: str) -> bool:
    """Return whether a dump name targets Weft runtime-only state."""

    return name.startswith(WEFT_STATE_QUEUE_PREFIX)


def _build_import_plan(input_file: TextIO, context: WeftContext) -> ImportPlan:
    """Parse import records and enrich them with destination broker state."""

    plan = _parse_import_file(input_file)
    _enrich_import_plan(plan, context)
    return plan


def _parse_import_file(input_file: TextIO) -> ImportPlan:
    """Parse the JSONL input into one reusable import plan."""

    plan = ImportPlan()
    skipped_runtime: set[str] = set()
    header_seen = False

    for line_num, raw_line in enumerate(input_file, 1):
        line = raw_line.strip()
        if not line:
            continue

        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise _dump_error(line_num, f"malformed JSON ({exc.msg})") from exc

        if not isinstance(record, dict):
            raise _dump_error(line_num, "record must be a JSON object")

        record_type = record.get("type")

        if not header_seen:
            if record_type != "header":
                raise _dump_error(line_num, "first record must be the dump header")
            if record.get("format") != SIMPLEBROKER_DUMP_FORMAT:
                raise _dump_error(line_num, "unrecognized dump format")
            if record.get("version") != SIMPLEBROKER_DUMP_VERSION:
                raise _dump_error(
                    line_num,
                    f"unsupported dump version {record.get('version')!r} "
                    f"(supported: {SIMPLEBROKER_DUMP_VERSION})",
                )
            header_seen = True
            plan.report.metadata.update(
                {key: value for key, value in record.items() if key != "type"}
            )
            plan.header_line = _dump_line(record)
            continue

        if record_type == "alias":
            alias = record.get("alias")
            target = record.get("target")
            if not isinstance(alias, str) or not isinstance(target, str):
                raise _dump_error(
                    line_num,
                    "alias record requires string 'alias' and 'target' fields",
                )

            runtime_names = tuple(
                name for name in (alias, target) if _is_runtime_name(name)
            )
            if runtime_names:
                warning_key = " / ".join(runtime_names)
                if warning_key not in skipped_runtime:
                    plan.report.validation_warnings.append(
                        f"Skipping runtime alias {alias} -> {target}"
                    )
                    skipped_runtime.add(warning_key)
                continue

            alias_record = AliasImportRecord(alias=alias, target=target)
            plan.alias_records.append(alias_record)
            continue

        if record_type == "message":
            queue_name = record.get("queue")
            body = record.get("body")
            message_id = record.get("id")
            if not isinstance(queue_name, str) or not isinstance(body, str):
                raise _dump_error(
                    line_num,
                    "message record requires string 'queue' and 'body' fields",
                )
            if (
                isinstance(message_id, bool)
                or not isinstance(message_id, int)
                or message_id < 0
            ):
                raise _dump_error(
                    line_num,
                    "message record requires a non-negative integer 'id' field",
                )

            if _is_runtime_name(queue_name):
                if queue_name not in skipped_runtime:
                    plan.report.validation_warnings.append(
                        f"Skipping runtime queue {queue_name}"
                    )
                    skipped_runtime.add(queue_name)
                continue

            plan.message_records.append(
                MessageImportRecord(
                    queue_name=queue_name,
                    message_id=message_id,
                    body=body,
                )
            )
            plan.report.message_counts_by_queue[queue_name] = (
                plan.report.message_counts_by_queue.get(queue_name, 0) + 1
            )
            plan.report.total_messages += 1
            plan.report.message_id_range = _update_message_id_range(
                plan.report.message_id_range,
                message_id,
            )
            continue

        if record_type == "header":
            raise _dump_error(line_num, "duplicate header")

        raise _dump_error(line_num, f"unknown record type {record_type!r}")

    if not header_seen:
        raise ValueError(
            "invalid dump input: missing header (is this a simplebroker dump?)"
        )

    return plan


def _build_apply_lines(plan: ImportPlan) -> list[str]:
    """Build the normalized dump stream that should be applied."""

    if plan.header_line is None:
        raise ValueError("import plan is missing dump header")

    lines = [plan.header_line]
    lines.extend(
        _dump_line({"type": "alias", "alias": record.alias, "target": record.target})
        for record in plan.alias_records
        if record.alias in plan.report.aliases_to_create
    )
    lines.extend(
        _dump_line(
            {
                "type": "message",
                "queue": record.queue_name,
                "body": record.body,
                "id": record.message_id,
            }
        )
        for record in plan.message_records
    )
    return lines


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
            existing_queues = set(broker.list_queues())
        except (
            BrokerError,
            OSError,
            RuntimeError,
        ):  # pragma: no cover - broker probe best effort
            existing_queues = set()

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

    plan.apply_lines = _build_apply_lines(plan)


def _execute_import(plan: ImportPlan, context: WeftContext) -> ImportReport:
    """Apply a preflighted import plan while preserving broker message IDs."""

    _ensure_exact_message_id_import_supported(plan, context)
    snapshot = _sqlite_snapshot_if_file_backed(context)
    writes_started = False

    try:
        with context.broker() as broker:
            writes_started = bool(plan.apply_lines[1:])
            load_lines(broker, plan.apply_lines)

    except Exception as exc:  # pragma: no cover - rollback must run on any failure
        failure_detail = _format_apply_failure(exc)
        if snapshot is not None:
            try:
                snapshot.restore()
            except Exception as restore_exc:  # pragma: no cover - rollback failure
                raise ImportError(
                    "import failed and file-backed rollback failed: "
                    f"{failure_detail}; restore failed: {restore_exc}"
                ) from exc
            raise ImportError(
                f"import failed and restored file-backed snapshot: {failure_detail}"
            ) from exc

        if writes_started:
            raise ImportError(
                "import failed after writes began; partial import may have occurred: "
                f"{failure_detail}"
            ) from exc

        raise ImportError(f"import failed: {failure_detail}") from exc

    finally:
        if snapshot is not None:
            snapshot.cleanup()

    return plan.report


def _format_apply_failure(exc: Exception) -> str:
    """Return a user-facing apply failure detail."""

    if isinstance(exc, IntegrityError):
        return f"exact message ID import failed: {exc}"
    return str(exc)


def _ensure_exact_message_id_import_supported(
    plan: ImportPlan,
    context: WeftContext,
) -> None:
    """Fail before writes when the backend cannot preserve dump message IDs."""

    if not plan.message_records:
        return
    with context.broker() as broker:
        if callable(getattr(broker, "insert_messages", None)):
            return
    raise ImportError(
        "backend cannot preserve message IDs during import; "
        "refusing to load runnable broker state under new message IDs"
    )


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


def _update_message_id_range(
    message_id_range: tuple[int | None, int | None],
    message_id: int,
) -> tuple[int | None, int | None]:
    """Expand a message ID range to include the supplied value."""

    start, end = message_id_range
    if start is None or end is None:
        return (message_id, message_id)
    return (min(start, message_id), max(end, message_id))


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
