"""Constants and configuration for Weft.

This module centralizes all constants and environment variable configuration
for Weft. Constants are immutable values that control various aspects
of the system's behavior, from message size limits to timing parameters.

Environment Variables:
    See the load_config() function for a complete list of supported environment
    variables and their default values.

Usage:
    from weft._constants import WEFT_DEBUG, load_config

    # Use constants directly
    if WEFT_DEBUG:
       #....

    # Load configuration once at module level
    _config = load_config()
    use_logging = _config["WEFT_LOGGING_ENABLED"]

"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from typing import Any, Final, Literal

from simplebroker import resolve_config as resolve_broker_config

# ==============================================================================
# VERSION INFORMATION
# ==============================================================================

__version__: Final[str] = "0.9.59"
"""Current version of Weft."""

# ==============================================================================
# PROGRAM IDENTIFICATION
# ==============================================================================

PROG_NAME: Final[str] = "weft"
"""Program name used in CLI help and error messages."""

# ==============================================================================
# EXIT CODES
# ==============================================================================

EXIT_SUCCESS: Final[int] = 0
"""Exit code for successful operations."""

EXIT_ERROR: Final[int] = 1
"""Exit code for general errors."""

# ==============================================================================
# TASKSPEC DEFAULTS
# ==============================================================================

# Version and Identification
# --------------------------
TASKSPEC_VERSION: Final[str] = "1.0"
"""Current TaskSpec schema version for future evolution."""

TASKSPEC_TID_LENGTH: Final[int] = 19
"""Required length for Task ID (19 digits from time.time_ns())."""

TASKSPEC_TID_SHORT_LENGTH: Final[int] = 10
"""Length for short TID display (last N digits for process titles)."""

# Spec Section Defaults
# ---------------------
DEFAULT_FUNCTION_TARGET: Final[str] = "weft.tasks:noop"
"""Default function target for tasks (no-op function)."""

DEFAULT_TIMEOUT: Final[float | None] = None
"""Default timeout in seconds. None means no timeout."""

DEFAULT_STREAM_OUTPUT: Final[bool] = False
"""Default for streaming output. False means all output in one message."""

DEFAULT_CLEANUP_ON_EXIT: Final[bool] = True
"""Default for cleaning up task queues on completion."""

DEFAULT_POLLING_INTERVAL: Final[float] = 1.0
"""Default psutil polling interval in seconds."""

ACTIVE_CONTROL_POLL_INTERVAL: Final[float] = 0.05
"""Internal control-loop polling interval for active task execution."""

TASK_LIFECYCLE_STATUS_VALUES: Final[frozenset[str]] = frozenset(
    (
        "created",
        "spawning",
        "running",
        "completed",
        "failed",
        "timeout",
        "cancelled",
        "killed",
    )
)
"""Allowed TaskSpec lifecycle status values."""

TERMINAL_TASK_LIFECYCLE_STATUS_VALUES: Final[frozenset[str]] = frozenset(
    ("completed", "failed", "timeout", "cancelled", "killed")
)
"""TaskSpec lifecycle statuses that cannot transition further."""

TASK_LIFECYCLE_ACTION_VALUES: Final[frozenset[str]] = frozenset(
    ("begin_spawn", "start_running", "complete", "fail", "timeout", "cancel", "kill")
)
"""Pure lifecycle reducer action values."""

TASK_LIFECYCLE_TRANSITION_SPECS: Final[tuple[tuple[str, str, str, str, str], ...]] = (
    (
        "created-to-spawning",
        "created",
        "spawning",
        "begin_spawn",
        "task spawn started",
    ),
    ("created-to-failed", "created", "failed", "fail", "task failed before start"),
    (
        "created-to-cancelled",
        "created",
        "cancelled",
        "cancel",
        "task cancelled before start",
    ),
    (
        "spawning-to-running",
        "spawning",
        "running",
        "start_running",
        "task running",
    ),
    (
        "spawning-to-completed",
        "spawning",
        "completed",
        "complete",
        "task completed during spawn",
    ),
    (
        "spawning-to-failed",
        "spawning",
        "failed",
        "fail",
        "task failed during spawn",
    ),
    (
        "spawning-to-timeout",
        "spawning",
        "timeout",
        "timeout",
        "task timed out during spawn",
    ),
    (
        "spawning-to-cancelled",
        "spawning",
        "cancelled",
        "cancel",
        "task cancelled during spawn",
    ),
    (
        "spawning-to-killed",
        "spawning",
        "killed",
        "kill",
        "task killed during spawn",
    ),
    ("running-to-completed", "running", "completed", "complete", "task completed"),
    ("running-to-failed", "running", "failed", "fail", "task failed"),
    ("running-to-timeout", "running", "timeout", "timeout", "task timed out"),
    ("running-to-cancelled", "running", "cancelled", "cancel", "task cancelled"),
    ("running-to-killed", "running", "killed", "kill", "task killed"),
)
"""TaskSpec lifecycle transition table as id/source/target/action/reason rows."""

CONTROL_CONVERGENCE_STATE_VALUES: Final[frozenset[str]] = frozenset(
    (
        "command_sent",
        "accepted",
        "terminal_observed",
        "runtime_dead_after_control",
        "escalating_runner",
        "escalating_host",
        "unknown",
    )
)
"""Command-control convergence reducer state values."""

TERMINAL_CONTROL_CONVERGENCE_STATE_VALUES: Final[frozenset[str]] = frozenset(
    ("terminal_observed", "runtime_dead_after_control", "unknown")
)
"""Command-control convergence terminal state values."""

CONTROL_CONVERGENCE_ACTION_VALUES: Final[frozenset[str]] = frozenset(
    (
        "wait",
        "accept_terminal",
        "accept_dead_runtime",
        "escalate_runner",
        "escalate_host",
        "report_unknown",
    )
)
"""Command-control convergence reducer action values."""

TASK_PROCESS_POLL_INTERVAL: Final[float] = 0.05
"""Polling interval for spawned task-process main loops between `process_once` calls."""

TASK_REACTOR_WAKEUP_MAX_SECONDS: Final[float] = 0.05
"""Maximum wait chunk while task worker lanes may produce local reactor results."""

TASK_INACTIVE_QUEUE_DISCOVERY_INTERVAL_SECONDS: Final[float] = 1.0
"""Minimum interval between broad inactive-queue discovery probes."""

TASK_WORKER_RESULT_QUEUE_MAXSIZE: Final[int] = 256
"""Maximum queued worker-result envelopes before worker lanes apply backpressure."""

TASK_WORKER_RESULT_DRAIN_MAX_PER_TURN: Final[int] = 64
"""Maximum worker-result envelopes applied in one reactor turn."""

CONSUMER_ACTIVE_WORKER_LANE: Final[str] = "consumer.active_work"
"""Worker lane name for broker-free Consumer target execution."""

CONSUMER_WORKER_EVENT_LANE: Final[str] = "consumer.worker_event"
"""Worker lane name for Consumer progress events returned to the reactor."""

MANAGER_CHILD_LAUNCH_WORKER_LANE: Final[str] = "manager.child_launch"
"""Worker lane name for broker-free Manager child process launch."""

TASK_MONITOR_PROCESSOR_WORKER_LANE: Final[str] = "task_monitor.processor"
"""Worker lane name for broker-free TaskMonitor custom processors."""

TASK_MONITOR_BUILTIN_CYCLE_WORKER_LANE: Final[str] = "task_monitor.builtin_cycle"
"""Worker lane name for TaskMonitor built-in monitor cycle work."""

TASK_MONITOR_CONTROL_CLEANUP_WORKER_LANE: Final[str] = "task_monitor.control_cleanup"
"""Worker lane name for TaskMonitor terminal task-local control cleanup."""

MANAGER_POLL_INTERVAL: Final[float] = TASK_PROCESS_POLL_INTERVAL
"""Polling interval for foreground manager-service loops."""

CONTROL_SURFACE_WAIT_TIMEOUT: Final[float] = 2.0
"""Maximum time to wait for durable terminal task state before CLI fallback."""

PONG_EXTENSION_KEY: Final[str] = "extended"
"""Optional PONG payload key for task-registered extension data."""

TASK_PING_TIMEOUT_SECONDS: Final[float] = 10.0
"""Default seconds `weft task ping` waits for a keyed PONG response."""

CONTROL_SURFACE_WAIT_INTERVAL: Final[float] = 0.05
"""Polling interval while waiting for task terminal state in CLI control flows."""

RESULT_SURFACE_WAIT_INTERVAL: Final[float] = 0.1
"""Maximum interval between CLI result-surface rescans when watcher wakeups are missed."""

QUEUE_CHANGE_MONITOR_WAITER_TIMEOUT_SECONDS: Final[float] = 1.0
"""Bounded wait chunk for multi-queue CLI activity monitors."""

QUEUE_CHANGE_MONITOR_JOIN_TIMEOUT_SECONDS: Final[float] = 2.0
"""Maximum time to join a CLI queue-change monitor thread during close."""

SPAWN_SUBMISSION_RECONCILIATION_TIMEOUT: Final[float] = 1.0
"""Default time budget for classifying a submitted spawn request via durable state."""

SPAWN_RESERVED_CLAIM_RECONCILIATION_TIMEOUT: Final[float] = 4.0
"""Time budget for a claimed spawn request to publish child evidence."""

STATUS_WATCH_MIN_INTERVAL: Final[float] = 0.1
"""Minimum poll interval for CLI status-watch loops to avoid broker-hot spins."""

AGENT_SESSION_READY_TIMEOUT_SECONDS: Final[float] = 30.0
"""Minimum startup-readiness budget for persistent agent session workers."""

RUNNER_DIAGNOSTICS_FIELD: Final[str] = "runner_diagnostics"
"""Task-log/result field for bounded runner-boundary diagnostic metadata."""

RUNNER_DIAGNOSTICS_MESSAGE_MAX_CHARS: Final[int] = 500
"""Maximum length for a single runner diagnostic message string."""

RUNNER_DIAGNOSTICS_TRACEBACK_MAX_CHARS: Final[int] = 4000
"""Maximum length for a runner diagnostic traceback tail."""

MANAGER_STARTUP_TIMEOUT_SECONDS: Final[float] = 10.0
"""Time budget for a detached manager to publish a stable active registry entry."""

MANAGER_REGISTRY_POLL_INTERVAL: Final[float] = 0.1
"""Polling interval while observing manager registry state during startup/shutdown."""

MANAGER_REGISTRY_HEARTBEAT_INTERVAL_SECONDS: Final[float] = 30.0
"""Interval for managers to refresh active registry records."""

MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS: Final[float] = 300.0
"""Age after which external-supervisor manager registry records are stale."""

MANAGER_PID_LIVENESS_RECHECK_INTERVAL: Final[float] = 0.5
"""Throttle for repeated PID-liveness probes during stop-if-absent manager waits."""

MANAGER_CHILD_EXIT_POLL_INTERVAL: Final[float] = 0.05
"""Timer cadence for Manager parent-side user child process reaping."""

MANAGER_CONTROL_DRAIN_MAX_MESSAGES: Final[int] = 32
"""Maximum manager control messages handled before yielding a manager turn."""

MANAGER_INTERNAL_SPAWN_DRAIN_MAX_MESSAGES: Final[int] = 128
"""Maximum internal spawn requests drained before yielding a manager turn."""

MANAGER_PUBLIC_SPAWN_DRAIN_MAX_MESSAGES: Final[int] = 128
"""Maximum public spawn requests drained before yielding a manager turn."""

MANAGER_DISPATCH_STALL_LOG_INTERVAL_SECONDS: Final[float] = 5.0
"""Minimum interval between public dispatch stalled operational warnings."""

MANAGER_STALLED_CONTROL_LOG_INTERVAL_SECONDS: Final[float] = 5.0
"""Minimum interval between repeated stalled manager-control warnings."""

MANAGER_STALLED_CONTROL_RETRY_SECONDS: Final[float] = 1.0
"""Delay before retrying a manager control message that did not acknowledge."""

MANAGER_CHILD_STARTUP_LIVENESS_GRACE_SECONDS: Final[float] = 1.0
"""Grace window before negative liveness can reap a just-launched Manager child."""

MANAGER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS: Final[float] = 5.0
"""Maximum time a Manager drain waits before forcefully reaping child processes."""

MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS: Final[float] = (
    MANAGER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS + 40.0
)
"""Default caller-side budget for observing Manager STOP completion.

