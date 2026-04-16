"""Unit tests for Windows provider CLI batch-shim rewriting."""

from __future__ import annotations

import sys
from pathlib import Path

from weft.core.agents.provider_cli.windows_shims import (
    resolve_windows_cmd_shim_command,
)


def test_resolve_windows_cmd_shim_command_rewrites_direct_python_wrapper(
    tmp_path: Path,
) -> None:
    launcher = tmp_path / "launcher.py"
    launcher.write_text("print('ok')\n", encoding="utf-8")
    shim = tmp_path / "codex.cmd"
    shim.write_text(
        f'@echo off\r\n"{sys.executable}" "{launcher}" %*\r\n',
        encoding="utf-8",
    )

    command = resolve_windows_cmd_shim_command((str(shim), "--flag", "line1\nline2"))

    assert Path(command[0]).resolve() == Path(sys.executable).resolve()
    assert Path(command[1]).resolve() == launcher.resolve()
    assert command[2:] == ("--flag", "line1\nline2")


def test_resolve_windows_cmd_shim_command_expands_percent_dp0_paths(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    launcher = tmp_path / "launcher.py"
    launcher.write_text("print('ok')\n", encoding="utf-8")
    shim = bin_dir / "qwen.cmd"
    shim.write_text(
        f'@echo off\r\n"{sys.executable}" "%~dp0/../launcher.py" %*\r\n',
        encoding="utf-8",
    )

    command = resolve_windows_cmd_shim_command((str(shim), "hello"))

    assert Path(command[0]).resolve() == Path(sys.executable).resolve()
    assert Path(command[1]).resolve() == launcher.resolve()
    assert command[2:] == ("hello",)
