"""Tests for explicit runtime-state pruning."""

from __future__ import annotations

import json
import os

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft._constants import (
    WEFT_ENDPOINTS_REGISTRY_QUEUE,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_MANAGERS_REGISTRY_QUEUE,
    WEFT_PIPELINES_STATE_QUEUE,
    WEFT_STREAMING_SESSIONS_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
)
from weft.commands.runtime_prune import (
    CLASS_STALE_ENDPOINT,
    CLASS_STALE_MANAGER,
    CLASS_STALE_STREAMING,
    CLASS_SUPERSEDED_ENDPOINT,
    CLASS_SUPERSEDED_MANAGER,
    CLASS_SUPERSEDED_TID_MAPPING,
    CLASS_UNSUPPORTED_PIPELINE,
    RuntimePruneConfig,
    run_runtime_prune,
)
from weft.context import build_context
from weft.core.endpoints import build_endpoint_record_payload
from weft.ext import RunnerHandle
from weft.helpers import iter_queue_json_entries

pytestmark = [pytest.mark.shared]


def _context(tmp_path):
    root = prepare_project_root(tmp_path)
    return build_context(spec_context=root)


def _write_json(ctx, queue_name: str, payload: dict[str, object]) -> int:
    queue = ctx.queue(queue_name, persistent=False)
    try:
        queue.write(json.dumps(payload))
        latest: int | None = None
        for row, message_id in iter_queue_json_entries(queue):
            if row == payload:
                latest = int(message_id)
        assert latest is not None
        return latest
    finally:
        queue.close()


def _read_rows(ctx, queue_name: str) -> list[tuple[dict[str, object], int]]:
    queue = ctx.queue(queue_name, persistent=False)
    try:
        return [
            (payload, int(message_id))
            for payload, message_id in iter_queue_json_entries(queue)
        ]
    finally:
        queue.close()


def _run(ctx, **kwargs):
    config = RuntimePruneConfig(
        context_path=ctx.root,
        min_age_seconds=0,
        queues=("tid-mappings",),
        **kwargs,
    )
    return run_runtime_prune(config)


def test_tid_mapping_dry_run_reports_older_duplicate_without_deleting(tmp_path) -> None:
    ctx = _context(tmp_path)
    old_id = _write_json(
        ctx,
        WEFT_TID_MAPPINGS_QUEUE,
        {"short": "111", "full": "1770000000000000001", "name": "old"},
    )
    new_id = _write_json(
        ctx,
        WEFT_TID_MAPPINGS_QUEUE,
        {"short": "222", "full": "1770000000000000001", "name": "new"},
    )
    _write_json(ctx, WEFT_TID_MAPPINGS_QUEUE, {"short": "bad"})

    result = _run(ctx)

    assert result.exit_code == 0
    assert [(c.message_id, c.classification) for c in result.candidates] == [
        (old_id, CLASS_SUPERSEDED_TID_MAPPING)
    ]
    rows = _read_rows(ctx, WEFT_TID_MAPPINGS_QUEUE)
    assert {message_id for _payload, message_id in rows} >= {old_id, new_id}


def test_tid_mapping_apply_deletes_exact_candidate_only(tmp_path) -> None:
    ctx = _context(tmp_path)
    old_id = _write_json(
        ctx,
        WEFT_TID_MAPPINGS_QUEUE,
        {"short": "111", "full": "1770000000000000002", "name": "old"},
    )
    new_id = _write_json(
        ctx,
        WEFT_TID_MAPPINGS_QUEUE,
        {"short": "222", "full": "1770000000000000002", "name": "new"},
    )

    result = _run(ctx, apply=True)

    assert result.deleted == 1
    assert result.applied_candidates[0].message_id == old_id
    remaining_ids = {
        message_id for _payload, message_id in _read_rows(ctx, WEFT_TID_MAPPINGS_QUEUE)
    }
    assert old_id not in remaining_ids
    assert new_id in remaining_ids


def test_manager_prune_reports_superseded_and_stale_active_rows(tmp_path) -> None:
    ctx = _context(tmp_path)
    old_stopped = _write_json(
        ctx,
        WEFT_MANAGERS_REGISTRY_QUEUE,
        {"tid": "1770000000000000010", "status": "stopped", "name": "manager"},
    )
    _write_json(
        ctx,
        WEFT_MANAGERS_REGISTRY_QUEUE,
        {"tid": "1770000000000000010", "status": "stopped", "name": "manager"},
    )
    stale_active = _write_json(
        ctx,
        WEFT_MANAGERS_REGISTRY_QUEUE,
        {"tid": "1770000000000000011", "status": "active", "name": "manager"},
    )
    live_handle = RunnerHandle(
        runner="host",
        kind="process",
        id=str(os.getpid()),
        control={"authority": "host-pid"},
        observations={"host_pids": [os.getpid()]},
    )
    live_active = _write_json(
        ctx,
        WEFT_MANAGERS_REGISTRY_QUEUE,
        {
            "tid": "1770000000000000012",
            "status": "active",
            "name": "manager",
            "runtime_handle": live_handle.to_dict(),
        },
    )

    result = run_runtime_prune(
        RuntimePruneConfig(
            context_path=ctx.root,
            queues=("managers",),
            min_age_seconds=0,
        )
    )

    classifications = {
        (candidate.message_id, candidate.classification)
        for candidate in result.candidates
    }
    assert (old_stopped, CLASS_SUPERSEDED_MANAGER) in classifications
    assert (stale_active, CLASS_STALE_MANAGER) in classifications
    assert all(candidate.message_id != live_active for candidate in result.candidates)


