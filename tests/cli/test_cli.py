"""Tests for the Weft CLI."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from weft._constants import PROG_NAME, __version__
from weft.cli.app import app

runner = CliRunner()
pytestmark = [pytest.mark.shared]
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_project_metadata_installs_canonical_weft_entrypoint() -> None:
    """The installed ``weft`` command enters through the bootstrap owner."""

    project = tomllib.loads(
        (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert project["project"]["scripts"]["weft"] == "weft.bootstrap:main"


class TestCLI:
    """Test CLI functionality."""

    def test_version_flag(self):
        """Test --version flag shows correct version."""
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert f"{PROG_NAME} {__version__}" in result.stdout

    def test_version_short_flag(self):
        """Test -v flag shows correct version."""
        result = runner.invoke(app, ["-v"])
        assert result.exit_code == 0
        assert f"{PROG_NAME} {__version__}" in result.stdout

    def test_help_flag(self):
        """Test --help flag shows help text."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Weft: the durable task substrate for agent systems" in result.stdout
        assert "Options:" in result.stdout
        assert "--version" in result.stdout
        assert "--help" in result.stdout

    def test_no_args_shows_help(self):
        """Test that running with no arguments shows help."""
        result = runner.invoke(app, [])
        # Exit code can be 0 or 2 depending on Python/Typer version
        assert result.exit_code in (0, 2)
        # Help output may be in stdout or stderr depending on version
        output = result.stdout or result.output
        assert "Weft: the durable task substrate for agent systems" in output
        assert "Options:" in output

    def test_no_error_box_on_no_args(self):
        """Test that no error box appears when no arguments provided."""
        result = runner.invoke(app, [])
        output = result.stdout or result.output
        assert "Error" not in output
        assert "╭" not in output  # Rich formatting character
        assert "╰" not in output  # Rich formatting character


class TestModuleExecution:
    """Test running weft as a module."""

    def test_module_version(self):
        """Test python -m weft --version."""
        result = subprocess.run(
            [sys.executable, "-m", "weft", "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert f"{PROG_NAME} {__version__}" in result.stdout

    def test_module_help(self):
        """Test python -m weft --help."""
        result = subprocess.run(
            [sys.executable, "-m", "weft", "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert "Weft: the durable task substrate for agent systems" in result.stdout

    def test_module_no_args(self):
        """Test python -m weft with no arguments."""
        result = subprocess.run(
            [sys.executable, "-m", "weft"],
            capture_output=True,
            text=True,
            check=False,
        )
        # Exit code can be 0 or 2 depending on Python/Typer version
        assert result.returncode in (0, 2)
        # Help output may be in stdout or stderr depending on version
        output = result.stdout or result.stderr
        assert "Weft: the durable task substrate for agent systems" in output

    def test_cli_package_is_not_an_executable_entrypoint(self):
        """Test that python -m weft.cli does not invoke the CLI."""
        result = subprocess.run(
            [sys.executable, "-m", "weft.cli", "--version"],
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode != 0
        assert "cannot be directly executed" in result.stderr
        assert result.stdout == ""


class TestCLIConstants:
    """Test that CLI uses correct constants."""

    def test_program_name(self):
        """Test that the program name is correctly set."""
        assert app.info.name == PROG_NAME

    def test_version_matches_constants(self):
        """Test that version in CLI matches _constants.__version__."""
        result = runner.invoke(app, ["--version"])
        assert __version__ in result.stdout

    @patch("weft.cli.app.PROG_NAME", "test-prog")
    @patch("weft.cli.app.__version__", "9.9.9")
    def test_constants_override(self):
        """Test that CLI correctly uses overridden constants."""
        from weft.cli.app import version_callback

        # Mock the typer.echo and Exit
        with (
            patch("weft.cli.app.typer.echo") as mock_echo,
            patch("weft.cli.app.typer.Exit", side_effect=SystemExit) as mock_exit,
            pytest.raises(SystemExit),
        ):
            version_callback(True)
        mock_echo.assert_called_with("test-prog 9.9.9")
        mock_exit.assert_called_once()
