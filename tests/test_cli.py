"""Tests for the Weft CLI."""

import subprocess
import sys
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from weft._constants import PROG_NAME, __version__
from weft.cli import app

runner = CliRunner()


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
        assert "Weft: The Multi-Agent Weaving Toolkit" in result.stdout
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
        assert "Weft: The Multi-Agent Weaving Toolkit" in output
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
        )
        assert result.returncode == 0
        assert f"{PROG_NAME} {__version__}" in result.stdout

    def test_module_help(self):
        """Test python -m weft --help."""
        result = subprocess.run(
            [sys.executable, "-m", "weft", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "Weft: The Multi-Agent Weaving Toolkit" in result.stdout

    def test_module_no_args(self):
        """Test python -m weft with no arguments."""
        result = subprocess.run(
            [sys.executable, "-m", "weft"],
            capture_output=True,
            text=True,
        )
        # Exit code can be 0 or 2 depending on Python/Typer version
        assert result.returncode in (0, 2)
        # Help output may be in stdout or stderr depending on version
        output = result.stdout or result.stderr
        assert "Weft: The Multi-Agent Weaving Toolkit" in output


class TestCLIConstants:
    """Test that CLI uses correct constants."""

    def test_program_name(self):
        """Test that the program name is correctly set."""
        assert app.info.name == PROG_NAME

    def test_version_matches_constants(self):
        """Test that version in CLI matches _constants.__version__."""
        result = runner.invoke(app, ["--version"])
        assert __version__ in result.stdout

    @patch("weft.cli.PROG_NAME", "test-prog")
    @patch("weft.cli.__version__", "9.9.9")
    def test_constants_override(self):
        """Test that CLI correctly uses overridden constants."""
        from weft.cli import version_callback

        # Mock the typer.echo and Exit
        with patch("weft.cli.typer.echo") as mock_echo:
            with patch("weft.cli.typer.Exit", side_effect=SystemExit) as mock_exit:
                with pytest.raises(SystemExit):
                    version_callback(True)
                mock_echo.assert_called_with("test-prog 9.9.9")
                mock_exit.assert_called_once()
