"""Identification helpers for well-known interactive interpreters."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class InterpreterHit:
    """Information about an interpreter recognised from the command list."""

    name: str
    extra_args: tuple[str, ...] = ()


KNOWN_INTERPRETERS: Mapping[str, InterpreterHit] = {
    # Python REPLs need -u (unbuffered) for prompt responsiveness and -i to
    # stay interactive when stdin is piped from the CLI.
    "python": InterpreterHit("python", ("-u", "-i")),
    "python3": InterpreterHit("python", ("-u", "-i")),
    "python3.10": InterpreterHit("python", ("-u", "-i")),
    "python3.11": InterpreterHit("python", ("-u", "-i")),
    "python3.12": InterpreterHit("python", ("-u", "-i")),
    "python3.13": InterpreterHit("python", ("-u", "-i")),
    "python3.14": InterpreterHit("python", ("-u", "-i")),
    "pypy": InterpreterHit("python", ("-u", "-i")),
    "pypy3": InterpreterHit("python", ("-u", "-i")),
    # Node already enters a REPL when no script is given, but --interactive
    # makes the intent explicit and keeps the session alive after stdin closes.
    "node": InterpreterHit("node", ("--interactive",)),
    # Bash supports -i for interactive shells; this unlocks prompt export when
    # invoked via the CLI.
    "bash": InterpreterHit("bash", ("-i",)),
}


def _normalize(entry: str) -> str:
    base = Path(entry).name.lower()
    return base


def recognise_interpreter(command: Sequence[str]) -> InterpreterHit | None:
    """Return interpreter metadata if *command* matches a known entry."""

    if not command:
        return None
    candidate = _normalize(command[0])
    if candidate in KNOWN_INTERPRETERS:
        return KNOWN_INTERPRETERS[candidate]

    # Allow dotted python versions without registering every minor release.
    if candidate.startswith("python"):
        return KNOWN_INTERPRETERS["python"]

    return None


__all__ = ["InterpreterHit", "recognise_interpreter"]
