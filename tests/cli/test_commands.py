"""Tests for CLI commands."""

import json

from tests.conftest import run_cli

VALID_TEST_TID = "1755033993077017000"

MINIMAL_VALID_TASKSPEC = {
    "tid": VALID_TEST_TID,
    "name": "test-minimal",
    "spec": {
        "type": "function",
        "function_target": "test:func",
        "reserved_policy_on_stop": "keep",
        "reserved_policy_on_error": "keep",
    },
    "io": {
        "inputs": {},
        "outputs": {"outbox": f"T{VALID_TEST_TID}.outbox"},
        "control": {
            "ctrl_in": f"T{VALID_TEST_TID}.ctrl_in",
            "ctrl_out": f"T{VALID_TEST_TID}.ctrl_out",
        },
    },
    "state": {
        "status": "created",
        "return_code": None,
        "started_at": None,
        "completed_at": None,
    },
    "metadata": {},
}

INVALID_SHORT_TID = {
    "tid": "123",
    "name": "test",
    "spec": {"type": "function", "function_target": "test:func"},
    "io": {
        "inputs": {},
        "outputs": {"outbox": "test.out"},
        "control": {"ctrl_in": "test.in", "ctrl_out": "test.out"},
    },
    "state": {
        "status": "created",
        "return_code": None,
        "started_at": None,
        "completed_at": None,
    },
    "metadata": {},
}

VALID_COMMAND_TASKSPEC = {
    "tid": VALID_TEST_TID,
    "name": "test-command",
    "version": "1.0",
    "spec": {
        "type": "command",
        "process_target": ["echo", "hello"],
        "timeout": 30.0,
        "limits": {
            "memory_mb": 256,
        },
        "env": {"TEST_VAR": "test_value"},
        "working_dir": "/tmp",
        "stream_output": True,
        "reserved_policy_on_stop": "keep",
        "reserved_policy_on_error": "keep",
    },
    "io": {
        "inputs": {"stdin": f"T{VALID_TEST_TID}.stdin"},
        "outputs": {
            "outbox": f"T{VALID_TEST_TID}.outbox",
            "stdout": f"T{VALID_TEST_TID}.stdout",
            "stderr": f"T{VALID_TEST_TID}.stderr",
        },
        "control": {
            "ctrl_in": f"T{VALID_TEST_TID}.ctrl_in",
            "ctrl_out": f"T{VALID_TEST_TID}.ctrl_out",
        },
    },
    "state": {
        "status": "created",
        "return_code": None,
        "started_at": None,
        "completed_at": None,
    },
    "metadata": {
        "owner": "test",
        "type": "command",
    },
}


class TestValidateTaskspecCommand:
    """Test the validate-taskspec CLI command."""

    def test_validate_valid_taskspec(self, workdir):
        """Test validating a valid TaskSpec file."""
        path = workdir / "valid_taskspec.json"
        path.write_text(json.dumps(MINIMAL_VALID_TASKSPEC), encoding="utf-8")

        rc, out, err = run_cli("validate-taskspec", path, cwd=workdir)
        assert rc == 0
        assert "TaskSpec is valid" in out
        assert "✓" in out
        assert err == ""

    def test_validate_invalid_taskspec(self, workdir):
        """Test validating an invalid TaskSpec file."""
        path = workdir / "invalid_taskspec.json"
        path.write_text(json.dumps(INVALID_SHORT_TID), encoding="utf-8")

        rc, out, err = run_cli("validate-taskspec", path, cwd=workdir)
        assert rc == 1
        assert "validation failed" in out
        assert "✗" in out
        assert "tid" in out
        assert err == ""

    def test_validate_nonexistent_file(self, workdir):
        """Test validating a non-existent file."""
        missing = workdir / "missing.json"

        rc, out, err = run_cli("validate-taskspec", missing, cwd=workdir)
        assert rc != 0
        combined = f"{out}\n{err}"
        assert "Invalid value" in combined or "File not found" in combined

    def test_validate_malformed_json(self, workdir):
        """Test validating a file with malformed JSON."""
        path = workdir / "malformed.json"
        path.write_text("{ invalid json", encoding="utf-8")

        rc, out, err = run_cli("validate-taskspec", path, cwd=workdir)
        assert rc == 1
        assert "validation failed" in out
        assert "_json" in out
        assert err == ""

    def test_validate_with_summary(self, workdir):
        """Test that valid TaskSpec shows a summary."""
        path = workdir / "command_taskspec.json"
        path.write_text(json.dumps(VALID_COMMAND_TASKSPEC), encoding="utf-8")

        rc, out, err = run_cli("validate-taskspec", path, cwd=workdir)
        assert rc == 0
        assert "TaskSpec Summary" in out
        assert "1755033993077017000" in out
        assert "test-command" in out
        assert "command" in out
        assert err == ""

    def test_validate_multiple_errors(self, workdir):
        """Test validation with multiple errors shows all errors."""
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

        path = workdir / "multiple_errors.json"
        path.write_text(json.dumps(invalid_taskspec), encoding="utf-8")

        rc, out, err = run_cli("validate-taskspec", path, cwd=workdir)
        assert rc == 1
        assert "Validation Errors" in out
        assert "tid" in out
        assert "name" in out
        assert err == ""
