"""Sample target functions used by Task tests."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any


def echo_payload(payload: str, *, suffix: str = "") -> str:
    """Return the payload with an optional suffix."""
    return f"{payload}{suffix}"


def surround_payload(
    payload: str,
    *,
    prefix: str = "[",
    suffix: str = "]",
) -> str:
    """Wrap the payload in a predictable prefix/suffix pair."""
    return f"{prefix}{payload}{suffix}"


def fail_payload(*args, **kwargs) -> None:
    """Raise an exception to simulate task failure."""
    raise RuntimeError("intentional failure for testing")


def simulate_work(
    *,
    duration: float = 0.0,
    memory_mb: int = 0,
    connections: int = 0,
    result: str = "done",
    output_size: int = 0,
    cpu_percent: float = 0.0,
) -> str:
    """Delegate to the shared process target helper as a callable."""
    from tests.tasks.process_target import run_task

    return run_task(
        duration=duration,
        memory_mb=memory_mb,
        connections=connections,
        result=result,
        output_size=output_size,
        cpu_percent=cpu_percent,
    )


def large_output(size: int = 4_194_304, *, char: str = "x") -> str:
    """Return a repeated character string of the requested size."""
    return char * size


def provide_payload(value: str = "payload") -> dict[str, str]:
    """Return a dictionary payload for interactive consumer tests."""
    return {"data": value}


def json_payload(payload: Mapping[str, Any]) -> str:
    """Return a canonical JSON representation of a mapping payload."""

    return json.dumps(dict(payload), sort_keys=True)


def json_kwargs(**kwargs: Any) -> str:
    """Return a canonical JSON representation of keyword arguments."""

    return json.dumps(kwargs, sort_keys=True)


def provide_stdio(
    stdout: str = "stdout",
    stderr: str = "stderr",
) -> dict[str, str]:
    """Return a structured stdout/stderr payload for result-command tests."""
    return {"stdout": stdout, "stderr": stderr}


def streaming_echo(
    value: str = "streaming",
    *,
    delay: float = 2.0,
) -> str:
    """Sleep long enough for queue-surface tests to observe a running task."""
    time.sleep(delay)
    return value
