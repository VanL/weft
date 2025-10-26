"""Interpreter detection helpers for interactive commands."""

from __future__ import annotations

from typing import Sequence

from .known_interpreters import InterpreterHit, recognise_interpreter

__all__ = ["prepare_command"]


def prepare_command(
    command: Sequence[str] | None,
    *,
    skip_detection: bool,
) -> list[str] | None:
    """Return a command list with interpreter-specific tweaks applied."""

    if command is None:
        return None
    if skip_detection:
        return list(command)

    hit: InterpreterHit | None = recognise_interpreter(command)
    if hit is None:
        return list(command)

    updated = list(command)
    insertion_index = 1 if len(updated) > 1 else len(updated)
    for arg in hit.extra_args:
        if arg not in updated[1:]:
            updated.insert(insertion_index, arg)
            insertion_index += 1
    return updated