This must stay materially greater than ``MANAGER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS``
because the Manager may use the full drain window before it forcefully reaps
tracked children, then needs backend and scheduler margin to publish and expose
its stopped registry record.
"""

MANAGER_COMPETING_STARTUP_GRACE_SECONDS: Final[float] = 0.5
"""Grace window for concurrent manager starts to yield to an existing winner."""

MANAGER_NAMESPACE_AMBIGUOUS_BACKLOG_GRACE_SECONDS: Final[float] = 2.0
"""Maximum ambiguous-incumbent age before pending public spawn backlog may start a helper manager."""

MANAGER_LEADERSHIP_PING_TIMEOUT_SECONDS: Final[float] = 0.05
"""Short PING budget for manager-owned leadership liveness fallback."""

MANAGER_LEADERSHIP_PING_CACHE_TTL_SECONDS: Final[float] = 1.0
"""How long manager-owned leadership checks reuse a candidate PING outcome."""

MANAGER_LEADERSHIP_CHECK_INTERVAL_SECONDS: Final[float] = 1.0
"""Timer cadence for ordinary manager leadership convergence checks."""

MANAGER_LEADERSHIP_DRAIN_REVALIDATE_SECONDS: Final[float] = 1.0
"""Interval for revalidating leader proof during voluntary leadership drain."""

MANAGER_TASK_CLASS_PATH: Final[str] = "weft.core.manager.Manager"
"""Import path for the runtime-owned Manager task class."""

MANAGER_STARTUP_LOG_DIRNAME: Final[str] = "manager-startup"
"""Subdirectory under the Weft logs directory used for detached manager bootstrap stderr."""

MANAGER_PONG_LIVE_AT_KEY: Final[str] = "_pong_live_at"
"""Internal registry key recording the timestamp of a matched manager PONG."""

MANAGER_LAUNCHER_SIGNAL_SUCCESS: Final[str] = "SUCCESS"
"""Parent-to-launcher signal indicating detached manager startup succeeded."""

MANAGER_LAUNCHER_SIGNAL_ABORT: Final[str] = "ABORT"
"""Parent-to-launcher signal requesting detached manager bootstrap abort."""

MANAGER_LAUNCHER_POLL_INTERVAL: Final[float] = 0.05
"""Polling interval for the detached manager bootstrap launcher wrapper."""

DEFAULT_REPORTING_INTERVAL: Final[Literal["transition"]] = "transition"
"""Default reporting interval. Either 'poll' or 'transition'."""

DEFAULT_ENABLE_PROCESS_TITLE: Final[bool] = True
"""Default for enabling OS process title updates for observability."""

DEFAULT_OUTPUT_SIZE_LIMIT_MB: Final[int] = 10
"""Default max output size before disk spill (SimpleBroker limit)."""

DEFAULT_WEFT_CONTEXT: Final[str | None] = None
"""Default weft context directory. None means auto-discovery."""

WEFT_DIRECTORY_NAME_DEFAULT: Final[str] = ".weft"
"""Default name for the Weft-owned project metadata directory."""

WEFT_ENV_FILE_ENV: Final[str] = "WEFT_ENV_FILE"
"""Bootstrap env var naming a dotenv-style file to load before CLI import."""

PROVIDER_CLI_VERSION_PROBE_TIMEOUT_SECONDS: Final[float] = 15.0
"""Timeout for basic provider CLI version probes."""

PROVIDER_CLI_OPENCODE_RUN_PROBE_TIMEOUT_SECONDS: Final[float] = 10.0
"""Timeout for probing OpenCode non-interactive `run` support."""

# Limits Section Defaults (moved from individual fields)
# -------------------------------------------------------
DEFAULT_MEMORY_MB: Final[int] = 1024
"""Default memory limit in MB (1GB)."""

DEFAULT_CPU_PERCENT: Final[int | None] = None
"""Default CPU limit in percent. None means no limit."""

DEFAULT_MAX_FDS: Final[int | None] = None
"""Default maximum number of open file descriptors. None means no limit."""

DEFAULT_MAX_CONNECTIONS: Final[int | None] = None
"""Default maximum number of network connections. None means no limit."""

# Queue Naming Conventions
# ------------------------
QUEUE_INBOX_SUFFIX: Final[str] = "inbox"
"""Suffix for input queue names (T{tid}.inbox)."""

QUEUE_OUTBOX_SUFFIX: Final[str] = "outbox"
"""Suffix for output queue names (T{tid}.outbox)."""

QUEUE_CTRL_IN_SUFFIX: Final[str] = "ctrl_in"
"""Suffix for control input queue names (T{tid}.ctrl_in)."""

QUEUE_CTRL_OUT_SUFFIX: Final[str] = "ctrl_out"
"""Suffix for control output queue names (T{tid}.ctrl_out)."""

QUEUE_RESERVED_SUFFIX: Final[str] = "reserved"
"""Suffix for reserved queue names (T{tid}.reserved) used for WIP and recovery."""

QUEUE_INTERNAL_RESERVED_SUFFIX: Final[str] = "internal_reserved"
"""Suffix for manager-owned internal spawn reserved queues."""

STANDARD_TASK_QUEUE_SUFFIXES: Final[tuple[str, ...]] = (
    QUEUE_INBOX_SUFFIX,
    QUEUE_RESERVED_SUFFIX,
    QUEUE_OUTBOX_SUFFIX,
    QUEUE_CTRL_IN_SUFFIX,
    QUEUE_CTRL_OUT_SUFFIX,
)
"""Suffixes for standard task-local queues with T{tid}.<suffix> names."""

QUEUE_PRIORITY_INTERNAL: Final[int] = 0
"""Queue watcher priority for manager-owned internal work. Lower runs first."""

QUEUE_PRIORITY_NORMAL: Final[int] = 100
"""Default queue watcher priority for ordinary task work."""

# Global Queue Names
# ------------------
# Spec: docs/specifications/00-Quick_Reference.md#queue-names
WEFT_GLOBAL_LOG_QUEUE: Final[str] = "weft.log.tasks"
"""Global queue for task state changes and events."""

TASK_MONITOR_SCHEMA_VERSION: Final[int] = 1
"""JSONL schema version for task monitor operational records."""

WEFT_MONITOR_SCHEMA_VERSION: Final[int] = 2
"""Schema version for Monitor-owned durable collation tables."""

WEFT_MONITOR_META_TABLE: Final[str] = "weft_monitor_meta"
"""Monitor-owned metadata table for schema version and checkpoints."""

WEFT_MONITOR_TASK_COLLATIONS_TABLE: Final[str] = "weft_monitor_task_collations"
"""Monitor-owned table for durable task lifecycle collation summaries."""

WEFT_MONITOR_TASK_MESSAGES_TABLE: Final[str] = "weft_monitor_task_messages"
"""Monitor-owned table for exact task-log message IDs included in summaries."""

WEFT_MONITOR_SCHEMA_VERSION_KEY: Final[str] = "schema_version"
"""Monitor metadata key storing the durable store schema version."""

WEFT_MONITOR_CHECKPOINT_META_PREFIX: Final[str] = "checkpoint:"
"""Monitor metadata key prefix for per-queue durable collation checkpoints."""

TASK_MONITOR_LOG_SUBDIR: Final[str] = "task-monitor"
"""Subdirectory under the configured logs directory for task monitor records."""

TASK_MONITOR_CHECKPOINT_PATH: Final[str] = "state/task-monitor/default.json"
"""Default task monitor checkpoint path under the Weft metadata directory."""

TASK_MONITOR_PONG_DETAIL_LIMIT: Final[int] = 20
"""Maximum list entries included in TaskMonitor extended PONG diagnostics."""

TASK_MONITOR_POLICY_TID_MAPPING_DELETE_MALFORMED: Final[str] = (
    "tid_mapping.delete_malformed"
)
"""TaskMonitor cleanup policy for malformed TID mapping runtime rows."""

TASK_MONITOR_POLICY_TID_MAPPING_DELETE_OLDER_THAN: Final[str] = (
    "tid_mapping.delete_older_than"
)
"""TaskMonitor cleanup policy for old TID mapping runtime rows."""

TASK_MONITOR_POLICY_TASK_LOG_DELETE_MALFORMED: Final[str] = "task_log.delete_malformed"
"""TaskMonitor cleanup policy for malformed task-log runtime rows."""

TASK_MONITOR_POLICY_TASK_LOG_DELETE_CLAIMED: Final[str] = "task_log.delete_claimed"
"""TaskMonitor cleanup policy for already claimed task-log runtime rows."""

TASK_MONITOR_POLICY_TASK_LOG_COLLATE_COMPLETE_LIFECYCLE: Final[str] = (
    "task_log.collate_complete_lifecycle"
)
"""TaskMonitor cleanup policy for complete task-log lifecycle groups."""

TASK_MONITOR_POLICY_TASK_LOG_COLLATE_TERMINAL_WITHOUT_START: Final[str] = (
    "task_log.collate_terminal_without_start"
)
"""TaskMonitor cleanup policy for terminal task-log groups without visible start."""

TASK_MONITOR_POLICY_TASK_LOG_DELETE_OLD_WITHOUT_START: Final[str] = (
    "task_log.delete_old_without_start"
)
"""TaskMonitor cleanup policy for old task-log rows without open start evidence."""

TASK_MONITOR_POLICY_TASK_LOG_EXTERNAL_RAW: Final[str] = (
    "task_log.external_raw_log_then_delete"
)
"""TaskMonitor cleanup policy for raw external log-before-delete mode."""

TASK_MONITOR_POLICY_RESERVED_DELETE_TERMINAL_PROVEN: Final[str] = (
    "reserved.delete_terminal_proven"
)
"""TaskMonitor cleanup policy for reserved rows proven terminal by task logs."""

TASK_MONITOR_TASK_LOG_SCAN_LIMIT_REACHED: Final[str] = "task_log_scan_limit_reached"
"""TaskMonitor task-log cleanup stop reason for scan limit exhaustion."""

TASK_MONITOR_TASK_LOG_SELECTION_LIMIT_REACHED: Final[str] = (
    "task_log_selection_limit_reached"
)
"""TaskMonitor task-log cleanup stop reason for selection limit exhaustion."""

WEFT_TASK_MONITOR_ENABLED_DEFAULT: Final[bool] = True
"""Default for manager supervision of the internal task monitor."""

WEFT_TASK_MONITOR_INTERVAL_SECONDS_DEFAULT: Final[int] = 300
"""Default heartbeat wake interval for the supervised task monitor."""

WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS_DEFAULT: Final[float] = 2.0
"""Default short wake interval while retained task-log backlog remains."""

WEFT_TASK_MONITOR_BATCH_SIZE_DEFAULT: Final[int] = 5000
"""Default maximum cleanup candidates selected by one supervised monitor cycle."""

WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT_DEFAULT: Final[int] = 50000
"""Default maximum task-log rows scanned by one supervised monitor cycle."""

WEFT_TASK_MONITOR_STORE_WRITE_BATCH_SIZE_DEFAULT: Final[int] = 100
"""Default Monitor-store rows written per transaction by one supervised cycle."""

WEFT_TASK_MONITOR_STALE_OPEN_FAMILY_SECONDS_DEFAULT: Final[float] = 604800.0
"""Default hard age before open Monitor families without intervals are suspected."""

WEFT_TASK_MONITOR_CONTROL_QUEUE_DELETE_LIMIT_DEFAULT: Final[int] = 1000
"""Default per-cycle family cap for terminal task control-queue cleanup."""

WEFT_TASK_MONITOR_CLEANUP_WORKERS_DEFAULT: Final[int] = 3
"""Default total worker cap for one TaskMonitor runtime cleanup epoch."""

TASK_MONITOR_RUNTIME_CLEANUP_SLICE_FAMILY_LIMIT: Final[int] = 50
"""Internal max task-local runtime families handled by one cleanup worker slice."""

TASK_MONITOR_RUNTIME_CLEANUP_SLICE_SECONDS: Final[float] = 1.0
"""Internal soft wall-clock budget for one task-local runtime cleanup slice."""

WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS_DEFAULT: Final[float] = 172800.0
"""Default minimum age before TaskMonitor logs/deletes task-log rows."""

WEFT_LOG_TASKS_EXTERNAL_PATH_DEFAULT: Final[str] = ""
"""Default external task-log JSONL path. Empty disables external logging."""

WEFT_LOG_TASKS_EXTERNAL_ENABLED_DEFAULT: Final[bool] = False
"""Default external task-log logging enablement when no path is configured."""

WEFT_LOG_TASKS_EXTERNAL_MODE_DEFAULT: Final[str] = "collated"
"""Default external task-log logging mode when a path is configured."""

WEFT_LOG_TASKS_EXTERNAL_MODES: Final[frozenset[str]] = frozenset({"collated", "raw"})
"""Supported external task-log logging modes."""

WEFT_LOG_TASKS_EXTERNAL_SCHEMA_VERSION: Final[int] = 1
"""JSONL schema version for external task-log operational records."""

WEFT_LOG_TASKS_RAW_BODY_PREVIEW_BYTES: Final[int] = 8192
"""Maximum malformed raw task-log body bytes included in external records."""

TASK_MONITOR_TASK_LOG_CLEANUP_SKIPPED_OWNER: Final[str] = (
    "task_log_cleanup_owned_by_external_or_store_policy"
)
"""Stop reason when broad task-log cleanup is disabled for a cycle owner."""

WEFT_TASK_MONITOR_PROCESSOR_DEFAULT: Final[str] = "delete"
"""Default processor for supervised task-monitor candidates."""

WEFT_TASK_MONITOR_PROCESSOR_BUILTINS: Final[frozenset[str]] = frozenset(
    {"report_only", "delete", "jsonl_then_delete"}
)
"""Built-in task-monitor processor names accepted by configuration."""

WEFT_TASK_MONITOR_LOG_SINK_DEFAULT: Final[str] = "stdout"
"""Default operational output sink selected for the supervised task monitor."""

WEFT_TASK_MONITOR_LOG_SINKS: Final[frozenset[str]] = frozenset(
    {"stdout", "disk", "none"}
)
"""Supported task-monitor operational output sink names."""

WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS_DEFAULT: Final[float] = 60.0
"""Default manager restart backoff for the supervised task monitor."""

WEFT_TASK_MONITOR_COLLATION_STORE_ENABLED_DEFAULT: Final[bool] = True
"""Default for the supervised monitor's durable collation store."""

