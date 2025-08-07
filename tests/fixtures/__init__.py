"""Test fixtures for Weft tests."""

from .taskspecs import (
    INVALID_MISSING_METADATA,
    MINIMAL_VALID_TASKSPEC_DICT,
    VALID_COMMAND_TASKSPEC_DICT,
    VALID_FUNCTION_TASKSPEC_DICT,
    create_minimal_taskspec,
    create_valid_command_taskspec,
    create_valid_function_taskspec,
)

__all__ = [
    "MINIMAL_VALID_TASKSPEC_DICT",
    "VALID_FUNCTION_TASKSPEC_DICT",
    "VALID_COMMAND_TASKSPEC_DICT",
    "INVALID_MISSING_METADATA",
    "create_minimal_taskspec",
    "create_valid_function_taskspec",
    "create_valid_command_taskspec",
]
