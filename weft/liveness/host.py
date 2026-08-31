"""Generic exact host-process identity inspection.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3.2]
- docs/specifications/07-System_Invariants.md [LIVENESS.R5]
"""

from __future__ import annotations

import math
import re

import psutil

from .models import HostProcessObservation


def inspect_host_process(
    pid: int,
    create_time: float | None,
    *,
    expected_tid: str | None = None,
) -> HostProcessObservation:
    """Observe one scoped ``(pid, create_time)`` identity without controlling it."""

    if pid <= 0 or create_time is None or not math.isfinite(create_time):
        return HostProcessObservation("unknown", "missing_exact_identity")
    try:
        process = psutil.Process(pid)
        current_create_time = float(process.create_time())
        status = process.status()
    except psutil.NoSuchProcess:
        return HostProcessObservation("stale", "process_absent")
    except (psutil.AccessDenied, psutil.Error):
        return HostProcessObservation("unknown", "process_unresolved")
    if status == psutil.STATUS_ZOMBIE:
        return HostProcessObservation("stale", "process_zombie")
    if not math.isclose(current_create_time, create_time, rel_tol=0.0, abs_tol=0.001):
        return HostProcessObservation("stale", "identity_mismatch")
    if expected_tid is None:
        return HostProcessObservation("live", "identity_match")
    try:
        cmdline = process.cmdline()
    except (psutil.AccessDenied, psutil.Error, AttributeError, OSError):
        cmdline = []
    title = cmdline[0] if cmdline and isinstance(cmdline[0], str) else ""
    short_tid = re.escape(expected_tid[-10:])
    if re.match(
        rf"^weft-[A-Za-z0-9_-]+-{short_tid}:[A-Za-z0-9_-]+:[A-Za-z0-9_-]+",
        title,
    ):
        return HostProcessObservation("live", "identity_match_title_match")
    return HostProcessObservation("live", "identity_match_title_unconfirmed")