WEFT_TASK_MONITOR_TABLE_DELETE_ENABLED_DEFAULT: Final[bool] = False
"""Default for raw task-log deletion from durable Monitor collation proof."""

MANAGER_SERVE_LOG_SCHEMA: Final[str] = "weft.manager_serve_log"
"""JSONL schema name for foreground manager operational log records."""

MANAGER_SERVE_LOG_SCHEMA_VERSION: Final[int] = 1
"""JSONL schema version for foreground manager operational log records."""

MANAGER_SERVE_LOG_EVENT_MAX_CHARS: Final[int] = 500
"""Maximum string length for one manager operational-log field value."""

MANAGER_SERVE_LOG_QUEUE_SIZE: Final[int] = 256
"""Maximum foreground manager operational-log records buffered per process.

The serve log is diagnostic. When stderr stops draining, the manager must keep
converging and may drop diagnostic records instead of blocking the control loop.
"""

MANAGER_SERVE_LOG_CANDIDATE_LIMIT: Final[int] = 8
"""Maximum service candidates included in one manager operational-log event."""

MANAGER_SERVE_LOG_CHILD_LIMIT: Final[int] = 8
"""Maximum tracked child summaries included in one manager operational-log event."""

SERVICE_OWNER_SCHEMA: Final[str] = "weft.service_owner.v1"
"""Runtime service-owner registry row schema."""

SERVICE_TYPE_MANAGER: Final[str] = "manager"
"""Service-owner type for the public spawn manager service."""

SERVICE_TYPE_MANAGED: Final[str] = "managed"
"""Service-owner type for manager-supervised internal/autostart services."""

SERVICE_STATUS_ACTIVE: Final[Literal["active"]] = "active"
"""Service-owner status for a live active service owner."""

SERVICE_STATUS_DRAINING: Final[Literal["draining"]] = "draining"
"""Service-owner status for an owner that is still visible while draining."""

SERVICE_STATUS_STOPPED: Final[Literal["stopped"]] = "stopped"
"""Service-owner status for a stopped manager/service owner."""

SERVICE_STATUS_SUPERSEDED: Final[Literal["superseded"]] = "superseded"
"""Service-owner status for a replaced manager/service owner."""

SERVICE_STATUS_TERMINAL: Final[Literal["terminal"]] = "terminal"
"""Service-owner status for terminal managed-service evidence."""

LIVE_SERVICE_STATUSES: Final[frozenset[str]] = frozenset(
    {SERVICE_STATUS_ACTIVE, SERVICE_STATUS_DRAINING}
)
"""Service-owner statuses that count as live convergence evidence."""

MANAGER_SERVE_LOG_ACTIVE_CONFIG_KEY: Final[str] = "_WEFT_MANAGER_SERVE_LOG_ACTIVE"
"""Internal config key proving the current process is a foreground serve invocation."""

WEFT_MANAGER_SERVE_LOG_LEVEL: Final[str] = "WEFT_MANAGER_SERVE_LOG_LEVEL"
"""Environment/config key selecting foreground manager operational-log verbosity."""

WEFT_MANAGER_SERVE_LOG_LEVEL_DEFAULT: Final[str] = "off"
"""Default foreground manager operational-log level."""

WEFT_MANAGER_SERVE_LOG_LEVELS: Final[frozenset[str]] = frozenset(
    {"off", "info", "debug", "trace"}
)
"""Allowed foreground manager operational-log levels."""

MANAGER_SERVE_LOG_LEVEL_ORDER: Final[Mapping[str, int]] = {
    "off": 0,
    "info": 1,
    "debug": 2,
    "trace": 3,
}
"""Ordering used to compare foreground manager operational-log levels."""

WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS: Final[str] = (
    "WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS"
)
"""Environment/config key selecting foreground manager operational-log throttle."""

WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS_DEFAULT: Final[float] = 5.0
"""Default interval for repeated foreground manager operational-log events."""

MANAGER_SERVE_LOG_COMPONENTS: Final[frozenset[str]] = frozenset(
    {
        "manager",
        "registry",
        "ownership",
        "service",
        "spawn",
        "task_monitor",
    }
)
"""Allowed foreground manager operational-log component labels."""

TASK_MONITOR_ACTIVITY_WAIT_CAP_SECONDS: Final[float] = 1.0
"""Maximum launcher wait for TaskMonitor when no queue wake is observed."""

TASK_MONITOR_HEARTBEAT_STARTUP_TIMEOUT_SECONDS: Final[float] = 0.5
"""Bounded heartbeat lookup budget used by TaskMonitor before retrying later."""

MANAGED_SERVICE_PING_TIMEOUT_SECONDS: Final[float] = 0.15
"""Bounded PING/PONG probe budget for manager-supervised singleton evidence."""

MANAGED_SERVICE_RECENT_EVIDENCE_GRACE_SECONDS: Final[float] = 5.0
"""Window where nonterminal service rows without live proof remain uncertain."""

MANAGED_SERVICE_CONVERGENCE_INTERVAL_SECONDS: Final[float] = 1.0
"""Maximum delay between active singleton convergence turns."""

MANAGED_SERVICE_STABLE_AUDIT_INTERVAL_SECONDS: Final[float] = 5.0
"""Maximum delay between audits when singleton services are already stable."""

MANAGED_SERVICE_UNCERTAIN_RETRY_LIMIT: Final[int] = 3
"""Number of uncertain singleton evidence turns that may block replacement."""

RUNTIME_PRUNE_SCHEMA_VERSION: Final[int] = 1
"""JSONL schema version for explicit runtime-state prune reports."""

RUNTIME_PRUNE_DEFAULT_MIN_AGE_SECONDS: Final[float] = 3600.0
"""Default minimum runtime-state row age before prune candidacy."""

RUNTIME_PRUNE_DEFAULT_KEEP_RECENT_PER_KEY: Final[int] = 1
"""Default newest runtime-state rows to preserve for each logical key."""

RUNTIME_PRUNE_CLASS_SUPERSEDED_TID_MAPPING: Final[str] = "superseded_tid_mapping"
"""Runtime-prune classification for older duplicate TID mapping rows."""

RUNTIME_PRUNE_CLASS_STALE_MANAGER: Final[str] = "stale_manager_registry"
"""Runtime-prune classification for stale active manager registry rows."""

RUNTIME_PRUNE_CLASS_SUPERSEDED_MANAGER: Final[str] = "superseded_manager_registry"
"""Runtime-prune classification for older duplicate manager registry rows."""

RUNTIME_PRUNE_CLASS_SUPERSEDED_SERVICE: Final[str] = "superseded_service_registry"
"""Runtime-prune classification for older managed-service registry rows."""

RUNTIME_PRUNE_CLASS_STALE_STREAMING: Final[str] = "stale_streaming_session"
"""Runtime-prune classification for stale streaming session markers."""

RUNTIME_PRUNE_CLASS_STALE_ENDPOINT: Final[str] = "stale_endpoint_record"
"""Runtime-prune classification for endpoint rows whose owner is not live."""

RUNTIME_PRUNE_CLASS_SUPERSEDED_ENDPOINT: Final[str] = "superseded_endpoint_record"
"""Runtime-prune classification for older duplicate endpoint rows."""

RUNTIME_PRUNE_CLASS_STALE_PIPELINE: Final[str] = "stale_pipeline_record"
"""Runtime-prune classification reserved for proven stale pipeline rows."""

RUNTIME_PRUNE_CLASS_UNSUPPORTED_PIPELINE: Final[str] = (
    "unsupported_pipeline_record_shape"
)
"""Runtime-prune report-only classification for unsupported pipeline row shapes."""

TASK_MONITOR_WEFT_ANOMALY_CLASSIFICATIONS: Final[frozenset[str]] = frozenset(
    {
        "wrapper_lost",
        "result_without_terminal",
        "runtime_conflict",
        "stale_created",
        "orphaned_reserved",
        "orphaned_inbox",
    }
)
"""Task monitor classifications owned by Weft task anomalies."""

WEFT_TID_MAPPINGS_QUEUE: Final[str] = "weft.state.tid_mappings"
"""Global queue for TID short->full mappings for process management."""

WEFT_SERVICES_REGISTRY_QUEUE: Final[str] = "weft.state.services"
"""Runtime queue where convergent services publish ownership evidence."""

WEFT_STREAMING_SESSIONS_QUEUE: Final[str] = "weft.state.streaming"
"""Queue tracking active streaming sessions (interactive/streaming outputs)."""

WEFT_ENDPOINTS_REGISTRY_QUEUE: Final[str] = "weft.state.endpoints"
"""Queue tracking active named task endpoints."""

ENDPOINT_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
)
"""Allowed stable named-endpoint syntax for runtime endpoint claims."""

INTERNAL_ENDPOINT_NAMESPACE_PREFIX: Final[str] = "_weft."
"""Reserved endpoint-name prefix for internal runtime-owned services."""

WEFT_PIPELINES_STATE_QUEUE: Final[str] = "weft.state.pipelines"
"""Queue tracking active first-class pipeline runs."""

PIPELINE_STATUS_QUEUE_SUFFIX: Final[str] = "status"
"""Suffix for pipeline status queues (P{tid}.status)."""

WEFT_STATE_QUEUE_PREFIX: Final[str] = "weft.state."
"""Prefix for runtime-only state queues excluded from system dumps."""

RUNTIME_PRUNE_SUPPORTED_QUEUE_GROUPS: Final[Mapping[str, str]] = {
    "tid-mappings": WEFT_TID_MAPPINGS_QUEUE,
    "managers": WEFT_SERVICES_REGISTRY_QUEUE,
    "services": WEFT_SERVICES_REGISTRY_QUEUE,
    "streaming": WEFT_STREAMING_SESSIONS_QUEUE,
    "endpoints": WEFT_ENDPOINTS_REGISTRY_QUEUE,
    "pipelines": WEFT_PIPELINES_STATE_QUEUE,
}
"""Runtime-state queue groups supported by explicit pruning."""

RUNTIME_PRUNE_DEFAULT_QUEUE_GROUPS: Final[tuple[str, ...]] = tuple(
    RUNTIME_PRUNE_SUPPORTED_QUEUE_GROUPS
)
"""Default runtime-state queue group scan order for explicit pruning."""

RUNTIME_PRUNE_REPORT_ONLY_CLASSIFICATIONS: Final[frozenset[str]] = frozenset(
    {RUNTIME_PRUNE_CLASS_UNSUPPORTED_PIPELINE}
)
"""Runtime-prune classifications that must never be actively deleted."""

RETENTION_PRUNE_SCHEMA_VERSION: Final[int] = 1
"""JSONL schema version for task-local and task-log retention prune records."""

RETENTION_PRUNE_DEFAULT_MIN_AGE_SECONDS: Final[float] = 604800.0
"""Default minimum task-local retention row age before ordinary prune candidacy."""

RETENTION_PRUNE_DEFAULT_KEEP_RECENT_PER_TASK: Final[int] = 1
"""Default newest lifecycle-log rows to preserve for each task."""

RETENTION_PRUNE_LOG_SUBDIR: Final[str] = "retention-prune"
"""Default subdirectory under the configured logs directory for retention pruning."""

RETENTION_PRUNE_CLASS_TERMINAL_CTRL_OUT_ARCHIVED: Final[str] = (
    "terminal_ctrl_out_archived"
)
"""Retention class for terminal ctrl_out rows with retained terminal log proof."""

RETENTION_PRUNE_CLASS_TERMINAL_CTRL_OUT_WITHOUT_LOG_REPORTED: Final[str] = (
    "terminal_ctrl_out_without_log_reported"
)
"""Retention class for terminal ctrl_out rows that are the only visible terminal proof."""

