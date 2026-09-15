"""Shared task-history readers used by multiple CLI command surfaces.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/10-CLI_Interface.md [CLI-1.2], [CLI-1.2.1]
- docs/specifications/12-Pipeline_Composition_and_UX.md [PL-5.2], [PL-5.3]
"""

from __future__ import annotations

from typing import Any

from weft._constants import PIPELINE_RUNTIME_METADATA_KEY, WEFT_GLOBAL_LOG_QUEUE
from weft.context import WeftContext
from weft.core.queue_window import iter_broker_queue_json_entries, queue_broker


def load_latest_taskspec_payload(
    context: WeftContext,
    tid: str,
    *,
    broker: Any | None = None,
) -> dict[str, Any] | None:
    """Read fresh TaskSpec history, borrowing a bounded owner's broker [MF-5]."""

    def _scan_history(*, since_timestamp: int | None) -> dict[str, Any] | None:
        latest_taskspec: dict[str, Any] | None = None
        for payload, _timestamp in iter_broker_queue_json_entries(
            db,
            WEFT_GLOBAL_LOG_QUEUE,
            since_timestamp=since_timestamp,
        ):
            if payload.get("tid") != tid:
                continue
            taskspec = payload.get("taskspec")
            if isinstance(taskspec, dict):
                latest_taskspec = taskspec
        return latest_taskspec

    with queue_broker(context, WEFT_GLOBAL_LOG_QUEUE, broker=broker) as db:
        since_timestamp = int(tid) - 1 if tid.isdigit() else None
        latest_taskspec = _scan_history(since_timestamp=since_timestamp)
        if latest_taskspec is not None or since_timestamp is None:
            return latest_taskspec
        return _scan_history(since_timestamp=None)


def is_pipeline_taskspec_payload(taskspec_payload: dict[str, Any] | None) -> bool:
    """Return ``True`` when a logged taskspec payload represents a pipeline task."""
    if not isinstance(taskspec_payload, dict):
        return False
    metadata = taskspec_payload.get("metadata")
    return isinstance(metadata, dict) and metadata.get("role") == "pipeline"


def pipeline_status_queue_name(
    tid: str,
    taskspec_payload: dict[str, Any] | None,
) -> str | None:
    """Return the pipeline status queue name for a logged pipeline taskspec."""
    if not is_pipeline_taskspec_payload(taskspec_payload):
        return None
    if not isinstance(taskspec_payload, dict):
        return None
    metadata = taskspec_payload.get("metadata")
    if not isinstance(metadata, dict):
        return None
    runtime = metadata.get(PIPELINE_RUNTIME_METADATA_KEY)
    if isinstance(runtime, dict):
        queues = runtime.get("queues")
        if isinstance(queues, dict):
            queue_name = queues.get("status")
            if isinstance(queue_name, str) and queue_name:
                return queue_name
    return f"P{tid}.status"
