"""Shared task-history readers used by multiple CLI command surfaces.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/10-CLI_Interface.md [CLI-1.2], [CLI-1.2.1]
- docs/specifications/12-Pipeline_Composition_and_UX.md [PL-5.2], [PL-5.3]
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from simplebroker import Queue
from weft._constants import PIPELINE_RUNTIME_METADATA_KEY
from weft.context import WeftContext
from weft.core.monitor.store import (
    MonitorStoreNotInitialized,
    MonitorTaskCollationRecord,
    open_monitor_store,
)
from weft.core.task_evidence import (
    TERMINAL_TASK_STATUSES,
    TaskEvidenceSnapshot,
    read_task_history,
)

logger = logging.getLogger(__name__)


def load_latest_taskspec_payload(
    context: WeftContext,
    tid: str,
    *,
    broker: Any | None = None,
    task_events: Sequence[tuple[dict[str, Any], int]] | None = None,
) -> dict[str, Any] | None:
    """Fold one acquired task history or read it once with the core reader.

    An empty loan is acquired absence; only ``None`` requests acquisition.
    Rows come from the shared reader in ascending message-ID order.

    Spec: [MF-5]
    """
    if task_events is None:
        task_events, _horizon = read_task_history(context, tid, broker=broker)
    latest_taskspec: dict[str, Any] | None = None
    for payload, _timestamp in task_events:
        taskspec = payload.get("taskspec")
        if isinstance(taskspec, dict):
            latest_taskspec = taskspec
    return latest_taskspec


def read_monitor_task_record(
    context: WeftContext,
    tid: str,
    *,
    queue: Queue | None = None,
) -> tuple[MonitorTaskCollationRecord | None, str | None]:
    """Read retained Monitor evidence without schema maintenance.

    Absence includes an uninitialized store. Other ordinary failures remain
    diagnostic errors; callers own terminal qualification and presentation.

    Spec: [MF-5], [SB-0.4a]
    """
    try:
        store = open_monitor_store(context, config=context.config, queue=queue)
        return store.get_task(tid), None
    except MonitorStoreNotInitialized:
        return None, None
    except Exception as exc:  # pragma: no cover - corrupt store reads remain diagnostic
        logger.debug("Monitor task record read failed for %s", tid, exc_info=True)
        return None, str(exc)


def monitor_terminal_evidence(
    record: MonitorTaskCollationRecord | None,
    *,
    acquired_evidence: TaskEvidenceSnapshot | None = None,
) -> TaskEvidenceSnapshot | None:
    """Qualify retained terminal evidence; callers apply MF-5 source priority.

    Nonterminal raw history alone does not prove a live runtime. The existing
    runtime/control classifiers determine whether stronger evidence exists.
    Claimed result residue ends an observation but is a recovery diagnostic,
    not terminal task proof; qualified retained proof outranks that diagnostic.

    Spec: [MF-5]
    """
    if acquired_evidence is not None and (
        (
            acquired_evidence.terminal
            and acquired_evidence.classification != "claimed_result_without_terminal"
        )
        or acquired_evidence.classification in {"live", "live_pong"}
    ):
        return None
    if record is None or not record.terminal_seen:
        return None
    status = record.terminal_status
    if status is None or status not in TERMINAL_TASK_STATUSES:
        return None
    error = record.state.get("error")
    return TaskEvidenceSnapshot(
        tid=record.tid,
        status=status,
        classification="terminal_monitor_store",
        source="monitor_store",
        terminal=True,
        taskspec_payload=record.taskspec_summary,
        observed_at=record.terminal_message_id,
        started_at=record.started_at_ns,
        completed_at=record.completed_at_ns,
        error=error if isinstance(error, str) else None,
        return_code=record.return_code,
    )


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
