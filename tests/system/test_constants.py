"""Tests for the _constants module."""

import os
from unittest.mock import patch

from weft._constants import (
    CONTROL_PAUSE,
    CONTROL_RESUME,
    CONTROL_STOP,
    DEFAULT_CLEANUP_ON_EXIT,
    DEFAULT_CPU_PERCENT,  # RENAMED from DEFAULT_CPU_LIMIT
    DEFAULT_FUNCTION_TARGET,
    DEFAULT_MAX_CONNECTIONS,
    DEFAULT_MAX_FDS,
    DEFAULT_MEMORY_MB,  # RENAMED from DEFAULT_MEMORY_LIMIT
    DEFAULT_POLLING_INTERVAL,
    DEFAULT_REPORTING_INTERVAL,
    DEFAULT_STATUS,
    DEFAULT_STREAM_OUTPUT,
    DEFAULT_TIMEOUT,
    EXIT_SUCCESS,
    MAX_CPU_LIMIT,
    MIN_CONNECTIONS_LIMIT,
    MIN_CPU_LIMIT,
    MIN_FDS_LIMIT,
    MIN_MEMORY_LIMIT,
    PROG_NAME,
    QUEUE_CTRL_IN_SUFFIX,
    QUEUE_CTRL_OUT_SUFFIX,
    QUEUE_INBOX_SUFFIX,
    QUEUE_OUTBOX_SUFFIX,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_CREATED,
    STATUS_FAILED,
    STATUS_RUNNING,
    TASKSPEC_TID_LENGTH,
    TASKSPEC_VERSION,
    WEFT_AUTOSTART_TASKS_DEFAULT,
    WEFT_MANAGER_LIFETIME_TIMEOUT,
    WEFT_MANAGER_REUSE_ENABLED,
    __version__,
    load_config,
)