RETENTION_PRUNE_CLASS_TERMINAL_RESULT_OUTBOX_ARCHIVED: Final[str] = (
    "terminal_result_outbox_archived"
)
"""Retention class for final one-shot outbox rows with independent terminal proof."""

RETENTION_PRUNE_CLASS_RESULT_WITHOUT_TERMINAL_OUTBOX_REPORTED: Final[str] = (
    "result_without_terminal_outbox_reported"
)
"""Retention class for final outbox rows that are the only visible terminal proof."""

RETENTION_PRUNE_CLASS_TERMINAL_TASK_LOG_SUPERSEDED: Final[str] = (
    "terminal_task_log_superseded"
)
"""Retention class for older terminal task-log rows with newer retained proof."""

RETENTION_PRUNE_CLASS_NONTERMINAL_TASK_LOG_SUPERSEDED: Final[str] = (
    "nonterminal_task_log_superseded"
)
"""Retention class for older nonterminal task-log rows with later terminal proof."""

RETENTION_PRUNE_CLASS_OBSOLETE_CTRL_IN_CONTROL: Final[str] = "obsolete_ctrl_in_control"
"""Retention class for stale task-local control input rows."""

RETENTION_PRUNE_CLASS_OBSOLETE_INBOX_WORK: Final[str] = "obsolete_inbox_work"
"""Retention class for obsolete task inbox work rows."""

RETENTION_PRUNE_CLASS_OBSOLETE_RESERVED_WORK: Final[str] = "obsolete_reserved_work"
"""Retention class for obsolete task reserved work rows."""

RETENTION_PRUNE_CLASS_UNSUPPORTED_TASK_LOCAL_SHAPE: Final[str] = (
    "unsupported_task_local_shape"
)
"""Retention class for task-local rows that normal mode cannot interpret safely."""

RETENTION_PRUNE_CLASS_CLAIMED_OUTBOX_RESIDUE_FORCE: Final[str] = (
    "claimed_outbox_residue_force"
)
"""Retention class for force-only claimed outbox residue cleanup."""

RETENTION_PRUNE_SUPPORTED_FAMILIES: Final[frozenset[str]] = frozenset(
    {"runtime-state", "task-local", "task-log", "retention", "all"}
)
"""Public `weft system prune --family` values."""

RETENTION_PRUNE_SUPPORTED_CLASSES: Final[frozenset[str]] = frozenset(
    {
        RETENTION_PRUNE_CLASS_TERMINAL_CTRL_OUT_ARCHIVED,
        RETENTION_PRUNE_CLASS_TERMINAL_CTRL_OUT_WITHOUT_LOG_REPORTED,
        RETENTION_PRUNE_CLASS_TERMINAL_RESULT_OUTBOX_ARCHIVED,
        RETENTION_PRUNE_CLASS_RESULT_WITHOUT_TERMINAL_OUTBOX_REPORTED,
        RETENTION_PRUNE_CLASS_TERMINAL_TASK_LOG_SUPERSEDED,
        RETENTION_PRUNE_CLASS_NONTERMINAL_TASK_LOG_SUPERSEDED,
        RETENTION_PRUNE_CLASS_OBSOLETE_CTRL_IN_CONTROL,
        RETENTION_PRUNE_CLASS_OBSOLETE_INBOX_WORK,
        RETENTION_PRUNE_CLASS_OBSOLETE_RESERVED_WORK,
        RETENTION_PRUNE_CLASS_UNSUPPORTED_TASK_LOCAL_SHAPE,
        RETENTION_PRUNE_CLASS_CLAIMED_OUTBOX_RESIDUE_FORCE,
    }
)
"""Retention candidate classes accepted by class filters."""

RETENTION_PRUNE_REPORT_ONLY_CLASSES: Final[frozenset[str]] = frozenset(
    {
        RETENTION_PRUNE_CLASS_TERMINAL_CTRL_OUT_WITHOUT_LOG_REPORTED,
        RETENTION_PRUNE_CLASS_RESULT_WITHOUT_TERMINAL_OUTBOX_REPORTED,
        RETENTION_PRUNE_CLASS_OBSOLETE_INBOX_WORK,
        RETENTION_PRUNE_CLASS_OBSOLETE_RESERVED_WORK,
        RETENTION_PRUNE_CLASS_UNSUPPORTED_TASK_LOCAL_SHAPE,
    }
)
"""Retention classes that ordinary apply reports but does not delete."""

WEFT_SPAWN_REQUESTS_QUEUE: Final[str] = "weft.spawn.requests"
"""Global queue for manager-consumed task spawn requests."""

WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE: Final[str] = "weft.spawn.internal"
"""Global queue for manager-owned internal spawn requests."""

WEFT_MANAGER_CTRL_IN_QUEUE: Final[str] = "weft.manager.ctrl_in"
"""Control inbox for manager lifecycle commands."""

WEFT_MANAGER_CTRL_OUT_QUEUE: Final[str] = "weft.manager.ctrl_out"
"""Control response queue for manager status messages."""

WEFT_MANAGER_OUTBOX_QUEUE: Final[str] = "weft.manager.outbox"
"""Manager output queue (e.g. informational responses)."""

WORK_ENVELOPE_START: Final[dict[str, bool]] = {"__weft_start__": True}
"""Sentinel payload the Manager uses to trigger Consumer startup."""

INTERNAL_RUNTIME_TASK_CLASS_KEY: Final[str] = "_weft_runtime_task_class"
"""Reserved metadata key selecting an internal runtime-owned task class."""

INTERNAL_RUNTIME_ENDPOINT_NAME_KEY: Final[str] = "_weft_endpoint_name"
"""Reserved metadata key selecting an explicit runtime endpoint claim."""

INTERNAL_RUNTIME_ENVELOPE_TASK_CLASS_KEY: Final[str] = (
    "_weft_internal_runtime_task_class"
)
"""Reserved spawn-envelope key selecting an internal runtime-owned task class."""

INTERNAL_RUNTIME_ENVELOPE_ENDPOINT_NAME_KEY: Final[str] = "_weft_internal_endpoint_name"
"""Reserved spawn-envelope key selecting an internal runtime endpoint claim."""

INTERNAL_RUNTIME_TASK_CLASS_PIPELINE: Final[str] = "pipeline"
"""Reserved internal runtime-owned class selector for PipelineTask."""

INTERNAL_RUNTIME_TASK_CLASS_PIPELINE_EDGE: Final[str] = "pipeline_edge"
"""Reserved internal runtime-owned class selector for PipelineEdgeTask."""

INTERNAL_RUNTIME_TASK_CLASS_HEARTBEAT: Final[str] = "heartbeat"
"""Reserved internal runtime-owned class selector for HeartbeatTask."""

INTERNAL_RUNTIME_TASK_CLASS_TASK_MONITOR: Final[str] = "task_monitor"
"""Reserved internal runtime-owned class selector for TaskMonitor."""

INTERNAL_HEARTBEAT_ENDPOINT_NAME: Final[str] = "_weft.heartbeat"
"""Reserved runtime endpoint claimed by the built-in heartbeat service."""

INTERNAL_SERVICE_KEY_METADATA_KEY: Final[str] = "_weft_service_key"
"""Reserved metadata key naming a manager-supervised singleton service."""

INTERNAL_SERVICE_LIFECYCLE_METADATA_KEY: Final[str] = "_weft_service_lifecycle"
"""Reserved metadata key selecting manager supervision policy for a service."""

INTERNAL_SERVICE_AUTHORITY_METADATA_KEY: Final[str] = "_weft_service_authority"
"""Reserved metadata key marking manager-authored service supervision data."""

INTERNAL_SERVICE_AUTHORITY_MANAGER: Final[str] = "manager"
"""Value for manager-authored service supervision metadata."""

INTERNAL_AUTOSTART_SOURCE_METADATA_KEY: Final[str] = "autostart_source"
"""Reserved metadata key linking an autostart task to its manifest source."""

INTERNAL_AUTOSTART_ENABLED_METADATA_KEY: Final[str] = "autostart"
"""Reserved metadata key marking a task as manager-authored autostart work."""

PUBLIC_RESERVED_SERVICE_METADATA_KEYS: Final[tuple[str, ...]] = (
    INTERNAL_SERVICE_KEY_METADATA_KEY,
    INTERNAL_SERVICE_LIFECYCLE_METADATA_KEY,
    INTERNAL_SERVICE_AUTHORITY_METADATA_KEY,
    INTERNAL_AUTOSTART_SOURCE_METADATA_KEY,
    INTERNAL_AUTOSTART_ENABLED_METADATA_KEY,
)
"""Metadata keys stripped from ordinary public spawn submissions."""

INTERNAL_SERVICE_KEY_HEARTBEAT: Final[str] = "_weft.service.heartbeat"
"""Manager-supervised singleton key for the built-in heartbeat service."""

INTERNAL_SERVICE_KEY_TASK_MONITOR: Final[str] = "_weft.service.task_monitor"
"""Manager-supervised singleton key for the built-in task monitor service."""

HEARTBEAT_MIN_INTERVAL_SECONDS: Final[int] = 60
"""Minimum interval accepted by the first-slice heartbeat service."""

HEARTBEAT_IDLE_TIMEOUT_SECONDS: Final[float] = 60.0
"""Idle timeout after the last registration before the heartbeat service exits."""

HEARTBEAT_ACTIVITY_WAIT_CAP_SECONDS: Final[float] = 1.0
"""Maximum HeartbeatTask wait chunk between supersession and idle checks."""

HEARTBEAT_ENDPOINT_PROBE_TIMEOUT: Final[float] = 0.25
"""Bounded PING/PONG probe timeout used when validating a heartbeat endpoint."""

PIPELINE_RUNTIME_METADATA_KEY: Final[str] = "_weft_pipeline_runtime"
"""Reserved metadata key carrying a precompiled pipeline runtime plan."""

PIPELINE_EDGE_RUNTIME_METADATA_KEY: Final[str] = "_weft_edge_runtime"
"""Reserved metadata key carrying compiled edge runtime configuration."""

PIPELINE_OWNER_METADATA_KEY: Final[str] = "_weft_pipeline_owner"
"""Reserved metadata key carrying compact pipeline-owner event configuration."""

# State Section Defaults
# ----------------------
STATUS_CREATED: Final[Literal["created"]] = "created"
"""Initial status for newly created tasks."""

STATUS_RUNNING: Final[Literal["running"]] = "running"
"""Status when task is actively running."""

STATUS_COMPLETED: Final[Literal["completed"]] = "completed"
"""Status when task has finished successfully."""

STATUS_FAILED: Final[Literal["failed"]] = "failed"
"""Status when task has failed with an error."""

STATUS_CANCELLED: Final[Literal["cancelled"]] = "cancelled"
"""Status when task was cancelled by user."""

DEFAULT_STATUS: Final[Literal["created"]] = STATUS_CREATED
"""Default initial status for tasks."""

TERMINAL_TASK_STATUSES: Final[frozenset[str]] = frozenset(
    {"completed", "failed", "timeout", "cancelled", "killed"}
)
"""Statuses that represent terminal task lifecycle states."""

TERMINAL_TASK_EVENTS: Final[Mapping[str, str]] = {
    "control_stop": "cancelled",
    "task_signal_stop": "cancelled",
    "control_kill": "killed",
    "task_signal_kill": "killed",
    "work_failed": "failed",
    "work_timeout": "timeout",
    "work_limit_violation": "failed",
    "work_completed": "completed",
}
"""Task-log events that prove terminal lifecycle status when completion is set."""

TASK_LOG_START_EVENTS: Final[frozenset[str]] = frozenset(
    {
        "task_initialized",
        "task_spawning",
        "task_started",
        "work_spawning",
        "work_started",
        "work_item_started",
    }
)
"""Task-log events that indicate visible lifecycle start evidence."""

TERMINAL_ENVELOPE_TYPE: Final[str] = "terminal"
"""Typed ctrl_out envelope marker for task-local terminal observations."""

TASK_TERMINAL_ENVELOPE_SOURCES: Final[frozenset[str]] = frozenset({"task", "manager"})
"""Accepted terminal ctrl_out envelope authors."""

WRAPPER_LOST_ERROR: Final[str] = "Task wrapper exited before publishing terminal state"
"""Manager-authored terminal error when a child exits before terminal publication."""

FAILURE_LIKE_TASK_STATUSES: Final[frozenset[str]] = frozenset(
    {"failed", "timeout", "cancelled", "killed"}
)
"""Terminal statuses that should be treated as unsuccessful outcomes."""

NON_LIVE_RUNTIME_STATES: Final[frozenset[str]] = frozenset(
    {
        "missing",
        "created",
        "exited",
        "dead",
        "stopped",
        "completed",
        "failed",
        "cancelled",
    }
)
"""Runtime states that should not be treated as live work."""

