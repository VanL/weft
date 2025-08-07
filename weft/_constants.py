"""Constants and configuration for Weft.

This module centralizes all constants and environment variable configuration
for Weft. Constants are immutable values that control various aspects
of the system's behavior, from message size limits to timing parameters.

Environment Variables:
    See the load_config() function for a complete list of supported environment
    variables and their default values.

Usage:
    from weft._constants import WEFT_DEBUG, load_config

    # Use constants directly
    if WEFT_DEBUG:
       #....

    # Load configuration once at module level
    _config = load_config()
    use_logging = _config["WEFT_LOGGING_ENABLED"]

"""

import os
from typing import Any, Final

# ==============================================================================
# VERSION INFORMATION
# ==============================================================================

__version__: Final[str] = "0.1.0"
"""Current version of Weft."""

# ==============================================================================
# PROGRAM IDENTIFICATION
# ==============================================================================

PROG_NAME: Final[str] = "weft"
"""Program name used in CLI help and error messages."""

# ==============================================================================
# EXIT CODES
# ==============================================================================

EXIT_SUCCESS: Final[int] = 0
"""Exit code for successful operations."""


def load_config() -> dict[str, Any]:
    """Load configuration from environment variables.

    This function reads all Weft environment variables and returns
    a configuration dictionary with validated values. It's designed to be
    called once at module initialization to avoid repeated environment lookups.

    Returns:
        dict: Configuration dictionary with the following keys:

        Debug:
            WEFT_DEBUG (bool): Enable debug output.
                Default: False
                Shows additional diagnostic information.

        Logging:
            WEFT_LOGGING_ENABLED (bool): Enable logging output.
                Default: False (disabled)
                Set to "1" to enable logging throughout Weft.
                When enabled, logs will be written using Python's logging module.
                Configure logging levels and handlers in your application as needed.

    """
    config = {
        # Debug
        "WEFT_DEBUG": bool(os.environ.get("WEFT_DEBUG")),
        # Logging
        "WEFT_LOGGING_ENABLED": os.environ.get("WEFT_LOGGING_ENABLED", "0") == "1",
    }

    return config
