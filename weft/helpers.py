"""Helper utility functions for Weft.

This module provides common utility functions used throughout the Weft codebase,
including logging and debugging helpers that respect environment configuration.
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from collections.abc import Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from simplebroker.db import DBConnection

from weft._constants import load_config

# Load configuration once at module level for efficiency
_config = load_config()

# Set up module logger
logger = logging.getLogger(__name__)


class CommandNotFoundError(FileNotFoundError):
    """Raised when a CLI command cannot be resolved to an executable path (Spec: [CLI-1.1.1])."""

    def __init__(self, command: str, *, search_path: str | None = None) -> None:
        message = f"Unable to locate command '{command}'"
        if search_path is not None:
            message += f" using search path '{search_path}'"
        else:
            message += " on PATH"
        super().__init__(message)
        self.command = command
        self.search_path = search_path


def resolve_cli_command(command: str, *, search_path: str | None = None) -> str:
    """Resolve a CLI command to its fully qualified executable path.

    Args:
        command: Name of the command to resolve. May include a path component.
        search_path: Optional PATH string to use instead of ``os.environ['PATH']``.

    Returns:
        Absolute path to the executable that will be invoked.

    Raises:
        ValueError: If *command* is an empty or whitespace-only string.
        CommandNotFoundError: If the command cannot be located.

    Notes:
        This is a thin wrapper around :func:`shutil.which` so all command
        resolution happens in one place. Centralising this logic makes it easy
        to layer additional policy (allow-lists, sandbox checks, etc.) without
        changing callers.

    Spec: [CLI-1.1.1]
    """
    candidate = command.strip()
    if not candidate:
        raise ValueError("command must be a non-empty string")

    resolved = shutil.which(candidate, path=search_path)
    if resolved is None:
        raise CommandNotFoundError(candidate, search_path=search_path)
    return resolved


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


def sqlite_wal_checkpoint_truncate(database_path: Path | str) -> None:
    """Run ``PRAGMA wal_checkpoint(TRUNCATE);`` against *database_path*.

    Uses the standard library sqlite3 module to execute the checkpoint in a
    best-effort fashion. Any exception will be logged at DEBUG level and
    swallowed so callers can treat this as a maintenance convenience.
    """

    db = Path(database_path)
    if not db.exists():
        logger.debug("Database %s does not exist; skipping WAL checkpoint", db)
        return

    try:
        with sqlite3.connect(db) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    except Exception:  # pragma: no cover - maintenance best effort
        logger.debug("Failed to run WAL checkpoint on %s", db, exc_info=True)


def simplebroker_vacuum(
    database_path: Path | str, *, config: dict[str, Any] | None = None
) -> None:
    """Compact the broker database using SimpleBroker's vacuum routine."""

    db_path = Path(database_path)
    if not db_path.exists():
        logger.debug("Database %s does not exist; skipping vacuum", db_path)
        return

    cfg = config or _config

    try:
        with DBConnection(str(db_path)) as connection:
            broker_db = connection.get_connection(config=cfg)
            broker_db.vacuum()
    except Exception:  # pragma: no cover - maintenance best effort
        logger.debug("simplebroker vacuum failed for %s", db_path, exc_info=True)


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


def write_file_atomically(
    file_path: Path | str,
    content: str | None = None,
    data: bytes | None = None,
    encoding: str = "utf-8",
) -> None:
    """Write content to a file atomically to prevent race conditions.

    This function uses the write-then-rename pattern to ensure that the file
    is either completely written or not written at all, preventing corruption
    from concurrent access or interruption.

    The approach is inspired by SimpleBroker's atomic operations but simplified
    for weft's needs. It's useful for any critical files that multiple processes
    might try to create simultaneously.

    Args:
        file_path: Path to the target file
        content: Text content to write (mutually exclusive with data)
        data: Binary data to write (mutually exclusive with content)
        encoding: Text encoding for content (default: 'utf-8')

    Raises:
        OSError: If file operations fail
        ValueError: If both content and data are provided or neither

    Example:
        >>> write_file_atomically("/path/to/file.txt", content="Hello, World!")
        >>> write_file_atomically("/path/to/file.bin", data=b"\\x00\\x01\\x02")
    """
    if (content is None) == (data is None):
        raise ValueError("Exactly one of 'content' or 'data' must be provided")

    target_path = Path(file_path)
    is_binary = data is not None

    try:
        # Create parent directory if it doesn't exist
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # Write to temporary file first (atomic on most filesystems)
        temp_fd, temp_path = tempfile.mkstemp(
            suffix=".tmp", prefix=f".{target_path.name}_", dir=target_path.parent
        )

        try:
            if data is not None:
                with os.fdopen(temp_fd, "wb") as f:
                    f.write(data)
            else:
                assert content is not None
                with os.fdopen(temp_fd, "w", encoding=encoding) as f:
                    f.write(content)

            # Atomic rename (on most filesystems)
            Path(temp_path).replace(target_path)

            log_debug(
                f"Atomically wrote {'binary' if is_binary else 'text'} to {target_path}"
            )

        except Exception:
            # Clean up temp file on error
            try:
                os.unlink(temp_path)
            except OSError:
                pass  # Best effort cleanup
            raise

    except Exception as e:
        # Fallback to simple write if atomic approach fails
        # This maintains compatibility but loses race condition protection
        log_warning(
            f"Atomic write failed for {target_path}, falling back to simple write: {e}"
        )

        if data is not None:
            with open(target_path, "wb") as f:
                f.write(data)
        else:
            assert content is not None
            with open(target_path, "w", encoding=encoding) as f:
                f.write(content)


