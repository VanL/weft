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

import os
from typing import Any, Final, Literal

# ==============================================================================
# VERSION INFORMATION
# ==============================================================================

__version__: Final[str] = "0.1.0"
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

DEFAULT_REPORTING_INTERVAL: Final[Literal["transition"]] = "transition"
"""Default reporting interval. Either 'poll' or 'transition'."""

DEFAULT_ENABLE_PROCESS_TITLE: Final[bool] = True
"""Default for enabling OS process title updates for observability."""

DEFAULT_OUTPUT_SIZE_LIMIT_MB: Final[int] = 10
"""Default max output size before disk spill (SimpleBroker limit)."""

DEFAULT_WEFT_CONTEXT: Final[str | None] = None
"""Default weft context directory. None means auto-discovery."""

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
WEFT_GLOBAL_LOG_QUEUE: Final[str] = "weft.tasks.log"
"""Global queue for task state changes and events."""

WEFT_TID_MAPPINGS_QUEUE: Final[str] = "weft.state.process.tid_mappings"
"""Global queue for TID short->full mappings for process management."""

WEFT_WORKERS_REGISTRY_QUEUE: Final[str] = "weft.workers.registry"
"""Queue where workers register their capabilities and status."""

WEFT_STREAMING_SESSIONS_QUEUE: Final[str] = "weft.state.streaming.sessions"
"""Queue tracking active streaming sessions (interactive/streaming outputs)."""

WEFT_SPAWN_REQUESTS_QUEUE: Final[str] = "weft.spawn.requests"
"""Global queue for worker spawn requests."""

WEFT_MANAGER_CTRL_IN_QUEUE: Final[str] = "weft.manager.ctrl_in"
"""Control inbox for manager lifecycle commands."""

WEFT_MANAGER_CTRL_OUT_QUEUE: Final[str] = "weft.manager.ctrl_out"
"""Control response queue for manager status messages."""

WEFT_MANAGER_OUTBOX_QUEUE: Final[str] = "weft.manager.outbox"
"""Manager output queue (e.g. informational responses)."""

WORK_ENVELOPE_START: Final[dict[str, bool]] = {"__weft_start__": True}
"""Sentinel payload the Manager uses to trigger Consumer startup."""

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

# Control Commands
# ----------------
CONTROL_STOP: Final[str] = "STOP"
"""Control command to stop a running task."""

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


# Autostart behaviour
# -------------------
WEFT_AUTOSTART_DIRECTORY_NAME: Final[str] = "autostart"
"""Name of the directory under .weft/ that stores auto-start TaskSpec templates."""

WEFT_AUTOSTART_TASKS_DEFAULT: Final[bool] = True
"""Default for enabling auto-start TaskSpec templates when a Manager boots."""


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
}

# Weft-specific defaults for SimpleBroker integration
WEFT_SIMPLEBROKER_DEFAULTS: Final[dict[str, str]] = {
    "BROKER_PROJECT_SCOPE": "true",
    "BROKER_DEFAULT_DB_NAME": ".weft/broker.db",
}


def _translate_weft_env_vars() -> dict[str, str]:
    """Translate WEFT_* environment variables to BROKER_* equivalents.

    Returns:
        Dict with BROKER_* keys and values from corresponding WEFT_* env vars
    """
    translated = {}

    for weft_key, broker_key in SIMPLEBROKER_ENV_MAPPING.items():
        env_value = os.environ.get(weft_key)
        if env_value is not None:
            translated[broker_key] = env_value

    return translated


def _apply_weft_simplebroker_defaults(config: dict[str, Any]) -> None:
    """Apply weft-specific defaults for SimpleBroker integration.

    Args:
        config: Configuration dictionary to modify in-place
    """
    for key, default_value in WEFT_SIMPLEBROKER_DEFAULTS.items():
        config.setdefault(key, default_value)


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


def _load_weft_env_vars() -> dict[str, Any]:
    """Load weft-specific configuration from environment variables.

    Returns:
        Dict with WEFT_* configuration values
    """
    timeout_value = WEFT_MANAGER_LIFETIME_TIMEOUT
    raw_timeout = os.environ.get("WEFT_MANAGER_LIFETIME_TIMEOUT")
    if raw_timeout is not None:
        try:
            parsed = float(raw_timeout)
            if parsed >= 0:
                timeout_value = parsed
        except ValueError:
            timeout_value = WEFT_MANAGER_LIFETIME_TIMEOUT

    reuse_value = os.environ.get("WEFT_MANAGER_REUSE_ENABLED")
    if reuse_value is None:
        reuse_enabled = WEFT_MANAGER_REUSE_ENABLED
    else:
        reuse_enabled = _parse_bool(reuse_value)

    autostart_value = os.environ.get("WEFT_AUTOSTART_TASKS")
    if autostart_value is None:
        autostart_enabled = WEFT_AUTOSTART_TASKS_DEFAULT
    else:
        autostart_enabled = _parse_bool(autostart_value)

    return {
        # Debug - uses flexible boolean parsing
        "WEFT_DEBUG": _parse_bool(os.environ.get("WEFT_DEBUG")),
        # Logging - strict "1" check for backward compatibility
        "WEFT_LOGGING_ENABLED": os.environ.get("WEFT_LOGGING_ENABLED", "0") == "1",
        # Comma-separated redaction paths for TaskSpec logging
        "WEFT_REDACT_TASKSPEC_FIELDS": os.environ.get(
            "WEFT_REDACT_TASKSPEC_FIELDS", ""
        ),
        "WEFT_MANAGER_LIFETIME_TIMEOUT": timeout_value,
        "WEFT_MANAGER_REUSE_ENABLED": reuse_enabled,
        "WEFT_AUTOSTART_TASKS": autostart_enabled,
    }


def _add_simplebroker_env_vars(config: dict[str, Any]) -> None:
    """Add SimpleBroker configuration to weft config dictionary.

    Args:
        config: Configuration dictionary to modify in-place

    This function:
    1. Translates WEFT_* environment variables to BROKER_* equivalents
    2. Applies weft-specific defaults for SimpleBroker integration
    """
    # Translate WEFT_* -> BROKER_* environment variables
    translated_vars = _translate_weft_env_vars()
    config.update(translated_vars)

    # Apply weft defaults for SimpleBroker (weft is always project-scoped)
    _apply_weft_simplebroker_defaults(config)

    # Set SimpleBroker debug/logging settings to match weft
    config["BROKER_DEBUG"] = config["WEFT_DEBUG"]
    config["BROKER_LOGGING_ENABLED"] = config["WEFT_LOGGING_ENABLED"]


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


def load_config() -> dict[str, Any]:
    """Public entry point for retrieving the current configuration.

    Returns:
        A fresh configuration dictionary containing both WEFT_* and BROKER_* keys.

    Notes:
        The returned dictionary is not cached; callers should cache it themselves
        if repeated lookups are required.
    """
    return load_environment()


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
