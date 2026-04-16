"""Identification helpers for well-known interactive interpreters."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from weft._constants import KNOWN_INTERPRETER_DEFINITIONS


@dataclass(frozen=True)
class InterpreterHit:
    """Information about an interpreter recognised from the command list."""

    name: str
    extra_args: tuple[str, ...] = ()


@cache
def _known_interpreters() -> Mapping[str, InterpreterHit]:
    return {
        key: InterpreterHit(name, extra_args)
        for key, (name, extra_args) in KNOWN_INTERPRETER_DEFINITIONS.items()
    }


def _normalize(entry: str) -> str:
    base = Path(entry).name.lower()
    return base


def recognise_interpreter(command: Sequence[str]) -> InterpreterHit | None:
    """Return interpreter metadata if *command* matches a known entry."""

    if not command:
        return None
    known_interpreters = _known_interpreters()
    candidate = _normalize(command[0])
    if candidate in known_interpreters:
        return known_interpreters[candidate]

    # Allow dotted python versions without registering every minor release.
    if candidate.startswith("python"):
        return known_interpreters["python"]

    return None


__all__ = ["InterpreterHit", "recognise_interpreter"]
