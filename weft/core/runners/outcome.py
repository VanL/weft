"""Shared result contract for task runner implementations.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3], [CC-3.2], [CC-3.4], [CC-3.5]
- docs/specifications/06-Resource_Management.md [RM-5], [RM-5.1]
"""

from __future__ import annotations

import math
from typing import Any

from weft.core.targets import (
    prepare_task_result,
    prepared_result_from_payload,
    prepared_result_to_payload,
)
from weft.ext import RunnerOutcome


def make_outcome_payload(outcome: RunnerOutcome) -> dict[str, Any]:
    """Prepare one host response while its execution context is active [CC-3.5]."""
    return {
        "type": "outcome",
        "status": outcome.status,
        "result": (
            prepared_result_to_payload(prepare_task_result(outcome.value))
            if outcome.status == "ok"
            else None
        ),
        "error": outcome.error,
        "stdout": outcome.stdout,
        "stderr": outcome.stderr,
        "returncode": outcome.returncode,
        "duration": outcome.duration,
        "diagnostics": outcome.diagnostics,
    }


def parse_outcome_payload(payload: dict[str, Any]) -> RunnerOutcome:
    """Validate a host response without reconstructing application types [CC-3.5]."""
    if (
        set(payload)
        != {
            "type",
            "status",
            "result",
            "error",
            "stdout",
            "stderr",
            "returncode",
            "duration",
            "diagnostics",
        }
        or payload["type"] != "outcome"
    ):
        raise ValueError("invalid host outcome fields or type")
    status = payload["status"]
    error = payload["error"]
    if status == "ok":
        if error is not None:
            raise ValueError("successful host outcome must have null error")
        result = prepared_result_from_payload(payload["result"])
    elif status == "error":
        if payload["result"] is not None or not isinstance(error, str) or not error:
            raise ValueError(
                "failed host outcome requires null result and nonempty error"
            )
        result = None
    else:
        raise ValueError("invalid host outcome status")
    _validate_outcome_details(payload)
    return RunnerOutcome(
        status=status,
        value=result,
        error=error,
        stdout=payload["stdout"],
        stderr=payload["stderr"],
        returncode=payload["returncode"],
        duration=payload["duration"],
        diagnostics=payload["diagnostics"],
    )


def _validate_outcome_details(payload: dict[str, Any]) -> None:
    """Check optional observations and the execution-duration field."""
    for name in ("stdout", "stderr"):
        if payload[name] is not None and not isinstance(payload[name], str):
            raise TypeError(f"host outcome {name} must be text or null")
    returncode = payload["returncode"]
    if returncode is not None and type(returncode) is not int:
        raise TypeError("host outcome returncode must be an integer or null")
    duration = payload["duration"]
    if (
        type(duration) not in (int, float)
        or duration < 0
        or (isinstance(duration, float) and not math.isfinite(duration))
    ):
        raise ValueError("host outcome duration must be a finite nonnegative number")
    diagnostics = payload["diagnostics"]
    if diagnostics is not None and not isinstance(diagnostics, dict):
        raise TypeError("host outcome diagnostics must be an object or null")


__all__ = ["RunnerOutcome", "make_outcome_payload", "parse_outcome_payload"]
