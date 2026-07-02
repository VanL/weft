"""Tests for TID-mapping cleanup candidate discovery.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5], Cleanup Boundary
- docs/specifications/07-System_Invariants.md [OBS.13.7]
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Mapping
from typing import Any

import psutil
import pytest

from weft.core.monitor.policies.tid_mapping import (
    decode_tid_mapping_row,
    tid_mapping_candidates,
)
from weft.core.queue_window import QueueWindowRow

pytestmark = [pytest.mark.shared]

_BASE_NS = 1_778_000_000_000_000_000


def _row(queue: str, message_id: int, payload: Mapping[str, Any]) -> QueueWindowRow:
    return QueueWindowRow(queue=queue, body=json.dumps(payload), message_id=message_id)


def _mapping_payload(
    *,
    full: str,
    short: str,
    host_processes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"short": short, "full": full}
    if host_processes is not None:
        payload["runtime_handle"] = {
            "runner": "host",
            "kind": "process",
            "id": full,
            "control": {"authority": "host-pid"},
            "observations": {"host_processes": host_processes},
            "metadata": {},
        }
    return payload


def _decoded(rows: list[QueueWindowRow]) -> tuple:
    return tuple(decode_tid_mapping_row(row) for row in rows)


def test_newest_row_of_live_task_survives_past_min_age() -> None:
    """RED: the newest mapping row of a live task must survive cleanup.

    Uses this test process's own (pid, create_time) as the live owner so the
    liveness probe is real, not mocked. min-age is overridden via the
    policy's parameter, not a monkeypatched constant.
    """

    self_pid = os.getpid()
    self_create_time = psutil.Process(self_pid).create_time()
    tid = "1778000000000000001"

    old_message_id = _BASE_NS
    now_ns = old_message_id + 3_600_000_000_000  # 1 hour later, well past min-age

    rows = [
        _row(
            "weft.state.tid_mappings",
            old_message_id,
            _mapping_payload(
                full=tid,
                short="0000000001",
                host_processes=[{"pid": self_pid, "create_time": self_create_time}],
            ),
        ),
    ]

    candidates, _queue_stats, _policy_stats, _progress = tid_mapping_candidates(
        _decoded(rows),
        now_ns=now_ns,
        min_age_seconds=1.0,
        exclude_tids=set(),
    )

    assert candidates == [], (
        "newest row of a live-probing task must not be selected for deletion, "
        f"got candidates={candidates}"
    )


def test_superseded_rows_of_live_task_are_deleted_past_min_age() -> None:
    """Non-newest rows for a live task's key keep the current age-only rule."""

    self_pid = os.getpid()
    self_create_time = psutil.Process(self_pid).create_time()
    tid = "1778000000000000002"

    superseded_id = _BASE_NS
    newest_id = _BASE_NS + 10_000_000_000  # 10s later, still old enough
    now_ns = newest_id + 3_600_000_000_000

    rows = [
        _row(
            "weft.state.tid_mappings",
            superseded_id,
            _mapping_payload(
                full=tid,
                short="0000000002",
                host_processes=[{"pid": self_pid, "create_time": self_create_time}],
            ),
        ),
        _row(
            "weft.state.tid_mappings",
            newest_id,
            _mapping_payload(
                full=tid,
                short="0000000002",
                host_processes=[{"pid": self_pid, "create_time": self_create_time}],
            ),
        ),
    ]

    candidates, _queue_stats, _policy_stats, _progress = tid_mapping_candidates(
        _decoded(rows),
        now_ns=now_ns,
        min_age_seconds=1.0,
        exclude_tids=set(),
    )

    selected_ids = {candidate.message_id for candidate in candidates}
    assert selected_ids == {superseded_id}
    assert candidates[0].candidate_class == "superseded_tid_mapping"


def test_newest_row_of_dead_owner_is_deleted_after_min_age() -> None:
    """A real spawned-then-exited child's mapping row is deletable once dead."""

    proc = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    ps_proc = psutil.Process(proc.pid)
    create_time = ps_proc.create_time()
    proc.wait(timeout=10)
    # Give the OS a moment to fully reap zombie state where applicable.
    deadline = time.monotonic() + 5.0
    while psutil.pid_exists(proc.pid) and time.monotonic() < deadline:
        time.sleep(0.05)

    tid = "1778000000000000003"
    message_id = _BASE_NS
    now_ns = message_id + 3_600_000_000_000

    rows = [
        _row(
            "weft.state.tid_mappings",
            message_id,
            _mapping_payload(
                full=tid,
                short="0000000003",
                host_processes=[{"pid": proc.pid, "create_time": create_time}],
            ),
        ),
    ]

    candidates, _queue_stats, _policy_stats, _progress = tid_mapping_candidates(
        _decoded(rows),
        now_ns=now_ns,
        min_age_seconds=1.0,
        exclude_tids=set(),
    )

    selected_ids = {candidate.message_id for candidate in candidates}
    assert selected_ids == {message_id}
    assert candidates[0].candidate_class == "old_tid_mapping"