# Control Commands
# ----------------
CONTROL_PING: Final[str] = "PING"
"""Control command to request a liveness/status response."""

CONTROL_STATUS: Final[str] = "STATUS"
"""Control command to request current task-local status."""

CONTROL_STOP: Final[str] = "STOP"
"""Control command to stop a running task."""

CONTROL_KILL: Final[str] = "KILL"
"""Control command to force-kill a running task."""

CONTROL_PAUSE: Final[str] = "PAUSE"
"""Control command to pause a task that supports live pausing."""

CONTROL_RESUME: Final[str] = "RESUME"
"""Control command to resume a paused task that supports live pausing."""

# Resource Limits
# ---------------
MIN_MEMORY_LIMIT: Final[int] = 1
"""Minimum allowed memory limit in MB."""

MAX_CPU_LIMIT: Final[int] = 100
"""Maximum allowed CPU limit in percent."""

MIN_CPU_LIMIT: Final[int] = 1
"""Minimum allowed CPU limit in percent."""

MIN_FDS_LIMIT: Final[int] = 1
"""Minimum allowed file descriptor limit."""

MIN_CONNECTIONS_LIMIT: Final[int] = 0
"""Minimum allowed network connections limit."""


# Manager behaviour
# ------------------
WEFT_MANAGER_LIFETIME_TIMEOUT: Final[float] = 600.0
"""Default idle-shutdown timeout for the Manager process (seconds)."""

STATUS_RUNTIMELESS_STALE_AFTER_SECONDS: Final[float] = WEFT_MANAGER_LIFETIME_TIMEOUT * 2
"""Age after which a running host task with no runtime proof is treated as stale."""

TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS: Final[float] = (
    STATUS_RUNTIMELESS_STALE_AFTER_SECONDS * 2
)
"""Default minimum age before TaskMonitor deletes stale TID mapping rows."""

WEFT_MANAGER_REUSE_ENABLED: Final[bool] = True
"""Whether a Manager started by the CLI should remain running after a task completes."""

WEFT_MANAGER_RUNTIME_HANDLE_JSON_ENV: Final[str] = "WEFT_MANAGER_RUNTIME_HANDLE_JSON"
"""Optional JSON RunnerHandle for externally supervised manager processes."""

WEFT_COMPLETED_RESULT_GRACE_SECONDS: Final[float] = 0.5
"""Time to keep polling an outbox after a completion event before assuming no result."""

INTERACTIVE_OUTPUT_DRAIN_TIMEOUT: Final[float] = 0.25
"""Best-effort drain window for late stdout/stderr after an interactive session exits."""

INTERACTIVE_OUTPUT_DRAIN_POLL_INTERVAL: Final[float] = 0.01
"""Polling interval while draining late interactive stdout/stderr chunks."""

INTERACTIVE_STOP_GRACE_SECONDS: Final[float] = 2.0
"""Grace period for interactive tasks to exit cleanly after stdin closure."""

INTERACTIVE_STOP_POLL_INTERVAL: Final[float] = 0.05
"""Polling interval while waiting for an interactive session to exit cooperatively."""

COMMAND_SESSION_TERMINATION_TIMEOUT: Final[float] = 2.0
"""Base timeout for command-session termination steps."""

COMMAND_SESSION_POST_TERMINATION_WAIT: Final[float] = 0.2
"""Short wait after tree termination before direct process fallback."""

INTERACTIVE_STOP_COMPLETION_TIMEOUT: Final[float] = (
    INTERACTIVE_STOP_GRACE_SECONDS
    + (COMMAND_SESSION_TERMINATION_TIMEOUT * 3)
    + COMMAND_SESSION_POST_TERMINATION_WAIT
    + 0.5
)
"""CLI wait budget for interactive STOP completion.

Composition:
- stdin-close grace for cooperative exits
- up to three command-session termination phases
- one short post-termination wait for process-tree cleanup visibility
- a small CLI-side slack budget for final queue drains and control acks
"""


# Autostart behaviour
# -------------------
WEFT_AUTOSTART_DIRECTORY_NAME: Final[str] = "autostart"
"""Name of the directory under the Weft metadata directory that stores auto-start TaskSpec templates."""

WEFT_AUTOSTART_TASKS_DEFAULT: Final[bool] = True
"""Default for enabling auto-start TaskSpec templates when a Manager boots."""

WEFT_AGENT_SETTINGS_FILENAME: Final[str] = "agents.json"
"""Project-local delegated-agent launch settings stored under the Weft metadata directory."""

WEFT_AGENT_HEALTH_FILENAME: Final[str] = "agent-health.json"
"""Best-effort delegated-agent health observations stored under the Weft metadata directory."""

BROKER_PROJECT_CONFIG_FILENAME: Final[str] = ".broker.toml"
"""Project-scoped broker configuration filename."""

WEFT_BROKER_PROJECT_CONFIG_FILENAME: Final[str] = "broker.toml"
"""Default broker project configuration filename under the Weft metadata directory."""

STREAM_CHUNK_SIZE_BYTES: Final[int] = 512 * 1024
"""Chunk size used when splitting large task output into queue messages."""

SUBPROCESS_STREAM_READ_SIZE: Final[int] = 64 * 1024
"""Read size for subprocess stdout/stderr drain loops."""

SUBPROCESS_TERMINATION_WAIT_TIMEOUT: Final[float] = 0.2
"""Short grace timeout between subprocess stop/kill escalation phases."""

SUBPROCESS_STREAM_DRAIN_TIMEOUT: Final[float] = 0.25
"""Best-effort timeout for draining subprocess stdout/stderr after exit."""

SUBPROCESS_POLL_INTERVAL_FLOOR: Final[float] = 0.01
"""Minimum sleep interval for subprocess coordination loops."""

RUNNER_ENTRY_POINT_GROUP: Final[str] = "weft.runners"
"""Entry-point group name for external runner plugins."""

DEFAULT_RUNNER_NAME: Final[str] = "host"
"""Built-in default runner plugin name."""

RUNNER_PLUGIN_EXTRA_INSTALL_HINTS: Final[dict[str, str]] = {
    "docker": "weft[docker]",
    "macos-sandbox": "weft[macos-sandbox]",
}
"""Optional install extras surfaced when a runner plugin is unavailable."""

POSTGRES_BACKEND_UNAVAILABLE: Final[str] = (
    "Requested backend 'postgres' is not available. Install simplebroker-pg."
)
"""Operator-facing error when the Postgres backend plugin is unavailable."""

POSTGRES_BACKEND_INSTALL_HINT: Final[str] = (
    "Requested backend 'postgres' is not available. "
    "Install with `uv add 'weft[pg]'` or install `simplebroker-pg` directly."
)
"""Operator-facing install hint for the Postgres backend plugin."""

SQLITE_SNAPSHOT_SUFFIXES: Final[tuple[str, ...]] = ("", "-wal", "-shm")
"""SQLite database sidecar suffixes that travel with snapshot imports."""

SUPPORTED_IMPORT_SCHEMA_VERSIONS: Final[frozenset[int]] = frozenset({4, 5})
"""Dump schema versions accepted by the current import surface."""

SPEC_TYPE_TASK: Final[str] = "task"
"""Canonical spec kind name for stored TaskSpecs."""

SPEC_TYPE_PIPELINE: Final[str] = "pipeline"
"""Canonical spec kind name for stored PipelineSpecs."""

SPEC_SOURCE_FILE: Final[Literal["file"]] = "file"
"""Resolved spec reference source for explicit file paths."""

SPEC_SOURCE_STORED: Final[Literal["stored"]] = "stored"
"""Resolved spec reference source for project-local stored specs."""

SPEC_SOURCE_BUILTIN: Final[Literal["builtin"]] = "builtin"
"""Resolved spec reference source for shipped builtin specs."""

SPEC_ENTRY_FILES: Final[dict[str, str]] = {
    SPEC_TYPE_TASK: "taskspec.json",
    SPEC_TYPE_PIPELINE: "pipeline.json",
}
"""Bundle entry filenames keyed by canonical spec kind."""

PIPELINE_SUPPORTED_STAGE_DEFAULT_KEYS: Final[frozenset[str]] = frozenset(
    {"input", "args", "keyword_args", "env"}
)
"""Supported stage-level default override keys in authored pipeline specs."""

PIPELINE_PLACEHOLDER_TARGET: Final[str] = "weft.core.tasks.pipeline:runtime"
"""Internal placeholder target used for first-class pipeline runtime tasks."""

RUN_COMMAND_RESERVED_OPTION_NAMES: Final[frozenset[str]] = frozenset(
    {
        "spec",
        "pipeline",
        "input",
        "function",
        "arg",
        "kw",
        "env",
        "name",
        "interactive",
        "non-interactive",
        "stream-output",
        "no-stream-output",
        "timeout",
        "memory",
        "cpu",
        "tag",
        "context",
        "wait",
        "no-wait",
        "json",
        "verbose",
        "monitor",
        "continuous",
        "once",
        "autostart",
        "no-autostart",
        "help",
    }
)
"""Long option names reserved by `weft run` and unavailable to spec adapters."""

SUBMIT_OVERRIDE_NAMES: Final[frozenset[str]] = frozenset(
    {
        "name",
        "description",
        "tags",
        "env",
        "working_dir",
        "stream_output",
        "timeout",
        "memory_mb",
        "cpu_percent",
        "runner",
        "runner_options",
        "metadata",
    }
)
"""Public TaskSpec fields that the shared submission surface accepts as overrides."""

TASKSPEC_BUNDLE_ROOT_FIELD: Final[str] = "_weft_bundle_root"
"""Private extra-field key storing a resolved TaskSpec bundle root path."""

AGENT_SESSION_PROTOCOL_VERSION: Final[int] = 1
"""Private multiprocessing protocol version for agent-session subprocesses."""

WINDOWS_CMD_SHIM_SUFFIXES: Final[frozenset[str]] = frozenset({".bat", ".cmd"})
"""Windows shim suffixes eligible for provider CLI command rewriting."""

PROVIDER_CONTAINER_DESCRIPTOR_VERSION: Final[str] = "v2"
"""Current internal descriptor schema version for provider container runtimes."""

PROVIDER_CONTAINER_DESCRIPTOR_PACKAGE: Final[str] = "weft.core.agents.provider_cli"
"""Package containing shipped provider container runtime descriptor files."""

PROVIDER_CONTAINER_DESCRIPTOR_DIRECTORY: Final[str] = "runtime_descriptors"
"""Resource subdirectory containing provider container runtime descriptors."""

PROVIDER_CONTAINER_DEFAULT_RUNTIME_HOME_ENV: Final[tuple[str, ...]] = (
    "HOME",
    "USERPROFILE",
)
"""Environment variable names updated when a generated runtime home is mounted."""

KNOWN_INTERPRETER_DEFINITIONS: Final[dict[str, tuple[str, tuple[str, ...]]]] = {
    "python": ("python", ("-u", "-i")),
    "python3": ("python", ("-u", "-i")),
    "python3.10": ("python", ("-u", "-i")),
    "python3.11": ("python", ("-u", "-i")),
    "python3.12": ("python", ("-u", "-i")),
    "python3.13": ("python", ("-u", "-i")),
    "python3.14": ("python", ("-u", "-i")),
    "pypy": ("python", ("-u", "-i")),
    "pypy3": ("python", ("-u", "-i")),
    "node": ("node", ("--interactive",)),
    "bash": ("bash", ("-i",)),
}
"""Known interactive interpreter launch adjustments keyed by executable name."""

DOCKER_CONTAINER_LOOKUP_TIMEOUT: Final[float] = 2.0
"""Time budget for Docker runner state reconciliation against the daemon."""

DOCKER_CONTAINER_START_TIMEOUT: Final[float] = 15.0
"""Time budget for a created Docker container to become runnable."""

DOCKER_CONTAINER_LOOKUP_INTERVAL: Final[float] = 0.05
"""Polling interval while waiting for Docker container state visibility."""

CONTAINER_CGROUP_RUNTIME_PATTERNS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("podman", ("libpod", "podman")),
    ("docker", ("docker",)),
    ("kubernetes", ("kubepods",)),
    ("containerd", ("containerd", "cri-containerd", "cri-")),
)
"""Runtime markers used when detecting containerized manager processes."""

BUILTIN_PLATFORM_DISPLAY_NAMES: Final[dict[str, str]] = {
    "linux": "Linux",
    "darwin": "macOS",
    "win32": "Windows",
}
"""Display-name mapping for builtin platform-compatibility messages."""

DOCKER_BUILTIN_SUPPORTED_PLATFORMS: Final[tuple[str, ...]] = ("linux", "darwin")
"""Supported host platforms for Docker-dependent shipped builtins."""

