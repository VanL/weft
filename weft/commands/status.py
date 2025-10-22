"""Status reporting helpers for the Weft CLI."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from simplebroker import Queue
from simplebroker.db import BrokerDB
from weft._constants import WEFT_SPAWN_REQUESTS_QUEUE, WEFT_WORKERS_REGISTRY_QUEUE
from weft.context import WeftContext, build_context
from weft.helpers import format_byte_size, format_timestamp_ns_relative

StatusMapping = Mapping[str, int | float | str | None]


def _to_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


@dataclass(frozen=True)
class BrokerStatusSnapshot:
    """Immutable container for broker status metrics."""

    total_messages: int
    last_timestamp: int
    db_size: int

    @classmethod
    def from_mapping(cls, data: StatusMapping) -> BrokerStatusSnapshot:
        """Create a snapshot from a generic mapping."""
        return cls(
            total_messages=_to_int(data.get("total_messages")),
            last_timestamp=_to_int(data.get("last_timestamp")),
            db_size=_to_int(data.get("db_size")),
        )

    def to_dict(self) -> dict[str, int]:
        """Return a JSON-serialisable dictionary."""
        return {
            "total_messages": self.total_messages,
            "last_timestamp": self.last_timestamp,
            "db_size": self.db_size,
        }

    def to_text(self) -> str:
        """Render a human-readable payload matching SimpleBroker output."""
        human_size = format_byte_size(self.db_size)
        relative_ts = format_timestamp_ns_relative(self.last_timestamp)

        timestamp_line = f"last_timestamp: {self.last_timestamp}"
        if relative_ts:
            timestamp_line += f" ({relative_ts})"

        size_line = f"db_size: {self.db_size} bytes ({human_size})"

        return "\n".join(
            (
                f"total_messages: {self.total_messages}",
                timestamp_line,
                size_line,
            )
        )


def _resolve_context(
    spec_context: str | os.PathLike[str] | None = None,
) -> WeftContext:
    """Resolve the Weft context respecting overrides and environment."""
    if spec_context:
        return build_context(spec_context=spec_context)

    env_context = os.environ.get("WEFT_CONTEXT")
    if env_context:
        return build_context(spec_context=env_context)

    return build_context(spec_context=os.getcwd())


def collect_status(ctx: WeftContext) -> BrokerStatusSnapshot:
    """Collect broker status information for *ctx*."""
    with BrokerDB(str(ctx.database_path)) as db:
        metrics = db.status()
    return BrokerStatusSnapshot.from_mapping(metrics)


def _collect_manager_records(ctx: WeftContext) -> list[dict[str, Any]]:
    queue = Queue(
        WEFT_WORKERS_REGISTRY_QUEUE,
        db_path=str(ctx.database_path),
        persistent=False,
        config=ctx.broker_config,
    )
    try:
        records_raw = cast(
            Sequence[tuple[str, int]] | None,
            queue.peek_many(limit=1000, with_timestamps=True),
        )
    except Exception:
        return []

    if not records_raw:
        return []

    records: dict[str, dict[str, Any]] = {}
    for entry, timestamp in records_raw:
        try:
            data = cast(dict[str, Any], json.loads(entry))
        except json.JSONDecodeError:
            continue
        tid = data.get("tid")
        if not tid:
            continue
        data["_timestamp"] = timestamp
        existing = records.get(tid)
        previous_timestamp = _to_int(existing.get("_timestamp")) if existing else -1
        if existing is None or previous_timestamp < timestamp:
            records[tid] = data

    formatted: list[dict[str, Any]] = []
    for record in records.values():
        timestamp = _to_int(record.get("_timestamp"))
        record.pop("_timestamp", None)
        record["timestamp"] = timestamp
        record.setdefault("requests", WEFT_SPAWN_REQUESTS_QUEUE)
        formatted.append(record)
    return formatted


def _format_manager_summary(records: list[dict[str, Any]]) -> str:
    if not records:
        return "Managers: none registered"

    lines = ["Managers:"]
    for record in records:
        tid = record.get("tid", "?")
        status = record.get("status", "unknown")
        role = record.get("role", "manager")
        pid = record.get("pid")
        requests = record.get("requests", WEFT_SPAWN_REQUESTS_QUEUE)
        outbox = record.get("outbox", "")
        timestamp = _to_int(record.get("timestamp"))
        relative_ts = format_timestamp_ns_relative(timestamp)
        ts_line = f"timestamp: {timestamp}"
        if relative_ts:
            ts_line += f" ({relative_ts})"

        lines.extend(
            [
                f"  - tid: {tid}",
                f"    role: {role}",
                f"    status: {status}",
                f"    pid: {pid if pid is not None else 'n/a'}",
                f"    requests: {requests}",
                f"    outbox: {outbox}",
                f"    {ts_line}",
            ]
        )

    return "\n".join(lines)


def cmd_status(
    *,
    json_output: bool = False,
    spec_context: str | os.PathLike[str] | None = None,
) -> tuple[int, str | None]:
    """Implement the ``weft status`` command."""
    try:
        context = _resolve_context(spec_context)
        snapshot = collect_status(context)
        managers = _collect_manager_records(context)
    except Exception as exc:  # pragma: no cover - defensive guard
        return 1, f"weft: failed to retrieve status: {exc}"

    if json_output:
        payload = json.dumps(
            {"broker": snapshot.to_dict(), "managers": managers},
            ensure_ascii=False,
        )
    else:
        payload = "\n".join((snapshot.to_text(), _format_manager_summary(managers)))

    return 0, payload


__all__ = ["BrokerStatusSnapshot", "collect_status", "cmd_status"]