def test_newest_ids_evidence_beyond_window_declassifies_protected_row() -> None:
    """Full-queue newest evidence overrides in-window newest computation.

    When the scanned window is truncated, a live-owner row that looks
    newest-in-window must still be deleted under the age-only rule if the
    caller's full-queue evidence proves a newer sibling exists beyond the
    window.
    """

    self_pid = os.getpid()
    self_create_time = psutil.Process(self_pid).create_time()
    tid = "1778000000000000006"

    superseded_id = _BASE_NS
    beyond_window_newest_id = _BASE_NS + 10_000_000_000
    now_ns = beyond_window_newest_id + 3_600_000_000_000

    rows = [
        _row(
            "weft.state.tid_mappings",
            superseded_id,
            _mapping_payload(
                full=tid,
                short="0000000006",
                host_processes=[{"pid": self_pid, "create_time": self_create_time}],
            ),
        ),
    ]

    # Without beyond-window evidence the row is protected (live newest).
    protected, _stats, _pstats, _progress = tid_mapping_candidates(
        _decoded(rows),
        now_ns=now_ns,
        min_age_seconds=1.0,
        exclude_tids=set(),
        scan_limit_reached=True,
    )
    assert protected == []

    # With evidence of a newer sibling beyond the window it is superseded.
    candidates, _stats, _pstats, progress = tid_mapping_candidates(
        _decoded(rows),
        now_ns=now_ns,
        min_age_seconds=1.0,
        exclude_tids=set(),
        scan_limit_reached=True,
        newest_ids={tid: beyond_window_newest_id},
    )
    assert [candidate.message_id for candidate in candidates] == [superseded_id]
    assert candidates[0].candidate_class == "superseded_tid_mapping"
    assert progress[0].waypoint_reached is True


def test_protected_full_window_does_not_claim_catchup_waypoint() -> None:
    """A full window of correctly protected rows must not claim a waypoint.

    waypoint_reached feeds the monitor's catch-up cadence; claiming it from
    pre-gate age candidates while the liveness gate filtered everything
    would hot-loop the monitor with zero forward progress.
    """

    self_pid = os.getpid()
    self_create_time = psutil.Process(self_pid).create_time()
    tid = "1778000000000000007"

    message_id = _BASE_NS
    now_ns = message_id + 3_600_000_000_000

    rows = [
        _row(
            "weft.state.tid_mappings",
            message_id,
            _mapping_payload(
                full=tid,
                short="0000000007",
                host_processes=[{"pid": self_pid, "create_time": self_create_time}],
            ),
        ),
    ]

    candidates, _stats, _pstats, progress = tid_mapping_candidates(
        _decoded(rows),
        now_ns=now_ns,
        min_age_seconds=1.0,
        exclude_tids=set(),
        scan_limit_reached=True,
        newest_ids={tid: message_id},
    )

    assert candidates == []
    assert progress[0].waypoint_reached is False


def test_newest_row_with_undecidable_liveness_is_skipped() -> None:
    """A payload with no probeable host PIDs is treated as live (skip, never delete)."""

    tid = "1778000000000000004"
    message_id = _BASE_NS
    now_ns = message_id + 3_600_000_000_000

    rows = [
        _row(
            "weft.state.tid_mappings",
            message_id,
            _mapping_payload(full=tid, short="0000000004", host_processes=None),
        ),
    ]

    candidates, _queue_stats, _policy_stats, _progress = tid_mapping_candidates(
        _decoded(rows),
        now_ns=now_ns,
        min_age_seconds=1.0,
        exclude_tids=set(),
    )

    assert candidates == []


def test_newest_row_with_empty_host_processes_is_skipped() -> None:
    """External/non-host runtime handles with an empty processes list are undecidable."""

    tid = "1778000000000000005"
    message_id = _BASE_NS
    now_ns = message_id + 3_600_000_000_000

    rows = [
        _row(
            "weft.state.tid_mappings",
            message_id,
            _mapping_payload(full=tid, short="0000000005", host_processes=[]),
        ),
    ]

    candidates, _queue_stats, _policy_stats, _progress = tid_mapping_candidates(
        _decoded(rows),
        now_ns=now_ns,
        min_age_seconds=1.0,
        exclude_tids=set(),
    )

    assert candidates == []