def write_json_atomically(file_path: Path | str, data: dict[str, Any]) -> None:
    """Write JSON data to a file atomically to prevent race conditions.

    Convenience wrapper around write_file_atomically for JSON data.

    Args:
        file_path: Path to the target file
        data: Dictionary to write as JSON

    Raises:
        OSError: If file operations fail
        json.JSONEncodeError: If data cannot be serialized to JSON

    Example:
        >>> config = {"version": "1.0", "project_name": "my-project"}
        >>> write_json_atomically("/path/to/config.json", config)
    """
    json_content = json.dumps(data, indent=2)
    write_file_atomically(file_path, content=json_content)


def redact_taskspec_dump(
    taskspec_dump: dict[str, Any],
    field_paths: Sequence[str],
    *,
    placeholder: str = "[REDACTED]",
) -> dict[str, Any]:
    """Return a redacted copy of a TaskSpec dump.

    Args:
        taskspec_dump: JSON-friendly dictionary produced by ``model_dump``.
        field_paths: Iterable of dot-separated keys to redact
                     (e.g. ``"spec.env.SECRET"``).
        placeholder: Value to assign to redacted fields.

    Returns:
        A deep-copied dictionary with the requested fields replaced by *placeholder*.
        Missing paths are ignored.
    """
    if not field_paths:
        return taskspec_dump

    redacted = deepcopy(taskspec_dump)

    for raw_path in field_paths:
        path = raw_path.strip()
        if not path:
            continue

        keys = path.split(".")
        target = redacted
        missing_parent = False
        for key in keys[:-1]:
            if isinstance(target, dict) and key in target:
                target = target[key]
            else:
                missing_parent = True
                break

        if missing_parent or not isinstance(target, dict):
            continue

        final_key = keys[-1]
        if final_key in target:
            target[final_key] = placeholder

    return redacted


def reload_config() -> None:
    """Reload configuration from environment variables.

    This function is primarily useful for testing or when environment
    variables might have changed during runtime.
    """
    global _config
    _config = load_config()


def format_byte_size(size: int, *, precision: int = 1) -> str:
    """Return a human-friendly byte size string (e.g. ``1.4 MB``).

    Args:
        size: Raw size in bytes (must be non-negative).
        precision: Decimal places when using units above bytes.
    """

    if size < 0:
        raise ValueError("size must be non-negative")

    units = ["B", "KB", "MB", "GB", "TB", "PB", "EB"]
    value = float(size)
    unit_index = 0

    while value >= 1024.0 and unit_index < len(units) - 1:
        value /= 1024.0
        unit_index += 1

    if unit_index == 0:
        return f"{int(value)} {units[unit_index]}"

    formatted = f"{value:.{precision}f}".rstrip("0").rstrip(".")
    return f"{formatted} {units[unit_index]}"


def _pluralise(value: int, unit: str) -> str:
    suffix = "s" if value != 1 else ""
    return f"{value} {unit}{suffix}"


def _format_duration(seconds: float, *, max_units: int = 2) -> str:
    seconds = max(seconds, 0.0)

    if seconds < 0.001:
        return "less than 1 ms"

    if seconds < 1.0:
        millis = round(seconds * 1000)
        if millis == 0:
            millis = 1
        return f"{millis} millisecond{'s' if millis != 1 else ''}"

    if seconds < 60:
        if seconds < 10:
            value = round(seconds, 1)
            if math.isclose(value, round(value)):
                value = round(value)
        else:
            value = round(seconds)
        unit = "second" if value == 1 else "seconds"
        return f"{value} {unit}"

    remaining = int(seconds)
    parts: list[str] = []
    components = [
        ("day", 86_400),
        ("hour", 3_600),
        ("minute", 60),
        ("second", 1),
    ]

    for name, unit_seconds in components:
        if len(parts) >= max_units:
            break
        value, remaining = divmod(remaining, unit_seconds)
        if value:
            parts.append(_pluralise(value, name))

    if len(parts) < max_units and remaining and components[-1][0] != "second":
        parts.append(_pluralise(remaining, "second"))

    if not parts:
        parts.append(_pluralise(int(round(seconds)), "second"))

    return ", ".join(parts)


def format_timestamp_ns_relative(
    timestamp_ns: int,
    *,
    reference_time: float | None = None,
    max_units: int = 2,
) -> str:
    """Return a human-friendly relative time string for a hybrid timestamp."""

    if timestamp_ns <= 0:
        return "never"

    if reference_time is None:
        reference_time = time.time()

    timestamp_seconds = timestamp_ns / 1_000_000_000
    delta = reference_time - timestamp_seconds

    if abs(delta) < 0.5:
        return "just now"

    if delta < 0:
        return f"in {_format_duration(-delta, max_units=max_units)}"

    return f"{_format_duration(delta, max_units=max_units)} ago"
