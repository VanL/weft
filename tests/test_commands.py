"""Tests for CLI commands."""

import json
import tempfile
from pathlib import Path

from typer.testing import CliRunner

from tests.fixtures import taskspecs as fixtures
from weft.cli import app

runner = CliRunner()


class TestValidateTaskspecCommand:
    """Test the validate-taskspec CLI command."""

    def test_validate_valid_taskspec(self):
        """Test validating a valid TaskSpec file."""
        # Use a valid TaskSpec from fixtures
        valid_taskspec = fixtures.MINIMAL_VALID_TASKSPEC_DICT

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(valid_taskspec, f)
            temp_path = f.name

        try:
            result = runner.invoke(app, ["validate-taskspec", temp_path])
            assert result.exit_code == 0
            assert "TaskSpec is valid" in result.output
            assert "✓" in result.output
        finally:
            Path(temp_path).unlink()

    def test_validate_invalid_taskspec(self):
        """Test validating an invalid TaskSpec file."""
        # Use an invalid TaskSpec from fixtures
        invalid_taskspec = fixtures.INVALID_SHORT_TID

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(invalid_taskspec, f)
            temp_path = f.name

        try:
            result = runner.invoke(app, ["validate-taskspec", temp_path])
            assert result.exit_code == 1
            assert "validation failed" in result.output
            assert "✗" in result.output
            assert "tid" in result.output
        finally:
            Path(temp_path).unlink()

    def test_validate_nonexistent_file(self):
        """Test validating a non-existent file."""
        result = runner.invoke(app, ["validate-taskspec", "/nonexistent/file.json"])
        # Typer validates file existence before calling our handler
        assert result.exit_code != 0

    def test_validate_malformed_json(self):
        """Test validating a file with malformed JSON."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{ invalid json")
            temp_path = f.name

        try:
            result = runner.invoke(app, ["validate-taskspec", temp_path])
            assert result.exit_code == 1
            assert "validation failed" in result.output
            assert "_json" in result.output
        finally:
            Path(temp_path).unlink()

    def test_validate_with_summary(self):
        """Test that valid TaskSpec shows a summary."""
        # Use a valid command TaskSpec from fixtures
        valid_taskspec = fixtures.VALID_COMMAND_TASKSPEC_DICT

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(valid_taskspec, f)
            temp_path = f.name

        try:
            result = runner.invoke(app, ["validate-taskspec", temp_path])
            assert result.exit_code == 0
            assert "TaskSpec Summary" in result.output
            assert "1234567890123456789" in result.output
            assert (
                "test-command" in result.output
            )  # Fixture has test-command, not test-task
            assert "command" in result.output
        finally:
            Path(temp_path).unlink()

    def test_validate_multiple_errors(self):
        """Test validation with multiple errors shows all errors."""
        # Create a TaskSpec with multiple errors
        invalid_taskspec = {
            "tid": "abc",  # Invalid format
            "name": "",  # Empty string
            "spec": {
                "type": "command"
                # Missing process_target
            },
            "io": {
                "inputs": {},
                "outputs": {"outbox": "test.out"},
                "control": {
                    "ctrl_in": "test.in"
                    # Missing ctrl_out
                },
            },
            "state": {
                "status": "created",
                "return_code": None,
                "started_at": None,
                "completed_at": None,
            },
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(invalid_taskspec, f)
            temp_path = f.name

        try:
            result = runner.invoke(app, ["validate-taskspec", temp_path])
            assert result.exit_code == 1
            assert "Validation Errors" in result.output
            assert "tid" in result.output
            assert "name" in result.output
        finally:
            Path(temp_path).unlink()
