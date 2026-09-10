"""Process-local native title updates with deferred macOS GUI registration.

Spec: docs/specifications/01-Core_Components.md [CC-2.4];
docs/specifications/07-System_Invariants.md [OBS.4], [OBS.7], [OBS.8].
Task formatting and diagnostic state belong to BaseTask, not this module.
"""

from __future__ import annotations

import logging
import random
import sys
import threading
import time
from collections.abc import Callable

import weft.core.deferred
from weft._constants import (
    PROCESS_TITLE_GUI_DELAY,
    PROCESS_TITLE_GUI_JITTER,
    PROCESS_TITLE_HANDOFF_LENGTH,
)

logger = logging.getLogger(__name__)


class _ProcessTitle:
    """Serialize the native writer's lifetime within one spawned process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._initialized = False
        self._setter: Callable[[str], object] | None = None
        self._latest = ""
        self._applied: str | None = None
        self._deadline: float | None = None

    def _initialize(self) -> None:
        self._initialized = True
        if sys.platform == "darwin" and "setproctitle" not in sys.modules:
            module = weft.core.deferred.get_processtitle()
            module.prepare(fork_safe_only=True)
            self._setter = module.set_to
            self._deadline = (
                time.monotonic()
                + PROCESS_TITLE_GUI_DELAY
                + random.uniform(0, PROCESS_TITLE_GUI_JITTER)
            )
        else:
            self._setter = weft.core.deferred.get_setproctitle().setproctitle

    def _apply(self, title: str) -> None:
        assert self._setter is not None
        if self._setter(title) is False:
            raise RuntimeError("native title setter reported failure")
        self._applied = title

    def set(self, title: str) -> str | None:
        """Return a detected failure, or None for success/no new error."""
        with self._lock:
            self._latest = title
            try:
                if not self._initialized:
                    self._initialize()
                if self._setter is not None and title != self._applied:
                    self._apply(title)
            except Exception as exc:  # pragma: no cover - optional native boundary
                logger.debug("Native process-title operation failed", exc_info=True)
                return f"Process title update failed: {type(exc).__name__}: {exc}"
        return None

    def seconds_until_due(self) -> float | None:
        """Read the deadline without importing or calling a native backend."""
        with self._lock:
            if self._deadline is None:
                return None
            return max(0.0, self._deadline - time.monotonic())

    def tick(self) -> str | None:
        """Attempt the macOS handoff once, on an eligible task drive turn."""
        with self._lock:
            if self._deadline is None or time.monotonic() < self._deadline:
                return None
            self._deadline = None
            try:
                # Stock 1.3.7 spt_status.c reserves one byte from the discovered
                # argv span for NUL. Pad before import: Darwin import initializes.
                self._apply(self._latest.ljust(PROCESS_TITLE_HANDOFF_LENGTH))
                # A partial stock import may take ownership of argv memory.
                # Stop writing on import failure rather than alternate writers.
                self._setter = None
                self._setter = weft.core.deferred.get_setproctitle().setproctitle
                self._apply(self._latest)
            except Exception as exc:  # pragma: no cover - optional native boundary
                logger.debug("Native process-title operation failed", exc_info=True)
                return (
                    f"Process title GUI activation failed: {type(exc).__name__}: {exc}"
                )
        return None


_title = _ProcessTitle()


def set_process_title(title: str) -> str | None:
    """Apply changed title text (Spec: [CC-2.4], [OBS.4])."""
    return _title.set(title)


def seconds_until_due() -> float | None:
    """Bound a live task's wait by GUI eligibility (Spec: [CC-2.4])."""
    return _title.seconds_until_due()


def tick() -> str | None:
    """Apply due GUI activation after a live task turn (Spec: [CC-2.4])."""
    return _title.tick()
