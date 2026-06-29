"""Property-based tests for dead-task queue identity helpers."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.helpers.hypothesis_strategies import (
    digit_tid_strings,
    queue_suffixes,
    taskspec_tid_strings,
)
from weft.core.monitor.policies.dead_task import (
    select_dead_task_tids_from_queue_names,
    standard_task_queue_identity,
    standard_task_queue_tid,
)

pytestmark = [pytest.mark.shared, pytest.mark.property]


@given(tid=digit_tid_strings(), suffix=queue_suffixes())
def test_standard_task_queue_identity_accepts_exact_task_queue_names(
    tid: str,
    suffix: str,
) -> None:
    queue_name = f"T{tid}.{suffix}"

    assert standard_task_queue_identity(queue_name) == (tid, suffix)
    assert standard_task_queue_tid(queue_name) == tid


@given(
    tid=digit_tid_strings(),
    suffix=queue_suffixes(),
    extra=st.text(min_size=1, max_size=12),
)
def test_standard_task_queue_identity_rejects_near_misses(
    tid: str,
    suffix: str,
    extra: str,
) -> None:
    malformed_names = (
        f"t{tid}.{suffix}",
        f"T.{suffix}",
        f"T{tid}.",
        f"T{tid}.custom",
        f"T{tid}.{suffix}.{extra}",
        f"weft.state.{tid}.{suffix}",
        f"T{tid}x.{suffix}",
    )

    for queue_name in malformed_names:
        assert standard_task_queue_identity(queue_name) is None
        assert standard_task_queue_tid(queue_name) is None


@given(
    tids=st.lists(taskspec_tid_strings(), min_size=1, max_size=12, unique=True),
    suffix=queue_suffixes(),
)
def test_dead_task_selection_deduplicates_orders_and_excludes_live_tids(
    tids: list[str],
    suffix: str,
) -> None:
    now_ns = max(int(tid) for tid in tids) + 10_000_000_000
    live_tids = set(tids[::2])
    queue_names = [
        queue_name
        for tid in reversed(tids)
        for queue_name in (f"T{tid}.{suffix}", f"T{tid}.{suffix}")
    ]
    queue_names.extend(("not-a-task", "Tbad.ctrl_in", "weft.manager.ctrl_in"))

    selection = select_dead_task_tids_from_queue_names(
        queue_names,
        live_tids=live_tids,
        now_ns=now_ns,
        min_age_seconds=0.0,
    )

    expected = tuple(sorted(set(tids) - live_tids, key=int))
    assert selection.selected_tids == expected
    assert selection.discovered_tids == len(tids)
    assert selection.skipped_live == len(live_tids)


@given(tid=taskspec_tid_strings(), suffix=queue_suffixes())
def test_dead_task_selection_excludes_too_young_tids(
    tid: str,
    suffix: str,
) -> None:
    selection = select_dead_task_tids_from_queue_names(
        (f"T{tid}.{suffix}",),
        live_tids=set(),
        now_ns=int(tid),
        min_age_seconds=1.0,
    )

    assert selection.selected_tids == ()
    assert selection.discovered_tids == 1
    assert selection.skipped_too_young == 1
