"""Tests for the _constants module."""

import os
from unittest.mock import patch

from weft._constants import (
    EXIT_SUCCESS,
    PROG_NAME,
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

        pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
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
        # Any non-empty value should enable debug
        for value in ["1", "true", "yes", "debug"]:
            with patch.dict(os.environ, {"WEFT_DEBUG": value}):
                config = load_config()
                assert config["WEFT_DEBUG"] is True

        # Empty or missing should be False
        with patch.dict(os.environ, {"WEFT_DEBUG": ""}):
            config = load_config()
            assert config["WEFT_DEBUG"] is False

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

    def test_all_config_keys_present(self) -> None:
        """Test that all expected configuration keys are present."""
        config = load_config()

        expected_keys = {
            # Debug
            "WEFT_DEBUG",
            # Logging
            "WEFT_LOGGING_ENABLED",
        }

        assert set(config.keys()) == expected_keys

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
