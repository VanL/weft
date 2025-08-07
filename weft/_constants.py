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

# ==============================================================================
# TASKSPEC DEFAULTS
# ==============================================================================

# Version and Identification
# --------------------------
TASKSPEC_VERSION: Final[str] = "1.0"
"""Current TaskSpec schema version for future evolution."""

TASKSPEC_TID_LENGTH: Final[int] = 19
"""Required length for Task ID (19 digits from time.time_ns())."""

# Spec Section Defaults
# ---------------------
DEFAULT_FUNCTION_TARGET: Final[str] = "weft.tasks:noop"
"""Default function target for tasks (no-op function)."""

DEFAULT_TIMEOUT: Final[float | None] = None
"""Default timeout in seconds. None means no timeout."""

DEFAULT_MEMORY_LIMIT: Final[int] = 1024
"""Default memory limit in MB (1GB)."""

DEFAULT_CPU_LIMIT: Final[int | None] = None
"""Default CPU limit in percent. None means no limit."""

DEFAULT_MAX_FDS: Final[int | None] = None
"""Default maximum number of open file descriptors. None means no limit."""

DEFAULT_MAX_CONNECTIONS: Final[int | None] = None
"""Default maximum number of network connections. None means no limit."""

DEFAULT_STREAM_OUTPUT: Final[bool] = False
"""Default for streaming output. False means all output in one message."""

DEFAULT_CLEANUP_ON_EXIT: Final[bool] = True
"""Default for cleaning up task queues on completion."""

DEFAULT_POLLING_INTERVAL: Final[float] = 1.0
"""Default psutil polling interval in seconds."""

DEFAULT_REPORTING_INTERVAL: Final[Literal["transition"]] = "transition"
"""Default reporting interval. Either 'poll' or 'transition'."""

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


def _parse_bool(value: str | None) -> bool:
    """Parse a boolean value from environment variable string.

    Args:
        value: String value from environment variable

    Returns:
        False if value is None, empty, "0", "f", "F", "false", "False", "FALSE"
        True otherwise (for any non-empty string not in the false list)
    """
    if not value:
        return False

    # Values that should be considered False
    false_values = {"0", "f", "F", "false", "False", "FALSE"}
    return value not in false_values


def load_config() -> dict[str, Any]:
    """Load configuration from environment variables.

    This function reads all Weft environment variables and returns
    a configuration dictionary with validated values. It's designed to be
    called once at module initialization to avoid repeated environment lookups.

    Returns:
        dict: Configuration dictionary with the following keys:

        Debug:
            WEFT_DEBUG (bool): Enable debug output.
                Default: False
                Shows additional diagnostic information.
                False for: empty, "0", "f", "F", "false", "False", "FALSE"
                True for: any other non-empty value (e.g., "1", "true", "yes")

        Logging:
            WEFT_LOGGING_ENABLED (bool): Enable logging output.
                Default: False (disabled)
                Set to "1" to enable logging throughout Weft.
                When enabled, logs will be written using Python's logging module.
                Configure logging levels and handlers in your application as needed.

    """
    config = {
        # Debug - uses flexible boolean parsing
        "WEFT_DEBUG": _parse_bool(os.environ.get("WEFT_DEBUG")),
        # Logging - strict "1" check for backward compatibility
        "WEFT_LOGGING_ENABLED": os.environ.get("WEFT_LOGGING_ENABLED", "0") == "1",
    }

    return config
