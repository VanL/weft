"""Dead-task cleanup candidate discovery.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.13]
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from weft._constants import STANDARD_TASK_QUEUE_SUFFIXES
from weft.core.queue_window import is_old_enough


@dataclass(frozen=True, slots=True)
class DeadTaskTidSelection:
    """Dead-task TID selection result from standard task-local queue names."""

    selected_tids: tuple[str, ...] = ()
    discovered_tids: int = 0
    skipped_live: int = 0
    skipped_too_young: int = 0


def standard_task_queue_tid(queue_name: str) -> str | None:
    """Return the TID encoded in a standard task-local queue name."""

    identity = standard_task_queue_identity(queue_name)
    if identity is None:
        return None
    tid, _suffix = identity
    return tid


def standard_task_queue_identity(queue_name: str) -> tuple[str, str] | None:
    """Return ``(tid, suffix)`` for a strict standard task-local queue name."""

    if not queue_name.startswith("T"):
        return None
    tid, separator, suffix = queue_name[1:].partition(".")
    if separator != "." or not tid.isdigit() or not tid:
        return None
    if suffix not in STANDARD_TASK_QUEUE_SUFFIXES:
        return None
    return tid, suffix


def select_dead_task_tids_from_queue_names(
    queue_names: Iterable[str],
    *,
    live_tids: set[str],
    now_ns: int,
    min_age_seconds: float,
) -> DeadTaskTidSelection:
    """Select dead task TIDs from standard task-local queue names."""

    historical_tids = {
        tid
        for queue_name in queue_names
        if (tid := standard_task_queue_tid(queue_name)) is not None
    }
    selected: list[str] = []
    skipped_live = 0
    skipped_too_young = 0
    for tid in sorted(historical_tids, key=int):
        if tid in live_tids:
            skipped_live += 1
            continue
        if not is_old_enough(int(tid), now_ns, min_age_seconds):
            skipped_too_young += 1
            continue
        selected.append(tid)
    return DeadTaskTidSelection(
        selected_tids=tuple(selected),
        discovered_tids=len(historical_tids),
        skipped_live=skipped_live,
        skipped_too_young=skipped_too_young,
    )


def dead_task_tids_from_queue_names(
    queue_names: Iterable[str],
    *,
    live_tids: set[str],
    now_ns: int,
    min_age_seconds: float,
) -> tuple[str, ...]:
    """Return dead task TIDs from standard task-local queue names."""

    return select_dead_task_tids_from_queue_names(
        queue_names,
        live_tids=live_tids,
        now_ns=now_ns,
        min_age_seconds=min_age_seconds,
    ).selected_tids
