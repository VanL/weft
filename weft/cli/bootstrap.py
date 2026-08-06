"""Compatibility re-export for the top-level Weft bootstrap module."""

from __future__ import annotations

from weft.bootstrap import (
    WEFT_ENV_FILE_ENV,
    EnvFileError,
    apply_env_file,
    main,
    parse_env_file,
)

__all__ = [
    "WEFT_ENV_FILE_ENV",
    "EnvFileError",
    "apply_env_file",
    "main",
    "parse_env_file",
]
