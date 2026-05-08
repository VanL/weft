"""Shared diagnostic rendering helpers for command and CLI surfaces."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from weft.core.runner_diagnostics import diagnostic_summary


def format_runner_diagnostics(diagnostics: Mapping[str, Any] | None) -> str | None:
    """Return a compact runner diagnostic summary for user-facing surfaces."""

    return diagnostic_summary(diagnostics)


__all__ = ["format_runner_diagnostics"]
