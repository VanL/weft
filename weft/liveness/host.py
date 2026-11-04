"""Generic exact host-process identity inspection.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3.2]
- docs/specifications/07-System_Invariants.md [LIVENESS.R5]
"""

from __future__ import annotations

import math

import psutil

from .models import HostProcessObservation


def inspect_host_process(
    pid: int,
    create_time: float | None,
) -> HostProcessObservation:
    """Observe one scoped ``(pid, create_time)`` identity without controlling it.

    The verdict rests on the scoped identity alone, with zombie rejection. The
    process title is observability only and is never read here.

    Spec: [LIVENESS.R5], [CC-3.2]
    """

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
    return HostProcessObservation("live", "identity_match")
