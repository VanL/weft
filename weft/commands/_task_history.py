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
from weft.helpers import iter_queue_json_entries


def load_latest_taskspec_payload(
    context: WeftContext,
    tid: str,
) -> dict[str, Any] | None:
    """Return the latest logged TaskSpec payload for ``tid``."""
    log_queue = context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=False)
    latest_taskspec: dict[str, Any] | None = None
    for payload, _timestamp in iter_queue_json_entries(log_queue):
        if payload.get("tid") != tid:
            continue
        taskspec = payload.get("taskspec")
        if isinstance(taskspec, dict):
            latest_taskspec = taskspec
    return latest_taskspec


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
