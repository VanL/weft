from __future__ import annotations

import sys

import pytest

from weft.shell import prepare_command


def test_prepare_command_injects_python_flags() -> None:
    result = prepare_command(["/usr/bin/python"], skip_detection=False)
    assert result[0].endswith("python")
    assert "-u" in result[1:]
    assert "-i" in result[1:]


def test_prepare_command_respects_existing_flags() -> None:
    original = ["python3", "-u", "-i", "-X", "utf8"]
    result = prepare_command(original, skip_detection=False)
    assert result.count("-u") == 1
    assert result.count("-i") == 1
    assert result[-1] == "utf8"


def test_prepare_command_skip_detection() -> None:
    original = ["python"]
    result = prepare_command(original, skip_detection=True)
    assert result == original


def test_prepare_command_python_script_omits_interactive() -> None:
    result = prepare_command(["python", "script.py"], skip_detection=False)
    assert "-u" in result[1:]
    assert "-i" in result[1:]


def test_prepare_command_node() -> None:
    result = prepare_command(["node"], skip_detection=False)
    assert result[0] == "node"
    assert "--interactive" in result[1:]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific path handling")
def test_prepare_command_windows_python() -> None:
    result = prepare_command(["C:/Python39/python.exe"], skip_detection=False)
    assert "-u" in result[1:]
    assert "-i" in result[1:]
