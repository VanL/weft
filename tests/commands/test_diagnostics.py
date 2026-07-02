"""Tests for shared runner-diagnostic rendering helpers."""

from __future__ import annotations

import pytest

from weft.commands.diagnostics import format_runner_diagnostics

pytestmark = [pytest.mark.shared]


def test_format_runner_diagnostics_returns_none_for_empty_input() -> None:
    assert format_runner_diagnostics(None) is None
    assert format_runner_diagnostics({}) is None


def test_format_runner_diagnostics_renders_known_fields_in_order() -> None:
    summary = format_runner_diagnostics(
        {
            "phase": "spawn",
            "pid": 123,
            "exitcode": 1,
            "alive": False,
            "message": "runner exited before handshake",
            "unknown_key": "ignored",
        }
    )
    assert summary == (
        "phase=spawn, pid=123, exitcode=1, alive=False, "
        "message=runner exited before handshake"
    )


def test_format_runner_diagnostics_skips_absent_fields() -> None:
    assert format_runner_diagnostics({"pid": 7}) == "pid=7"