class TestConstants:
    """Test that all constants are defined with expected values."""

    def test_version(self) -> None:
        """Test version constant is consistent with pyproject.toml."""
        assert isinstance(__version__, str)

        # Check consistency with pyproject.toml
        import re
        from pathlib import Path

        pyproject_path = Path(__file__).parent.parent.parent / "pyproject.toml"
        with open(pyproject_path, encoding="utf-8") as f:
            content = f.read()

        # Find version in pyproject.toml using regex
        # Look for version = "x.y.z" pattern
        match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
        if not match:
            raise ValueError("Could not find version in pyproject.toml")

        pyproject_version = match.group(1)
        assert __version__ == pyproject_version, (
            f"Version mismatch: __version__={__version__} but "
            f"pyproject.toml has version={pyproject_version}"
        )

    def test_program_constants(self) -> None:
        """Test program identification constants."""
        assert PROG_NAME == "weft"
        assert isinstance(PROG_NAME, str)

    def test_exit_codes(self) -> None:
        """Test exit code constants."""
        assert EXIT_SUCCESS == 0
        assert isinstance(EXIT_SUCCESS, int)

    def test_taskspec_version_and_identification(self) -> None:
        """Test TaskSpec version and identification constants."""
        assert TASKSPEC_VERSION == "1.0"
        assert isinstance(TASKSPEC_VERSION, str)

        assert TASKSPEC_TID_LENGTH == 19
        assert isinstance(TASKSPEC_TID_LENGTH, int)

    def test_spec_section_defaults(self) -> None:
        """Test SpecSection default value constants."""
        assert DEFAULT_FUNCTION_TARGET == "weft.tasks:noop"
        assert isinstance(DEFAULT_FUNCTION_TARGET, str)

        assert DEFAULT_TIMEOUT is None

        assert DEFAULT_MEMORY_MB == 1024
        assert isinstance(DEFAULT_MEMORY_MB, int)

        assert DEFAULT_CPU_PERCENT is None
        assert DEFAULT_MAX_FDS is None
        assert DEFAULT_MAX_CONNECTIONS is None

        assert DEFAULT_STREAM_OUTPUT is False
        assert isinstance(DEFAULT_STREAM_OUTPUT, bool)

        assert DEFAULT_CLEANUP_ON_EXIT is True
        assert isinstance(DEFAULT_CLEANUP_ON_EXIT, bool)

        assert DEFAULT_POLLING_INTERVAL == 1.0
        assert isinstance(DEFAULT_POLLING_INTERVAL, float)

        assert DEFAULT_REPORTING_INTERVAL == "transition"
        assert isinstance(DEFAULT_REPORTING_INTERVAL, str)

    def test_queue_naming_conventions(self) -> None:
        """Test queue naming suffix constants."""
        assert QUEUE_INBOX_SUFFIX == "inbox"
        assert QUEUE_OUTBOX_SUFFIX == "outbox"
        assert QUEUE_CTRL_IN_SUFFIX == "ctrl_in"
        assert QUEUE_CTRL_OUT_SUFFIX == "ctrl_out"

        # All should be strings
        for suffix in [
            QUEUE_INBOX_SUFFIX,
            QUEUE_OUTBOX_SUFFIX,
            QUEUE_CTRL_IN_SUFFIX,
            QUEUE_CTRL_OUT_SUFFIX,
        ]:
            assert isinstance(suffix, str)

    def test_state_section_defaults(self) -> None:
        """Test StateSection status constants."""
        assert STATUS_CREATED == "created"
        assert STATUS_RUNNING == "running"
        assert STATUS_COMPLETED == "completed"
        assert STATUS_FAILED == "failed"
        assert STATUS_CANCELLED == "cancelled"

        assert DEFAULT_STATUS == STATUS_CREATED
        assert DEFAULT_STATUS == "created"

        # All should be strings
        for status in [
            STATUS_CREATED,
            STATUS_RUNNING,
            STATUS_COMPLETED,
            STATUS_FAILED,
            STATUS_CANCELLED,
        ]:
            assert isinstance(status, str)

    def test_control_commands(self) -> None:
        """Test control command constants."""
        assert CONTROL_STOP == "STOP"
        assert CONTROL_PAUSE == "PAUSE"
        assert CONTROL_RESUME == "RESUME"

        # All should be strings
        for cmd in [CONTROL_STOP, CONTROL_PAUSE, CONTROL_RESUME]:
            assert isinstance(cmd, str)

    def test_resource_limits(self) -> None:
        """Test resource limit constants."""
        assert MIN_MEMORY_LIMIT == 1
        assert isinstance(MIN_MEMORY_LIMIT, int)

        assert MAX_CPU_LIMIT == 100
        assert MIN_CPU_LIMIT == 1
        assert isinstance(MAX_CPU_LIMIT, int)
        assert isinstance(MIN_CPU_LIMIT, int)

        # CPU limits should be sensible
        assert MIN_CPU_LIMIT < MAX_CPU_LIMIT
        assert MIN_CPU_LIMIT > 0
        assert MAX_CPU_LIMIT <= 100

        assert MIN_FDS_LIMIT == 1
        assert isinstance(MIN_FDS_LIMIT, int)

        assert MIN_CONNECTIONS_LIMIT == 0
        assert isinstance(MIN_CONNECTIONS_LIMIT, int)