DOCKERIZED_AGENT_CONTAINER_DOC_PATH: Final[str] = "/tmp/00-Overview_and_Architecture.md"
"""In-container document path used by shipped Dockerized agent example tasks."""

CLAUDE_DOCKER_SANDBOX_ENV: Final[dict[str, str]] = {"IS_SANDBOX": "1"}
"""Environment overrides injected into the shipped Claude Docker example."""

CLAUDE_PORTABLE_AUTH_ENV_NAMES: Final[tuple[str, ...]] = (
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
)
"""Portable Claude auth environment variables honored by the Docker example."""

CLAUDE_KEYCHAIN_SERVICE: Final[str] = "Claude Code-credentials"
"""macOS Keychain service name used by the shipped Claude Docker example."""

DOCKERIZED_AGENT_PROVIDER_CHOICES: Final[frozenset[str]] = frozenset(
    {"claude_code", "codex", "gemini", "opencode", "qwen"}
)
"""Supported provider names for the shipped Dockerized agent builtin."""

DOCKERIZED_AGENT_DEFAULT_ENVIRONMENT_PROFILE_REF: Final[str] = (
    "dockerized_agent:dockerized_agent_environment_profile"
)
"""Default environment-profile hook for the Dockerized agent builtin."""

DOCKERIZED_AGENT_CLAUDE_ENVIRONMENT_PROFILE_REF: Final[str] = (
    "weft.builtins.dockerized_agent_examples:"
    "claude_code_dockerized_agent_environment_profile"
)
"""Claude-specific environment-profile hook for the Dockerized agent builtin."""


# ==============================================================================
# SIMPLEBROKER INTEGRATION MAPPINGS
# ==============================================================================

# Mapping of WEFT_* environment variables to BROKER_* equivalents
# This allows weft to use SimpleBroker configuration without conflicts
SIMPLEBROKER_ENV_MAPPING: Final[dict[str, str]] = {
    "WEFT_BUSY_TIMEOUT": "BROKER_BUSY_TIMEOUT",
    "WEFT_CACHE_MB": "BROKER_CACHE_MB",
    "WEFT_SYNC_MODE": "BROKER_SYNC_MODE",
    "WEFT_WAL_AUTOCHECKPOINT": "BROKER_WAL_AUTOCHECKPOINT",
    "WEFT_MAX_MESSAGE_SIZE": "BROKER_MAX_MESSAGE_SIZE",
    "WEFT_READ_COMMIT_INTERVAL": "BROKER_READ_COMMIT_INTERVAL",
    "WEFT_GENERATOR_BATCH_SIZE": "BROKER_GENERATOR_BATCH_SIZE",
    "WEFT_AUTO_VACUUM": "BROKER_AUTO_VACUUM",
    "WEFT_AUTO_VACUUM_INTERVAL": "BROKER_AUTO_VACUUM_INTERVAL",
    "WEFT_VACUUM_THRESHOLD": "BROKER_VACUUM_THRESHOLD",
    "WEFT_VACUUM_BATCH_SIZE": "BROKER_VACUUM_BATCH_SIZE",
    "WEFT_VACUUM_LOCK_TIMEOUT": "BROKER_VACUUM_LOCK_TIMEOUT",
    "WEFT_SKIP_IDLE_CHECK": "BROKER_SKIP_IDLE_CHECK",
    "WEFT_JITTER_FACTOR": "BROKER_JITTER_FACTOR",
    "WEFT_INITIAL_CHECKS": "BROKER_INITIAL_CHECKS",
    "WEFT_MAX_INTERVAL": "BROKER_MAX_INTERVAL",
    "WEFT_BURST_SLEEP": "BROKER_BURST_SLEEP",
    "WEFT_DEFAULT_DB_LOCATION": "BROKER_DEFAULT_DB_LOCATION",
    "WEFT_DEFAULT_DB_NAME": "BROKER_DEFAULT_DB_NAME",
    "WEFT_PROJECT_CONFIG_PATH": "BROKER_PROJECT_CONFIG_PATH",
    "WEFT_PROJECT_CONFIG_NAME": "BROKER_PROJECT_CONFIG_NAME",
    "WEFT_PROJECT_SCOPE": "BROKER_PROJECT_SCOPE",
    "WEFT_BACKEND": "BROKER_BACKEND",
    "WEFT_BACKEND_HOST": "BROKER_BACKEND_HOST",
    "WEFT_BACKEND_PORT": "BROKER_BACKEND_PORT",
    "WEFT_BACKEND_USER": "BROKER_BACKEND_USER",
    "WEFT_BACKEND_PASSWORD": "BROKER_BACKEND_PASSWORD",
    "WEFT_BACKEND_DATABASE": "BROKER_BACKEND_DATABASE",
    "WEFT_BACKEND_SCHEMA": "BROKER_BACKEND_SCHEMA",
    "WEFT_BACKEND_TARGET": "BROKER_BACKEND_TARGET",
}

# Weft-specific defaults for SimpleBroker integration
WEFT_SIMPLEBROKER_DEFAULTS: Final[dict[str, Any]] = {
    "BROKER_PROJECT_SCOPE": True,
}


def _default_broker_default_db_name(weft_directory_name: str) -> str:
    """Return the default sqlite broker path rooted under the Weft metadata directory."""

    return f"{weft_directory_name}/broker.db"


def _translate_weft_config_vars(config: Mapping[str, Any]) -> dict[str, Any]:
    """Translate raw WEFT_* config values to BROKER_* equivalents."""

    translated: dict[str, Any] = {}

    for weft_key, broker_key in SIMPLEBROKER_ENV_MAPPING.items():
        if weft_key not in config:
            continue
        value = config[weft_key]
        if value is not None:
            translated[broker_key] = value

    return translated


def _apply_weft_simplebroker_defaults(
    config: dict[str, Any], *, weft_directory_name: str
) -> None:
    """Apply weft-specific defaults for SimpleBroker integration.

    Args:
        config: Configuration dictionary to modify in-place
    """
    for key, default_value in WEFT_SIMPLEBROKER_DEFAULTS.items():
        config.setdefault(key, default_value)
    config.setdefault(
        "BROKER_DEFAULT_DB_NAME",
        _default_broker_default_db_name(weft_directory_name),
    )
    config.setdefault("BROKER_PROJECT_CONFIG_PATH", weft_directory_name)
    config.setdefault("BROKER_PROJECT_CONFIG_NAME", WEFT_BROKER_PROJECT_CONFIG_FILENAME)


