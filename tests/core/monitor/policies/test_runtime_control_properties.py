"""Property-based tests for runtime-control queue cleanup helpers."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.helpers.hypothesis_strategies import (
    digit_tid_strings,
    queue_suffixes,
    taskspec_tid_strings,
)
from weft._constants import QUEUE_RESERVED_SUFFIX
from weft.core.monitor.policies.runtime_control import (
    reserved_queue_tid,
    reserved_queue_tids,
    select_runtime_reserved_cleanup_candidates,
)

pytestmark = [pytest.mark.shared, pytest.mark.property]


@given(tid=digit_tid_strings())
def test_reserved_queue_tid_accepts_exact_reserved_queue_name(tid: str) -> None:
    assert reserved_queue_tid(f"T{tid}.{QUEUE_RESERVED_SUFFIX}") == tid


@given(tid=digit_tid_strings(), suffix=queue_suffixes())
def test_reserved_queue_tid_rejects_non_reserved_or_malformed_names(
    tid: str,
    suffix: str,
) -> None:
    malformed_names = [
        f"t{tid}.{QUEUE_RESERVED_SUFFIX}",
        f"T.{QUEUE_RESERVED_SUFFIX}",
        f"T{tid}.custom",
        f"T{tid}.{QUEUE_RESERVED_SUFFIX}.extra",
        f"T{tid}x.{QUEUE_RESERVED_SUFFIX}",
    ]
    if suffix != QUEUE_RESERVED_SUFFIX:
        malformed_names.append(f"T{tid}.{suffix}")

    for queue_name in malformed_names:
        assert reserved_queue_tid(queue_name) is None


@given(tids=st.lists(digit_tid_strings(), min_size=1, max_size=12))
def test_reserved_queue_tids_returns_first_seen_unique_tids(tids: list[str]) -> None:
    queue_names = [
        queue_name
        for tid in tids
        for queue_name in (
            f"T{tid}.{QUEUE_RESERVED_SUFFIX}",
            f"T{tid}.{QUEUE_RESERVED_SUFFIX}",
            f"T{tid}.inbox",
        )
    ]

    expected = tuple(dict.fromkeys(tids))
    assert reserved_queue_tids(queue_names) == expected


@given(tids=st.lists(taskspec_tid_strings(), min_size=1, max_size=8, unique=True))
def test_runtime_reserved_cleanup_selection_skips_active_and_keeps_order(
    tids: list[str],
) -> None:
    now_ns = max(int(tid) for tid in tids) + 10_000_000_000
    active_tids = set(tids[::2])
    queue_names = tuple(f"T{tid}.{QUEUE_RESERVED_SUFFIX}" for tid in tids)

    selection = select_runtime_reserved_cleanup_candidates(
        now_ns=now_ns,
        retention_seconds=0.0,
        limit=len(queue_names),
        active_tids=active_tids,
        queue_names=queue_names,
        task_record=lambda _tid: None,
        deadline_reached=lambda: False,
    )

    assert selection.queue_names == tuple(
        queue_name
        for queue_name in queue_names
        if reserved_queue_tid(queue_name) not in active_tids
    )
    assert selection.skipped_active == len(active_tids)
    assert selection.skipped_not_ready == 0
    assert selection.pending is False
    assert selection.deadline_hit is False


@given(tid=taskspec_tid_strings())
def test_runtime_reserved_cleanup_selection_skips_not_ready_queue(tid: str) -> None:
    queue_name = f"T{tid}.{QUEUE_RESERVED_SUFFIX}"

    selection = select_runtime_reserved_cleanup_candidates(
        now_ns=int(tid),
        retention_seconds=1.0,
        limit=1,
        active_tids=set(),
        queue_names=(queue_name,),
        task_record=lambda _tid: None,
        deadline_reached=lambda: False,
    )

    assert selection.queue_names == ()
    assert selection.skipped_active == 0
    assert selection.skipped_not_ready == 1
