"""CLI adapter package."""

from __future__ import annotations

import typer

from weft._constants import PROG_NAME, __version__

from .app import app, version_callback

__all__ = ["PROG_NAME", "__version__", "app", "typer", "version_callback"]