def _resolve_weft_broker_config(
    config: Mapping[str, Any],
    *,
    base_broker_config: Mapping[str, Any] | None = None,
    explicit_broker_overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the complete typed SimpleBroker config for the supplied Weft config."""

    broker_overrides = dict(base_broker_config or {})
    broker_overrides.update(_translate_weft_config_vars(config))
    if explicit_broker_overrides is not None:
        broker_overrides.update(explicit_broker_overrides)
    _apply_weft_simplebroker_defaults(
        broker_overrides,
        weft_directory_name=get_weft_directory_name(config),
    )
    broker_overrides["BROKER_DEBUG"] = config["WEFT_DEBUG"]
    broker_overrides["BROKER_LOGGING_ENABLED"] = config["WEFT_LOGGING_ENABLED"]
    _validate_postgres_backend_config_shape(broker_overrides)
    resolved = resolve_broker_config(broker_overrides)
    return resolved


def _parse_bool(value: str | None) -> bool:
    """Parse a boolean value from environment variable string.

    Args:
        value: String value from environment variable

    Returns:
        False if value is None, null, empty, "0", "f", "F", "false", "False", "FALSE"
        True otherwise (for any non-empty string not in the false list)
    """
    if not value:
        return False

    # Values that should be considered False
    false_values = {"0", "F", "NONE", "NULL", "FALSE"}
    return value.upper() not in false_values


def _parse_logging_enabled(value: str) -> bool:
    """Parse logging enablement with the existing strict compatibility rule."""

    return value == "1"


def _parse_non_negative_float(value: str, *, name: str) -> float:
    """Parse a non-negative float environment value."""

    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a non-negative float, got {value!r}") from exc

    if parsed < 0:
        raise ValueError(f"{name} must be a non-negative float, got {value!r}")

    return parsed


def _parse_positive_float(value: str, *, name: str) -> float:
    """Parse a positive float environment value."""

    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive float, got {value!r}") from exc

    if parsed <= 0:
        raise ValueError(f"{name} must be a positive float, got {value!r}")

    return parsed


def _parse_positive_int(value: str, *, name: str) -> int:
    """Parse a positive integer environment value."""

    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer, got {value!r}") from exc

    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer, got {value!r}")

    return parsed


def _parse_manager_lifetime_timeout(value: str) -> float:
    """Parse the manager idle timeout environment variable."""

    return _parse_non_negative_float(value, name="WEFT_MANAGER_LIFETIME_TIMEOUT")


def _parse_task_monitor_interval_seconds(value: str) -> int:
    """Parse the task-monitor heartbeat interval environment variable."""

    parsed = _parse_positive_int(value, name="WEFT_TASK_MONITOR_INTERVAL_SECONDS")
    if parsed < HEARTBEAT_MIN_INTERVAL_SECONDS:
        raise ValueError(
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS must be at least "
            f"{HEARTBEAT_MIN_INTERVAL_SECONDS}, got {parsed}"
        )
    return parsed


def _parse_task_monitor_catchup_interval_seconds(value: str) -> float:
    """Parse the task-monitor backlog catch-up interval."""

    return _parse_positive_float(
        value,
        name="WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS",
    )


def _parse_task_monitor_batch_size(value: str) -> int:
    """Parse the task-monitor batch size environment variable."""

    return _parse_positive_int(value, name="WEFT_TASK_MONITOR_BATCH_SIZE")


def _parse_task_monitor_task_log_scan_limit(value: str) -> int:
    """Parse the task-monitor task-log scan limit environment variable."""

    return _parse_positive_int(
        value,
        name="WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT",
    )


def _parse_task_monitor_store_write_batch_size(value: str) -> int:
    """Parse the task-monitor store write batch size environment variable."""

    return _parse_positive_int(
        value,
        name="WEFT_TASK_MONITOR_STORE_WRITE_BATCH_SIZE",
    )


def _parse_task_monitor_stale_open_family_seconds(value: str) -> float:
    """Parse the task-monitor stale-open family hard-age threshold."""

    return _parse_positive_float(
        value,
        name="WEFT_TASK_MONITOR_STALE_OPEN_FAMILY_SECONDS",
    )


def _parse_task_monitor_control_queue_delete_limit(value: str) -> int:
    """Parse the task-monitor per-cycle control-cleanup family limit."""

    return _parse_positive_int(
        value,
        name="WEFT_TASK_MONITOR_CONTROL_QUEUE_DELETE_LIMIT",
    )


def _parse_task_monitor_cleanup_workers(value: str) -> int:
    """Parse the task-monitor cleanup worker cap."""

    parsed = _parse_positive_int(
        value,
        name="WEFT_TASK_MONITOR_CLEANUP_WORKERS",
    )
    if parsed > WEFT_TASK_MONITOR_CLEANUP_WORKERS_DEFAULT:
        raise ValueError(
            "WEFT_TASK_MONITOR_CLEANUP_WORKERS must be between 1 and "
            f"{WEFT_TASK_MONITOR_CLEANUP_WORKERS_DEFAULT}, got {parsed}"
        )
    return parsed


def _parse_log_tasks_retention_period_seconds(value: str) -> float:
    """Parse the task-log external retention period environment variable."""

    return _parse_positive_float(
        value,
        name="WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS",
    )


def _parse_log_tasks_external_path(value: str) -> str:
    """Parse the optional external task-log JSONL path."""

    return value.strip()


def _parse_log_tasks_external_mode(value: str) -> str:
    """Parse the external task-log logging mode."""

    parsed = value.strip().lower()
    if parsed not in WEFT_LOG_TASKS_EXTERNAL_MODES:
        allowed = ", ".join(sorted(WEFT_LOG_TASKS_EXTERNAL_MODES))
        raise ValueError(f"WEFT_LOG_TASKS_EXTERNAL_MODE must be one of: {allowed}")
    return parsed


def _parse_task_monitor_processor(value: str) -> str:
    """Parse the task-monitor processor name or dotted callable reference."""

    parsed = value.strip()
    if not parsed:
        raise ValueError("WEFT_TASK_MONITOR_PROCESSOR must be non-empty")
    if parsed in WEFT_TASK_MONITOR_PROCESSOR_BUILTINS:
        return parsed
    if ":" not in parsed:
        raise ValueError(
            "WEFT_TASK_MONITOR_PROCESSOR must be a built-in processor name "
            "or a module:function reference"
        )
    module_name, function_name = parsed.split(":", 1)
    if not module_name.strip() or not function_name.strip():
        raise ValueError(
            "WEFT_TASK_MONITOR_PROCESSOR module:function reference is malformed"
        )
    return parsed


def _parse_task_monitor_log_sink(value: str) -> str:
    """Parse the task-monitor operational output sink name."""

    parsed = value.strip().lower()
    if parsed not in WEFT_TASK_MONITOR_LOG_SINKS:
        allowed = ", ".join(sorted(WEFT_TASK_MONITOR_LOG_SINKS))
        raise ValueError(f"WEFT_TASK_MONITOR_LOG_SINK must be one of: {allowed}")
    return parsed


def _parse_task_monitor_restart_backoff_seconds(value: str) -> float:
    """Parse the task-monitor restart backoff environment variable."""

    return _parse_positive_float(
        value,
        name="WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS",
    )


def _parse_manager_serve_log_level(value: str) -> str:
    """Parse the foreground manager operational-log level."""

    parsed = value.strip().lower()
    if parsed not in WEFT_MANAGER_SERVE_LOG_LEVELS:
        allowed = ", ".join(sorted(WEFT_MANAGER_SERVE_LOG_LEVELS))
        raise ValueError(f"WEFT_MANAGER_SERVE_LOG_LEVEL must be one of: {allowed}")
    return parsed


def _parse_manager_serve_log_interval(value: str) -> float:
    """Parse the foreground manager operational-log rate-limit interval."""

    return _parse_positive_float(
        value,
        name="WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS",
    )


def _parse_weft_directory_name(value: str) -> str:
    """Parse the configurable Weft metadata directory name.

    The value is intentionally a directory name, not an arbitrary path.
    """

    name = value.strip()
    if not name:
        raise ValueError("WEFT_DIRECTORY_NAME must be a non-empty directory name")
    if name in {".", ".."}:
        raise ValueError(
            "WEFT_DIRECTORY_NAME must not be '.' or '..'; use a real directory name"
        )
    if "/" in name or "\\" in name:
        raise ValueError("WEFT_DIRECTORY_NAME must be a directory name, not a path")
    return name


def _parse_weft_logs_dir(value: str) -> str | None:
    """Parse the optional operational logs directory override."""

    path = value.strip()
    return path or None


def _load_weft_env_value(
    name: str,
    *,
    default: Any,
    parser: Callable[[str], Any],
) -> Any:
    """Load and normalize one Weft-owned environment value."""

    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    return parser(raw_value)


def _config_has_non_empty_value(config: Mapping[str, Any], *keys: str) -> bool:
    """Return whether any named config key is set to a non-empty value."""

    for key in keys:
        value = config.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            if value.strip():
                return True
            continue
        return True
    return False


POSTGRES_BACKEND_PART_DEFAULTS: Final[dict[str, Any]] = {
    "BROKER_BACKEND_HOST": "localhost",
    "BROKER_BACKEND_PORT": 5432,
    "BROKER_BACKEND_USER": "postgres",
    "BROKER_BACKEND_DATABASE": "simplebroker",
}
"""SimpleBroker's implicit Postgres connection-part defaults."""


def _config_has_non_default_postgres_part(
    config: Mapping[str, Any],
    key: str,
) -> bool:
    """Return whether a Postgres connection part differs from its implicit default."""

    if not _config_has_non_empty_value(config, key):
        return False
    return bool(config.get(key) != POSTGRES_BACKEND_PART_DEFAULTS[key])


def _validate_postgres_backend_config_shape(config: Mapping[str, Any]) -> None:
    """Reject ambiguous Postgres backend configuration shapes."""

    backend_name = str(config.get("BROKER_BACKEND", "sqlite")).strip().lower()
    if backend_name != "postgres":
        return

    if not _config_has_non_empty_value(config, "BROKER_BACKEND_TARGET"):
        return

    conflicting_parts: list[str] = []
    if _config_has_non_default_postgres_part(config, "BROKER_BACKEND_HOST"):
        conflicting_parts.append("host")
    if _config_has_non_default_postgres_part(config, "BROKER_BACKEND_PORT"):
        conflicting_parts.append("port")
    if _config_has_non_default_postgres_part(config, "BROKER_BACKEND_USER"):
        conflicting_parts.append("user")
    if _config_has_non_default_postgres_part(config, "BROKER_BACKEND_DATABASE"):
        conflicting_parts.append("database")

    if conflicting_parts:
        parts = ", ".join(conflicting_parts)
        raise ValueError(
            "Postgres backend configuration is ambiguous: set "
            "WEFT/BROKER_BACKEND_TARGET or WEFT/BROKER_BACKEND_HOST/PORT/USER/"
            f"DATABASE, not both (conflicting parts: {parts})"
        )


def _load_weft_env_vars() -> dict[str, Any]:
    """Load weft-specific configuration from environment variables.

    Returns:
        Dict with WEFT_* configuration values
    """
    if "WEFT_TASK_MONITOR_TASK_LOG_CUTOFF_SECONDS" in os.environ:
        raise ValueError(
            "WEFT_TASK_MONITOR_TASK_LOG_CUTOFF_SECONDS was removed; use "
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS"
        )
    external_task_log_path = _load_weft_env_value(
        "WEFT_LOG_TASKS_EXTERNAL_PATH",
        default=WEFT_LOG_TASKS_EXTERNAL_PATH_DEFAULT,
        parser=_parse_log_tasks_external_path,
    )
    external_task_log_enabled = _load_weft_env_value(
        "WEFT_LOG_TASKS_EXTERNAL_ENABLED",
        default=bool(external_task_log_path),
        parser=_parse_bool,
    )
    env_vars = {
        "WEFT_DEBUG": _load_weft_env_value(
            "WEFT_DEBUG",
            default=False,
            parser=_parse_bool,
        ),
        "WEFT_LOGGING_ENABLED": _load_weft_env_value(
            "WEFT_LOGGING_ENABLED",
            default=False,
            parser=_parse_logging_enabled,
        ),
        "WEFT_LOGS_DIR": _load_weft_env_value(
            "WEFT_LOGS_DIR",
            default=None,
            parser=_parse_weft_logs_dir,
        ),
        "WEFT_REDACT_TASKSPEC_FIELDS": _load_weft_env_value(
            "WEFT_REDACT_TASKSPEC_FIELDS",
            default="",
            parser=str,
        ),
        "WEFT_LOG_TASKS_EXTERNAL_PATH": external_task_log_path,
        "WEFT_LOG_TASKS_EXTERNAL_ENABLED": external_task_log_enabled,
        "WEFT_LOG_TASKS_EXTERNAL_MODE": _load_weft_env_value(
            "WEFT_LOG_TASKS_EXTERNAL_MODE",
            default=WEFT_LOG_TASKS_EXTERNAL_MODE_DEFAULT,
            parser=_parse_log_tasks_external_mode,
        ),
        "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": _load_weft_env_value(
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS",
            default=WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS_DEFAULT,
            parser=_parse_log_tasks_retention_period_seconds,
        ),
        "WEFT_DIRECTORY_NAME": _load_weft_env_value(
            "WEFT_DIRECTORY_NAME",
            default=WEFT_DIRECTORY_NAME_DEFAULT,
            parser=_parse_weft_directory_name,
        ),
        "WEFT_MANAGER_LIFETIME_TIMEOUT": _load_weft_env_value(
            "WEFT_MANAGER_LIFETIME_TIMEOUT",
            default=WEFT_MANAGER_LIFETIME_TIMEOUT,
            parser=_parse_manager_lifetime_timeout,
        ),
        "WEFT_MANAGER_REUSE_ENABLED": _load_weft_env_value(
            "WEFT_MANAGER_REUSE_ENABLED",
            default=WEFT_MANAGER_REUSE_ENABLED,
            parser=_parse_bool,
        ),
        "WEFT_MANAGER_RUNTIME_HANDLE_JSON": _load_weft_env_value(
            WEFT_MANAGER_RUNTIME_HANDLE_JSON_ENV,
            default=None,
            parser=lambda value: value,
        ),
        "WEFT_AUTOSTART_TASKS": _load_weft_env_value(
            "WEFT_AUTOSTART_TASKS",
            default=WEFT_AUTOSTART_TASKS_DEFAULT,
            parser=_parse_bool,
        ),
        "WEFT_TASK_MONITOR_ENABLED": _load_weft_env_value(
            "WEFT_TASK_MONITOR_ENABLED",
            default=WEFT_TASK_MONITOR_ENABLED_DEFAULT,
            parser=_parse_bool,
        ),
        "WEFT_TASK_MONITOR_INTERVAL_SECONDS": _load_weft_env_value(
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS",
            default=WEFT_TASK_MONITOR_INTERVAL_SECONDS_DEFAULT,
            parser=_parse_task_monitor_interval_seconds,
        ),
        "WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS": _load_weft_env_value(
            "WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS",
            default=WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS_DEFAULT,
            parser=_parse_task_monitor_catchup_interval_seconds,
        ),
        "WEFT_TASK_MONITOR_BATCH_SIZE": _load_weft_env_value(
            "WEFT_TASK_MONITOR_BATCH_SIZE",
            default=WEFT_TASK_MONITOR_BATCH_SIZE_DEFAULT,
            parser=_parse_task_monitor_batch_size,
        ),
        "WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT": _load_weft_env_value(
            "WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT",
            default=WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT_DEFAULT,
            parser=_parse_task_monitor_task_log_scan_limit,
        ),
        "WEFT_TASK_MONITOR_STORE_WRITE_BATCH_SIZE": _load_weft_env_value(
            "WEFT_TASK_MONITOR_STORE_WRITE_BATCH_SIZE",
            default=WEFT_TASK_MONITOR_STORE_WRITE_BATCH_SIZE_DEFAULT,
            parser=_parse_task_monitor_store_write_batch_size,
        ),
        "WEFT_TASK_MONITOR_STALE_OPEN_FAMILY_SECONDS": _load_weft_env_value(
            "WEFT_TASK_MONITOR_STALE_OPEN_FAMILY_SECONDS",
            default=WEFT_TASK_MONITOR_STALE_OPEN_FAMILY_SECONDS_DEFAULT,
            parser=_parse_task_monitor_stale_open_family_seconds,
        ),
        "WEFT_TASK_MONITOR_CONTROL_QUEUE_DELETE_LIMIT": _load_weft_env_value(
            "WEFT_TASK_MONITOR_CONTROL_QUEUE_DELETE_LIMIT",
            default=WEFT_TASK_MONITOR_CONTROL_QUEUE_DELETE_LIMIT_DEFAULT,
            parser=_parse_task_monitor_control_queue_delete_limit,
        ),
        "WEFT_TASK_MONITOR_CLEANUP_WORKERS": _load_weft_env_value(
            "WEFT_TASK_MONITOR_CLEANUP_WORKERS",
            default=WEFT_TASK_MONITOR_CLEANUP_WORKERS_DEFAULT,
            parser=_parse_task_monitor_cleanup_workers,
        ),
        "WEFT_TASK_MONITOR_PROCESSOR": _load_weft_env_value(
            "WEFT_TASK_MONITOR_PROCESSOR",
            default=WEFT_TASK_MONITOR_PROCESSOR_DEFAULT,
            parser=_parse_task_monitor_processor,
        ),
        "WEFT_TASK_MONITOR_LOG_SINK": _load_weft_env_value(
            "WEFT_TASK_MONITOR_LOG_SINK",
            default=WEFT_TASK_MONITOR_LOG_SINK_DEFAULT,
            parser=_parse_task_monitor_log_sink,
        ),
        "WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS": _load_weft_env_value(
            "WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS",
            default=WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS_DEFAULT,
            parser=_parse_task_monitor_restart_backoff_seconds,
        ),
        "WEFT_TASK_MONITOR_COLLATION_STORE_ENABLED": _load_weft_env_value(
            "WEFT_TASK_MONITOR_COLLATION_STORE_ENABLED",
            default=WEFT_TASK_MONITOR_COLLATION_STORE_ENABLED_DEFAULT,
            parser=_parse_bool,
        ),
        "WEFT_TASK_MONITOR_TABLE_DELETE_ENABLED": _load_weft_env_value(
            "WEFT_TASK_MONITOR_TABLE_DELETE_ENABLED",
            default=WEFT_TASK_MONITOR_TABLE_DELETE_ENABLED_DEFAULT,
            parser=_parse_bool,
        ),
        WEFT_MANAGER_SERVE_LOG_LEVEL: _load_weft_env_value(
            WEFT_MANAGER_SERVE_LOG_LEVEL,
            default=WEFT_MANAGER_SERVE_LOG_LEVEL_DEFAULT,
            parser=_parse_manager_serve_log_level,
        ),
        WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS: _load_weft_env_value(
            WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS,
            default=WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS_DEFAULT,
            parser=_parse_manager_serve_log_interval,
        ),
    }
    for weft_key in SIMPLEBROKER_ENV_MAPPING:
        if weft_key in env_vars:
            continue
        raw_value = os.environ.get(weft_key)
        if raw_value is not None:
            env_vars[weft_key] = raw_value
    return env_vars


def _add_simplebroker_env_vars(config: dict[str, Any]) -> None:
    """Add SimpleBroker configuration to weft config dictionary.

    Args:
        config: Configuration dictionary to modify in-place

    This function:
    1. Translates WEFT_* environment variables to BROKER_* equivalents
    2. Applies weft-specific defaults for SimpleBroker integration
    """
    config.update(_resolve_weft_broker_config(config))


def _normalize_weft_override_value(name: str, value: Any) -> Any:
    """Normalize one explicit in-process config override."""

    if name == "WEFT_DEBUG":
        return _parse_bool(value) if isinstance(value, str) else bool(value)
    if name == "WEFT_LOGGING_ENABLED":
        if isinstance(value, str):
            return _parse_logging_enabled(value)
        if isinstance(value, bool):
            return value
        raise TypeError("WEFT_LOGGING_ENABLED override must be bool or str")
    if name == "WEFT_LOGS_DIR":
        if value is None:
            return None
        if isinstance(value, str):
            return _parse_weft_logs_dir(value)
        raise TypeError("WEFT_LOGS_DIR override must be str or None")
    if name == "WEFT_REDACT_TASKSPEC_FIELDS":
        if isinstance(value, str):
            return value
        raise TypeError("WEFT_REDACT_TASKSPEC_FIELDS override must be str")
    if name == "WEFT_LOG_TASKS_EXTERNAL_PATH":
        if isinstance(value, str):
            return _parse_log_tasks_external_path(value)
        raise TypeError("WEFT_LOG_TASKS_EXTERNAL_PATH override must be str")
    if name == "WEFT_LOG_TASKS_EXTERNAL_ENABLED":
        return _parse_bool(value) if isinstance(value, str) else bool(value)
    if name == "WEFT_LOG_TASKS_EXTERNAL_MODE":
        if isinstance(value, str):
            return _parse_log_tasks_external_mode(value)
        raise TypeError("WEFT_LOG_TASKS_EXTERNAL_MODE override must be str")
    if name == "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS":
        if isinstance(value, str):
            return _parse_log_tasks_retention_period_seconds(value)
        if isinstance(value, int | float):
            return _parse_log_tasks_retention_period_seconds(str(float(value)))
        raise TypeError(
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS override must be int, "
            "float, or str"
        )
    if name == "WEFT_TASK_MONITOR_TASK_LOG_CUTOFF_SECONDS":
        raise ValueError(
            "WEFT_TASK_MONITOR_TASK_LOG_CUTOFF_SECONDS was removed; use "
            "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS"
        )
    if name == "WEFT_DIRECTORY_NAME":
        if isinstance(value, str):
            return _parse_weft_directory_name(value)
        raise TypeError("WEFT_DIRECTORY_NAME override must be str")
    if name == "WEFT_MANAGER_LIFETIME_TIMEOUT":
        if isinstance(value, str):
            return _parse_manager_lifetime_timeout(value)
        if isinstance(value, int | float):
            return _parse_non_negative_float(
                str(float(value)),
                name="WEFT_MANAGER_LIFETIME_TIMEOUT",
            )
        raise TypeError(
            "WEFT_MANAGER_LIFETIME_TIMEOUT override must be int, float, or str"
        )
    if name in {
        "WEFT_MANAGER_REUSE_ENABLED",
        "WEFT_AUTOSTART_TASKS",
        "WEFT_TASK_MONITOR_ENABLED",
        "WEFT_TASK_MONITOR_COLLATION_STORE_ENABLED",
        "WEFT_TASK_MONITOR_TABLE_DELETE_ENABLED",
    }:
        return _parse_bool(value) if isinstance(value, str) else bool(value)
    if name == "WEFT_TASK_MONITOR_INTERVAL_SECONDS":
        if isinstance(value, str):
            return _parse_task_monitor_interval_seconds(value)
        if isinstance(value, int):
            return _parse_task_monitor_interval_seconds(str(value))
        raise TypeError(
            "WEFT_TASK_MONITOR_INTERVAL_SECONDS override must be int or str"
        )
    if name == "WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS":
        if isinstance(value, str):
            return _parse_task_monitor_catchup_interval_seconds(value)
        if isinstance(value, int | float):
            return _parse_task_monitor_catchup_interval_seconds(str(float(value)))
        raise TypeError(
            "WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS override must be int, "
            "float, or str"
        )
    if name == "WEFT_TASK_MONITOR_BATCH_SIZE":
        if isinstance(value, str):
            return _parse_task_monitor_batch_size(value)
        if isinstance(value, int):
            return _parse_task_monitor_batch_size(str(value))
        raise TypeError("WEFT_TASK_MONITOR_BATCH_SIZE override must be int or str")
    if name == "WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT":
        if isinstance(value, str):
            return _parse_task_monitor_task_log_scan_limit(value)
        if isinstance(value, int):
            return _parse_task_monitor_task_log_scan_limit(str(value))
        raise TypeError(
            "WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT override must be int or str"
        )
    if name == "WEFT_TASK_MONITOR_STORE_WRITE_BATCH_SIZE":
        if isinstance(value, str):
            return _parse_task_monitor_store_write_batch_size(value)
        if isinstance(value, int):
            return _parse_task_monitor_store_write_batch_size(str(value))
        raise TypeError(
            "WEFT_TASK_MONITOR_STORE_WRITE_BATCH_SIZE override must be int or str"
        )
    if name == "WEFT_TASK_MONITOR_STALE_OPEN_FAMILY_SECONDS":
        if isinstance(value, str):
            return _parse_task_monitor_stale_open_family_seconds(value)
        if isinstance(value, int | float):
            return _parse_task_monitor_stale_open_family_seconds(str(float(value)))
        raise TypeError(
            "WEFT_TASK_MONITOR_STALE_OPEN_FAMILY_SECONDS override must be int, "
            "float, or str"
        )
    if name == "WEFT_TASK_MONITOR_CONTROL_QUEUE_DELETE_LIMIT":
        if isinstance(value, str):
            return _parse_task_monitor_control_queue_delete_limit(value)
        if isinstance(value, int):
            return _parse_task_monitor_control_queue_delete_limit(str(value))
        raise TypeError(
            "WEFT_TASK_MONITOR_CONTROL_QUEUE_DELETE_LIMIT override must be int or str"
        )
    if name == "WEFT_TASK_MONITOR_CLEANUP_WORKERS":
        if isinstance(value, str):
            return _parse_task_monitor_cleanup_workers(value)
        if isinstance(value, int):
            return _parse_task_monitor_cleanup_workers(str(value))
        raise TypeError("WEFT_TASK_MONITOR_CLEANUP_WORKERS override must be int or str")
    if name == "WEFT_TASK_MONITOR_PROCESSOR":
        if isinstance(value, str):
            return _parse_task_monitor_processor(value)
        raise TypeError("WEFT_TASK_MONITOR_PROCESSOR override must be str")
    if name == "WEFT_TASK_MONITOR_LOG_SINK":
        if isinstance(value, str):
            return _parse_task_monitor_log_sink(value)
        raise TypeError("WEFT_TASK_MONITOR_LOG_SINK override must be str")
    if name == "WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS":
        if isinstance(value, str):
            return _parse_task_monitor_restart_backoff_seconds(value)
        if isinstance(value, int | float):
            return _parse_task_monitor_restart_backoff_seconds(str(float(value)))
        raise TypeError(
            "WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS override must be int, "
            "float, or str"
        )
    if name == WEFT_MANAGER_SERVE_LOG_LEVEL:
        if isinstance(value, str):
            return _parse_manager_serve_log_level(value)
        raise TypeError("WEFT_MANAGER_SERVE_LOG_LEVEL override must be str")
    if name == WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS:
        if isinstance(value, str):
            return _parse_manager_serve_log_interval(value)
        if isinstance(value, int | float):
            return _parse_manager_serve_log_interval(str(float(value)))
        raise TypeError(
            "WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS override must be int, "
            "float, or str"
        )
    if name == MANAGER_SERVE_LOG_ACTIVE_CONFIG_KEY:
        return _parse_bool(value) if isinstance(value, str) else bool(value)
    return value


def _normalize_weft_overrides(overrides: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize explicit in-process config overrides."""

    return {
        key: _normalize_weft_override_value(key, value)
        for key, value in overrides.items()
    }


def load_environment() -> dict[str, Any]:
    """Load configuration from environment variables.

    This function reads all Weft environment variables and returns
    a configuration dictionary with validated values. It's designed to be
    called once at module initialization to avoid repeated environment lookups.

    Returns:
        dict: Configuration dictionary with the following keys:

        Weft-specific:
            WEFT_DEBUG (bool): Enable debug output.
                Default: False
                Shows additional diagnostic information.
                False for: empty, "0", "f", "F", "false", "False", "FALSE"
                True for: any other non-empty value (e.g., "1", "true", "yes")

            WEFT_LOGGING_ENABLED (bool): Enable logging output.
                Default: False (disabled)
                Set to "1" to enable logging throughout Weft.
                When enabled, logs will be written using Python's logging module.
                Configure logging levels and handlers in your application as needed.

            WEFT_MANAGER_SERVE_LOG_LEVEL (str): Enable structured JSONL
                operational logs for `weft manager serve`.
                Default: "off"; allowed: "off", "info", "debug", "trace".

            WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS (float): Minimum interval
                for repeated foreground manager operational-log events.
                Default: 5.0.

        SimpleBroker integration:
            BROKER_* keys translated from WEFT_* environment variables
            for seamless SimpleBroker API integration without conflicts.

    """
    env_vars = _load_weft_env_vars()
    _add_simplebroker_env_vars(env_vars)
    return env_vars


def compile_config(overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Compile canonical Weft config from env plus optional in-process overrides.

    Args:
        overrides: Optional mapping of explicit WEFT_* and/or BROKER_* overrides.

    Returns:
        A fresh configuration dictionary containing resolved WEFT_* and BROKER_* keys.
    """

    base_config = load_environment()
    if overrides is None:
        return base_config

    normalized_overrides = _normalize_weft_overrides(overrides)
    resolved_config = dict(base_config)
    resolved_config.update(normalized_overrides)
    if (
        "WEFT_LOG_TASKS_EXTERNAL_PATH" in normalized_overrides
        and "WEFT_LOG_TASKS_EXTERNAL_ENABLED" not in normalized_overrides
    ):
        resolved_config["WEFT_LOG_TASKS_EXTERNAL_ENABLED"] = bool(
            resolved_config["WEFT_LOG_TASKS_EXTERNAL_PATH"]
        )

    base_broker_config = {
        key: value for key, value in base_config.items() if key.startswith("BROKER_")
    }
    explicit_broker_overrides = {
        key: value
        for key, value in normalized_overrides.items()
        if key.startswith("BROKER_")
    }
    if (
        "WEFT_DIRECTORY_NAME" in normalized_overrides
        and "WEFT_DEFAULT_DB_NAME" not in normalized_overrides
        and "BROKER_DEFAULT_DB_NAME" not in explicit_broker_overrides
    ):
        base_broker_config.pop("BROKER_DEFAULT_DB_NAME", None)
    if (
        "WEFT_DIRECTORY_NAME" in normalized_overrides
        and "WEFT_PROJECT_CONFIG_PATH" not in normalized_overrides
        and "BROKER_PROJECT_CONFIG_PATH" not in explicit_broker_overrides
    ):
        base_broker_config.pop("BROKER_PROJECT_CONFIG_PATH", None)
    resolved_config.update(
        _resolve_weft_broker_config(
            resolved_config,
            base_broker_config=base_broker_config,
            explicit_broker_overrides=explicit_broker_overrides,
        )
    )
    return resolved_config


def load_config(overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Public entry point for retrieving the current configuration.

    Args:
        overrides: Optional explicit WEFT_* and/or BROKER_* overrides to
            compile on top of the current process environment.

    Returns:
        A fresh configuration dictionary containing both WEFT_* and BROKER_* keys.

    Notes:
        The returned dictionary is not cached; callers should cache it themselves
        if repeated lookups are required.
    """
    return compile_config(overrides)


def get_weft_directory_name(config: Mapping[str, Any] | None = None) -> str:
    """Return the configured Weft metadata directory name."""

    source = config if config is not None else load_config()
    value = source.get("WEFT_DIRECTORY_NAME")
    if isinstance(value, str) and value.strip():
        return value
    return WEFT_DIRECTORY_NAME_DEFAULT


def reload_environment(config: dict[str, Any]) -> dict[str, Any]:
    """Reload the environment variables and update the configuration.

    Args:
        config: The current configuration dictionary.

    Returns:
        The updated configuration dictionary with reloaded environment variables.
    """
    new_env_vars = load_environment()
    config.update(new_env_vars)
    return config
