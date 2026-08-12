"""Shared timing engine for manual in-process reactor tests.

The driver centralizes the callback order used by tests that own one reactor.
Domain evidence and diagnostics stay in caller-owned adapters.

Spec: docs/specifications/08-Testing_Strategy.md [TS-0].
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Protocol


class WaitForActivity(Protocol):
    """Owner-thread wait adapter used between reactor turns."""

    def __call__(self, *, timeout: float) -> None:
        """Wait for reactor activity for at most ``timeout`` seconds."""


def drive_until[T](
    observe: Callable[[], T],
    matches: Callable[[T], bool],
    *,
    step: Callable[[], None],
    wait: WaitForActivity,
    timeout: float,
    wait_slice: float = 0.02,
    pending_work: Sequence[Callable[[], bool]] = (),
    diagnostics: Callable[[], object] | None = None,
) -> T:
    """Drive one reactor until caller-owned evidence matches.

    Spec: docs/specifications/08-Testing_Strategy.md [TS-0].
    """

    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if wait_slice <= 0:
        raise ValueError("wait_slice must be positive")

    deadline = time.monotonic() + timeout
    turns = 0
    while True:
        step()
        turns += 1
        evidence = observe()
        if matches(evidence):
            return evidence
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if any(has_pending_work() for has_pending_work in pending_work):
                step()
                turns += 1
                evidence = observe()
                if matches(evidence):
                    return evidence
            detail = ""
            if diagnostics is not None:
                try:
                    detail = f", diagnostics={diagnostics()!r}"
                except Exception as exc:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-366] exception
                    # Diagnostic failures must not replace the timeout failure.
                    detail = f", diagnostics raised {type(exc).__name__}: {exc}"
            raise AssertionError(
                "reactor evidence did not match before timeout "
                f"(timeout={timeout!r}, turns={turns}, latest={evidence!r}"
                f"{detail})"
            )
        wait(timeout=min(wait_slice, remaining))