class TestLoadConfig:
    """Test the load_config function with various environment configurations."""

    def test_default_config(self) -> None:
        """Test load_config returns expected defaults when no env vars are set."""
        with patch.dict(os.environ, {}, clear=True):
            config = load_config()

            # Debug
            assert config["WEFT_DEBUG"] is False

            # Logging
            assert config["WEFT_LOGGING_ENABLED"] is False

    def test_debug_setting(self) -> None:
        """Test debug environment variable."""
        # Values that should enable debug
        for value in ["1", "true", "yes", "debug", "TRUE", "Y"]:
            with patch.dict(os.environ, {"WEFT_DEBUG": value}):
                config = load_config()
                assert config["WEFT_DEBUG"] is True, (
                    f"Expected True for WEFT_DEBUG={value}"
                )

        # Values that should disable debug
        for value in ["", "0", "f", "F", "false", "False", "FALSE"]:
            with patch.dict(os.environ, {"WEFT_DEBUG": value}):
                config = load_config()
                assert config["WEFT_DEBUG"] is False, (
                    f"Expected False for WEFT_DEBUG={value}"
                )

        # Missing should be False
        with patch.dict(os.environ, {}, clear=True):
            config = load_config()
            assert config["WEFT_DEBUG"] is False

    def test_logging_setting(self) -> None:
        """Test logging environment variable."""
        # Only "1" should enable logging
        with patch.dict(os.environ, {"WEFT_LOGGING_ENABLED": "1"}):
            config = load_config()
            assert config["WEFT_LOGGING_ENABLED"] is True

        # Any other value should be False
        for value in ["0", "true", "yes", "enabled", ""]:
            with patch.dict(os.environ, {"WEFT_LOGGING_ENABLED": value}):
                config = load_config()
                assert config["WEFT_LOGGING_ENABLED"] is False

        # Missing should be False
        with patch.dict(os.environ, {}, clear=True):
            config = load_config()
            assert config["WEFT_LOGGING_ENABLED"] is False

    def test_manager_reuse_env(self) -> None:
        with patch.dict(os.environ, {"WEFT_MANAGER_REUSE_ENABLED": "0"}):
            config = load_config()
            assert config["WEFT_MANAGER_REUSE_ENABLED"] is False

        with patch.dict(os.environ, {"WEFT_MANAGER_REUSE_ENABLED": "true"}):
            config = load_config()
            assert config["WEFT_MANAGER_REUSE_ENABLED"] is True

    def test_manager_timeout_env(self) -> None:
        """Manager timeout honours the environment variable."""
        with patch.dict(os.environ, {"WEFT_MANAGER_LIFETIME_TIMEOUT": "42.5"}):
            config = load_config()
            assert config["WEFT_MANAGER_LIFETIME_TIMEOUT"] == 42.5

        with patch.dict(os.environ, {"WEFT_MANAGER_LIFETIME_TIMEOUT": "-1"}):
            config = load_config()
            assert (
                config["WEFT_MANAGER_LIFETIME_TIMEOUT"] == WEFT_MANAGER_LIFETIME_TIMEOUT
            )

    def test_all_config_keys_present(self) -> None:
        """Test that all expected configuration keys are present."""
        config = load_config()

        expected_keys = {
            "WEFT_DEBUG",
            "WEFT_LOGGING_ENABLED",
            "WEFT_REDACT_TASKSPEC_FIELDS",
            "WEFT_MANAGER_REUSE_ENABLED",
            "WEFT_MANAGER_LIFETIME_TIMEOUT",
            "WEFT_AUTOSTART_TASKS",
            "WEFT_TEST_TRACE",
            "BROKER_PROJECT_SCOPE",
            "BROKER_DEFAULT_DB_NAME",
            "BROKER_DEBUG",
            "BROKER_LOGGING_ENABLED",
        }

        assert set(config.keys()) == expected_keys
        assert config["BROKER_PROJECT_SCOPE"] in {"true", "True", True}
        assert config["BROKER_DEFAULT_DB_NAME"] == ".weft/broker.db"
        assert config["BROKER_DEBUG"] == config["WEFT_DEBUG"]
        assert config["BROKER_LOGGING_ENABLED"] == config["WEFT_LOGGING_ENABLED"]
        assert config["WEFT_REDACT_TASKSPEC_FIELDS"] == ""
        assert config["WEFT_MANAGER_LIFETIME_TIMEOUT"] == WEFT_MANAGER_LIFETIME_TIMEOUT
        assert config["WEFT_MANAGER_REUSE_ENABLED"] == WEFT_MANAGER_REUSE_ENABLED
        assert config["WEFT_AUTOSTART_TASKS"] == WEFT_AUTOSTART_TASKS_DEFAULT
        assert config["WEFT_TEST_TRACE"] is False

    def test_config_immutability(self) -> None:
        """Test that modifying returned config doesn't affect subsequent calls."""
        config1 = load_config()
        original_debug = config1["WEFT_DEBUG"]

        # Modify the returned config
        config1["WEFT_DEBUG"] = not original_debug

        # Get a new config
        config2 = load_config()

        # Should have original value, not modified one
        assert config2["WEFT_DEBUG"] == original_debug
        assert config2["WEFT_DEBUG"] != config1["WEFT_DEBUG"]
