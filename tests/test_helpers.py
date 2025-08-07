"""Tests for helper utility functions."""

import logging
import os
from io import StringIO
from unittest.mock import patch

import pytest

from weft.helpers import (
    debug_print,
    format_tid,
    is_debug_enabled,
    is_logging_enabled,
    log_critical,
    log_debug,
    log_error,
    log_exception,
    log_info,
    log_warning,
    parse_tid,
    reload_config,
    send_log,
)


class TestSendLog:
    """Test the send_log function."""

    def test_send_log_disabled(self, caplog):
        """Test that send_log does nothing when logging is disabled."""
        with patch.dict(os.environ, {"WEFT_LOGGING_ENABLED": "0"}):
            reload_config()

            with caplog.at_level(logging.DEBUG):
                send_log("Test message")
                send_log("Debug message", level=logging.DEBUG)
                send_log("Error message", level=logging.ERROR)

            # No logs should be recorded
            assert len(caplog.records) == 0

            # Restore config
            reload_config()

    def test_send_log_enabled(self, caplog):
        """Test that send_log works when logging is enabled."""
        with patch.dict(os.environ, {"WEFT_LOGGING_ENABLED": "1"}):
            reload_config()

            with caplog.at_level(logging.DEBUG):
                send_log("Info message")
                send_log("Debug message", level=logging.DEBUG)
                send_log("Error message", level=logging.ERROR)

            # All logs should be recorded
            assert len(caplog.records) == 3
            assert caplog.records[0].message == "Info message"
            assert caplog.records[0].levelname == "INFO"
            assert caplog.records[1].message == "Debug message"
            assert caplog.records[1].levelname == "DEBUG"
            assert caplog.records[2].message == "Error message"
            assert caplog.records[2].levelname == "ERROR"

            # Restore config
            reload_config()

    def test_send_log_custom_logger(self, caplog):
        """Test send_log with a custom logger name."""
        with patch.dict(os.environ, {"WEFT_LOGGING_ENABLED": "1"}):
            reload_config()

            with caplog.at_level(logging.INFO):
                send_log("Custom logger message", logger_name="weft.custom")

            assert len(caplog.records) == 1
            assert caplog.records[0].name == "weft.custom"
            assert caplog.records[0].message == "Custom logger message"

            # Restore config
            reload_config()

    def test_send_log_with_kwargs(self, caplog):
        """Test send_log with additional keyword arguments."""
        with patch.dict(os.environ, {"WEFT_LOGGING_ENABLED": "1"}):
            reload_config()

            # Create a mock exception for testing
            try:
                raise ValueError("Test error")
            except ValueError:
                with caplog.at_level(logging.ERROR):
                    send_log("Error with traceback", level=logging.ERROR, exc_info=True)

            assert len(caplog.records) == 1
            assert "Error with traceback" in caplog.records[0].message
            assert caplog.records[0].exc_info is not None

            # Restore config
            reload_config()


class TestDebugPrint:
    """Test the debug_print function."""

    def test_debug_print_disabled(self, capsys):
        """Test that debug_print does nothing when debug is disabled."""
        with patch.dict(os.environ, {"WEFT_DEBUG": ""}):
            reload_config()

            debug_print("Test message")
            debug_print("Multiple", "arguments", sep="-")

            captured = capsys.readouterr()
            assert captured.out == ""
            assert captured.err == ""

            # Restore config
            reload_config()

    def test_debug_print_enabled(self, capsys):
        """Test that debug_print works when debug is enabled."""
        with patch.dict(os.environ, {"WEFT_DEBUG": "1"}):
            reload_config()

            debug_print("Test message")

            captured = capsys.readouterr()
            assert captured.out == ""
            assert captured.err == "Test message\n"

            # Restore config
            reload_config()

    def test_debug_print_multiple_args(self, capsys):
        """Test debug_print with multiple arguments."""
        with patch.dict(os.environ, {"WEFT_DEBUG": "1"}):
            reload_config()

            debug_print("Value:", 42, "Status:", True)

            captured = capsys.readouterr()
            assert captured.err == "Value: 42 Status: True\n"

            # Restore config
            reload_config()

    def test_debug_print_custom_separator(self, capsys):
        """Test debug_print with custom separator."""
        with patch.dict(os.environ, {"WEFT_DEBUG": "1"}):
            reload_config()

            debug_print("A", "B", "C", sep="-")

            captured = capsys.readouterr()
            assert captured.err == "A-B-C\n"

            # Restore config
            reload_config()

    def test_debug_print_custom_end(self, capsys):
        """Test debug_print with custom end character."""
        with patch.dict(os.environ, {"WEFT_DEBUG": "1"}):
            reload_config()

            debug_print("Line 1", end=" ")
            debug_print("Line 2")

            captured = capsys.readouterr()
            assert captured.err == "Line 1 Line 2\n"

            # Restore config
            reload_config()

    def test_debug_print_to_file(self):
        """Test debug_print writing to a file object."""
        with patch.dict(os.environ, {"WEFT_DEBUG": "1"}):
            reload_config()

            output = StringIO()
            debug_print("Test output", file=output)

            assert output.getvalue() == "Test output\n"

            # Restore config
            reload_config()


