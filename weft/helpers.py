"""Helper utility functions for Weft.

This module provides shared helpers used across command, manager, and runtime
code. It includes queue-history readers, command-resolution helpers, and
runtime ownership reducers that multiple spec-owned surfaces reuse.

Spec references:
- docs/specifications/03-Manager_Architecture.md [MA-3]
- docs/specifications/05-Message_Flow_and_State.md [MF-3.1], [MF-6]
- docs/specifications/10-CLI_Interface.md [CLI-1.1.1]
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import psutil

from simplebroker import Queue
from simplebroker import commands as sb_commands
from simplebroker.ext import BrokerError
from weft._constants import WEFT_SPAWN_REQUESTS_QUEUE, load_config

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


def stdin_is_tty(stream: Any | None = None) -> bool:
    """Return whether *stream* is an interactive terminal."""
    candidate = sys.stdin if stream is None else stream
    if candidate is None or getattr(candidate, "closed", False):
        return False
    try:
        return bool(candidate.isatty())
    except Exception:  # pragma: no cover - defensive for mocked stdin
        return False


def safe_cancel(callback: Callable[[], bool] | None) -> bool:
    """Return True when an optional external cancel callback requests stop."""
    if callback is None:
        return False
    try:
        return bool(callback())
    except Exception:  # pragma: no cover - external cancel hook is best effort
        return False


def resolve_broker_max_message_size(config: Mapping[str, Any]) -> int:
    """Return the effective broker message-size limit for the active context."""
    raw_value = config.get("BROKER_MAX_MESSAGE_SIZE")
    if raw_value in (None, ""):
        return int(getattr(sb_commands, "MAX_MESSAGE_SIZE", 10 * 1024 * 1024))

    try:
        max_bytes = int(str(raw_value))
    except (TypeError, ValueError) as exc:
        raise ValueError("BROKER_MAX_MESSAGE_SIZE must be a positive integer") from exc
    if max_bytes <= 0:
        raise ValueError("BROKER_MAX_MESSAGE_SIZE must be a positive integer")
    return max_bytes


def read_limited_stdin(max_bytes: int, *, stream: Any | None = None) -> str:
    """Read stdin in chunks while enforcing a byte limit."""
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")

    candidate = sys.stdin if stream is None else stream
    if candidate is None or getattr(candidate, "closed", False):
        return ""

    chunks: list[bytes] = []
    total_bytes = 0
    chunk_size = 4096
    reader = getattr(candidate, "buffer", None)

    try:
        if reader is not None:
            while True:
                chunk = reader.read(chunk_size)
                if not chunk:
                    break
                if isinstance(chunk, str):
                    chunk_bytes = chunk.encode("utf-8")
                else:
                    chunk_bytes = bytes(chunk)
                total_bytes += len(chunk_bytes)
                if total_bytes > max_bytes:
                    raise ValueError(f"Input exceeds maximum size of {max_bytes} bytes")
                chunks.append(chunk_bytes)
        else:
            while True:
                chunk_text = candidate.read(chunk_size)
                if not chunk_text:
                    break
                chunk_bytes = str(chunk_text).encode("utf-8")
                total_bytes += len(chunk_bytes)
                if total_bytes > max_bytes:
                    raise ValueError(f"Input exceeds maximum size of {max_bytes} bytes")
                chunks.append(chunk_bytes)
    except OSError:
        return ""

    return b"".join(chunks).decode("utf-8")


def resolve_cli_message_content(
    message: str | None,
    *,
    max_bytes: int,
    stream: Any | None = None,
) -> str:
    """Resolve queue message content from argv or piped stdin."""
    if message == "-":
        return read_limited_stdin(max_bytes, stream=stream)
    if message is None:
        if stdin_is_tty(stream):
            raise ValueError(
                "message is required when stdin is a terminal; pass a message or pipe input"
            )
        return read_limited_stdin(max_bytes, stream=stream)

    message_bytes = len(message.encode("utf-8"))
    if message_bytes > max_bytes:
        raise ValueError(f"Message exceeds maximum size of {max_bytes} bytes")
    return message


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


def iter_queue_entries(
    queue: Queue,
    *,
    since_timestamp: int | None = None,
) -> Iterator[tuple[str, int]]:
    """Yield queue entries with timestamps using the broker generator API.

    This avoids fixed-size ``peek_many(limit=...)`` reads, which silently miss
    newer entries once append-only queues grow beyond the chosen limit.
    """

    try:
        raw_entries = queue.peek_generator(
            with_timestamps=True,
            since_timestamp=since_timestamp,
        )
    except (
        BrokerError,
        OSError,
        RuntimeError,
    ):  # pragma: no cover - queue history best effort
        logger.debug("Failed to open queue generator for %s", queue, exc_info=True)
        return iter(())

    def _generator() -> Iterator[tuple[str, int]]:
        for entry in cast(Iterable[Any], raw_entries):
            if not isinstance(entry, tuple) or len(entry) != 2:
                continue
            body, timestamp = entry
            try:
                timestamp_value = int(timestamp)
            except (TypeError, ValueError):
                continue
            yield str(body), timestamp_value

    return _generator()


def iter_queue_json_entries(
    queue: Queue,
    *,
    since_timestamp: int | None = None,
) -> Iterator[tuple[dict[str, Any], int]]:
    """Yield decoded JSON objects from a queue, skipping invalid entries."""

    for body, timestamp in iter_queue_entries(
        queue,
        since_timestamp=since_timestamp,
    ):
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            yield payload, timestamp


def pid_is_live(pid: int | None) -> bool:
    """Return whether *pid* refers to a currently live, non-zombie process.

    Spec: docs/specifications/03-Manager_Architecture.md [MA-3]
    """

    if pid is None or pid <= 0:
        return False

    try:
        process = psutil.Process(pid)
    except psutil.Error:
        return False

    try:
        if not process.is_running():
            return False
    except psutil.Error:
        return False

    try:
        return bool(process.status() != psutil.STATUS_ZOMBIE)
    except psutil.ZombieProcess:
        return False
    except psutil.AccessDenied:
        # Fall back to the existence check above when status details are hidden.
        return True
    except psutil.Error:
        return False


def is_canonical_manager_record(record: Mapping[str, Any]) -> bool:
    """Return whether a registry record represents the canonical spawn-queue manager.

    Spec: docs/specifications/03-Manager_Architecture.md [MA-3]
    """

    role = record.get("role", "manager")
    requests = record.get("requests", WEFT_SPAWN_REQUESTS_QUEUE)
    return bool(role == "manager" and requests == WEFT_SPAWN_REQUESTS_QUEUE)


def canonical_owner_tid(claimant_tids: Iterable[str]) -> str | None:
    """Return the canonical owner TID from already-eligible claimants.

    This helper is intentionally narrow: callers own registry replay, liveness,
    and schema interpretation. The helper only reduces a set of eligible live
    claimant TIDs to the lowest numeric TID, which is Weft's canonical-owner
    rule for convergent runtime ownership.

    Spec:
    - docs/specifications/03-Manager_Architecture.md [MA-3]
    - docs/specifications/05-Message_Flow_and_State.md [MF-3.1]
    """

    lowest_value: int | None = None
    lowest_tid: str | None = None
    for tid in claimant_tids:
        try:
            numeric_tid = int(tid)
        except (TypeError, ValueError):
            continue
        if lowest_value is None or numeric_tid < lowest_value:
            lowest_value = numeric_tid
            lowest_tid = tid
    return lowest_tid


def terminate_process_tree(
    root_pid: int,
    *,
    timeout: float = 0.5,
    kill_after: bool = True,
) -> set[int]:
    """Terminate ``root_pid`` and every descendant process.

    Algorithm overview
    ------------------
    1. **Snapshot the tree.** ``_list_process_descendants`` walks the process
       tree recursively via ``psutil`` and deduplicates PIDs.  The snapshot is
       taken once; processes that spawn children after this point are not
       included, but that is an acceptable race — they will typically inherit
       the parent's fate anyway.

    2. **Signal leaves before root.** The target list is ordered
       ``[descendants..., root]``.  Sending SIGTERM to descendants first means
       the root process cannot exit and orphan its children before they have
       had a chance to receive their own signals.

    3. **Graceful wait.** ``psutil.wait_procs`` waits up to ``timeout``
       seconds for each signalled process to exit.  Processes that exit
       cleanly during this window are collected in ``_gone``; the remainder
       stay in ``alive``.

    4. **Escalate to SIGKILL** (when ``kill_after=True``).  Any process still
       alive after the grace period is force-killed with SIGKILL, followed by
       a second ``wait_procs`` to reap zombies.

    5. **Return the terminated set.**  The function returns the PIDs of every
       process that is no longer running after the sequence, regardless of
       whether it exited gracefully or was killed.

    Returns
    -------
    set[int]
        PIDs confirmed terminated.  An empty set means nothing was signalled
        (e.g. the root PID was already gone or invalid).
    """

    if root_pid <= 0:
        return set()

    try:
        root = psutil.Process(root_pid)
    except psutil.Error:
        return set()

    descendants = _list_process_descendants(root)
    targeted = descendants + [root]
    signaled: list[psutil.Process] = []
    for proc in targeted:
        try:
            proc.terminate()
        except psutil.Error:
            continue
        else:
            signaled.append(proc)

    if not signaled:
        return set()

    _gone, alive = psutil.wait_procs(signaled, timeout=timeout)
    if alive and kill_after:
        for proc in alive:
            try:
                proc.kill()
            except psutil.Error:
                continue
        psutil.wait_procs(alive, timeout=timeout)

    terminated: set[int] = set()
    for proc in signaled:
        try:
            if not proc.is_running():
                terminated.add(proc.pid)
        except psutil.Error:
            terminated.add(proc.pid)
    return terminated


def kill_process_tree(root_pid: int, *, timeout: float = 0.5) -> set[int]:
    """Force-kill ``root_pid`` and any descendant processes."""

    if root_pid <= 0:
        return set()

    try:
        root = psutil.Process(root_pid)
    except psutil.Error:
        return set()

    descendants = _list_process_descendants(root)
    targeted = descendants + [root]
    killed: list[psutil.Process] = []
    for proc in targeted:
        try:
            proc.kill()
        except psutil.Error:
            continue
        else:
            killed.append(proc)

    if killed:
        psutil.wait_procs(killed, timeout=timeout)

    terminated: set[int] = set()
    for proc in killed:
        try:
            if not proc.is_running():
                terminated.add(proc.pid)
        except psutil.Error:
            terminated.add(proc.pid)
    return terminated


def _list_process_descendants(root: psutil.Process) -> list[psutil.Process]:
    """Return descendant processes for ``root`` with duplicates removed."""

    try:
        descendants = root.children(recursive=True)
    except psutil.Error:
        return []

    unique: dict[int, psutil.Process] = {}
    for proc in descendants:
        unique[proc.pid] = proc
    return list(unique.values())


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
    retry_attempts = 10
    retry_sleep_seconds = 0.01

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

            temp_file = Path(temp_path)
            for attempt in range(retry_attempts):
                try:
                    # Atomic rename (on most filesystems)
                    temp_file.replace(target_path)
                    break
                except PermissionError:
                    if attempt == retry_attempts - 1:
                        raise
                    time.sleep(retry_sleep_seconds)

            log_debug(
                f"Atomically wrote {'binary' if is_binary else 'text'} to {target_path}"
            )

        except Exception:  # pragma: no cover - atomic cleanup before re-raise
            # Clean up temp file on error
            try:
                os.unlink(temp_path)
            except OSError:
                pass  # Best effort cleanup
            raise

    except Exception as e:  # pragma: no cover - atomic write fallback
        # Fallback to simple write if atomic approach fails
        # This maintains compatibility but loses race condition protection
        log_warning(
            f"Atomic write failed for {target_path}, falling back to simple write: {e}"
        )

        for attempt in range(retry_attempts):
            try:
                if data is not None:
                    with open(target_path, "wb") as f:
                        f.write(data)
                else:
                    assert content is not None
                    with open(target_path, "w", encoding=encoding) as f:
                        f.write(content)
                break
            except PermissionError:
                if attempt == retry_attempts - 1:
                    raise
                time.sleep(retry_sleep_seconds)


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


def _pluralize(value: int, unit: str) -> str:
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
            parts.append(_pluralize(value, name))

    if not parts:
        parts.append(_pluralize(int(round(seconds)), "second"))

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
