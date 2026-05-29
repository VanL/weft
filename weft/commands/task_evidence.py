"""Compatibility re-exports for shared task evidence helpers.

The implementation lives in ``weft.core.task_evidence`` so foreground commands,
client helpers, status/result reconciliation, and the supervised TaskMonitor use
one classification contract.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-3], [MF-5]
- docs/specifications/10-CLI_Interface.md [CLI-1.2], [CLI-1.2.1]
"""

from __future__ import annotations

from weft._constants import STATUS_RUNTIMELESS_STALE_AFTER_SECONDS, WRAPPER_LOST_ERROR
from weft.core.task_evidence import (
    QueueAckTarget,
    QueueMessageCounts,
    TaskEvidenceSnapshot,
    TaskTerminalSnapshot,
    bounded_log_terminal_evidence,
    claimed_outbox_result_evidence,
    coerce_terminal_envelope,
    control_queue_names_for_tid,
    known_tid_evidence,
    log_terminal_evidence,
    peek_final_outbox_evidence,
    peek_terminal_ctrl_out_evidence,
    ping_pong_evidence,
    queue_message_counts,
    queue_names_for_tid,
    reconciliation_for_claimed_result_without_terminal,
    reconciliation_for_live_pong,
    reconciliation_for_result_without_terminal,
    reconciliation_for_terminal_ctrl_out,
    return_code_from_event,
    runtime_evidence,
    select_terminal_envelope,
    split_stdio,
    stale_observer_evidence,
    task_is_interactive_payload,
    task_is_persistent_payload,
    task_local_terminal_evidence,
    terminal_error_message,
    terminal_snapshot_from_evidence,
    terminal_status_from_event,
)

__all__ = [
    "QueueAckTarget",
    "QueueMessageCounts",
    "STATUS_RUNTIMELESS_STALE_AFTER_SECONDS",
    "TaskEvidenceSnapshot",
    "TaskTerminalSnapshot",
    "WRAPPER_LOST_ERROR",
    "bounded_log_terminal_evidence",
    "claimed_outbox_result_evidence",
    "coerce_terminal_envelope",
    "control_queue_names_for_tid",
    "known_tid_evidence",
    "log_terminal_evidence",
    "peek_final_outbox_evidence",
    "peek_terminal_ctrl_out_evidence",
    "ping_pong_evidence",
    "queue_message_counts",
    "queue_names_for_tid",
    "reconciliation_for_claimed_result_without_terminal",
    "reconciliation_for_live_pong",
    "reconciliation_for_result_without_terminal",
    "reconciliation_for_terminal_ctrl_out",
    "return_code_from_event",
    "runtime_evidence",
    "select_terminal_envelope",
    "stale_observer_evidence",
    "split_stdio",
    "task_is_interactive_payload",
    "task_is_persistent_payload",
    "task_local_terminal_evidence",
    "terminal_error_message",
    "terminal_snapshot_from_evidence",
    "terminal_status_from_event",
]