class TestLogConvenienceFunctions:
    """Test the convenience logging functions."""

    def test_log_levels(self, caplog):
        """Test all convenience log functions."""
        with patch.dict(os.environ, {"WEFT_LOGGING_ENABLED": "1"}):
            reload_config()

            with caplog.at_level(logging.DEBUG):
                log_debug("Debug message")
                log_info("Info message")
                log_warning("Warning message")
                log_error("Error message")
                log_critical("Critical message")

            assert len(caplog.records) == 5
            assert caplog.records[0].levelname == "DEBUG"
            assert caplog.records[1].levelname == "INFO"
            assert caplog.records[2].levelname == "WARNING"
            assert caplog.records[3].levelname == "ERROR"
            assert caplog.records[4].levelname == "CRITICAL"

            # Restore config
            reload_config()

    def test_log_exception(self, caplog):
        """Test log_exception includes exception info."""
        with patch.dict(os.environ, {"WEFT_LOGGING_ENABLED": "1"}):
            reload_config()

            try:
                raise RuntimeError("Test exception")
            except RuntimeError:
                with caplog.at_level(logging.ERROR):
                    log_exception("Exception occurred")

            assert len(caplog.records) == 1
            assert caplog.records[0].levelname == "ERROR"
            assert "Exception occurred" in caplog.records[0].message
            assert caplog.records[0].exc_info is not None

            # Restore config
            reload_config()


class TestTIDFormatting:
    """Test TID formatting and parsing functions."""

    def test_format_tid_string(self):
        """Test formatting TID from string."""
        assert format_tid("1234567890123456789") == "T1234567890123456789"
        assert format_tid("0000000000000000001") == "T0000000000000000001"

    def test_format_tid_int(self):
        """Test formatting TID from integer."""
        assert format_tid(1234567890123456789) == "T1234567890123456789"
        assert format_tid(1) == "T1"

    def test_parse_tid_valid(self):
        """Test parsing valid formatted TID."""
        assert parse_tid("T1234567890123456789") == "1234567890123456789"
        assert parse_tid("T1") == "1"

    def test_parse_tid_invalid(self):
        """Test parsing invalid formatted TID raises error."""
        with pytest.raises(ValueError, match="Invalid formatted TID"):
            parse_tid("1234567890123456789")

        with pytest.raises(ValueError, match="Invalid formatted TID"):
            parse_tid("X1234567890123456789")

        with pytest.raises(ValueError, match="Invalid formatted TID"):
            parse_tid("")


class TestConfigurationChecks:
    """Test configuration check functions."""

    def test_is_logging_enabled(self):
        """Test is_logging_enabled function."""
        with patch.dict(os.environ, {"WEFT_LOGGING_ENABLED": "1"}):
            reload_config()
            assert is_logging_enabled() is True

        with patch.dict(os.environ, {"WEFT_LOGGING_ENABLED": "0"}):
            reload_config()
            assert is_logging_enabled() is False

        # Restore config
        reload_config()

    def test_is_debug_enabled(self):
        """Test is_debug_enabled function."""
        with patch.dict(os.environ, {"WEFT_DEBUG": "1"}):
            reload_config()
            assert is_debug_enabled() is True

        with patch.dict(os.environ, {"WEFT_DEBUG": ""}):
            reload_config()
            assert is_debug_enabled() is False

        # Restore config
        reload_config()

    def test_reload_config(self):
        """Test reload_config updates the configuration."""
        # Set initial state - empty string for debug, "0" for logging
        with patch.dict(os.environ, {"WEFT_DEBUG": "", "WEFT_LOGGING_ENABLED": "0"}):
            reload_config()
            assert is_debug_enabled() is False
            assert is_logging_enabled() is False

        # Change environment and reload
        with patch.dict(os.environ, {"WEFT_DEBUG": "1", "WEFT_LOGGING_ENABLED": "1"}):
            reload_config()
            assert is_debug_enabled() is True
            assert is_logging_enabled() is True

        # Restore config
        reload_config()
