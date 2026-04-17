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

__version__: Final[str] = "0.9.2"
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

TASK_PROCESS_POLL_INTERVAL: Final[float] = 0.05
"""Polling interval for spawned task-process main loops between `process_once` calls."""

MANAGER_POLL_INTERVAL: Final[float] = TASK_PROCESS_POLL_INTERVAL
"""Polling interval for foreground manager-service loops."""

CONTROL_SURFACE_WAIT_TIMEOUT: Final[float] = 2.0
"""Maximum time to wait for durable terminal task state before CLI fallback."""

CONTROL_SURFACE_WAIT_INTERVAL: Final[float] = 0.05
"""Polling interval while waiting for task terminal state in CLI control flows."""

SPAWN_SUBMISSION_RECONCILIATION_TIMEOUT: Final[float] = 1.0
"""Default time budget for classifying a submitted spawn request via durable state."""

STATUS_WATCH_MIN_INTERVAL: Final[float] = 0.1
"""Minimum poll interval for CLI status-watch loops to avoid broker-hot spins."""

MANAGER_STARTUP_TIMEOUT_SECONDS: Final[float] = 10.0
"""Time budget for a detached manager to publish a stable active registry entry."""

MANAGER_REGISTRY_POLL_INTERVAL: Final[float] = 0.1
"""Polling interval while observing manager registry state during startup/shutdown."""

MANAGER_PID_LIVENESS_RECHECK_INTERVAL: Final[float] = 0.5
"""Throttle for repeated PID-liveness probes during stop-if-absent manager waits."""

MANAGER_CHILD_EXIT_POLL_INTERVAL: Final[float] = 0.05
"""Polling interval while the Manager waits for tracked child processes to exit."""

MANAGER_COMPETING_STARTUP_GRACE_SECONDS: Final[float] = 0.5
"""Grace window for concurrent manager starts to yield to an existing winner."""

MANAGER_TASK_CLASS_PATH: Final[str] = "weft.core.manager.Manager"
"""Import path for the runtime-owned Manager task class."""

MANAGER_STARTUP_LOG_DIRNAME: Final[str] = "manager-startup"
"""Subdirectory under the Weft logs directory used for detached manager bootstrap stderr."""

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

PROVIDER_CLI_VERSION_PROBE_TIMEOUT_SECONDS: Final[float] = 5.0
"""Timeout for basic provider CLI version probes."""

PROVIDER_CLI_OPENCODE_RUN_PROBE_TIMEOUT_SECONDS: Final[float] = 2.0
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

# Global Queue Names
# ------------------
# Spec: docs/specifications/00-Quick_Reference.md#queue-names
WEFT_GLOBAL_LOG_QUEUE: Final[str] = "weft.log.tasks"
"""Global queue for task state changes and events."""

WEFT_TID_MAPPINGS_QUEUE: Final[str] = "weft.state.tid_mappings"
"""Global queue for TID short->full mappings for process management."""

WEFT_MANAGERS_REGISTRY_QUEUE: Final[str] = "weft.state.managers"
"""Queue where managers register their capabilities and status."""

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

WEFT_SPAWN_REQUESTS_QUEUE: Final[str] = "weft.spawn.requests"
"""Global queue for manager-consumed task spawn requests."""

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

INTERNAL_HEARTBEAT_ENDPOINT_NAME: Final[str] = "_weft.heartbeat"
"""Reserved runtime endpoint claimed by the built-in heartbeat service."""

HEARTBEAT_MIN_INTERVAL_SECONDS: Final[int] = 60
"""Minimum interval accepted by the first-slice heartbeat service."""

HEARTBEAT_IDLE_TIMEOUT_SECONDS: Final[float] = 60.0
"""Idle timeout after the last registration before the heartbeat service exits."""

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
CONTROL_STOP: Final[str] = "STOP"
"""Control command to stop a running task."""

CONTROL_KILL: Final[str] = "KILL"
"""Control command to force-kill a running task."""

CONTROL_PAUSE: Final[str] = "PAUSE"
"""Control command to pause a running task (future feature)."""

CONTROL_RESUME: Final[str] = "RESUME"
"""Control command to resume a paused task (future feature)."""

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

WEFT_MANAGER_REUSE_ENABLED: Final[bool] = True
"""Whether a Manager started by the CLI should remain running after a task completes."""

WEFT_COMPLETED_RESULT_GRACE_SECONDS: Final[float] = 0.25
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

DOCKER_CONTAINER_LOOKUP_INTERVAL: Final[float] = 0.05
"""Polling interval while waiting for Docker container state visibility."""

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


def _parse_manager_lifetime_timeout(value: str) -> float:
    """Parse the manager idle timeout environment variable."""

    return _parse_non_negative_float(value, name="WEFT_MANAGER_LIFETIME_TIMEOUT")


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


def _validate_postgres_backend_config_shape(config: Mapping[str, Any]) -> None:
    """Reject ambiguous Postgres backend configuration shapes."""

    backend_name = str(config.get("BROKER_BACKEND", "sqlite")).strip().lower()
    if backend_name != "postgres":
        return

    if not _config_has_non_empty_value(config, "BROKER_BACKEND_TARGET"):
        return

    conflicting_parts: list[str] = []
    if _config_has_non_empty_value(config, "BROKER_BACKEND_HOST"):
        conflicting_parts.append("host")
    if _config_has_non_empty_value(config, "BROKER_BACKEND_PORT"):
        conflicting_parts.append("port")
    if _config_has_non_empty_value(config, "BROKER_BACKEND_USER"):
        conflicting_parts.append("user")
    if _config_has_non_empty_value(config, "BROKER_BACKEND_DATABASE"):
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
        "WEFT_REDACT_TASKSPEC_FIELDS": _load_weft_env_value(
            "WEFT_REDACT_TASKSPEC_FIELDS",
            default="",
            parser=str,
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
        "WEFT_AUTOSTART_TASKS": _load_weft_env_value(
            "WEFT_AUTOSTART_TASKS",
            default=WEFT_AUTOSTART_TASKS_DEFAULT,
            parser=_parse_bool,
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
    if name == "WEFT_REDACT_TASKSPEC_FIELDS":
        if isinstance(value, str):
            return value
        raise TypeError("WEFT_REDACT_TASKSPEC_FIELDS override must be str")
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
    if name in {"WEFT_MANAGER_REUSE_ENABLED", "WEFT_AUTOSTART_TASKS"}:
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
