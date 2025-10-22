"""Tests for CLI argument rearrangement."""

from __future__ import annotations

import pytest

from weft.cli_utils import ArgumentParserError, rearrange_args


def test_rearrange_simple():
    argv = ["queue", "write", "name", "message"]
    assert rearrange_args(argv) == argv


def test_rearrange_globals_after_command():
    argv = ["queue", "write", "name", "message", "-q"]
    assert rearrange_args(argv) == ["-q", "queue", "write", "name", "message"]


def test_rearrange_globals_with_values():
    argv = ["queue", "read", "name", "--dir", "/tmp"]
    assert rearrange_args(argv) == ["--dir", "/tmp", "queue", "read", "name"]


def test_missing_value_raises():
    with pytest.raises(ArgumentParserError):
        rearrange_args(["--dir"])