def test_streaming_prune_deletes_terminal_owner_marker_only(tmp_path) -> None:
    ctx = _context(tmp_path)
    stale_id = _write_json(
        ctx,
        WEFT_STREAMING_SESSIONS_QUEUE,
        {"tid": "1770000000000000020", "session_id": "stale", "queue": "T1.outbox"},
    )
    active_id = _write_json(
        ctx,
        WEFT_STREAMING_SESSIONS_QUEUE,
        {"tid": "1770000000000000021", "session_id": "active", "queue": "T2.outbox"},
    )
    _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"tid": "1770000000000000020", "status": "completed"},
    )
    _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"tid": "1770000000000000021", "status": "running"},
    )

    result = run_runtime_prune(
        RuntimePruneConfig(
            context_path=ctx.root,
            queues=("streaming",),
            min_age_seconds=0,
            apply=True,
        )
    )

    assert [(c.message_id, c.classification) for c in result.candidates] == [
        (stale_id, CLASS_STALE_STREAMING)
    ]
    remaining_ids = {
        message_id
        for _payload, message_id in _read_rows(ctx, WEFT_STREAMING_SESSIONS_QUEUE)
    }
    assert stale_id not in remaining_ids
    assert active_id in remaining_ids


def test_endpoint_prune_preserves_live_duplicate_claimants(tmp_path) -> None:
    ctx = _context(tmp_path)
    old_id = _write_json(
        ctx,
        WEFT_ENDPOINTS_REGISTRY_QUEUE,
        build_endpoint_record_payload(
            name="api",
            tid="1770000000000000030",
            inbox="T30.inbox",
            outbox="T30.outbox",
            ctrl_in="T30.ctrl_in",
            ctrl_out="T30.ctrl_out",
        ),
    )
    _write_json(
        ctx,
        WEFT_ENDPOINTS_REGISTRY_QUEUE,
        build_endpoint_record_payload(
            name="api",
            tid="1770000000000000030",
            inbox="T30.inbox",
            outbox="T30.outbox",
            ctrl_in="T30.ctrl_in",
            ctrl_out="T30.ctrl_out",
        ),
    )
    stale_owner = _write_json(
        ctx,
        WEFT_ENDPOINTS_REGISTRY_QUEUE,
        build_endpoint_record_payload(
            name="dead",
            tid="1770000000000000031",
            inbox="T31.inbox",
            outbox="T31.outbox",
            ctrl_in="T31.ctrl_in",
            ctrl_out="T31.ctrl_out",
        ),
    )
    live_a = _write_json(
        ctx,
        WEFT_ENDPOINTS_REGISTRY_QUEUE,
        build_endpoint_record_payload(
            name="shared",
            tid="1770000000000000032",
            inbox="T32.inbox",
            outbox="T32.outbox",
            ctrl_in="T32.ctrl_in",
            ctrl_out="T32.ctrl_out",
        ),
    )
    live_b = _write_json(
        ctx,
        WEFT_ENDPOINTS_REGISTRY_QUEUE,
        build_endpoint_record_payload(
            name="shared",
            tid="1770000000000000033",
            inbox="T33.inbox",
            outbox="T33.outbox",
            ctrl_in="T33.ctrl_in",
            ctrl_out="T33.ctrl_out",
        ),
    )
    for tid in (
        "1770000000000000030",
        "1770000000000000032",
        "1770000000000000033",
    ):
        _write_json(ctx, WEFT_TID_MAPPINGS_QUEUE, {"full": tid, "short": tid[-10:]})
    _write_json(
        ctx,
        WEFT_GLOBAL_LOG_QUEUE,
        {"tid": "1770000000000000031", "status": "completed"},
    )

    result = run_runtime_prune(
        RuntimePruneConfig(
            context_path=ctx.root,
            queues=("endpoints",),
            min_age_seconds=0,
        )
    )

    classifications = {
        (candidate.message_id, candidate.classification)
        for candidate in result.candidates
    }
    assert (old_id, CLASS_SUPERSEDED_ENDPOINT) in classifications
    assert (stale_owner, CLASS_STALE_ENDPOINT) in classifications
    assert all(
        candidate.message_id not in {live_a, live_b} for candidate in result.candidates
    )


def test_pipeline_rows_are_report_only_in_first_slice(tmp_path) -> None:
    ctx = _context(tmp_path)
    pipeline_id = _write_json(
        ctx,
        WEFT_PIPELINES_STATE_QUEUE,
        {"pipeline_tid": "1770000000000000040", "status": "completed"},
    )

    result = run_runtime_prune(
        RuntimePruneConfig(
            context_path=ctx.root,
            queues=("pipelines",),
            min_age_seconds=0,
            apply=True,
        )
    )

    assert [
        (c.message_id, c.classification, c.report_only) for c in result.candidates
    ] == [(pipeline_id, CLASS_UNSUPPORTED_PIPELINE, True)]
    assert result.deleted == 0
    remaining_ids = {
        message_id
        for _payload, message_id in _read_rows(ctx, WEFT_PIPELINES_STATE_QUEUE)
    }
    assert pipeline_id in remaining_ids
