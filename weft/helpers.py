"""Helper utility functions for Weft.

This module provides common utility functions used throughout the Weft codebase,
including logging and debugging helpers that respect environment configuration.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from weft._constants import load_config

# Load configuration once at module level for efficiency
_config = load_config()

# Set up module logger
logger = logging.getLogger(__name__)


def send_log(
    message: str,
    level: int = logging.INFO,
    logger_name: str | None = None,
    **kwargs: Any,
) -> None:
    """Send a log message if logging is enabled.

    This function checks the WEFT_LOGGING_ENABLED configuration and only
    logs if it's enabled. This allows for conditional logging throughout
    the codebase without repeated environment checks.

    Args:
        message: The message to log
        level: The logging level (default: logging.INFO)
        logger_name: Optional logger name. If None, uses the module logger
        **kwargs: Additional keyword arguments to pass to the logging function
                 (e.g., exc_info, stack_info, stacklevel)

    Example:
        >>> send_log("Task started", level=logging.DEBUG)
        >>> send_log("Error occurred", level=logging.ERROR, exc_info=True)
        >>> send_log("Custom logger message", logger_name="weft.tasks")
    """
    if not _config["WEFT_LOGGING_ENABLED"]:
        return

    # Get the appropriate logger
    if logger_name:
        log = logging.getLogger(logger_name)
    else:
        log = logger

    # Log at the specified level
    log.log(level, message, **kwargs)


def debug_print(
    *args: Any,
    sep: str = " ",
    end: str = "\n",
    file: Any = None,
    flush: bool = False,
) -> None:
    """Print debug output if debugging is enabled.

    This function checks the WEFT_DEBUG configuration and only prints
    if debugging is enabled. It has the same signature as the built-in
    print() function for easy drop-in replacement.

    Args:
        *args: Values to print
        sep: String inserted between values (default: " ")
        end: String appended after the last value (default: "\\n")
        file: File object to write to (default: sys.stderr for debug output)
        flush: Whether to forcibly flush the stream (default: False)

    Example:
        >>> debug_print("Debug:", "Task ID =", task_id)
        >>> debug_print("Values:", x, y, z, sep=", ")
    """
    if not _config["WEFT_DEBUG"]:
        return

    # Default to stderr for debug output
    if file is None:
        file = sys.stderr

    print(*args, sep=sep, end=end, file=file, flush=flush)


def log_debug(message: str, **kwargs: Any) -> None:
    """Convenience function for debug-level logging.

    Equivalent to send_log(message, level=logging.DEBUG, **kwargs)

    Args:
        message: The debug message to log
        **kwargs: Additional keyword arguments for logging
    """
    send_log(message, level=logging.DEBUG, **kwargs)


def log_info(message: str, **kwargs: Any) -> None:
    """Convenience function for info-level logging.

    Equivalent to send_log(message, level=logging.INFO, **kwargs)

    Args:
        message: The info message to log
        **kwargs: Additional keyword arguments for logging
    """
    send_log(message, level=logging.INFO, **kwargs)


def log_warning(message: str, **kwargs: Any) -> None:
    """Convenience function for warning-level logging.

    Equivalent to send_log(message, level=logging.WARNING, **kwargs)

    Args:
        message: The warning message to log
        **kwargs: Additional keyword arguments for logging
    """
    send_log(message, level=logging.WARNING, **kwargs)


def log_error(message: str, **kwargs: Any) -> None:
    """Convenience function for error-level logging.

    Equivalent to send_log(message, level=logging.ERROR, **kwargs)

    Args:
        message: The error message to log
        **kwargs: Additional keyword arguments for logging
    """
    send_log(message, level=logging.ERROR, **kwargs)


def log_critical(message: str, **kwargs: Any) -> None:
    """Convenience function for critical-level logging.

    Equivalent to send_log(message, level=logging.CRITICAL, **kwargs)

    Args:
        message: The critical message to log
        **kwargs: Additional keyword arguments for logging
    """
    send_log(message, level=logging.CRITICAL, **kwargs)


def log_exception(message: str, **kwargs: Any) -> None:
    """Log an error message with exception information.

    This is a convenience function that automatically includes
    exception information in the log.

    Args:
        message: The error message to log
        **kwargs: Additional keyword arguments for logging
    """
    send_log(message, level=logging.ERROR, exc_info=True, **kwargs)


def format_tid(tid: str | int) -> str:
    """Format a Task ID for display.

    Args:
        tid: The task ID (as string or int)

    Returns:
        Formatted TID string (e.g., "T1234567890123456789")

    Example:
        >>> format_tid("1234567890123456789")
        'T1234567890123456789'
        >>> format_tid(1234567890123456789)
        'T1234567890123456789'
    """
    return f"T{tid}"


def parse_tid(formatted_tid: str) -> str:
    """Parse a formatted TID to extract the numeric ID.

    Args:
        formatted_tid: The formatted TID (e.g., "T1234567890123456789")

    Returns:
        The numeric TID string

    Raises:
        ValueError: If the formatted TID is invalid

    Example:
        >>> parse_tid("T1234567890123456789")
        '1234567890123456789'
    """
    if not formatted_tid.startswith("T"):
        raise ValueError(f"Invalid formatted TID: {formatted_tid}")
    return formatted_tid[1:]


def is_logging_enabled() -> bool:
    """Check if logging is currently enabled.

    Returns:
        True if logging is enabled, False otherwise
    """
    return bool(_config["WEFT_LOGGING_ENABLED"])


def is_debug_enabled() -> bool:
    """Check if debug mode is currently enabled.

    Returns:
        True if debug mode is enabled, False otherwise
    """
    return bool(_config["WEFT_DEBUG"])


def reload_config() -> None:
    """Reload configuration from environment variables.

    This function is primarily useful for testing or when environment
    variables might have changed during runtime.
    """
    global _config
    _config = load_config()
