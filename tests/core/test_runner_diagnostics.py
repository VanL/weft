"""Tests for shared runner diagnostic payload helpers."""

from __future__ import annotations

import json

import pytest

from weft._constants import (
    RUNNER_DIAGNOSTICS_MESSAGE_MAX_CHARS,
    RUNNER_DIAGNOSTICS_TRACEBACK_MAX_CHARS,
)
from weft.core.runner_diagnostics import (
    diagnostic_summary,
    merge_runner_diagnostics,
    runner_diagnostics,
)

pytestmark = pytest.mark.shared


def test_runner_diagnostics_are_json_safe_and_bounded() -> None:
    message = "m" * (RUNNER_DIAGNOSTICS_MESSAGE_MAX_CHARS + 20)
    traceback_text = "t" * (RUNNER_DIAGNOSTICS_TRACEBACK_MAX_CHARS + 20)

    payload = runner_diagnostics(
        phase="runtime_startup",
        runner="host",
        target_type="agent",
        pid=123,
        exitcode=73,
        alive=False,
        duration_seconds=1.25,
        timeout_seconds=10.0,
        message=message,
        exception_type="RuntimeError",
        traceback_text=traceback_text,
        last_handshake="booted",
        extra={"non_json": object(), "nested": {"value": "ok"}},
    )

    json.dumps(payload)
    assert payload["message"].endswith("...")
    assert len(payload["message"]) == RUNNER_DIAGNOSTICS_MESSAGE_MAX_CHARS
    assert payload["traceback_tail"].startswith("...")
    assert len(payload["traceback_tail"]) == RUNNER_DIAGNOSTICS_TRACEBACK_MAX_CHARS
    assert payload["non_json"].startswith("<object object")
    assert payload["nested"] == {"value": "ok"}


def test_merge_runner_diagnostics_drops_none_fragments() -> None:
    payload = merge_runner_diagnostics(None, {"phase": "execute", "pid": 123})

    assert payload == {"phase": "execute", "pid": 123}
    assert diagnostic_summary(payload) == "phase=execute, pid=123"
